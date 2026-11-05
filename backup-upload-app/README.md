# Backup-Upload

Uploads the newest automatic Home Assistant backup to a Nextcloud with rclone's **chunked upload**.
Use it when backups have outgrown what the server accepts as a single upload (HTTP 413), which the
HA WebDAV integration cannot work around.

- Checks every 15 minutes and uploads the newest `automatic_backup_*.tar` that is not there yet.
- Only uploads encrypted backups. Otherwise it stops and shows a notification in HA.
- Writes the same file names and `.metadata.json` as the WebDAV integration, so the backups stay visible and restorable in HA.
- After a successful upload it keeps the newest `behalten` complete backups. Files in that folder that do not start with `Automatic_backup_` are left alone.
- Failures show one persistent notification in HA and are retried hourly.

In the HA backup settings, untick the WebDAV location for automatic backups, so HA no longer tries the upload itself.
