#!/bin/bash
# Copies <app>/dist/*.jad + *.jar to the memory card of the 6303i over Bluetooth (gammu)
# and reads them back to check. Usage: ./upload.sh signal|homeassistant
# Close the app on the phone first and switch Bluetooth on.
# gammu's final message "file does not exist" is wrong on this phone; the read-back decides.
set -e
cd "$(dirname "$0")"
APP=${1:?usage: ./upload.sh signal|homeassistant}
RC=private/gammu-bluetooth.rc
PHONE=$(sed -n 's/^device *= *//p' $RC)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# A short connect wakes the phone up, otherwise gammu often gets no answer.
timeout 20 bluetoothctl connect "$PHONE" >/dev/null 2>&1 || true
timeout 15 bluetoothctl disconnect "$PHONE" >/dev/null 2>&1 || true

for f in "$APP"/dist/*.jad "$APP"/dist/*.jar; do
    echo "uploading $(basename "$f") …"
    (cd "$(dirname "$f")" && timeout 150 gammu -c "$OLDPWD/$RC" addfile a:/ "$(basename "$f")" >/dev/null 2>&1) || true
    (cd "$TMP" && timeout 150 gammu -c "$OLDPWD/$RC" getfiles "a:/$(basename "$f")" >/dev/null 2>&1) || true
    if cmp -s "$f" "$TMP/$(basename "$f")"; then
        echo "  ok, identical on the card"
    else
        echo "  ERROR: not (completely) on the card" >&2
        exit 1
    fi
done
