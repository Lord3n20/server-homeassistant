#!/bin/bash
# Serves the configured Nextcloud folders read-only as one NFSv3 export
# (rclone serve nfs, no FUSE needed). The VFS cache lives in /tmp, which is
# a tmpfs (config.yaml), so file data never touches the SSD. Without a cache
# every 1 MiB NFS read reopened the file with a fresh read-ahead buffer, which
# meant one HTTP request per read and let rclone grow to 4.5 GB until the OOM
# killer stopped it. NFS handles are kept on disk (tiny) so clients survive a
# restart of this app. Port 2049 is not
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

CACHE_MB=$(opt '.ram_cache_mb // 1024')
# Soft limit for the Go heap on top of the tmpfs cache.
export GOMEMLIMIT=768MiB
mkdir -p /data/nfs-handles

exec rclone serve nfs fotos: \
  --addr :2049 \
  --read-only \
  --vfs-cache-mode full \
  --cache-dir /tmp/rclone \
  --vfs-cache-max-size "${CACHE_MB}M" \
  --vfs-cache-max-age 15m \
  --vfs-cache-poll-interval 15s \
  --vfs-read-chunk-size 8M \
  --vfs-read-chunk-size-limit 64M \
  --buffer-size 0 \
  --transfers 4 \
  --nfs-cache-type disk \
  --nfs-cache-dir /data/nfs-handles \
  --dir-cache-time 30m \
  --log-level NOTICE
