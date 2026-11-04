"""Das Haushaltsbuch: eine unkomprimierte GnuCash-XML-Datei plus kleine JSON-Dateien.

Gelesen wird mit ElementTree, geschrieben auf Textebene, damit alles andere in
der Datei unangetastet bleibt und GnuCash sie weiter öffnen kann. Vor jeder
Änderung landet eine Kopie in /data/versionen/. Beträge sind überall ganze Cent.
"""
import gzip
import json
import os
import re
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import escape

DATA = Path(os.environ.get("BUDGET_DATA", "/data"))
BUCH = DATA / "haushaltsbuch.gnucash"
VERSIONEN = DATA / "versionen"
MAX_VERSIONEN = 300

GNC = "{http://www.gnucash.org/XML/gnc}"
ACT = "{http://www.gnucash.org/XML/act}"
TRN = "{http://www.gnucash.org/XML/trn}"
TS = "{http://www.gnucash.org/XML/ts}"
SPLIT = "{http://www.gnucash.org/XML/split}"
SLOT = "{http://www.gnucash.org/XML/slot}"
CMDTY = "{http://www.gnucash.org/XML/cmdty}"

ZAHLKONTO_TYPEN = ("BANK", "CASH", "CREDIT")
VERMOEGEN_TYPEN = ("ASSET", "BANK", "CASH", "STOCK", "MUTUAL", "CREDIT", "LIABILITY", "RECEIVABLE", "PAYABLE")
ARTEN = ("ausgabe", "einnahme", "umbuchung")

lock = threading.RLock()


class BuchFehler(ValueError):
    """Eingabe oder Datei passt nicht; der Text geht so an den Aufrufer."""


# ---------- Hilfen ----------

def cent(wert):
    """Macht aus 12.5, "12,50" oder "1.234,50" ganze Cent."""
    text = str(wert).strip().replace("€", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        betrag = Decimal(text) * 100
    except InvalidOperation:
        raise BuchFehler(f"Betrag nicht lesbar: {wert}")
    if not betrag.is_finite() or betrag != betrag.to_integral_value():
        raise BuchFehler(f"Betrag mit mehr als zwei Nachkommastellen: {wert}")
    if abs(betrag) > 100_000_000:
        raise BuchFehler(f"Betrag zu groß: {wert}")
    return int(betrag)


def tag(wert):
    try:
        return date.fromisoformat(str(wert)[:10])
    except ValueError:
        raise BuchFehler(f"Datum nicht lesbar (JJJJ-MM-TT): {wert}")


def letzter_sonntag(jahr, monat):
    d = date(jahr + (monat == 12), monat % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def berlin_stunden(d):
    return 2 if letzter_sonntag(d.year, 3) <= d < letzter_sonntag(d.year, 10) else 1


def zeitstempel(d, uhrzeit="12:00:00"):
    return f"{d.isoformat()} {uhrzeit} +0{berlin_stunden(d)}00"


def lokales_datum(text):
    zeit = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S %z").astimezone(timezone.utc)
    return (zeit + timedelta(hours=berlin_stunden(zeit.date()))).date()


def monat_plus(d, monate, tag_im_monat):
    index = d.year * 12 + d.month - 1 + monate
    jahr, monat = index // 12, index % 12 + 1
    ende = (date(jahr + (monat == 12), monat % 12 + 1, 1) - timedelta(days=1)).day
    return date(jahr, monat, min(tag_im_monat, ende))


def json_laden(pfad, standard):
    try:
        return json.loads(pfad.read_text())
    except FileNotFoundError:
        return standard


def json_speichern(pfad, daten):
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=1))
    os.replace(tmp, pfad)


# ---------- Lesen ----------

