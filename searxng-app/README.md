# Suche (SearXNG)

[SearXNG](https://github.com/searxng/searxng) (AGPL-3.0) aus dem offiziellen Image, ohne Port nach außen. Die Budget-App
fragt darüber vor dem lokalen Monatsbericht ein paar Fakten ab (`http://91c9b2fc-searxng:8080/search?format=json&q=…`):
Tagesgeldzinsen, EZB-Leitzins, Inflation und je Depotposition Kosten und Infos zur ISIN. Die Suchmaschinen sehen nur
diese Anfragen (also die ISINs), nie Beträge.
