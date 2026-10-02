# Packs both upstream services of claude-mail-mcp into one Home Assistant app:
#   - the connector (IMAP/SMTP/CalDAV), listening only on 127.0.0.1:3220
#   - the OAuth 2.1 layer in front of it, listening on 8080
# Both are taken from the published multi-arch images (amd64 + arm64), so
# nothing is compiled on the Home Assistant host.
#
# To update: bump MCP_VERSION here and `version` in config.yaml.
ARG MCP_VERSION=0.7.2

FROM ghcr.io/yannichock/claude-mail-mcp-oauth:${MCP_VERSION} AS oauth

FROM ghcr.io/yannichock/claude-mail-mcp:${MCP_VERSION}

USER root

# The connector image already has the user mailmcp (uid 100 / gid 101).
# mailoauth gets the same ids it has in the upstream OAuth image.
# mailsecrets is the one group both share, for /data/secrets/shared only.
RUN apk add --no-cache su-exec \
 && addgroup -S -g 103 mailoauth \
 && adduser -S -u 102 -G mailoauth mailoauth \
 && addgroup -S -g 1500 mailsecrets \
 && addgroup mailmcp mailsecrets \
 && addgroup mailoauth mailsecrets

COPY --from=oauth /app/oauth /app/oauth
COPY run.sh /run.sh
RUN chmod 755 /run.sh

ENTRYPOINT ["/run.sh"]
