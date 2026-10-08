# Morgen-Briefing

Home-Assistant-App, die jeden Morgen ein kurzes Briefing erstellt, **ohne KI**:

- **Termine heute** aus allen CalDAV-Kalendern (außer den ausgeschlossenen) und der erste Termin von morgen.
  Wiederkehrende Termine löst Nextcloud selbst auf.
- **Mails, die dich brauchen**: Posteingang der letzten 2 Tage, nicht beantwortet, keine Newsletter,
  Benachrichtigungen oder Auto-Antworten. Als Kurzfassung steht darunter der erste richtige Satz
  der Mail (ohne Anrede, Zitat und Grußformel).
- **Fristen aus Mails**: Datumsangaben in Mails der letzten 7 Tage, wenn im selben Satz
  „bis“, „Frist“, „spätestens“, „fällig“, „Anmeldung“ o. Ä. steht. Fristen in den nächsten 3 Tagen sind markiert.
- **Kalender-Wecker**: Beginnt heute ein Termin vor 12:00, ruft die App
  `script.kalender_wecker_stellen` mit Beginn minus 90 Minuten auf (nur wenn das noch in der Zukunft liegt).

Das Postfach wird nur lesend geöffnet: nichts wird als gelesen markiert, verschoben oder gesendet.

Das Briefing erscheint als Seite **Briefing** in der Seitenleiste (Ingress, also nur mit HA-Anmeldung),
dazu kommt zur eingestellten Uhrzeit eine Push-Nachricht mit Kurzfassung, die die Seite öffnet.
Der Knopf **Neu erstellen** unten auf der Seite baut sie sofort neu.

## Einrichtung

1. App installieren.
2. In der **Konfiguration** eintragen:
   - `imap_benutzer` / `imap_passwort`: Postfach (IONOS: `imap.ionos.de`)
   - `caldav_url`: Kalender-Home, z.B. `https://nc.example.org/remote.php/dav/calendars/<Benutzer>/`
   - `caldav_benutzer` / `caldav_passwort`: am besten ein eigenes **App-Passwort** aus Nextcloud
     (Einstellungen → Sicherheit)
   - `kalender_ausschliessen`: Kalender, die nicht erscheinen sollen (letzter Teil der URL oder Anzeigename)
   - `push_dienst`: Name des Notify-Dienstes ohne `notify.`, z.B. `mobile_app_pixel_8` (leer = kein Push)
   - `uhrzeit`: wann das Briefing erstellt wird (Europe/Berlin)
3. App starten und „In Seitenleiste anzeigen“ aktivieren.

Lokal testen (ohne Wecker/Push auszulösen):

```
BRIEFING_DATA=/tmp/b BRIEFING_DEV=1 BRIEFING_DRY=1 HA_URL=https://ha.example.org HA_TOKEN=… \
  python3 briefing.py einmal --push
```

`/tmp/b/options.json` enthält dabei dieselben Felder wie die Konfiguration.
