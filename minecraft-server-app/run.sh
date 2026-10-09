#!/bin/bash
# Take RAM and distances from the add-on options (HA UI), then start the itzg server
OPTIONS=/data/options.json
if [ -f "$OPTIONS" ]; then
  export INIT_MEMORY="$(jq -r '.init_memory' "$OPTIONS")"
  export MAX_MEMORY="$(jq -r '.max_memory' "$OPTIONS")"
  export VIEW_DISTANCE="$(jq -r '.view_distance' "$OPTIONS")"
  export SIMULATION_DISTANCE="$(jq -r '.simulation_distance' "$OPTIONS")"
fi
exec /image/scripts/start "$@"
