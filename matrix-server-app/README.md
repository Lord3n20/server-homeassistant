# Matrix-Server

Home-Assistant-App mit einem eigenen [Matrix](https://matrix.org)-Homeserver. Dahinter steckt
[Continuwuity](https://continuwuity.org) (Rust, läuft mit wenig RAM, Datenbank RocksDB in `/data`).
Föderation mit anderen Matrix-Servern ist möglich, Clients wie Element X oder FluffyChat
verbinden sich direkt.

## Einrichtung

1. **DNS:** einen A-Record für den Servernamen (z. B. `matrix.example.org`) auf die eigene IP.
2. **Reverse Proxy** (z. B. Nginx Proxy Manager): Proxy-Host `matrix.example.org` →
   `http://<IP des HA-Servers>:8008`, SSL-Zertifikat, „Force SSL“ an. Unter *Advanced*:
   `client_max_body_size 100m;`
   Mehr braucht es nicht: Die App liefert `/.well-known/matrix/server` und `/.well-known/matrix/client`
   selbst aus, damit laufen Föderation und Client-Anmeldung über Port 443.
3. In der Konfiguration `server_name` setzen (genau der Name aus Schritt 1) und die App starten.
   **Der Name lässt sich danach nicht mehr ändern**, er steckt in jeder Benutzer- und Raum-ID.
4. Im **Protokoll** steht beim ersten Start ein einmaliges Registrierungs-Token. Damit im Matrix-Client
   auf dem eigenen Server ein Konto anlegen. Das erste Konto wird Admin und landet im Raum `#admins`.
5. Föderation prüfen: <https://federationtester.matrix.org>

## Optionen

| Option | Bedeutung |
|---|---|
| `server_name` | Name des Servers, Teil jeder ID (`@name:server_name`) |
| `foederation` | Mit anderen Matrix-Servern sprechen |
| `registrierungs_token` | Leer: niemand kann sich selbst registrieren (Standard). Gesetzt: Registrierung mit diesem Token möglich |

## Bridges

Bridges (Appservices) aus anderen Apps werden automatisch angemeldet: Jede Registrierungsdatei
`/share/matrix/appservices/*.yaml` wird beim Start per Admin-Befehl eingetragen. Ändert sich dort
etwas, startet der Server nach spätestens 15 s selbst neu und übernimmt die Änderung.
