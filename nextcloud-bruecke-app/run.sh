#!/bin/bash
# Mounts each configured Nextcloud folder read-only via rclone (no VFS disk
# cache, so nothing is written to the SSD) and shares /mnt/fotos over SMB.
# Only reachable inside the hassio network (no ports published).
set -euo pipefail

O=/data/options.json
opt() { jq -r "$1" "$O"; }

URL=$(opt .webdav_url)
SMB_USER=$(opt .smb_benutzer)
SMB_PASS=$(opt .smb_passwort)
PUFFER=$(opt .ram_puffer_mb)
if [ -z "$URL" ] || [ -z "$(opt .app_passwort)" ] || [ -z "$SMB_PASS" ]; then
  echo "webdav_url, app_passwort und smb_passwort setzen" >&2
  exit 1
fi

export RCLONE_CONFIG_NC_TYPE=webdav
export RCLONE_CONFIG_NC_VENDOR=nextcloud
export RCLONE_CONFIG_NC_URL="$URL"
export RCLONE_CONFIG_NC_USER="$(opt .benutzer)"
export RCLONE_CONFIG_NC_PASS="$(rclone obscure "$(opt .app_passwort)")"

mkdir -p /mnt/fotos
pids=()
while IFS=$'\t' read -r name pfad; do
  ziel="/mnt/fotos/$name"
  mkdir -p "$ziel"
  echo "Binde nc:$pfad nach $ziel ein"
  rclone mount "nc:$pfad" "$ziel" \
    --read-only --allow-other \
    --vfs-cache-mode off \
    --buffer-size "${PUFFER}M" \
    --dir-cache-time 30m \
    --attr-timeout 30m \
    --log-level NOTICE &
  pids+=($!)
done < <(jq -r '.ordner[] | [.name, .pfad] | @tsv' "$O")

id "$SMB_USER" >/dev/null 2>&1 || adduser -D -H -s /sbin/nologin "$SMB_USER"
cat > /etc/samba/smb.conf <<CONF
[global]
  server role = standalone server
  server min protocol = SMB2
  disable netbios = yes
  smb ports = 445
  load printers = no
  printing = bsd
  printcap name = /dev/null
  map to guest = never
  hosts allow = 172.30.32.0/23 127.0.0.1
  log file = /dev/stdout
  log level = 1

[fotos]
  path = /mnt/fotos
  read only = yes
  browseable = yes
  valid users = $SMB_USER
  force user = root
CONF
printf '%s\n%s\n' "$SMB_PASS" "$SMB_PASS" | smbpasswd -a -s "$SMB_USER" >/dev/null

smbd --foreground --no-process-group --debug-stdout &
pids+=($!)

# Stop everything when one mount or smbd ends, so the watchdog restarts us.
trap 'kill "${pids[@]}" 2>/dev/null; fusermount3 -uz /mnt/fotos/* 2>/dev/null' EXIT
wait -n "${pids[@]}"
echo "Ein Prozess ist beendet, Neustart" >&2
exit 1
