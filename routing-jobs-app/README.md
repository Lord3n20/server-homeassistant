# SchnellWeg routing-jobs

Home-Assistant-App mit den Datenjobs von [SchnellWeg](https://github.com/Lord3n20/SchnellWeg). Sie läuft einmal durch
und beendet sich; gestartet wird sie von einer HA-Automation (`hassio.addon_start`), nicht von selbst.

| Job | macht | Ergebnis im Konfigurationsordner |
|---|---|---|
| `gtfs` | Fahrpläne von gtfs.de laden (ETag), Nahverkehr auf `gtfs_box` schneiden, mit dem RAPTOR-Lader für heute und in 6 Tagen prüfen | `gtfs/current/{sr_gtfs,de_rv,de_fv}.zip` |
| `osm` | nur wenn der Stand älter als `osm_tage` ist: Geofabrik-Ausschnitte laden (ETag + md5), mit `osmconvert` auf das Regionspolygon schneiden, zusammenführen | `osm/current/region.osm.pbf` |
| `valhalla` | Valhalla-Tiles bauen, mit Testrouten (Fuß/Auto/Rad) prüfen, Valhalla-App neu starten | `valhalla/current/tiles.tar` |
| `geocode` | Adressen, Straßen, Orte und benannte Einrichtungen aus dem OSM-Stand für die Suche, nur bei neuem OSM-Stand | `geocode/current/geocode.tsv` |
| `map` | Kartenausschnitt aus der Protomaps-Basiskarte (`pmtiles extract`, nur nötige Byte-Bereiche) bis `map_zoom`, höchstens alle `osm_tage` | `map/current/region.pmtiles` |
| `footpaths` | Umstiegswege zwischen nahen Halten (≤ 400 m) mit einem kurz gestarteten Valhalla auf den aktuellen Tiles rechnen, nur bei neuem GTFS- oder Tile-Stand | `footpaths/current/footpaths.csv` |

- Unveränderte Quellen → Job übersprungen. Neuer Stand wird erst nach der Prüfung per Symlink umgeschaltet,
  der vorige bleibt als `previous`.
- Vor dem Start: Speicherbudget = min(Option, freier Speicher − 1 GB), mindestens 1 GB, sonst Abbruch.
  Während des Laufs wird der anonyme Speicher des Containers gemessen; beim Überschreiten bricht der Job ab.
- Alle Programme laufen mit `nice -n 19` und `ionice -c3`, höchstens 4 Threads.
- Ergebnis (Spitze, Laufzeit, Stand) als HA-Benachrichtigung; Detailprotokolle liegen im jeweiligen Stand.
- Hat `gtfs`, `footpaths` oder `geocode` einen neuen Stand gebracht, wird routing-core am Ende einmal neu gestartet.
- Die großen Dateien sind vom HA-Backup ausgenommen.

## Automation (Beispiel)

Täglich, mit allen Jobs: `gtfs` baut nur bei neuen Fahrplänen, `osm` + `valhalla` nur etwa wöchentlich
(`osm_tage`). In der Praxis zusätzlich: nur starten, wenn auf dem Spieleserver niemand online ist, sonst alle
30 min erneut prüfen bis 07:00.

```yaml
triggers:
  - trigger: time
    at: "04:00:00"
actions:
  - action: hassio.app_start
    data:
      app: <id>_routing_jobs
```

## Versionen

| Version | Image |
|---|---|
| 0.1.8 | `ghcr.io/lord3n20/schnellweg-routing-jobs:0.1.8` @sha256:8698aaadfe2774a7197f0b3b805b5d9885165fa128352f659f38ca34c4c16563 — Job `rwth` (RWTH-Gebäudeliste, D-18), neuer Image-Name `schnellweg-*` |
| 0.1.7 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.7` @sha256:60d4661918158d49e4625c004efec104772c58af3f71730d1c5c4dfd2c5d71b6 — Jobs `geocode` und `map` |
| 0.1.6 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.6` @sha256:5ad546f7c18320fb53775704f7f3bb750f0e9fd66bde8fb7523f374a24e90302 — `footpaths` ohne Valhalla-Protokoll (~1 MB je Lauf) |
| 0.1.5 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.5` @sha256:d5162163a5b0c1911ac3a3009d86541865db0162dcd7738c228b0e71d72a2dc5 — Job `footpaths` |
| 0.1.4 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.4` @sha256:b50e3e8e384b5da7dedc39ed60809bb3ead3a99a6eb7828ba2e2537211375fb9 — Job `gtfs`, OSM + Valhalla nur wöchentlich (`osm_tage`) |
| 0.1.3 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.3` @sha256:ca041c489dfbdb13d3c68489d60f52888d24d189ef7d112d2dea868f913cfec4 — Regionssignatur über Inhalt (SHA-256) statt Änderungszeit |
| 0.1.2 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.2` @sha256:e3159633e55133daa6eccd957537b003f214dc8eb38d87736caef45466f5155f — Job hängt nicht mehr nach der Valhalla-Prüfung |
| 0.1.1 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.1` @sha256:eb8d8e6384dedc23f5d0d30308b277a7f7563ea9ec454aafaf5317f9e6cbf27d — OSM-IDs neu nummeriert (Valhalla-Bau 2,9 → 1,2 GB) |
| 0.1.0 | `ghcr.io/lord3n20/rwaysth-routing-jobs:0.1.0` @sha256:194f022511d58f23d6857bf57de3c86d5ece3d7240c30817b533306cc55c71da |
