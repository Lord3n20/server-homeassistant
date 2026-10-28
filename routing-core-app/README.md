# SchnellWeg routing-core

Home-Assistant-App mit dem ÖPNV-Router von [SchnellWeg](https://github.com/Lord3n20/SchnellWeg): eigener RAPTOR auf
den GTFS-Fahrplänen der Region Aachen plus Regional- und Fernzügen. Die App lädt den Fahrplan des heutigen Tages
(~25–40 MB RAM) und wechselt um Mitternacht ohne Ausfall auf den nächsten Tag.

Das Image wird im CI von SchnellWeg gebaut (`ghcr.io/lord3n20/rwaysth-routing-core`, ab dem nächsten Release `schnellweg-routing-core`), auf dem HA-Host wird nichts
kompiliert. Anfragen, Start und Ziel werden nie protokolliert; Koordinaten kommen nur im Request-Body.

## Einrichtung

1. Die App „SchnellWeg routing-jobs“ einmal mit `gtfs,osm,valhalla,footpaths` laufen lassen: Sie legt Fahrpläne und
   Fußwege ab, die diese App standardmäßig liest (`gtfs_dir`, `footpaths`). Ohne Job-App: Dateien in den eigenen
   Konfigurationsordner legen (`/addon_configs/<id>_routing_core/`) und `gtfs_dir` leeren.
2. Optionen prüfen (Dateinamen, „:fern“ beim Fernverkehrs-Feed) und starten. Im Protokoll steht die Ladezeit.
3. **Nicht direkt ins Internet freigeben:** Reverse Proxy (z. B. Nginx Proxy Manager) mit Client-Zertifikat
   (`ssl_verify_client on`) und `access_log off` davorsetzen.

## API

- `GET /v1/status` – geladener Tag, Zahl der Halte/Muster, Echtzeitstand
- `POST /v1/route` – `{"from":{"lat":…,"lon":…},"to":{…},"time":"2026-10-28T08:00:00+01:00","fern":false}`;
  `fern: true` lässt Fernverkehr zu, der nicht im Deutschlandticket ist; `"kmh": 5.5` (2–8, ab 0.1.8) rechnet
  Zugangswege und Umstiege mit diesem Gehtempo statt 4,3 km/h (Mindestumstieg 60 s bleibt). Ab 0.1.9 mit
  Echtzeit: `dep_rt`/`arr_rt`, `canceled`, `skipped` je Fahrt, `risk` (`anschluss` | `ausfall`) je Weg
- `POST /v1/departures` – `{"stops":["0:233718",…],"n":3}` → nächste Abfahrten je Halt (Plan + Echtzeit)
- `GET /v1/stops` – alle Halte als `[id, name, lat, lon]` für die App (ETag)
- `POST /v1/search` – `{"q":"pontstr 5"}` → Adressen, Straßen, Orte, Einrichtungen
- `GET /v1/map` – Liste der Kartendateien (Übersicht + Bundesländer, Größe, Stand); `GET /v1/map/{id}` – Datei (PMTiles, Range, Last-Modified); © OpenStreetMap-Mitwirkende, Protomaps

Echtzeit (gtfs.de, CC BY-SA 4.0) wird nur abgerufen, wenn Anfragen kommen, höchstens alle 30 s.

## Optionen

| Option | Bedeutung |
|---|---|
| `gtfs_dir` | Ordner der Feeds; Standard `/addon_configs/*_routing_jobs/gtfs/current` (Stand der Job-App, ab 0.1.6). Leer = eigener Konfigurationsordner |
| `gtfs` | Feeds im Ordner `gtfs_dir`, durch Komma getrennt; `:fern` markiert einen Feed als Fernverkehr |
| `footpaths` | Valhalla-Fußwege (CSV); Standard aus der Job-App (Glob, ab 0.1.7), leer oder nicht gefunden = Luftlinie |
| `geocode` | Suchlisten aus der Job-App (ab 0.1.9; ab 0.1.11 mehrere, durch Komma getrennt: OSM + RWTH-Gebäude), leer oder nicht gefunden = Suche aus |
| `map` | Kartenordner der Job-App (`map/current`, ab 0.1.13; vorher eine Datei), leer oder nicht gefunden = keine Karte |
| `rt` | GTFS-RT-Feed, leer = nur Fahrplan |
| `walk` | längster Umstiegsweg in m |
| `access` | Suchradius für Zugangshalte in m (Luftlinie); echte Wege bis zum Doppelten |
| `valhalla` | Valhalla-App für echte Zugangswege (`http://91c9b2fc-valhalla:8002`), leer = Luftlinie |
| `par` | gleichzeitige Anfragen |

## Versionen

| Version | Image |
|---|---|
| 0.1.13 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.13` @sha256:1203ebec836d5193d7d7d4a373f162c2513fbef708eb81e38ff1eecf5b03cb68 — `/v1/map` listet Regionen, `/v1/map/{id}` (D-20) |
| 0.1.12 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.12` @sha256:c427c05d72f7fe09a105a96bdec5adc7bd158c48263382c6d4709f2de061cd6c — Wegformen der Fußwege, Halte je Fahrt, reiner Fußweg als Vergleich (D-19) |
| 0.1.11 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.11` @sha256:ec6596380f8837ea21cda5504249579f4e8fdd1981a41f3ba3efe9c75ae5f858 — mehrere Suchlisten (`geocode` mit Komma), RWTH-Gebäude aus der Job-App (D-18), neuer Image-Name `schnellweg-*` |
| 0.1.10 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.10` @sha256:1f4a40cbc43b7f1e4a4c8ebb17bd77859c3ee63fbce1b5d9e25ced4d56b193e7 — CA-Zertifikate (Echtzeit über HTTPS), schnellere Suche |
| 0.1.9 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.9` @sha256:9560ee825de73e7e2e65db3834178e84986356cd01b35b6e3f7196628515db7f — Echtzeit, Abfahrten, Halteliste, Suche, Karte |
| 0.1.8 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.8` @sha256:5e5b0f50ed89e39c307ff2b4086e7cb6431138c5f224ae943020f5392e8eab83 — Gehtempo je Anfrage (`kmh`) |
| 0.1.7 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.7` @sha256:4d6daebc08d27f0ffc6aa788d4b3e78c98e361bb7f961874353a9d6141fbda89 — Fußwege aus der Job-App (Glob, ohne Treffer Luftlinie) |
| 0.1.6 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.6` @sha256:b1a22207097efcbb2fe0e9f64d3527f607cd056b4ac07f0ae48aa54ab01bd420 — Fahrpläne aus der Job-App (`gtfs_dir`) |
| 0.1.5 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.5` @sha256:1ed54c28abc323bf67f845b6b60e0fa762b45674e9530791f75db2b1981b1580 — `"debug": true` schlüsselt Valhalla-Zeiten auf |
| 0.1.4 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.4` @sha256:3affd6647d2daede9607db873fa95cf66b8851e86d5258620ce24ab892594bc5 — Valhalla-Aufrufe ohne Keep-Alive (schneller) |
| 0.1.3 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.3` @sha256:d5f28df828e584ad7bff4b90c9c4c681c2df534564e219683ee1636abb202711 — Antwort enthält Dauer der Valhalla-Aufrufe (`vh_ms`) |
| 0.1.2 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.2` @sha256:e5b692448f6f1ca812e86a7aedf9a125293810d93dcac1f0bd78f2662d5b2a66 — schnellere Halte-Suche vor der Valhalla-Matrix |
| 0.1.1 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.1` @sha256:4be2dedcc22b1000123aeeed342af2daef1a48904304d1fc4f2891f902bf8273 — Zugangswege über Valhalla (Option `valhalla`), Rückfall Luftlinie |
| 0.1.0 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.0` @sha256:a5e983dcdf07b36e27fe266fae0d2e41a715f2c43686f04dc9302e3d611b7937 |
