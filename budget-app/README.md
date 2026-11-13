# Budget

Haushaltsbuch im GnuCash-Format. Alles läuft im Panel „Budget“ (Seitenleiste): Übersicht, Buchungen,
Eintragen von Hand, wiederkehrende Buchungen, Sparziel und Monatsberichte. Claude trägt Kassenbons über
Skripte in Home Assistant als Ausgaben ein und kennt nur Ausgaben.

## Ordergebühr

Bei Umbuchungen (Eintragen) und bei Abgängen von Trade Republic (Reiter Bank) gibt es einen Knopf „1 € Gebühr“:
normale Käufe kosten dort 1 €, Sparpläne nicht. Die Gebühr wird als eigene Ausgabe vom selben Konto gebucht, in die
erste Ausgabenkategorie mit „Gebühr“ im Namen (am liebsten „…Ordergebühr…“).

## Übersicht (Board)

Übersicht und Berichte sind ein Reiter: auf großen Bildschirmen (ab 1500 × 760) ein Board ohne Seiten-Scrollen,
Karten scrollen bei Bedarf in sich; schmal stapelt sich alles. Bis 700 px Breite (Handy) ist alles dichter: Reiter
umbrechend statt abgeschnitten, Kacheln zweispaltig, Kategorien mit Betrag und Ø in einer Zeile und dem Balken darunter. Oben Konten (mit Kachel „Depot“; alles im Depot zählt als erspart) und
der Zeitraum („Gesamt“ oder ein Monat) mit Kacheln, darunter Geldfluss (Sankey), Kategorien gegen den Ø aller
bisherigen Monate (ohne gezeigten und laufenden Monat), Feldkarte (je Kategorie ein Feld, rot über / grün unter Ø),
Monatsverlauf als eine Kurslinie (Einnahmen − Ausgaben, aufsummiert, grün/rot um null, Ø gestrichelt), Trend, Sparziel und größte Posten (Festes zusammengefasst oben).
In „Gesamt“ läuft der Geldfluss ohne Art-Spalte: Einnahmen → Ausgaben → je Kategorie ein Knoten.
Was ins Depot geht oder dort liegt, zählt als erspart: Im Geldfluss teilt sich „Übrig“ in „Ins Depot“ und „Auf dem
Konto“, das Sparziel misst sich am Gesamtvermögen bzw. am ganzen „Übrig“, eine Lücke steht rot gestrichelt darunter. Das Sparpotenzial aus dem Monatsbericht steht gestrichelt unter „Übrig“;
haben Vorschläge eine Kategorie, ist der Teil dort gestreift markiert. Claude liefert
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

## Ohne Home Assistant (Programm für macOS, Windows, Linux)

`desktop/budget_desktop.py` startet denselben Server als Programm: nur auf 127.0.0.1 mit zufälligem Port, geschützt
durch ein Sitzungs-Cookie, das nur das Programm kennt. Es fehlt der Reiter Bank.
macOS und Windows zeigen das Panel in einem eigenen Fenster (pywebview, unter Windows mit WebView2); das
Linux-AppImage öffnet den Browser und beendet sich fünf Minuten, nachdem das Panel zuletzt gefragt hat. Ein zweiter
Start öffnet das laufende Budget. Daten: macOS `~/Library/Application Support/Budget`, Windows `%APPDATA%\Budget`,
Linux `~/.local/share/budget` (oder `BUDGET_DATA`).

**Claude** (`desktop/claude_mcp.py`): Unter Einstellungen → Claude meldet „Mit Claude Desktop verbinden“ das Programm
als lokalen MCP-Dienst in der `claude_desktop_config.json` an (Eintrag `budget`: das Programm selbst mit
`--mcp --schluessel …`). Claude Desktop startet es dann selbst und spricht über stdin/stdout. Die Werkzeuge sind
dieselben wie im Add-on (ohne `bank_einreichen`), also nur Ausgaben, Kategorien und Kontonamen. Ohne gültigen
Schlüssel antwortet jedes Werkzeug nur mit einem Hinweis; „Abmelden“ löscht Schlüssel und Eintrag. Programm und
MCP-Dienst sind zwei Prozesse; `buch.lock` sperrt darum zusätzlich die Datei `.sperre` im Datenordner. Vom Handy
aus geht es über ein Claude-Projekt: Bons dort hochladen, den Chat später in Claude Desktop mit „eintragen“
fortsetzen (Anweisung zum Kopieren steht in der Karte).

Beim ersten Start lässt sich statt einer GnuCash-Datei auch ein leeres Buch anlegen (Girokonto, Bargeld, Depot,
übliche Kategorien, Anfangsbestand; geht auch im Add-on).

