# Nextcloud-Brücke

Serves folders of a Nextcloud (WebDAV) read-only as one NFSv3 export with `rclone serve nfs`,
with a **VFS cache in RAM only** (tmpfs, size limit `ram_cache_mb`, nothing written to the SSD). No FUSE needed (HAOS has none).
Port 2049 is not published; only apps in the Home Assistant app network can reach it.

Each entry of `ordner` becomes a top-level directory of the export. Mount it e.g. from a
custom start script of the Immich app (`/addon_configs/<immich>/immich.sh`):

```sh
mkdir -p /mnt/fotos
mount -t nfs -o ro,vers=3,proto=tcp,port=2049,mountport=2049,nolock,soft,timeo=600 \
  91c9b2fc-nextcloud-bruecke:/ /mnt/fotos
```
