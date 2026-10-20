Minecraft Java server based on [Pumpkin](https://github.com/Pumpkin-MC/Pumpkin), a server written in Rust,
using the fork [Lord3n20/pumpkin-fork](https://github.com/Lord3n20/pumpkin-fork) (branch `stierzunge`).
No Java needed. The server binary and the WASM plugins are downloaded from the fork's release when the
app image is built; the release tag is `PUMPKIN_RELEASE` in the `Dockerfile`.

Ports: 25565/tcp (Minecraft), 24454/udp (Simple Voice Chat). Clients need the matching Minecraft version
(26.3). Pumpkin is still in development, so expect missing vanilla features.

Additions of the fork, all switchable in the Configuration tab:
- Seed protection: a new world gets a secret (`<world>/stierzunge-seed-secret`) that is mixed into the seeds of
  structures, caves and ores, so seed crackers cannot find villages or diamonds. Biomes and terrain stay as
  for the normal seed. The secret is kept for the world's lifetime – never delete or change that file.
- Light anti-cheat (moved-too-quickly set-back, kick for flying) and anti-xray (hidden ores).
- Hidden from the server list (`enable_status` off), like vanilla `enable-status=false`.

Plugins: Simple Voice Chat (PumpkinVoice) and PumpkinPregen. On start the app pre-generates the Overworld,
then the Nether, then the End around 0 0 (radii in the options, 0 = off). Finished radii are remembered in
`<world>/stierzunge-pregen.json`; a larger radius later goes over the whole area again, where existing
chunks are only loaded, which is much faster. `pregen cancel` in the console
stops it until the next restart.

Whitelist and operators are lists in the options and authoritative: on every start names are added or removed
so `whitelist.json` and `ops.json` match them.

Console: the sidebar entry "Pumpkin-Konsole" (admins only, via Ingress) shows the live server log and sends
commands through RCON, e.g. `list`, `say …`, `pregen status`. RCON only listens inside the container with
a new random password on every start.

All data (`pumpkin.toml`, worlds, logs, whitelist) is in the app's `/data` folder. `pumpkin.toml` is rewritten
from the options on every start. Telemetry and Bedrock support of Pumpkin are switched off.
