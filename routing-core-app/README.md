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
  `fern: true` lässt Fernverkehr zu, der nicht im Deutschlandticket ist; `"kmh": 5.5` (2–8, ab 0.1.8; mit `"art": "einrad"` 4–20, `"rad"` 6–35, ab 0.1.14; `"klapprad"` 6–30, `"fixie"` 6–40, `"rennrad"` 10–45, `"mtb"` 6–35, ab 0.1.15) rechnet
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
| 0.1.14 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.14` @sha256:bd1c492db5468779d135032925c6ec9a0054d226486144b8adb98911c4397776 — Fortbewegungsart `art` (fuss/einrad/rad), Rad nur Schiene mit Fahrradticket-Hinweis (D-21) |
| 0.1.15 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.15` @sha256:b4777e95db7288ea76ab969170efac4f83aba813f6a91b1ae2d00c40c3a3e80d — Fahrradtypen klapprad/fixie/rennrad/mtb: Klapprad im Bus ohne Ticket, Rennrad mit Valhalla-Fahrradkosten, MTB meidet Treppen (D-22) |
| 0.1.17 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.17` @sha256:11b1aa83b5e7b1d59564ff00711384e21278a3a60b071d863dc7f38309bd51c4 — Klapprad meidet Busse (nur wenn ≥ 3 min schneller), Zugangsradius mit Bus ≤ ×2,5, kein doppelter Direktweg (D-22) |
| 0.1.18 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.18` @sha256:a145f5f114db7cb7e2671084cbd5b6de04e1f2bc778eb2cb018b762b9156a2b5 — Klapprad-Bus-Malus 5 min; GOMEMLIMIT 400 MiB für den NRW-Fahrplan (~200 000 Halte, D-24) |
| 0.1.19 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.19` @sha256:d225058403474053ea37ea32d53d09f3f86d4d6c7f1d94aad7e710af65a88d88 — `/v1/stops` mit gzip (NRW ~6 MB JSON) |
| 0.1.20 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.20` @sha256:b22a0aa28bf193bab97dfa84df265d34fb11000d33c2b4be4d17ace9c61a3581 — GOGC 50 + GOMEMLIMIT 280 MiB (NRW lebend ~225 MB, vorher 444 MB RSS, D-24) |
| 0.1.21 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.21` @sha256:adb959679059dcfb0687ca5af723a55aacb2c34e5c709c2d05bc7b4979237031 — `/v1/status` meldet den eigenen Speicher (`mem`) |
| 0.1.22 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.22` @sha256:5ce58d19b30e9b85198b4da7c00ba101f93ce6e8acf32945836c544c12ae76f5 — Suchliste per mmap (Heap nur Zeilenlage + Koordinaten), GOMEMLIMIT 350 MiB (D-24) |
| 0.1.24 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.24` @sha256:596587acf96981d81d1d5da55be7b323f2c8191d683a2437d492161a0ff54940 — ohne Halt in Gehweite Suche bis 15 km, ab 5 km Fußweg erst grobe Schätzung + Nachfrage (D-25) |
| 0.1.25 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.25` @sha256:3faf4a15a5850a5522dadd8ae83600031459c1165978fdb66eeff28bbc4da688 — Adresssuche ganz DE: Suchliste + Index per mmap, nahe Treffer zuerst (`near`, D-26); Planung nach Ankunftszeit (`ankunft`, D-27) |
| 0.1.26 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.26` @sha256:b4d9c2026a3290a171ea4829353254968636fdf12969f9d25f871f1a4f07b4d9 — Klapprad, Fixie und MTB mit Valhallas Fahrradkosten ihres Typs statt Fußwegen (D-30) |
| 0.1.27 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.27` @sha256:da640ccc9c1dae0910f3c829862be5bfb10d8740047dcc7829db823a64d00e7b — RWTH-Gebäude nur vorn, wenn Name oder Nummer passt (D-26) |
| 0.1.28 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.28` @sha256:d1dda8d58b4c7f1f2543ccbbbc4e4ba3f7a80b67831d386dd32915f0fa0d3d94 — Custom (`einrad`): jedes Tempo über 0 km/h erlaubt |
| 0.1.29 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.29` @sha256:7e3fa1a2586c03f706781268332a808b1cc22d34b6b4255242528ddf0e665dc9 — mehrere Verbindungen je Anfrage, knappe Verbindungen mit nötigem Tempo (`knapp`, D-31) |
| 0.1.30 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.30` @sha256:0c317b30a27bd236ebdfc812a839ea74eb01c2ea646373f8973cb842aff4d65c — Autorouten: `art` auto, `kmh` = Höchsttempo (D-32) |
| 0.1.31 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.31` @sha256:dd7934f9d36023c145a8b96a143517ab982d2f8c59b8a771a3e009a453eb7f1d — Profile aus der App: `meiden` statt fester Art, ältere Apps unverändert (D-34) |
| 0.1.39 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.39` @sha256:46bbfc3ca76549498f852dec95737ef43412851e97446c1349c5c592f2f55725 — Mehrere Verbindungen auch bei dichtem Zubringer zum selben Zug (D-31) |
| 0.1.40 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.40` @sha256:9c571cf525c41f17907d1a7ba8f9503ed6e294302e37694a288e11ce746442ef — Spätester Bus zur schnellsten Fahrt, keine unsinnig langen Verbindungen (D-31); GOGC 30 fürs RAM-Ziel |
| 0.1.41 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.41` @sha256:34648b8ff2dacf1a34d6543a88ce2a1908e732d3dc463061f746eaed592aba02 — RAPTOR-Labels nur noch für erreichte Halte, spart ~60 MB im Betrieb (D-41) |
| 0.1.42 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.42` @sha256:bbfd72862185e478bd43088b8e237d2ee1b26830d3763a78aa742a8adfa1d83e — Fahrplan lädt reproduzierbar: feste Reihenfolge statt Map-Iteration (D-41) |
| 0.1.43 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.43` @sha256:433aa872fbc2d2a73b56627e0852580b11be4ac917e91117ff93cab58f578cd2 — Gleiche Ankunfts- und Abfahrtszeiten teilen sich ein Feld, ~7 MB weniger Heap (D-41) |
| 0.1.44 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.44` @sha256:3828321210153ed0789f3ec85bd9887f3ca217b0b2469f26f9436334d744b0f5 — Stand eines ausgefallenen Verkehrsdienstes gilt höchstens 30 min |
| 0.1.45 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.45` @sha256:0920ad46579cf9c3fdf861b1c8c2602bc7f0fcb81a729652b1d45a522a898066 — Stand eines ausgefallenen Verkehrsdienstes gilt höchstens 30 min |
| 0.1.38 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.38` @sha256:1cf4345bb9b6d8ccea930291af3d7c4803656fd515c9e9ec8752377b025c2891 — Manövertyp und Stau-Verlauf für die Fahransicht (D-40) |
| 0.1.37 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.37` @sha256:82fa8776a7cbc256d19d83fddd8723880afd5091111593988b3b65672f595d8b — Abbiegehinweise repariert |
| 0.1.36 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.36` @sha256:fd9edc775519c5454a8568c9a57cf201a68a5a3768c334fa797259a702eca46c — Umfahrung als Angebot, Abbiegehinweise (D-39) |
| 0.1.35 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.35` @sha256:d91e191387611a13ac8b5fe1ec1189865fc596a4a742a1cae85e1b558afd8b91 — Verkehrslage bei Autorouten aus der Autobahn-API (D-38) |
| 0.1.34 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.34` @sha256:34c0b2959c6a7184473b425f2315d33fc17d6825130662356fbc42761d5a3e54 — /v1/departures antwortet nie mit null (D-37) |
| 0.1.33 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.33` @sha256:d1dda8d58b4c7f1f2543ccbbbc4e4ba3f7a80b67831d386dd32915f0fa0d3d94 — Auto bis 500 km/h, Tempo zählt auf Autobahn ohne Limit (D-35) |
| 0.1.32 | `ghcr.io/lord3n20/schnellweg-routing-core:0.1.32` @sha256:4894d82361897b2f96dd30a062e4321c3c4fbb3a8fa3e44053b126134146952b — Radtempo außerhalb 5–60 km/h, Weg ohne Fahrt nur einmal, Auto-Belag ohne Sackgasse (D-34) |
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
