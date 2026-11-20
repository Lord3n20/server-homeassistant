# Offizielles Image des SearXNG-Projekts (AGPL-3.0), fester Stand.
FROM searxng/searxng:2026.11.1-10db664f1

COPY settings.yml /etc/searxng/settings.yml
# Bei jedem Start ein neuer geheimer Schlüssel (nur für Sitzungen, hier ohne Bedeutung).
ENTRYPOINT ["/bin/sh", "-c", "export SEARXNG_SECRET=\"$(head -c 32 /dev/urandom | base64 | tr -dc a-zA-Z0-9)\"; exec /usr/local/searxng/entrypoint.sh"]
