# Nextcloud-Brücke

Mounts folders of a Nextcloud (WebDAV) read-only with rclone, **without a VFS disk cache**
(files are only streamed through RAM), and shares them as the SMB share `fotos`
inside the Home Assistant app network (no ports published).

Meant for photo apps such as Immich that need a local path or an SMB share, e.g. in the
Immich app: `networkdisks: //91c9b2fc-nextcloud-bruecke/fotos`.
