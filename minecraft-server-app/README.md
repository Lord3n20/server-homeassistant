Very simple minecraft java paper server based on https://github.com/itzg/docker-minecraft-server \
without whitelist\
Port 25565\
Survival\
Maybe options will be added later

Minecraft version (`version`, default `LATEST`) and world folder (`welt`, default `world`) are app options.
Minecraft cannot load a world in an older version than the one that last saved it (Paper refuses to start),
so for a downgrade pick a new world name or restore a backup from that version; the current world stays untouched.
The plugins are optional (`?` in `MODRINTH_PROJECTS`): if one has no build for the chosen version, the server starts without it.

RAM, view distance, simulation distance and the security settings (enforce whitelist, visibility in the server list,
proxy blocking, packet limit) can be changed in the app's Configuration tab (restart the app afterwards).
RCON stays enabled for the console but is not published as a port.

Plugins (downloaded from Modrinth on every start): Voxy Server Side (sends far terrain to players
with the Voxy client mod, same Minecraft version needed) and Chunky (pre-generating chunks).

Console: the sidebar entry "Minecraft-Konsole" (admins only, via Ingress) shows the live server log and
sends commands through RCON, e.g. `list`, `say …`, `chunky progress`.
