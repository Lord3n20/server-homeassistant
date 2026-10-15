# Bibliothek

Home-Assistant-App für den eigenen Bücherschrank. Bücher kommen über die ISBN, eine Suche nach
Titel/Autor oder von Hand hinein. Im Panel „Bibliothek“ (Seitenleiste) lassen sie sich nach Status
(am Lesen, gelesen, ungelesen), Genre und Autor sortieren und bearbeiten.

Nach jeder Änderung schreibt die App eine fertige, statische Seite nach
`/media/<website_ordner>/` (Standard: `website/bibliothek`), den die nginx-App dieses Repos
ausliefert. Die Seite übernimmt `style.css`, Navigation und Wappen der Website und lädt nichts von
fremden Servern. Cover erscheinen bewusst nur im Panel, nicht auf der Website.

## Daten

- Buchdaten: Deutsche Nationalbibliothek (SRU-Schnittstelle, ohne Schlüssel), ersatzweise Open Library.
  Das Genre wird aus der DNB-Sachgruppe vorgeschlagen (z. B. „B“ → Belletristik, 100 → Philosophie)
  und lässt sich frei ändern.
- Cover: DNB, sonst Open Library; eigene Fotos lassen sich im Panel hochladen (werden vorher auf
  600 px verkleinert).
- Gespeichert in `/data/buecher.json` und `/data/cover/`, also Teil der normalen HA-Backups.
- Notizen und Bücher mit ausgeschaltetem „Auf der Website zeigen“ bleiben nur im Panel.

## Bedienung

- **ISBN**: eintippen oder mit einem USB-/Bluetooth-Barcodescanner einlesen (Enter schickt ab).
  In Browsern mit Barcode-Erkennung (z. B. Chrome auf Android) gibt es zusätzlich den Knopf „Kamera“.
- **Suchen**: Titel und/oder Autor; die Trefferliste zeigt Ausgaben mit Jahr, Verlag und ISBN.
- **Von Hand**: für Bücher, die nirgends verzeichnet sind.
- **Sterne**: in der Liste 1–5 Sterne anklicken, derselbe Stern noch einmal = keine Bewertung.
  Die Sterne erscheinen auch auf der Website.
- **Ausleihe**: beim Bearbeiten „Verliehen an …“ oder „Geliehen von …“ mit Person und Datum.
  In der Liste steht dann ein Hinweis mit „zurück“-Knopf; Filter „Verliehen“/„Geliehen“.
  Namen und Ausleihe bleiben nur im Panel.

Das Panel ist nur über Home Assistant (Ingress) erreichbar, die App öffnet keinen Port nach außen.

## Lokal testen

```sh
BIBLIOTHEK_DATA=./data BIBLIOTHEK_MEDIA=./media BIBLIOTHEK_PORT=8199 python3 bibliothek.py
# Panel: http://127.0.0.1:8199/
```
