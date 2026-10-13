#!/bin/bash
# Builds dist/Signal.jar + .jad (signed if ../private/certificate/ exists).
# Also needs JSR-75 (files), JSR-135 (camera) and the Nokia UI API (lights, vibration).
#
# Signing: the 6303i trusts the own certificate because it sits in the certificate folder
# d:/predefhiddenfolder/certificates/user and its ext_info.sys entry allows code signing
# (see README). The app only gets the permissions listed here.
cd "$(dirname "$0")"
source ../tools/common.sh
build_app Signal SG ":$L/microemu-jsr-75.jar:$L/microemu-jsr-135.jar:$L/microemu-nokiaui.jar" \
"MIDlet-Permissions: javax.microedition.io.Connector.http, javax.microedition.io.Connector.file.read, javax.microedition.media.control.VideoControl.getSnapshot
MIDlet-Permissions-Opt: javax.microedition.io.PushRegistry" yes
