#!/bin/bash
# Take Minecraft version, world folder, RAM, distances and security settings from the add-on options (HA UI), then start the itzg server
OPTIONS=/data/options.json
if [ -f "$OPTIONS" ]; then
  export VERSION="$(jq -r '.version // "LATEST"' "$OPTIONS")"
  export LEVEL="$(jq -r '.welt // "world"' "$OPTIONS")"
  export INIT_MEMORY="$(jq -r '.init_memory' "$OPTIONS")"
  export MAX_MEMORY="$(jq -r '.max_memory' "$OPTIONS")"
  export VIEW_DISTANCE="$(jq -r '.view_distance' "$OPTIONS")"
  export SIMULATION_DISTANCE="$(jq -r '.simulation_distance' "$OPTIONS")"
  export ENFORCE_WHITELIST="$(jq -r '.enforce_whitelist | if . == null then true else . end' "$OPTIONS")"
  export ENABLE_STATUS="$(jq -r '.enable_status | if . == null then false else . end' "$OPTIONS")"
  export PREVENT_PROXY_CONNECTIONS="$(jq -r '.prevent_proxy_connections | if . == null then true else . end' "$OPTIONS")"
  export RATE_LIMIT="$(jq -r '.rate_limit | if . == null then 500 else . end' "$OPTIONS")"
fi
# Console panel for the HA sidebar (Ingress, port 8099), next to the server
python3 /konsole.py &
exec /image/scripts/start "$@"