def auswerten(text):
    """Macht aus dem XML Konten und Buchungen und prüft dabei, dass alles aufgeht."""
    try:
        wurzel = ET.fromstring(text)
    except ET.ParseError as ex:
        raise BuchFehler(f"Keine lesbare GnuCash-XML-Datei: {ex}")
    book = wurzel.find(f"{GNC}book")
    if wurzel.tag != "gnc-v2" or book is None:
        raise BuchFehler("Keine GnuCash-XML-Datei")

    konten = {}
    for a in book.findall(f"{GNC}account"):
        schluessel = {s.findtext(f"{SLOT}key"): s.findtext(f"{SLOT}value") for s in a.iter("slot")}
        konten[a.findtext(f"{ACT}id")] = {
            "id": a.findtext(f"{ACT}id"),
            "name": a.findtext(f"{ACT}name"),
            "typ": a.findtext(f"{ACT}type"),
            "eltern": a.findtext(f"{ACT}parent"),
            "waehrung": a.findtext(f"{ACT}commodity/{CMDTY}id"),
            "platzhalter": schluessel.get("placeholder") == "true",
        }
    for k in konten.values():
        teile, aktuell = [], k
        while aktuell and aktuell["typ"] != "ROOT":
            teile.append(aktuell["name"])
            aktuell = konten.get(aktuell["eltern"])
        k["pfad"] = ":".join(reversed(teile))

    buchungen = []
    for t in book.findall(f"{GNC}transaction"):
        splits, summe = [], Fraction(0)
        for s in t.findall(f"{TRN}splits/{TRN}split"):
            wert = Fraction(s.findtext(f"{SPLIT}value"))
            summe += wert
            splits.append({
                "konto": s.findtext(f"{SPLIT}account"),
                "betrag": round(Fraction(s.findtext(f"{SPLIT}quantity")) * 100),
                "memo": s.findtext(f"{SPLIT}memo") or "",
            })
        datum = lokales_datum(t.findtext(f"{TRN}date-posted/{TS}date"))
        beschreibung = t.findtext(f"{TRN}description") or ""
        if summe:
            raise BuchFehler(f"Buchung geht nicht auf 0 auf: {datum} {beschreibung}")
        buchungen.append({"id": t.findtext(f"{TRN}id"), "datum": datum.isoformat(),
                          "beschreibung": beschreibung, "splits": splits})
    buchungen.sort(key=lambda b: b["datum"])
    return konten, buchungen


def vorhanden():
    return BUCH.exists()


def lesen():
    if not vorhanden():
        raise BuchFehler("Es ist noch kein Haushaltsbuch importiert")
    return auswerten(BUCH.read_text(encoding="utf-8"))


# ---------- Schreiben ----------

def schreiben(neu):
    """Prüft den neuen Text vollständig, sichert den alten und ersetzt die Datei."""
    auswerten(neu)
    VERSIONEN.mkdir(parents=True, exist_ok=True)
    if vorhanden():
        stempel = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        (VERSIONEN / f"haushaltsbuch-{stempel}.gnucash").write_bytes(BUCH.read_bytes())
        for alt in sorted(VERSIONEN.glob("haushaltsbuch-*.gnucash"))[:-MAX_VERSIONEN]:
            alt.unlink(missing_ok=True)
    tmp = BUCH.with_suffix(".tmp")
    tmp.write_text(neu, encoding="utf-8")
    os.replace(tmp, BUCH)


def zaehler(text, art, plus):
    muster = rf'(<gnc:count-data cd:type="{art}">)(\d+)(</gnc:count-data>)'
    if re.search(muster, text):
        return re.sub(muster, lambda m: f"{m[1]}{int(m[2]) + plus}{m[3]}", text, count=1)
    return text.replace("<gnc:account", f'<gnc:count-data cd:type="{art}">{plus}</gnc:count-data>\n<gnc:account', 1)


