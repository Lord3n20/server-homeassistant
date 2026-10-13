#!/bin/bash
# Builds dist/HomeAssistant.jar + .jad (unsigned).
cd "$(dirname "$0")"
source ../tools/common.sh
build_app HomeAssistant HA "" "" no
