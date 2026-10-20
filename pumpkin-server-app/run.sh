#!/bin/bash
# Write pumpkin.toml from the add-on options (HA UI), start the console panel, then the server.
# Everything (config, worlds, logs, whitelist) lives in /data, Pumpkin's working directory.
set -e
cd /data
# RCON only listens on 127.0.0.1; a fresh password on every start is enough for the console
RCON_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
export RCON_PASSWORD
python3 /konfig.py
# Console panel for the HA sidebar (Ingress, port 8099); also syncs whitelist/ops and runs the pregen
python3 /konsole.py &
exec /opt/pumpkin/pumpkin "$@"
