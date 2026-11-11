# Budget

Haushaltsbuch im GnuCash-Format. Alles läuft im Panel „Budget“ (Seitenleiste): Übersicht, Buchungen,
Eintragen von Hand, wiederkehrende Buchungen, Sparziel und Monatsberichte. Claude trägt Kassenbons über
Skripte in Home Assistant als Ausgaben ein und kennt nur Ausgaben.

## Berichte

Der Reiter „Berichte“ zeichnet alle Grafiken selbst aus dem Buch, für jeden Monat und die letzten 7 Tage. Ganz oben zeigt ein
Geldfluss (Sankey), wie die Einnahmen eines Monats in Fest, Alltag, große Posten und Übrig und weiter in die Kategorien fließen;
in der Wochenansicht beginnt er bei den Ausgaben. Das Sparpotenzial aus dem Monatsbericht steht gestrichelt unter „Übrig“;
haben Vorschläge eine Kategorie, ist der Teil dort gestreift markiert. Ein Kuchendiagramm zeigt von innen nach außen
Fest, Alltag und große Posten, dann Kategorie, dann Händler. Claude liefert
nur ein kurzes Fazit und die Sparvorschläge, die Sparquote und der Vergleich mit den Einnahmen entstehen nur im Panel.

## Depot

Im Reiter „Depot“ trägst du je Position ISIN, Stückzahl und durchschnittlichen Kaufkurs ein. Das Panel zeigt dann den
aktuellen Wert und Gewinn oder Verlust zum Kaufpreis; Vermögen und Sparziel rechnen mit diesen Werten. Ins Buch wird dabei nichts
geschrieben. Bewertet wird zum Geldkurs von Tradegate, ersatzweise zum Mittelkurs von Lang & Schwarz, höchstens einmal pro Minute und nur,
solange das Panel offen ist. Von der Bewertung bei deinem Broker kann das leicht abweichen, weil dort ein anderer Handelsplatz zählen kann. Beide Schnittstellen sind inoffiziell und können sich ändern; dann bleibt der letzte Kurs mit seiner Uhrzeit
stehen. Käufe und Verkäufe, die danach im Buch landen, werden mit dem aktuellen Kurs geschätzt nachgezogen und markiert, bis du
die Werte neu einträgst.

## Was Claude sieht

Die Schnittstelle läuft auf einem internen Port ohne Freigabe nach außen und nimmt nur Anfragen von Home Assistant
an. Sie hat keinen Weg zu den Daten des Panels. Claude erreicht sie über den MCP-Server von Home Assistant.

- **Sieht Claude:** gebuchte Ausgaben, Ausgabenkategorien, die Namen der Zahl- und Einnahmekonten und wiederkehrende Ausgaben.
- **Darf Claude buchen:** Ausgaben, Einnahmen und Umbuchungen zwischen Zahlkonten, jeweils erst als Vorschlag und nach deinem OK. Gebuchte Einnahmen und Umbuchungen sieht Claude danach nicht.
- **Sieht Claude nicht:** Salden, Vermögen, Sparziel, gebuchte Einnahmen, Umbuchungen, Depot und Kurse, wiederkehrende Einnahmen.
- Erstattungen trägst du im Panel ein
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

## Bankumsätze (Trade Republic, GLS)

Die Chromium-Erweiterung in `erweiterung/` (entpackt laden) meldet sich auf Knopfdruck selbst bei
Trade Republic bzw. im GLS-Onlinebanking an: Telefonnummer/VR-NetKey und PIN gibst du dort jedes Mal ein,
gespeichert wird nichts davon. Bestätigt wird in der TR-App bzw. in SecureGo plus. Die Erweiterung liest
die Umsätze, zeigt sie an und schickt sie nach deinem OK an einen Home-Assistant-Webhook
(Automation „Budget: Bankumsätze aus der Erweiterung“), der sie an das Werkzeug `bank_einreichen` gibt.
Im Panel (Reiter „Bank“) ordnest du sie ein; die Kategorie ist nach dem letzten gleichen Empfänger
vorausgefüllt, Umsätze, die schon im Buch stehen, kommen als „Schon drin“.

- Trade Republic: Umsätze über die Timeline der Web-App (dieselbe Schnittstelle, die die Seite nutzt).
- GLS: liest die JSON-Antworten der Umsatzseite; erkennt die Felder selbst und zeigt sie unter Einstellungen an.
- Die Webhook-Adresse steht in `erweiterung/konfig.js` (nicht im Repo, siehe `konfig.beispiel.js`).
- Claude kommt nicht an die Umsätze: Für `bank_einreichen` gibt es kein Skript, die Antwort ist nur die Anzahl.
