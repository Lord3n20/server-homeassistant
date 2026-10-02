#!/bin/sh
# Starts the claude-mail-mcp connector and its OAuth layer side by side.
# Each runs as its own unprivileged user, as in the upstream docker-compose setup.
set -eu

log() { echo "[claude-mail-mcp] $*"; }

OPTS=/data/options.json
opt() {
  node -e 'const o=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));const v=o[process.argv[2]];process.stdout.write(v==null?"":String(v))' "$OPTS" "$1"
}

PUBLIC_URL="$(opt public_url)"
PUBLIC_URL="${PUBLIC_URL%/}"
LOG_LEVEL="$(opt log_level)"
[ -n "$LOG_LEVEL" ] || LOG_LEVEL=info

if [ -z "$PUBLIC_URL" ]; then
  log "public_url ist nicht gesetzt."
  log "Trage in der Konfiguration die öffentliche HTTPS-Adresse ein, unter der dieses Add-on erreichbar ist"
  log "(z.B. https://mail-mcp.deine-domain.de), und starte das Add-on neu."
  exit 1
fi

# Persistent layout inside the add-on's /data:
#   /data/mail            accounts.json (all mailbox passwords)  -> connector only
#   /data/oauth           OAuth state, operator, claim token      -> OAuth layer only
#   /data/secrets/oauth   OAuth signing key                       -> OAuth layer only
#   /data/secrets/shared  auth token + settings key                -> both (group mailsecrets)
mkdir -p /data/mail /data/oauth /data/secrets/shared /data/secrets/oauth
chown mailmcp:mailmcp /data/mail
chmod 700 /data/mail
chown mailoauth:mailoauth /data/oauth /data/secrets/oauth
chmod 700 /data/oauth /data/secrets/oauth
chown root:mailsecrets /data/secrets/shared
chmod 2770 /data/secrets/shared

COMMON_ENV="NODE_ENV=production PUBLIC_URL=$PUBLIC_URL LOG_LEVEL=$LOG_LEVEL TRUST_PROXY=1 SETTINGS_SIGNING_KEY_FILE=/data/secrets/shared/settings_signing_key.txt"

log "Starte Connector (intern auf 127.0.0.1:3220) ..."
# shellcheck disable=SC2086
( cd /app && exec su-exec mailmcp env $COMMON_ENV \
    HOST=127.0.0.1 PORT=3220 \
    ACCOUNTS_FILE=/data/mail/accounts.json \
    AUTH_TOKEN_FILE=/data/secrets/shared/auth_token.txt \
    node dist/src/index.js ) &
MCP_PID=$!

# The connector generates the shared secrets on first boot; give it a moment
# so the OAuth layer reads them instead of racing it.
i=0
while [ ! -s /data/secrets/shared/auth_token.txt ] && [ "$i" -lt 30 ]; do
  sleep 1
  i=$((i + 1))
done

log "Starte OAuth-Schicht (Port 8080) ..."
# shellcheck disable=SC2086
( cd /app/oauth && exec su-exec mailoauth env $COMMON_ENV \
    HOST=0.0.0.0 PORT=8080 \
    UPSTREAM_MCP_URL=http://127.0.0.1:3220 \
    UPSTREAM_AUTH_TOKEN_FILE=/data/secrets/shared/auth_token.txt \
    SIGNING_KEY_FILE=/data/secrets/oauth/oauth_signing_key.txt \
    STATE_FILE=/data/oauth/oauth-state.json \
    node dist/oauth/src/index.js ) &
OAUTH_PID=$!

stop() {
  log "Beende ..."
  kill -TERM "$MCP_PID" "$OAUTH_PID" 2>/dev/null || true
  wait || true
  exit 0
}
trap stop TERM INT

# If either process dies, stop the other and exit so the Supervisor watchdog restarts us.
while kill -0 "$MCP_PID" 2>/dev/null && kill -0 "$OAUTH_PID" 2>/dev/null; do
  sleep 5 &
  wait $! || true
done

log "Ein Dienst wurde beendet, stoppe das Add-on."
kill -TERM "$MCP_PID" "$OAUTH_PID" 2>/dev/null || true
wait || true
exit 1
