# Nokia-Brücke

Home-Assistant-App, über die ein altes Nokia (getestet: 6303i classic, J2ME) das Dashboard
„Main“ anzeigen und schalten kann. Die Handy-App liegt nicht in diesem Repo.

Das Handy kann kein TLS 1.2, deshalb spricht die Brücke **nur HTTP** mit einem knappen
Textformat. Damit das vertretbar bleibt:

- Das Home-Assistant-Token verlässt die App nie (sie nutzt intern den Supervisor-Zugang).
- Das Handy braucht nur einen eigenen Schlüssel. Wer ihn abfängt, kann höchstens die Einträge
  in `ITEMS` (oben in `bridge.py`) lesen und genau die dort vorgesehenen Aktionen auslösen.
- Nach mehr als 5 falschen Schlüsseln pro Minute wird jede weitere Anfrage 5 s verzögert.

## Einrichtung

1. App installieren und starten.
2. Im **Protokoll** steht der Schlüssel (`Schlüssel für das Handy: …`). Er wird einmal erzeugt
   und in `/data` gespeichert. Alternativ in der Konfiguration unter `schluessel` selbst setzen.
3. Im Nginx Proxy Manager einen Proxy-Host anlegen, z.B. `nokia.deine-domain.de`:
   - Ziel: `http://<IP des HA-Servers>:8790`
   - **Kein** „Force SSL“, sonst kommt das Handy nicht mehr dran.
   - Beim DNS-Anbieter einen A-Record für den Namen auf die öffentliche IP setzen.
4. In der Handy-App unter „Einstellungen“ die Adresse (`http://nokia.deine-domain.de`) und
   den Schlüssel eintragen.

Prüfen vom Rechner aus: `curl "http://nokia.deine-domain.de/s?k=<schlüssel>"`

## Schnittstelle

Alle Antworten sind UTF-8-Text, erste Zeile `OK` oder `ERR<TAB>Meldung`.

| Pfad | Inhalt |
|---|---|
| `/s?k=` | je Eintrag: `index, art, name, zustand, optionen` (Tab-getrennt) |
| `/w?k=` | Wettervorhersage, je Tag: `tag, max, min, wetter` |
| `/g?k=&i=` | 24-h-Verlauf als 48 Werte in Zehnteln, kommagetrennt |
| `/a?k=&i=&c=&v=` | Aktion: `toggle`, `select` (v=Option), `bright` (v=0–100), `power`/`prev`/`play`/`next`, `press` |

Arten: `H` Überschrift, `R` nur lesen, `G` lesen mit Verlauf, `T` Schalter, `S` Auswahl,
`L` dimmbares Licht, `B` Knopf (Radio), `M` Medienplayer, `W` Wetter.

## Einträge ändern

`ITEMS` in `bridge.py` anpassen, `version` in `config.yaml` erhöhen, App neu bauen.
