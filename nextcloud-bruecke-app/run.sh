#!/bin/bash
# Serves the configured Nextcloud folders read-only as one NFSv3 export
# (rclone serve nfs, no FUSE needed). No VFS disk cache: files are only
# streamed through RAM, nothing is written to the SSD. Port 2049 is not
# published, so only apps in the hassio network can reach it. Mount with
#   mount -t nfs -o ro,vers=3,proto=tcp,port=2049,mountport=2049,nolock \
#     91c9b2fc-nextcloud-bruecke:/ /mnt/fotos
set -euo pipefail

O=/data/options.json
opt() { jq -r "$1" "$O"; }

URL=$(opt .webdav_url)
if [ -z "$URL" ] || [ -z "$(opt .app_passwort)" ]; then
  echo "webdav_url und app_passwort setzen" >&2
  exit 1
fi

export RCLONE_CONFIG_NC_TYPE=webdav
export RCLONE_CONFIG_NC_VENDOR=nextcloud
export RCLONE_CONFIG_NC_URL="$URL"
export RCLONE_CONFIG_NC_USER="$(opt .benutzer)"
export RCLONE_CONFIG_NC_PASS="$(rclone obscure "$(opt .app_passwort)")"

# One combine remote: each folder becomes a top-level directory.
# Entries are quoted CSV-style because paths may contain spaces.
export RCLONE_CONFIG_FOTOS_TYPE=combine
export RCLONE_CONFIG_FOTOS_UPSTREAMS="$(jq -r '[.ordner[] | "\"" + .name + "=nc:" + .pfad + "\""] | join(" ")' "$O")"
echo "Freigabe: $RCLONE_CONFIG_FOTOS_UPSTREAMS"

exec rclone serve nfs fotos: \
  --addr :2049 \
  --read-only \
  --vfs-cache-mode off \
  --buffer-size "$(opt .ram_puffer_mb)M" \
  --dir-cache-time 30m \
  --log-level NOTICE