def buchung_xml(buchung_id, datum, beschreibung, splits):
    teile = []
    for konto, betrag, memo in splits:
        notiz = f"<split:memo>{escape(memo)}</split:memo>" if memo else ""
        teile.append(
            f'<trn:split><split:id type="guid">{uuid.uuid4().hex}</split:id>{notiz}'
            f"<split:reconciled-state>n</split:reconciled-state>"
            f"<split:value>{betrag}/100</split:value><split:quantity>{betrag}/100</split:quantity>"
            f'<split:account type="guid">{konto}</split:account></trn:split>')
    jetzt = date.today()
    return (
        f'<gnc:transaction version="2.0.0"><trn:id type="guid">{buchung_id}</trn:id>'
        f"<trn:currency><cmdty:space>CURRENCY</cmdty:space><cmdty:id>EUR</cmdty:id></trn:currency>"
        f"<trn:date-posted><ts:date>{zeitstempel(datum)}</ts:date></trn:date-posted>"
        f"<trn:date-entered><ts:date>{zeitstempel(jetzt)}</ts:date></trn:date-entered>"
        f"<trn:description>{escape(beschreibung)}</trn:description>"
        f"<trn:splits>{''.join(teile)}</trn:splits></gnc:transaction>\n")


def text_ohne(text, buchung_id):
    if not re.fullmatch(r"[0-9a-f]{32}", buchung_id or ""):
        raise BuchFehler("Buchung nicht gefunden")
    muster = (r"<gnc:transaction\b(?:(?!</gnc:transaction>).)*?"
              rf'<trn:id type="guid">{buchung_id}</trn:id>.*?</gnc:transaction>\s*')
    neu, anzahl = re.subn(muster, "", text, count=1, flags=re.S)
    if not anzahl:
        raise BuchFehler("Buchung nicht gefunden")
    return neu


def splits_bauen(konten, art, konto_id, positionen):
    """Prüft Art und Konten und liefert die Splits; Positionen: konto, betrag (Cent), memo."""
    if art not in ARTEN:
        raise BuchFehler("Unbekannte Art")
    if not positionen:
        raise BuchFehler("Mindestens eine Position nötig")

    def konto_von(kid, typen, was):
        k = konten.get(kid)
        if not k or k["platzhalter"] or k["typ"] not in typen:
            raise BuchFehler(f"{was} passt nicht zu dieser Buchung")
        return k

    konto_von(konto_id, VERMOEGEN_TYPEN, "Konto")
    ziel = {"ausgabe": ("EXPENSE",), "einnahme": ("INCOME",), "umbuchung": VERMOEGEN_TYPEN}[art]
    splits, summe = [], 0
    for p in positionen:
        konto_von(p["konto"], ziel, "Kategorie")
        if p["betrag"] == 0:
            raise BuchFehler("Positionen dürfen nicht 0 € sein")
        if art == "umbuchung" and p["konto"] == konto_id:
            raise BuchFehler("Von- und Nach-Konto sind gleich")
        wert = -p["betrag"] if art == "einnahme" else p["betrag"]
        splits.append((p["konto"], wert, p.get("memo", "")))
        summe += wert
    splits.append((konto_id, -summe, ""))
    return splits


def buchen(art, datum, beschreibung, konto_id, positionen, ersetzt=None):
    beschreibung = (beschreibung or "").strip()
    if not beschreibung:
        raise BuchFehler("Beschreibung fehlt")
    d = tag(datum)
    with lock:
        konten, _ = lesen()
        splits = splits_bauen(konten, art, konto_id, positionen)
        text = BUCH.read_text(encoding="utf-8")
        if ersetzt:
            text = zaehler(text_ohne(text, ersetzt), "transaction", -1)
        neue_id = uuid.uuid4().hex
        stelle = text.rfind("</gnc:book>")
        text = text[:stelle] + buchung_xml(neue_id, d, beschreibung, splits) + text[stelle:]
        schreiben(zaehler(text, "transaction", 1))
    return neue_id


def loeschen(buchung_id):
    with lock:
        text = BUCH.read_text(encoding="utf-8") if vorhanden() else ""
        schreiben(zaehler(text_ohne(text, buchung_id), "transaction", -1))


