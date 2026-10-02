# Nokia-Signal

Home-Assistant-App, über die ein altes Nokia (getestet für 6303i classic, J2ME) Signal-Nachrichten
lesen und schreiben sowie Bilder ansehen und senden kann. Die Handy-App liegt nicht in diesem Repo.

Die App koppelt sich mit [signal-cli](https://github.com/AsamK/signal-cli) als **verknüpftes Gerät**
an ein bestehendes Signal-Konto, genau wie Signal Desktop. Das Handy mit Signal bleibt Hauptgerät.

## Sicherheit

Das Nokia kann kein modernes TLS, daher läuft die Verbindung über reines HTTP. Damit niemand
mitlesen kann, ist **jede Anfrage und jede Antwort verschlüsselt** (ChaCha20, HMAC-SHA256) mit einem
Schlüssel, den nur App und Handy kennen. Der Schlüssel selbst wird nie übertragen.

- Ohne Schlüssel bekommt man nur `403`. Nach mehr als 5 Fehlversuchen pro Minute wird jede weitere
  Anfrage 5 s verzögert.
- Mitgeschnittene Anfragen lassen sich nicht erneut abspielen (Zeitstempel + Nonce).
- Im Klartext liegen die Nachrichten nur im App-Ordner (`/data`) auf dem Home-Assistant-Server,
  so wie bei Signal Desktop auf einem Rechner. Backups von Home Assistant enthalten diesen Ordner.

## Einrichtung

1. App installieren und starten (nur `amd64`, signal-cli gibt es nativ nur für x86_64).
2. **Öffnen** (Web-Oberfläche): Dort erscheint ein QR-Code. Am Handy in Signal unter
   *Einstellungen → Verknüpfte Geräte → Gerät hinzufügen* scannen. Ältere Nachrichten werden nicht
   übernommen, nur neue ab der Kopplung.
3. Auf derselben Seite (und im Protokoll) steht der **Schlüssel für das Handy**. Er wird einmal
   erzeugt und in `/data` gespeichert. Alternativ in der Konfiguration unter `schluessel` selbst setzen.
4. Im Nginx Proxy Manager einen Proxy-Host anlegen, z.B. `nokiasignal.deine-domain.de`:
   - Ziel: `http://<IP des HA-Servers>:8791`
   - **Kein** „Force SSL“, sonst kommt das Handy nicht mehr dran.
5. In der Handy-App unter „Einstellungen“ die Adresse und den Schlüssel eintragen.

Wird das Gerät am Handy entkoppelt, zeigt die Oberfläche „vom Handy entkoppelt“; mit
**Neu koppeln** gibt es einen neuen QR-Code. Gespeicherte Chats und Bilder bleiben erhalten.

## Protokoll

Ein Endpunkt: `POST /x`, Inhalt ist ein Rahmen

    nonce (12 Byte) | ChaCha20(klartext) | HMAC-SHA256(nonce | geheimtext), erste 16 Byte

ChaCha20 nach RFC 8439 mit Blockzähler 0. Schlüssel: `SHA-256(label + "\n" + schlüssel)` mit den
Labels `up-enc`/`up-mac` (Handy → App) und `down-enc`/`down-mac` (App → Handy).

Klartext der Anfrage: Länge des Kopfs (4 Byte, big endian), Kopf (UTF-8, Tab-getrennt:
`zeit_ms, befehl, argumente …`), danach Nutzdaten. Klartext der Antwort: `OK` oder `ERR<TAB>Meldung`,
Zeilenumbruch, danach der Inhalt. Weicht die Uhr des Handys mehr als 10 min ab, kommt
`ERR<TAB>ZEIT<TAB><server_ms>`, das Handy merkt sich den Versatz und fragt erneut.

| Befehl | Nutzdaten | Antwort |
|---|---|---|
| `ping` | – | `status<TAB>nummer` |
| `chats` | – | je Chat: `id, name, ungelesen, zeit, vorschau` |
| `msgs<TAB>chat<TAB>nach_id<TAB>vor_id` | – | je Nachricht: `id, von_mir, absender, zeit, text, bild_ids` (höchstens 30) |
| `send<TAB>chat` | Text (UTF-8) | neue Nachrichten-ID |
| `img<TAB>chat` | Bilddatei | neue Nachrichten-ID |
| `att<TAB>bild_id<TAB>breite<TAB>höhe` | – | JPEG, passend verkleinert |

Zeilenumbrüche in Texten kommen als Zeichen `0x1E`. Emoji werden zu Text-Smileys (`:)`, `<3`, `(Y)`),
unbekannte zu `[?]`, weil die Schrift des Handys keine kennt.
