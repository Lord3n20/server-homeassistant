#!/bin/bash
# Runs Continuwuity and registers every appservice (bridge) whose registration
# lies in /share/matrix/appservices. Registration happens through admin
# commands at startup, so a new or changed file there restarts the homeserver.

OPTS=/data/options.json
APPSERVICES=/share/matrix/appservices
CONFIG=/data/continuwuity.toml

SERVER_NAME=$(jq -r '.server_name // ""' "$OPTS")
FEDERATION=$(jq -r '.foederation' "$OPTS")
TOKEN=$(jq -r '.registrierungs_token // ""' "$OPTS")

if [ -z "$SERVER_NAME" ]; then
    echo "Bitte in der Konfiguration server_name setzen (z. B. matrix.example.org)."
    exit 1
fi

# The server name is baked into every user and room ID and can't change later.
mkdir -p /data/continuwuity
if [ -f /data/server_name ] && [ "$(cat /data/server_name)" != "$SERVER_NAME" ]; then
    echo "server_name war bisher $(cat /data/server_name) und lässt sich nicht mehr ändern."
    echo "Für einen neuen Namen die App neu installieren (alle Konten und Chats gehen verloren)."
    exit 1
fi
echo "$SERVER_NAME" > /data/server_name

registrations_hash() {
    cat "$APPSERVICES"/*.yaml 2>/dev/null | sha256sum
}

write_config() {
    local commands=() f
    for f in "$APPSERVICES"/*.yaml; do
        [ -f "$f" ] || continue
        echo "Bridge-Anmeldung gefunden: $(basename "$f")"
        commands+=("$(printf 'appservices register\n```\n%s\n```' "$(cat "$f")" | jq -Rs .)")
    done

    {
        echo '[global]'
        echo "server_name = \"$SERVER_NAME\""
        echo 'database_path = "/data/continuwuity"'
        echo 'address = ["0.0.0.0"]'
        echo 'port = 8008'
        echo "allow_federation = $FEDERATION"
        if [ -n "$TOKEN" ]; then
            echo 'allow_registration = true'
            echo "registration_token = $(jq -Rn --arg t "$TOKEN" '$t')"
        else
            echo 'allow_registration = false'
        fi
        echo 'new_user_displayname_suffix = ""'
        # WhatsApp/Signal videos and files can be large.
        echo 'max_request_size = 104857600'
        echo "admin_execute = [$(IFS=,; echo "${commands[*]}")]"
        echo 'admin_execute_errors_ignore = true'
        echo
        echo '[global.well_known]'
        echo "client = \"https://$SERVER_NAME\""
        echo "server = \"$SERVER_NAME:443\""
    } > "$CONFIG"
}

PID=
trap '[ -n "$PID" ] && kill "$PID" 2>/dev/null; wait "$PID"; exit 0' TERM INT

while true; do
    STATE=$(registrations_hash)
    write_config
    CONTINUWUITY_CONFIG="$CONFIG" /usr/bin/continuwuity &
    PID=$!

    RESTART=
    while kill -0 "$PID" 2>/dev/null; do
        sleep 15 &
        wait $!
        if [ "$(registrations_hash)" != "$STATE" ]; then
            echo "Bridge-Anmeldungen geändert, starte den Server neu."
            RESTART=1
            kill "$PID"
            wait "$PID"
            break
        fi
    done

    if [ -z "$RESTART" ]; then
        wait "$PID"
        exit $?
    fi
done
