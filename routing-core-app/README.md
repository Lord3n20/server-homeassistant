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
| `gtfs` | Feeds im Konfigurationsordner, durch Komma getrennt; `:fern` markiert einen Feed als Fernverkehr |
| `footpaths` | Valhalla-Fußwege (CSV), leer = Luftlinie |
| `walk` | längster Umstiegsweg in m |
| `access` | längster Zugangsweg in m |
| `par` | gleichzeitige Anfragen |

## Versionen

| Version | Image |
|---|---|
| 0.1.0 | `ghcr.io/lord3n20/rwaysth-routing-core:0.1.0` @sha256:a5e983dcdf07b36e27fe266fae0d2e41a715f2c43686f04dc9302e3d611b7937 |
