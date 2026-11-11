"""Bankumsätze aus der Chromium-Erweiterung (Trade Republic, GLS) zum Einordnen im Panel.

Die Erweiterung liest die Umsätze aus dem angemeldeten Online-Banking und schickt sie
über einen Home-Assistant-Webhook an das Werkzeug bank_einreichen (ausgaben.py). Dort
werden sie nur abgelegt, die Antwort enthält nur die Anzahl. Gebucht wird erst, wenn
Enzo im Panel bestätigt. Es gibt hier keine Zugangsdaten.
"""
import hashlib
import re
import time
from datetime import date, timedelta

import buch
from buch import BuchFehler, DATA, cent, json_laden, json_speichern, lock

BANK = DATA / "bank.json"
ALT_ZUGANG = DATA / "bank-zugang.json"  # vom FinTS-Abruf (0.11.0), wird entfernt
BANKEN = {"tr": "Trade Republic", "gls": "GLS Bank"}
MAX_UMSAETZE = 3000
MAX_EINREICHEN = 2000
UEBERLAPPUNG = 5  # Tage vor dem letzten Abruf, die noch angenommen werden (spät gebuchte Kartenzahlungen)


def aufraeumen():
    ALT_ZUGANG.unlink(missing_ok=True)


def buchkonten(konten=None):
    """Bank → Konto-ID; ohne eigene Wahl das Zahlkonto, dessen Pfad den Banknamen enthält."""
    konten = konten or buch.lesen()[0]
    gewaehlt = json_laden(BANK, {}).get("konten", {})
    ergebnis = {}
    for kuerzel, name in BANKEN.items():
        kid = gewaehlt.get(kuerzel)
        if not kid or kid not in konten:
            passend = [k for k in konten.values() if k["typ"] in buch.ZAHLKONTO_TYPEN and not k["platzhalter"]
                       and name.split()[0].lower() in k["pfad"].lower()]
            kid = passend[0]["id"] if len(passend) == 1 else ""
        ergebnis[kuerzel] = kid
    return ergebnis


def einstellungen_lesen():
    daten = json_laden(BANK, {})
    return {"banken": BANKEN, "konten": buchkonten(), "zuletzt": daten.get("zuletzt", {})}


def einstellungen_speichern(neu):
    with lock:
        konten, _ = buch.lesen()
        wahl = {}
        for kuerzel in BANKEN:
            kid = str((neu.get("konten") or {}).get(kuerzel) or "")
            if kid and (kid not in konten or konten[kid]["typ"] not in buch.ZAHLKONTO_TYPEN):
                raise BuchFehler("Das Buchkonto muss ein Zahlkonto sein")
            wahl[kuerzel] = kid
        daten = json_laden(BANK, {})
        daten["konten"] = wahl
        json_speichern(BANK, daten)


# ---------- Umsätze ----------

def _text(x, laenge=300):
    return re.sub(r"\s+", " ", str(x or "")).strip()[:laenge]


def umsaetze_pruefen(bank, roh):
    """Macht aus den Einträgen der Erweiterung feste Umsätze; ohne Bank-Kennung zählen gleiche am selben Tag mit."""
    if bank not in BANKEN:
        raise BuchFehler("Unbekannte Bank")
    if not isinstance(roh, list) or len(roh) > MAX_EINREICHEN:
        raise BuchFehler("Umsätze als Liste schicken")
    liste, gesehen = [], {}
    for e in roh:
        tag = buch.tag(e.get("datum")).isoformat()
        betrag = cent(e.get("betrag"))
        if betrag == 0:
            continue
        name = _text(e.get("name"), 120) or "Unbekannt"
        zweck = _text(e.get("zweck"))
        if e.get("id"):
            schluessel = f"{bank}|id|{_text(e['id'], 100)}"
        else:
            grund = f"{bank}|{tag}|{betrag}|{name}|{zweck}"
            n = gesehen.get(grund, 0)
            gesehen[grund] = n + 1
            schluessel = f"{grund}|{n}"
        kennung = "u" + hashlib.sha1(schluessel.encode()).hexdigest()[:16]
        liste.append({"id": kennung, "bank": bank, "datum": tag, "betrag": betrag, "name": name, "zweck": zweck})
    return liste


def _gegenkonto(b, kontoid):
    andere = [s for s in b["splits"] if s["konto"] != kontoid]
    return max(andere, key=lambda s: abs(s["betrag"]))["konto"] if andere else ""


