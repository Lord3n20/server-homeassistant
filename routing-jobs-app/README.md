# RWaysTH routing-jobs

Home-Assistant-App mit den Datenjobs von [RWaysTH](https://github.com/Lord3n20/RWaysTH). Sie läuft einmal durch
und beendet sich; gestartet wird sie von einer HA-Automation (`hassio.addon_start`), nicht von selbst.

| Job | macht | Ergebnis im Konfigurationsordner |
|---|---|---|
| `osm` | Geofabrik-Ausschnitte laden (ETag + md5), mit `osmconvert` auf das Regionspolygon schneiden, zusammenführen | `osm/current/region.osm.pbf` |
| `valhalla` | Valhalla-Tiles bauen, mit Testrouten (Fuß/Auto/Rad) prüfen, Valhalla-App neu starten | `valhalla/current/tiles.tar` |

- Unveränderte Quellen → Job übersprungen. Neuer Stand wird erst nach der Prüfung per Symlink umgeschaltet,
  der vorige bleibt als `previous`.
- Vor dem Start: Speicherbudget = min(Option, freier Speicher − 1 GB), mindestens 1 GB, sonst Abbruch.
  Während des Laufs wird der anonyme Speicher des Containers gemessen; beim Überschreiten bricht der Job ab.
- Alle Programme laufen mit `nice -n 19` und `ionice -c3`, höchstens 4 Threads.
- Ergebnis (Spitze, Laufzeit, Stand) als HA-Benachrichtigung; Detailprotokolle liegen im jeweiligen Stand.
- Die großen Dateien sind vom HA-Backup ausgenommen.

## Automation (Beispiel)

```yaml
triggers:
  - trigger: time
    at: "04:00:00"
actions:
  - action: hassio.addon_start
    data:
      addon: <id>_routing_jobs
```

## Versionen

| Version | Image |
|---|---|
| 0.1.3 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.3` @sha256:ca041c489dfbdb13d3c68489d60f52888d24d189ef7d112d2dea868f913cfec4 — Regionssignatur über Inhalt (SHA-256) statt Änderungszeit |
| 0.1.2 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.2` @sha256:e3159633e55133daa6eccd957537b003f214dc8eb38d87736caef45466f5155f — Job hängt nicht mehr nach der Valhalla-Prüfung |
| 0.1.1 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.1` @sha256:eb8d8e6384dedc23f5d0d30308b277a7f7563ea9ec454aafaf5317f9e6cbf27d — OSM-IDs neu nummeriert (Valhalla-Bau 2,9 → 1,2 GB) |
| 0.1.0 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.0` @sha256:194f022511d58f23d6857bf57de3c86d5ece3d7240c30817b533306cc55c71da |
