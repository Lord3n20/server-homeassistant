# Claude Mail & Kalender (MCP)

Home-Assistant-App für [claude-mail-mcp](https://github.com/YannicHock/claude-mail-mcp) (v0.7.2).
Sie macht ein IMAP/SMTP-Postfach (z.B. 1&1) und einen CalDAV-Kalender (z.B. Nextcloud)
als **eigenen Connector in claude.ai** verfügbar, inklusive der OAuth-Anmeldung, die claude.ai verlangt.

Intern laufen zwei Dienste, jeweils unter einem eigenen Benutzer:

- der Connector (Mail + Kalender), nur intern auf `127.0.0.1:3220`
- die OAuth-Schicht davor, auf Port `8080` (auf dem Host standardmäßig `8787`)

## Voraussetzung: eine öffentliche HTTPS-Adresse

claude.ai muss das Add-on aus dem Internet per HTTPS erreichen. Claude verbindet sich nur über IPv4,
die Adresse braucht also einen A-Record (bei einem Cloudflare Tunnel ist das automatisch so).
Am einfachsten geht das mit dem **Cloudflared**-Add-on (Cloudflare Tunnel) und einer Domain bei Cloudflare:

- Neuer Hostname, z.B. `mail-mcp.deine-domain.de`
- Ziel: `http://<Hostname dieses Add-ons>:8080` (steht auf der Info-Seite des Add-ons)
  oder `http://<IP deines HA-Servers>:8787`

Ein vorhandener Reverse Proxy (z.B. Nginx Proxy Manager) funktioniert genauso.
Nur Port 8080 freigeben, nie den internen Port 3220.

## Einrichtung

1. Add-on installieren.
2. In der **Konfiguration** `public_url` auf die öffentliche Adresse setzen,
   z.B. `https://mail-mcp.deine-domain.de` (ohne `/` am Ende).
3. Add-on starten und das **Protokoll** öffnen. Beim ersten Start steht dort ein
   Einrichtungs-Link (`https://…/setup/…`). Diesen Link niemandem zeigen: Wer ihn hat,
   kann die Instanz übernehmen, bis die Einrichtung abgeschlossen ist.
4. Im Assistenten:
   - Benutzername und Passwort (mindestens 12 Zeichen) für die Anmeldung festlegen.
   - Postfach anlegen. Nach Eingabe der Adresse sucht der Assistent die Servereinstellungen
     selbst und testet die Verbindung. Zum Abgleich, für 1&1 / IONOS:
     - IMAP: `imap.1und1.de`, Port 993, SSL
     - SMTP: `smtp.1und1.de`, Port 587 (STARTTLS) oder 465 (SSL)
   - Kalender (CalDAV) beim selben Konto eintragen:
     - URL: `https://<deine-nextcloud>/remote.php/dav`
     - Benutzer: dein Nextcloud-Benutzername
     - Passwort: ein **App-Passwort** aus Nextcloud (Einstellungen → Sicherheit)
5. Am Ende zeigt der Assistent die Connector-URL (`https://…/mcp`).
6. In claude.ai unter Einstellungen → Connectors → **Eigenen Connector hinzufügen**
   diese URL eintragen und dich mit dem Benutzer aus Schritt 4 anmelden.

Weitere Postfächer, Passwortwechsel und angemeldete Clients verwaltest du später unter
`https://<public_url>/settings`.

## Daten

Alles liegt im persistenten `/data` des Add-ons und ist in HA-Backups enthalten.
Achtung: Das Backup enthält damit auch die Postfach-Passwörter im Klartext.

## Aktualisieren

`MCP_VERSION` im `Dockerfile` und `version` in `config.yaml` erhöhen,
dann in Home Assistant das Add-on neu bauen/aktualisieren.
