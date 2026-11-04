"""Depot im Panel: aktuelle Kurse und Gewinn oder Verlust zum Kaufpreis.

Stückzahl und Kaufpreis trägt der Nutzer im Panel ein, sie liegen in
/data/depot.json. Spätere Käufe und Verkäufe im Buch werden mit dem aktuellen Kurs
geschätzt nachgezogen. Bewertet wird zum Geldkurs von Tradegate, ersatzweise zum
Mittelkurs von Lang & Schwarz (beide ohne Schlüssel und inoffiziell). Kurse werden
höchstens einmal pro Minute geholt, nach außen geht dabei nur die ISIN. ausgaben.py
kennt dieses Modul nicht.
"""
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import buch
from buch import BuchFehler, DATA, cent, json_laden, json_speichern, lock

DEPOT = DATA / "depot.json"
KURSE = DATA / "kurse.json"
MAX_ALTER = 60
UA = "Mozilla/5.0 (Budget-HA-App; privat)"
LS = "https://www.ls-tc.de/_rpc/json"
TRADEGATE = "https://www.tradegatebsx.com/refresh.php"

kurs_lock = threading.Lock()


# ---------- Kurse ----------

def json_holen(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read())


def von_tradegate(isin):
    d = json_holen(f"{TRADEGATE}?" + urllib.parse.urlencode({"isin": isin}))
    zahl = lambda w: float(str(w).replace(".", "").replace(",", ".")) if "," in str(w) else float(w)
    kurs = zahl(d["bid"])
    if kurs <= 0:
        raise ValueError("kein Geldkurs")
    return {"kurs": kurs, "vortag": zahl(d["close"]), "quelle": "Geldkurs Tradegate", "zeit": time.time()}


def von_ls(isin):
    treffer = json_holen(f"{LS}/.lstc/instrument/search/main?" + urllib.parse.urlencode({"q": isin, "localeId": 2}))
    kennung = next(x["instrumentId"] for x in treffer if x.get("isin") == isin)
    daten = json_holen(f"{LS}/instrument/chart/dataForInstrument?" + urllib.parse.urlencode({"instrumentId": kennung}))
    linien = daten["info"].get("plotlines") or []
    vortag = next((p["value"] for p in linien if p.get("id") == "previousDay"), None)
    stempel, kurs = daten["series"]["intraday"]["data"][-1]
    # Die Zeitstempel sind Berliner Ortszeit, als wäre sie UTC.
    tag = datetime.fromtimestamp(stempel / 1000, timezone.utc).date()
    return {"kurs": float(kurs), "vortag": vortag, "quelle": "Mittelkurs Lang & Schwarz",
            "zeit": stempel / 1000 - buch.berlin_stunden(tag) * 3600}


def kurs_holen(isin):
    for quelle in (von_tradegate, von_ls):
        try:
            return quelle(isin)
        except (OSError, ValueError, KeyError, IndexError, StopIteration, TypeError):
            continue
    return None


def aktualisieren(isins):
    """Holt veraltete Kurse; schlägt das fehl, bleibt der letzte bekannte Kurs stehen."""
    with kurs_lock:
        kurse = json_laden(KURSE, {})
        alt = [i for i in isins if time.time() - kurse.get(i, {}).get("abgerufen", 0) > MAX_ALTER]
        if not alt:
            return kurse
        with ThreadPoolExecutor(max_workers=4) as pool:
            for isin, kurs in zip(alt, pool.map(kurs_holen, alt)):
                if kurs:
                    kurse[isin] = {**kurs, "abgerufen": time.time()}
        json_speichern(KURSE, kurse)
        return kurse


def im_hintergrund(isins):
    threading.Thread(target=aktualisieren, args=(isins,), daemon=True).start()


# ---------- Positionen ----------

def ist_position(k):
    return ":Depot:" in k["pfad"] and not k["platzhalter"] and k["typ"] in buch.VERMOEGEN_TYPEN


def abgleichen(kurse):
    """Zieht Buchungen seit der letzten Eingabe nach, mit dem aktuellen Kurs geschätzt."""
    with lock:
        konten, buchungen = buch.lesen()
        depot = json_laden(DEPOT, {})
        geaendert = False
        for kid, p in depot.items():
            kurs = kurse.get(p["isin"], {}).get("kurs")
            if not kurs:
                continue
            for b in buchungen:
                if b["datum"] <= p["stand"] or b["id"] in p["verarbeitet"]:
                    continue
                if any("Kursgewinn" in konten[s["konto"]]["name"] for s in b["splits"]):
                    continue
                betrag = sum(s["betrag"] for s in b["splits"] if s["konto"] == kid)
                if not betrag:
                    continue
                if betrag > 0:
                    p["einstand"] += betrag
                    p["stueck"] += betrag / 100 / kurs
                else:
                    anteil = min(1, -betrag / 100 / kurs / p["stueck"]) if p["stueck"] else 0
                    p["einstand"] = round(p["einstand"] * (1 - anteil))
                    p["stueck"] *= 1 - anteil
                p["verarbeitet"].append(b["id"])
                p["geschaetzt"] = True
                geaendert = True
        if geaendert:
            json_speichern(DEPOT, depot)
        return depot