Bauen: Tag `budget-desktop-vX.Y.Z` pushen; der Workflow „Budget Desktop“ baut DMG (Apple Silicon und Intel), EXE und
AppImage mit PyInstaller und legt das Release an. Die Programme sind nicht signiert: macOS will einmal „Dennoch
öffnen“ (Systemeinstellungen → Datenschutz & Sicherheit), Windows SmartScreen „Trotzdem ausführen“.
Lokal ausprobieren: `python3 desktop/budget_desktop.py` (ohne pywebview im Browser).

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

## Kreditkarte (TF Bank Mastercard Gold)

Die Karte ist ein eigenes Konto vom GnuCash-Typ CREDIT (`Aktiva:TF Bank:Mastercard Gold`; anlegen unter
Einstellungen → Neues Konto, Art „Kreditkarte“). Sein Saldo ist das, was auf der Karte offen ist: negativ, rot in der
Kontenkachel, und es geht vom frei Verfügbaren und vom Vermögen ab.

- **Kauf mit der Karte:** Ausgabe mit „Bezahlt von“ = Karte. Sie zählt im Monat des Kaufs (nicht erst mit der Rechnung).
- **Rechnung bezahlen:** Umbuchung vom Girokonto auf die Karte. Das ist keine Ausgabe, die Karte steht danach wieder bei 0.
  Kommt die Zahlung später auch über die Karte herein, steht sie dort als „Schon drin“.
- **Erstattung auf die Karte:** im Reiter Bank die Kategorie wählen; das wird eine negative Ausgabe.
- Die TF Bank zieht ohne Auftrag nur einen Teilbetrag ein und berechnet auf den Rest Zinsen: die Rechnung immer ganz zahlen
  (oder in „Meine TF Bank“ die volle Abbuchung einstellen).
- Claude sieht die Karte nur als Name eines Zahlkontos und kann Bons darauf buchen, aber keinen Saldo.

## Bankumsätze (Trade Republic, GLS, TF Bank)

Die Chromium-Erweiterung in `erweiterung/` (entpackt laden) meldet sich auf Knopfdruck selbst bei
Trade Republic bzw. im GLS-Onlinebanking an: Telefonnummer/VR-NetKey und PIN gibst du dort ein und kannst sie
merken lassen (nur im Chromium-Profil, unverschlüsselt in chrome.storage.local). Bestätigt wird in der TR-App bzw. in SecureGo plus. Die Erweiterung liest
die Umsätze, zeigt sie an und schickt sie nach deinem OK an einen Home-Assistant-Webhook
(Automation „Budget: Bankumsätze aus der Erweiterung“), der sie an das Werkzeug `bank_einreichen` gibt.
Im Panel (Reiter „Bank“) ordnest du sie ein; die Kategorie ist nach dem letzten gleichen Empfänger
vorausgefüllt, Umsätze, die schon im Buch stehen, kommen als „Schon drin“.

- Trade Republic: Umsätze über die Timeline der Web-App (dieselbe Schnittstelle, die die Seite nutzt).
- GLS: liest die JSON-Antworten der Umsatzseite; erkennt die Felder selbst und zeigt sie unter Einstellungen an.
- TF Bank (Kreditkarte, „Meine TF Bank“): Kundennummer der Karte und Geburtsdatum, dann den SMS-Code in der Erweiterung
  (oder direkt im Bank-Tab) eingeben. Gelesen werden die Antworten von `/api/creditcard/transactions`; vorgemerkte
  Zahlungen kommen erst, wenn sie gebucht sind. Ob die Bank Käufe positiv oder negativ liefert, ist nicht dokumentiert:
  Käufe werden als Abgang gezeigt (die Mehrheit), sonst vor dem Senden „Vorzeichen tauschen“. Die Erweiterung liegt
  entpackt in `~/.local/share/chromium-ext/budget-bank` (mit `konfig.js`).
- Die Webhook-Adresse steht in `erweiterung/konfig.js` (nicht im Repo, siehe `konfig.beispiel.js`).
- Doppelt kommt nichts: Schon geschickte Umsätze (auch gebuchte/ignorierte) werden übergangen, und was älter ist als der letzte Abruf dieser Bank minus 5 Tage, nimmt die App gar nicht mehr an. Die Erweiterung holt standardmäßig „seit dem letzten Abruf“.
- Claude kommt nicht an die Umsätze: Für `bank_einreichen` gibt es kein Skript, die Antwort ist nur die Anzahl.
