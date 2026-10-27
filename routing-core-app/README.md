# RWaysTH routing-core

Home-Assistant-App mit dem ÖPNV-Router von [RWaysTH](https://github.com/Lord3n20/RWaysTH): eigener RAPTOR auf
den GTFS-Fahrplänen der Region Aachen plus Regional- und Fernzügen. Die App lädt den Fahrplan des heutigen Tages
(~25–40 MB RAM) und wechselt um Mitternacht ohne Ausfall auf den nächsten Tag.

Das Image wird im CI von RWaysTH gebaut (`ghcr.io/lord3n20/rwaysth-routing-core`), auf dem HA-Host wird nichts
kompiliert. Anfragen, Start und Ziel werden nie protokolliert; Koordinaten kommen nur im Request-Body.

## Einrichtung

1. GTFS-Dateien und optional `footpaths.csv` in den Konfigurationsordner der App legen
   (`/addon_configs/<id>_routing_core/`, z. B. über das Samba- oder Terminal-Add-on). Später übernimmt das
   ein eigenes Job-Add-on.
2. Optionen prüfen (Dateinamen, „:fern“ beim Fernverkehrs-Feed) und starten. Im Protokoll steht die Ladezeit.
3. **Nicht direkt ins Internet freigeben:** Reverse Proxy (z. B. Nginx Proxy Manager) mit Client-Zertifikat
   (`ssl_verify_client on`) und `access_log off` davorsetzen.

## API

- `GET /v1/status` – geladener Tag, Zahl der Halte/Muster
- `POST /v1/route` – `{"from":{"lat":…,"lon":…},"to":{…},"time":"2026-10-28T08:00:00+01:00","fern":false}`;
  `fern: true` lässt Fernverkehr zu, der nicht im Deutschlandticket ist

## Optionen

| Option | Bedeutung |
|---|---|
| `gtfs_dir` | Ordner der Feeds; Standard `/addon_configs/*_routing_jobs/gtfs/current` (Stand der Job-App, ab 0.1.6). Leer = eigener Konfigurationsordner |
| `gtfs` | Feeds im Ordner `gtfs_dir`, durch Komma getrennt; `:fern` markiert einen Feed als Fernverkehr |
| `footpaths` | Valhalla-Fußwege (CSV), leer = Luftlinie |
| `walk` | längster Umstiegsweg in m |
| `access` | Suchradius für Zugangshalte in m (Luftlinie); echte Wege bis zum Doppelten |
| `valhalla` | Valhalla-App für echte Zugangswege (`http://91c9b2fc-valhalla:8002`), leer = Luftlinie |
| `par` | gleichzeitige Anfragen |

## Versionen

| Version | Image |
|---|---|
| 0.1.6 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.6` @sha256:b1a22207097efcbb2fe0e9f64d3527f607cd056b4ac07f0ae48aa54ab01bd420 — Fahrpläne aus der Job-App (`gtfs_dir`) |
| 0.1.5 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.5` @sha256:1ed54c28abc323bf67f845b6b60e0fa762b45674e9530791f75db2b1981b1580 — `"debug": true` schlüsselt Valhalla-Zeiten auf |
| 0.1.4 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.4` @sha256:3affd6647d2daede9607db873fa95cf66b8851e86d5258620ce24ab892594bc5 — Valhalla-Aufrufe ohne Keep-Alive (schneller) |
| 0.1.3 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.3` @sha256:d5f28df828e584ad7bff4b90c9c4c681c2df534564e219683ee1636abb202711 — Antwort enthält Dauer der Valhalla-Aufrufe (`vh_ms`) |
| 0.1.2 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.2` @sha256:e5b692448f6f1ca812e86a7aedf9a125293810d93dcac1f0bd78f2662d5b2a66 — schnellere Halte-Suche vor der Valhalla-Matrix |
| 0.1.1 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.1` @sha256:4be2dedcc22b1000123aeeed342af2daef1a48904304d1fc4f2891f902bf8273 — Zugangswege über Valhalla (Option `valhalla`), Rückfall Luftlinie |
| 0.1.0 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.0` @sha256:a5e983dcdf07b36e27fe266fae0d2e41a715f2c43686f04dc9302e3d611b7937 |
