# Budget

Haushaltsbuch im GnuCash-Format. Alles läuft im Panel „Budget“ (Seitenleiste): Übersicht, Buchungen,
Eintragen von Hand, wiederkehrende Buchungen, Sparziel und Monatsberichte. Dazu kommt ein
Claude-Connector, der Kassenbons als Ausgaben einträgt und nur Ausgaben kennt.

## Was Claude sieht

Der Connector läuft auf einem eigenen Port und hat keinen Weg zu den Daten des Panels.

- **Sieht Claude:** gebuchte Ausgaben, Ausgabenkategorien, die Namen der Zahlkonten und wiederkehrende Ausgaben.
- **Sieht Claude nicht:** Salden, Vermögen, Sparziel, Einnahmen, Umbuchungen, Depot und wiederkehrende Einnahmen.
- Claude kann eine Ausgabe nur vorschlagen und nach dem OK buchen. Erstattungen trägst du im Panel ein
  (negativer Betrag bei einer Ausgabe); Claude sieht sie danach als negative Ausgabe.

`test_budget.py` prüft das mit Testdaten. Das Panel ist nur über das Ingress-Gateway von Home Assistant erreichbar.

## Einrichtung

1. App installieren und starten, im Panel die GnuCash-Datei hochladen (gepackt oder unkomprimiert, XML).
2. In den Optionen `oeffentliche_url` (z. B. `https://budget.example.org`, ohne `/` am Ende) und
   `connector_passwort` (mindestens 12 Zeichen) setzen.
3. Im Reverse Proxy die öffentliche Adresse auf Port 8798 zeigen lassen, nur per HTTPS.
4. In Claude unter Connectors `https://…/mcp` hinzufügen und beim Anmelden das Passwort eingeben.

## Daten

- Buch: `/data/haushaltsbuch.gnucash`, immer unkomprimiert und über „Buch herunterladen“ in GnuCash zu öffnen.
  Wer es dort ändert, lädt es danach wieder hoch.
- Vor jeder Änderung liegt eine Kopie in `/data/versionen/` (die letzten 300).
- Wiederkehrend, Vorschläge, Berichte, Einstellungen und Anmeldungen: JSON-Dateien in `/data/`, Tokens nur als Hash.
- Alles gehört zu den normalen HA-Backups.

## Wiederkehrende Buchungen

Fällige Einträge werden beim Start und danach stündlich gebucht, verpasste werden nachgeholt. Ein Tag wie der 31.
rutscht in kürzeren Monaten auf den letzten Tag. Pausieren oder Entfernen lässt gebuchte Einträge im Buch.

## Tests

`python3 test_budget.py`
