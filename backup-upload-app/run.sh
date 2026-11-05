#!/bin/bash
# Uploads the newest automatic Home Assistant backup to Nextcloud with rclone's
# chunked upload, for backups the server refuses as one PUT (HTTP 413). The
# file names and the .metadata.json match what the HA WebDAV integration
# writes, so the backups stay visible and restorable in HA.
#
# Only encrypted backups are uploaded. The metadata goes last, so a backup only
# counts as complete once all of it arrived. Old backups are removed only after
# a successful upload, and only if enough complete newer ones exist.
set -uo pipefail

O=${OPTIONS_DATEI:-/data/options.json}
BACKUPS=${BACKUP_ORDNER:-/backup}
TMP=${TMPDIR:-/tmp}
RUHE_S=900          # pause between checks
FEHLER_RUHE_S=3600  # pause after a failed upload
MIN_ALTER_S=600     # a younger file may still be being written

opt() { jq -r "$1" "$O"; }

URL=$(opt .webdav_url)
if [ -z "$URL" ] || [ -z "$(opt .app_passwort)" ]; then
  echo "webdav_url und app_passwort setzen" >&2
  exit 1
fi

: "${RCLONE_CONFIG_NC_TYPE:=webdav}"
export RCLONE_CONFIG_NC_TYPE
export RCLONE_CONFIG_NC_VENDOR=nextcloud
export RCLONE_CONFIG_NC_URL="$URL"
export RCLONE_CONFIG_NC_USER="$(opt .benutzer)"
RCLONE_CONFIG_NC_PASS="$(rclone obscure "$(opt .app_passwort)")"
export RCLONE_CONFIG_NC_PASS

ZIEL="nc:$(opt .ordner)"
BEHALTEN=$(opt '.behalten // 3')
RCLONE_OPT=(--retries 3 --low-level-retries 20 --log-level NOTICE)

melden() {
  echo "FEHLER: $1" >&2
  [ -n "${SUPERVISOR_TOKEN:-}" ] || return 0
  jq -n --arg m "$1" '{title: "Backup-Upload", message: $m, notification_id: "backup_upload"}' |
    curl -sS -m 30 -o /dev/null -X POST \
      -H "Authorization: Bearer $SUPERVISOR_TOKEN" -H 'Content-Type: application/json' \
      -d @- http://supervisor/core/api/services/persistent_notification/create || true
}

# "automatic_backup_2026_11_0_2026-11-05_15.28_15578979.tar" -> "2026-11-05_15.28_15578979"
stempel() { sed -nE 's/.*_([0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}\.[0-9]{2}_[0-9]+)(\.tar|\.metadata\.json)$/\1/p' <<<"$1"; }

neuestes_backup() {
  local f s
  for f in "$BACKUPS"/automatic_backup_*.tar; do
    [ -f "$f" ] || continue
    s=$(stempel "$f")
    [ -n "$s" ] && printf '%s\t%s\n' "$s" "$f"
  done | sort | tail -n 1 | cut -f2-
}

aufraeumen() {
  local alle komplett grenze name s
  alle=$(rclone lsf "$ZIEL" --files-only --include 'Automatic_backup_*') || return 1
  komplett=$(grep '\.metadata\.json$' <<<"$alle" | while read -r name; do stempel "$name"; done | sort)
  [ "$(grep -c . <<<"$komplett")" -ge "$BEHALTEN" ] || return 0
  grenze=$(tail -n "$BEHALTEN" <<<"$komplett" | head -n 1)
  while read -r name; do
    s=$(stempel "$name")
    if [ -n "$s" ] && [[ "$s" < "$grenze" ]]; then
      echo "Entferne altes Backup: $name"
      rclone deletefile "$ZIEL/$name" "${RCLONE_OPT[@]}" || return 1
    fi
  done <<<"$alle"
}

# 0 = nothing to do or uploaded, 1 = failed
ein_lauf() {
  local f alter info name ziel groesse fern
  f=$(neuestes_backup)
  [ -n "$f" ] || return 0
  alter=$(( $(date +%s) - $(stat -c %Y "$f") ))
  [ "$alter" -ge "$MIN_ALTER_S" ] || return 0

  info=$(tar -xOf "$f" --occurrence=1 ./backup.json) || { LETZTER_FEHLER="$f nicht lesbar"; return 1; }
  if [ "$(jq -r .protected <<<"$info")" != "true" ]; then
    LETZTER_FEHLER="$(basename "$f") ist nicht verschlüsselt, wird nicht hochgeladen"
    return 1
  fi

  name=$(jq -r '.name | gsub(" "; "_")' <<<"$info")
  ziel="${name}_$(stempel "$f")"
  if [ -n "$(rclone lsf "$ZIEL" --files-only --include "$ziel.metadata.json" 2>/dev/null)" ]; then
    return 0
  fi

  groesse=$(stat -c %s "$f")
  echo "Lade hoch: $ziel.tar ($(( groesse / 1048576 )) MiB)"
  if ! rclone copyto "$f" "$ZIEL/$ziel.tar" "${RCLONE_OPT[@]}" --stats 10m --stats-log-level NOTICE --stats-one-line; then
    LETZTER_FEHLER="Upload von $ziel fehlgeschlagen"
    return 1
  fi
  fern=$(rclone lsf "$ZIEL" --files-only --format s --include "$ziel.tar")
  if [ "$fern" != "$groesse" ]; then
    LETZTER_FEHLER="$ziel: Größe in der Nextcloud ($fern) passt nicht zu lokal ($groesse)"
    return 1
  fi

  jq -c --argjson size "$groesse" '{
      addons: [.addons[] | {name, slug, version}],
      backup_id: .slug,
      date: (.extra["supervisor.backup_request_date"] // .date),
      database_included: (.homeassistant.exclude_database | not),
      extra_metadata: .extra,
      folders: .folders,
      homeassistant_included: (.homeassistant != null),
      homeassistant_version: .homeassistant.version,
      name: .name,
      protected: .protected,
      size: $size
    }' <<<"$info" >"$TMP/$ziel.metadata.json" || { LETZTER_FEHLER="$ziel: Metadaten nicht erzeugbar"; return 1; }
  if ! rclone copyto "$TMP/$ziel.metadata.json" "$ZIEL/$ziel.metadata.json" "${RCLONE_OPT[@]}"; then
    rm -f "$TMP/$ziel.metadata.json"
    LETZTER_FEHLER="$ziel: Metadaten-Upload fehlgeschlagen"
    return 1
  fi
  rm -f "$TMP/$ziel.metadata.json"
  echo "Fertig: $ziel"

  aufraeumen || echo "Aufräumen fehlgeschlagen, nächster Versuch beim nächsten Upload" >&2
  return 0
}

LETZTER_FEHLER=""
GEMELDET=""
if [ "${1:-}" = "--einmal" ]; then
  ein_lauf || { echo "FEHLER: $LETZTER_FEHLER" >&2; exit 1; }
  exit 0
fi

echo "Bereit: $ZIEL, behalte $BEHALTEN"
while true; do
  if ein_lauf; then
    GEMELDET=""
    sleep "$RUHE_S"
  else
    # One notification per problem, not one per retry.
    if [ "$LETZTER_FEHLER" != "$GEMELDET" ]; then
      melden "$LETZTER_FEHLER"
      GEMELDET=$LETZTER_FEHLER
    else
      echo "FEHLER: $LETZTER_FEHLER" >&2
    fi
    sleep "$FEHLER_RUHE_S"
  fi
done