def einordnen(umsatz, kontoid, konten, buchungen, verknuepft):
    """Kategorie vom letzten gleichen Empfänger und mögliche Buchung, die schon im Buch steht."""
    name = umsatz["name"].lower()
    vorschlag = ""
    for b in sorted(buchungen, key=lambda b: b["datum"], reverse=True):
        if b["beschreibung"].lower() == name and any(s["konto"] == kontoid for s in b["splits"]):
            vorschlag = _gegenkonto(b, kontoid)
            if vorschlag and not konten[vorschlag]["platzhalter"]:
                break
            vorschlag = ""
    d = date.fromisoformat(umsatz["datum"])
    von, bis = (d - timedelta(days=3)).isoformat(), (d + timedelta(days=3)).isoformat()
    duplikat = next((b for b in buchungen if von <= b["datum"] <= bis and b["id"] not in verknuepft
                     and any(s["konto"] == kontoid and s["betrag"] == umsatz["betrag"] for s in b["splits"])), None)
    return vorschlag, ({"id": duplikat["id"], "datum": duplikat["datum"], "beschreibung": duplikat["beschreibung"]}
                       if duplikat else None)


def einreichen(bank, roh):
    """Neue Umsätze ablegen; liefert nur die Anzahl.

    Bekannte (gleiche Kennung, auch gebuchte und ignorierte) kommen nicht wieder. Was älter ist als der
    letzte Abruf dieser Bank (abzüglich ein paar Tage Überlappung), wird gar nicht mehr nachgefragt.
    """
    neue = umsaetze_pruefen(bank, roh)
    with lock:
        konten, buchungen = buch.lesen()
        kontoid = buchkonten(konten)[bank]
        daten = json_laden(BANK, {})
        zuletzt = daten.get("zuletzt", {}).get(bank)
        grenze = (date.fromisoformat(zuletzt[:10]) - timedelta(days=UEBERLAPPUNG)).isoformat() if zuletzt else ""
        liste = daten.get("umsaetze", [])
        bekannt = {u["id"] for u in liste}
        verknuepft = {u.get("buchung_id") for u in liste} | {(u.get("duplikat") or {}).get("id") for u in liste}
        dazu = 0
        for u in sorted(neue, key=lambda u: u["datum"]):
            if u["id"] in bekannt or u["datum"] < grenze:
                continue
            vorschlag, duplikat = einordnen(u, kontoid, konten, buchungen, verknuepft) if kontoid else ("", None)
            if duplikat:
                verknuepft.add(duplikat["id"])
            liste.append({**u, "status": "offen", "vorschlag": vorschlag, "duplikat": duplikat})
            bekannt.add(u["id"])
            dazu += 1
        daten["umsaetze"] = liste[-MAX_UMSAETZE:]
        daten.setdefault("zuletzt", {})[bank] = time.strftime("%Y-%m-%d %H:%M")
        json_speichern(BANK, daten)
    return dazu


def offene():
    return [u for u in json_laden(BANK, {}).get("umsaetze", []) if u["status"] == "offen"]


def umsatz_buchen(kennung, gegenkonto, beschreibung=None):
    with lock:
        daten = json_laden(BANK, {})
        u = next((x for x in daten.get("umsaetze", []) if x["id"] == kennung), None)
        if not u or u["status"] != "offen":
            raise BuchFehler("Kein offener Umsatz mit dieser Nummer")
        konten, _ = buch.lesen()
        kontoid = buchkonten(konten).get(u.get("bank", "gls"))
        g = konten.get(gegenkonto)
        if not kontoid:
            raise BuchFehler(f"Erst das Buchkonto für {BANKEN[u['bank']]} wählen")
        if not g or g["platzhalter"] or g["id"] == kontoid:
            raise BuchFehler("Bitte eine Kategorie wählen")
        text = _text(beschreibung) or u["name"]
        memo = u["zweck"][:120]
        if g["typ"] == "EXPENSE":
            art, konto, pos = "ausgabe", kontoid, [{"konto": g["id"], "betrag": -u["betrag"], "memo": memo}]
        elif g["typ"] == "INCOME":
            art, konto, pos = "einnahme", kontoid, [{"konto": g["id"], "betrag": u["betrag"], "memo": memo}]
        elif g["typ"] in buch.VERMOEGEN_TYPEN:
            # Geht Geld ab, fließt es vom Buchkonto aufs andere; kommt Geld an, umgekehrt.
            if u["betrag"] < 0:
                art, konto, pos = "umbuchung", kontoid, [{"konto": g["id"], "betrag": -u["betrag"], "memo": memo}]
            else:
                art, konto, pos = "umbuchung", g["id"], [{"konto": kontoid, "betrag": u["betrag"], "memo": memo}]
        else:
            raise BuchFehler("Diese Kategorie passt nicht")
        u["buchung_id"] = buch.buchen(art, u["datum"], text, konto, pos)
        u["status"] = "gebucht"
        json_speichern(BANK, daten)
    return u["buchung_id"]


def umsatz_ignorieren(kennung):
    """Für Umsätze, die schon im Buch stehen (z. B. per Bon) oder nicht ins Buch sollen."""
    with lock:
        daten = json_laden(BANK, {})
        u = next((x for x in daten.get("umsaetze", []) if x["id"] == kennung and x["status"] == "offen"), None)
        if not u:
            raise BuchFehler("Kein offener Umsatz mit dieser Nummer")
        u["status"] = "ignoriert"
        json_speichern(BANK, daten)