def eintragen(kid, daten):
    with lock:
        konten, _ = buch.lesen()
        if kid not in konten or not ist_position(konten[kid]):
            raise BuchFehler("Depotposition nicht gefunden")
        isin = str(daten.get("isin") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin):
            raise BuchFehler("Die ISIN hat nicht das richtige Format, z. B. IE00B4L5Y983")
        try:
            stueck = float(str(daten.get("stueck")).replace(".", "").replace(",", ".") if "," in str(daten.get("stueck")) else daten.get("stueck"))
        except (TypeError, ValueError):
            raise BuchFehler("Stückzahl nicht lesbar")
        if not 0 < stueck < 10_000_000:
            raise BuchFehler("Die Stückzahl muss über 0 liegen")
        # Der Kaufkurs darf mehr als zwei Nachkommastellen haben, gespeichert wird der Einstand in Cent.
        roh = str(daten.get("kaufkurs") or "").strip().replace("€", "")
        try:
            kaufkurs = float(roh.replace(".", "").replace(",", ".") if "," in roh else roh)
        except ValueError:
            raise BuchFehler("Kaufpreis nicht lesbar")
        if kaufkurs <= 0:
            raise BuchFehler("Der Kaufpreis muss über 0 liegen")
        depot = json_laden(DEPOT, {})
        depot[kid] = {"isin": isin, "stueck": stueck, "einstand": round(stueck * kaufkurs * 100),
                      "stand": date.today().isoformat(), "verarbeitet": [], "geschaetzt": False}
        json_speichern(DEPOT, depot)


def entfernen(kid):
    with lock:
        depot = json_laden(DEPOT, {})
        depot.pop(kid, None)
        json_speichern(DEPOT, depot)


def uebersicht(warten=True):
    konten, buchungen = buch.lesen()
    depot = json_laden(DEPOT, {})
    isins = [p["isin"] for p in depot.values()]
    if warten:
        kurse = aktualisieren(isins)
    else:
        kurse = json_laden(KURSE, {})
        im_hintergrund(isins)
    depot = abgleichen(kurse)
    salden = {}
    for b in buchungen:
        for s in b["splits"]:
            salden[s["konto"]] = salden.get(s["konto"], 0) + s["betrag"]
    liste = []
    for k in sorted((k for k in konten.values() if ist_position(k)), key=lambda k: k["pfad"]):
        p = depot.get(k["id"])
        zeile = {"konto": k["id"], "name": k["name"], "buchwert": salden.get(k["id"], 0), "eingerichtet": bool(p)}
        kurs = kurse.get(p["isin"]) if p else None
        if p:
            zeile.update(isin=p["isin"], stueck=p["stueck"], einstand=p["einstand"], geschaetzt=p["geschaetzt"])
        if p and kurs:
            wert = round(p["stueck"] * kurs["kurs"] * 100)
            zeile.update(kurs=kurs["kurs"], quelle=kurs["quelle"], zeit=kurs["zeit"], wert=wert, gewinn=wert - p["einstand"],
                         prozent=(wert - p["einstand"]) / p["einstand"] * 100 if p["einstand"] else 0,
                         heute=(kurs["kurs"] / kurs["vortag"] - 1) * 100 if kurs.get("vortag") else None)
        liste.append(zeile)
    mit_kurs = [z for z in liste if "wert" in z]
    wert, einstand = sum(z["wert"] for z in mit_kurs), sum(z["einstand"] for z in mit_kurs)
    return {"positionen": liste, "wert": wert, "einstand": einstand, "gewinn": wert - einstand,
            "prozent": (wert - einstand) / einstand * 100 if einstand else None,
            "zeit": min((z["zeit"] for z in mit_kurs), default=None)}


def aktuelle_werte():
    """Konto -> aktueller Wert, für Vermögen und Sparziel in der Übersicht."""
    return {z["konto"]: z["wert"] for z in uebersicht(warten=False)["positionen"] if "wert" in z}
