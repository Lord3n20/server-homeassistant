# Budget

Haushaltsbuch im GnuCash-Format. Alles läuft im Panel „Budget“ (Seitenleiste): Übersicht, Buchungen,
Eintragen von Hand, wiederkehrende Buchungen, Sparziel und Monatsberichte. Claude trägt Kassenbons über
Skripte in Home Assistant als Ausgaben ein und kennt nur Ausgaben.

## Berichte

Der Reiter „Berichte“ zeichnet alle Grafiken selbst aus dem Buch, für jeden Monat und die letzten 7 Tage. Claude liefert
nur ein kurzes Fazit und die Sparvorschläge, die Sparquote und der Vergleich mit den Einnahmen entstehen nur im Panel.

## Was Claude sieht

Die Schnittstelle läuft auf einem internen Port ohne Freigabe nach außen und nimmt nur Anfragen von Home Assistant
an. Sie hat keinen Weg zu den Daten des Panels. Claude erreicht sie über den MCP-Server von Home Assistant.

- **Sieht Claude:** gebuchte Ausgaben, Ausgabenkategorien, die Namen der Zahlkonten und wiederkehrende Ausgaben.
- **Sieht Claude nicht:** Salden, Vermögen, Sparziel, Einnahmen, Umbuchungen, Depot und wiederkehrende Einnahmen.
- Claude kann eine Ausgabe nur vorschlagen und nach dem OK buchen. Erstattungen trägst du im Panel ein
  (negativer Betrag bei einer Ausgabe); Claude sieht sie danach als negative Ausgabe.

`test_budget.py` prüft das mit Testdaten. Das Panel ist nur über das Ingress-Gateway von Home Assistant erreichbar.

## Einrichtung

1. App installieren und starten, im Panel die GnuCash-Datei hochladen (gepackt oder unkomprimiert, XML).
2. `ha-beispiel.yaml` übernehmen: `rest_command` in die `configuration.yaml`, die Skripte in die `scripts.yaml`.
3. Die Skripte für Assist freigeben (nichts sonst von dieser App), damit sie im MCP-Server auftauchen.

## Daten

- Buch: `/data/haushaltsbuch.gnucash`, immer unkomprimiert und über „Buch herunterladen“ in GnuCash zu öffnen.
  Wer es dort ändert, lädt es danach wieder hoch.
- Vor jeder Änderung liegt eine Kopie in `/data/versionen/` (die letzten 300).
- Wiederkehrend, Vorschläge, Berichte und Einstellungen: JSON-Dateien in `/data/`.
- Monatsberichte bleiben alle erhalten, von den Wochenübersichten nur die neueste.
- Alles gehört zu den normalen HA-Backups.

## Wiederkehrende Buchungen

Fällige Einträge werden beim Start und danach stündlich gebucht, verpasste werden nachgeholt. Ein Tag wie der 31.
rutscht in kürzeren Monaten auf den letzten Tag. Pausieren oder Entfernen lässt gebuchte Einträge im Buch.

## Tests

`python3 test_budget.py`