def konto_anlegen(name, eltern_id):
    name = (name or "").strip()
    if not name or ":" in name:
        raise BuchFehler("Kontoname fehlt oder enthält einen Doppelpunkt")
    with lock:
        konten, _ = lesen()
        eltern = konten.get(eltern_id)
        if not eltern or eltern["typ"] == "ROOT":
            raise BuchFehler("Oberkonto nicht gefunden")
        if any(k["eltern"] == eltern_id and k["name"] == name for k in konten.values()):
            raise BuchFehler("Dieses Konto gibt es dort schon")
        # Unter einem Vermögens-Sammelkonto entsteht ein Bankkonto, sonst derselbe Typ.
        typ = "BANK" if eltern["typ"] == "ASSET" else eltern["typ"]
        neu = uuid.uuid4().hex
        xml = (f'<gnc:account version="2.0.0"><act:name>{escape(name)}</act:name>'
               f'<act:id type="guid">{neu}</act:id><act:type>{typ}</act:type>'
               f"<act:commodity><cmdty:space>CURRENCY</cmdty:space><cmdty:id>EUR</cmdty:id></act:commodity>"
               f"<act:commodity-scu>100</act:commodity-scu>"
               f'<act:parent type="guid">{eltern_id}</act:parent></gnc:account>\n')
        text = BUCH.read_text(encoding="utf-8")
        stelle = text.find("<gnc:transaction")
        stelle = stelle if stelle >= 0 else text.rfind("</gnc:book>")
        schreiben(zaehler(text[:stelle] + xml + text[stelle:], "account", 1))
    return neu


def importieren(roh):
    """Übernimmt eine GnuCash-Datei (auch gepackt, so speichert GnuCash sie selbst)."""
    if roh[:2] == b"\x1f\x8b":
        try:
            roh = gzip.decompress(roh)
        except OSError:
            raise BuchFehler("Die gepackte Datei ist beschädigt")
    try:
        text = roh.decode("utf-8")
    except UnicodeDecodeError:
        raise BuchFehler("Keine GnuCash-XML-Datei (als SQLite gespeichert?)")
    konten, buchungen = auswerten(text)
    with lock:
        schreiben(text)
    return len(konten), len(buchungen)


# ---------- Sicht aufs Buch ----------

def einordnen(konten, b):
    """Liefert Art, Hauptkonto und Positionen einer Buchung, soweit sie einfach gebaut ist."""
    typen = [konten[s["konto"]]["typ"] for s in b["splits"]]
    art = ("ausgabe" if "EXPENSE" in typen else "einnahme" if "INCOME" in typen
           else "eroeffnung" if "EQUITY" in typen else "umbuchung")
    if art == "eroeffnung":
        return art, None, [], False
    if art == "umbuchung":
        ab = [s for s in b["splits"] if s["betrag"] < 0]
        zu = [s for s in b["splits"] if s["betrag"] > 0]
        einfach = len(ab) == 1
        konto = ab[0]["konto"] if ab else None
        return art, konto, [dict(s) for s in zu], einfach
    ziel = "EXPENSE" if art == "ausgabe" else "INCOME"
    faktor = 1 if art == "ausgabe" else -1
    positionen = [{**s, "betrag": s["betrag"] * faktor} for s in b["splits"] if konten[s["konto"]]["typ"] == ziel]
    rest = [s for s in b["splits"] if konten[s["konto"]]["typ"] != ziel]
    return art, rest[0]["konto"] if len(rest) == 1 else None, positionen, len(rest) == 1


def ausgabezeilen(von=None, bis=None):
    """Nur Ausgaben, ohne Zahlkonto: das Einzige, was an Claude geht."""
    konten, buchungen = lesen()
    zeilen = []
    for b in buchungen:
        if (von and b["datum"] < von) or (bis and b["datum"] > bis):
            continue
        positionen = [{"kategorie": konten[s["konto"]]["pfad"], "betrag": s["betrag"], "memo": s["memo"]}
                      for s in b["splits"] if konten[s["konto"]]["typ"] == "EXPENSE"]
        if positionen:
            zeilen.append({"id": b["id"], "datum": b["datum"], "beschreibung": b["beschreibung"],
                           "positionen": positionen, "gesamt": sum(p["betrag"] for p in positionen)})
    return zeilen
