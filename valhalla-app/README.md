# SchnellWeg Valhalla

Home-Assistant-App mit [Valhalla](https://github.com/valhalla/valhalla) 3.9.1 für
[SchnellWeg](https://github.com/Lord3n20/SchnellWeg): Fußwege und erste/letzte Meile. Die Tiles baut die App
routing-jobs (Job `valhalla`); diese App liest `valhalla/current/tiles.tar` aus deren Konfigurationsordner und wird
nach einem neuen Stand von dort neu gestartet.

- Kein Port nach außen; routing-core spricht die App im internen Netz an (Port 8002).
- Valhalla protokolliert nichts (Protokoll nach `/dev/null`), damit keine Koordinaten aus Anfragen in Logs landen.
  Im App-Protokoll steht nur der geladene Stand.

## Versionen

| Version | Image |
|---|---|
| 0.1.0 | `ghcr.io/lord3n20/rwaysth-valhalla:0.1.0` @sha256:3f376574e68df7c5bb6512b0dd4d20505394b25b9bc17dec236c42208351a59e |
