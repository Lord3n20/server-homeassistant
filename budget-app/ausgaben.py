"""Die Ausgaben-Schnittstelle für Claude: läuft über Skripte in Home Assistant.

Dieses Modul darf nur Ausgabenzeilen, Kategorien und die Namen der Zahlkonten
herausgeben. Salden, Einnahmen, Umbuchungen und Depot bleiben im Panel; darum
gibt es hier weder ein Werkzeug noch eine Antwort, die sie enthalten könnte.
Der Port ist nicht nach außen freigegeben, Anfragen kommen nur von Home Assistant.
Geschrieben wird in zwei Schritten: erst Vorschlag, nach dem OK Buchung.
"""
import json
import re
import secrets
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler

import buch
from buch import BuchFehler, DATA, cent, json_laden, json_speichern, lock

VORSCHLAEGE = DATA / "vorschlaege.json"
BERICHTE = DATA / "berichte.json"
WIEDERKEHREND = DATA / "wiederkehrend.json"
MAX_KOERPER = 1024 * 1024
# Home Assistant läuft im Host-Netz und kommt über das Gateway des App-Netzes.
ERLAUBT = {"172.30.32.1", "127.0.0.1", "::1"}


def euro(cents):
    return round(cents / 100, 2)


# ---------- Vorschläge (auch vom Panel benutzt) ----------

def vorschlaege_laden():
    return json_laden(VORSCHLAEGE, [])


def konto_nach_pfad(konten, pfad, typen, was):
    treffer = [k for k in konten.values() if k["pfad"] == pfad and k["typ"] in typen and not k["platzhalter"]]
    if not treffer:
        raise BuchFehler(f"{was} unbekannt: {pfad}. Gültige Namen liefert kategorien_und_zahlkonten.")
    return treffer[0]


def duplikate(gesamt, datum):
    """Ausgaben mit gleicher Summe bis zwei Tage daneben, auch aus Dauerbuchungen."""
    d = date.fromisoformat(datum)
    von, bis = (d - timedelta(days=2)).isoformat(), (d + timedelta(days=2)).isoformat()
    return [{"id": z["id"], "datum": z["datum"], "beschreibung": z["beschreibung"], "gesamt": euro(z["gesamt"])}
            for z in buch.ausgabezeilen(von, bis) if z["gesamt"] == gesamt]


def vorschlagen(a):
    d = buch.tag(a.get("datum"))
    if d > date.today() + timedelta(days=1):
        raise BuchFehler("Das Datum liegt in der Zukunft")
    beschreibung = str(a.get("beschreibung") or "").strip()
    if not beschreibung:
        raise BuchFehler("Beschreibung fehlt")
    with lock:
        konten, _ = buch.lesen()
        zahlkonto = konto_nach_pfad(konten, a.get("zahlkonto"), buch.ZAHLKONTO_TYPEN, "Zahlkonto")
        positionen = []
        roh = a.get("positionen") or []
        for p in json.loads(roh) if isinstance(roh, str) else roh:
            k = konto_nach_pfad(konten, p.get("kategorie"), ("EXPENSE",), "Kategorie")
            positionen.append({"konto": k["id"], "kategorie": k["pfad"], "betrag": cent(p.get("betrag")),
                               "memo": str(p.get("memo") or "").strip()})
        buch.splits_bauen(konten, "ausgabe", zahlkonto["id"], positionen)
        gesamt = sum(p["betrag"] for p in positionen)
        # Erstattungen sind ein Eingang und werden im Panel eingetragen; Pfand im Bon ist in Ordnung.
        if gesamt <= 0:
            raise BuchFehler("Die Summe muss über 0 € liegen. Erstattungen trägt der Nutzer im Panel ein.")
        if a.get("bonsumme") not in (None, ""):
            bon = cent(a["bonsumme"])
            if bon != gesamt:
                raise BuchFehler(f"Positionen ergeben {euro(gesamt)} €, der Bon {euro(bon)} €. "
                                 "Bitte Positionen prüfen.")
        vorschlag = {"id": "v" + secrets.token_hex(8), "status": "offen", "erstellt": time.strftime("%Y-%m-%d %H:%M"),
                     "datum": d.isoformat(), "beschreibung": beschreibung, "zahlkonto": zahlkonto["id"],
                     "zahlkonto_name": zahlkonto["pfad"], "positionen": positionen, "gesamt": gesamt,
                     "duplikate": duplikate(gesamt, d.isoformat())}
        liste = vorschlaege_laden()
        liste.append(vorschlag)
        json_speichern(VORSCHLAEGE, liste[-200:])
    return vorschlag


def vorschlag_buchen(vorschlag_id, trotz_duplikat=False):
    """Bucht einen offenen Vorschlag; ein zweiter Aufruf bucht nicht noch einmal."""
    with lock:
        liste = vorschlaege_laden()
        v = next((x for x in liste if x["id"] == vorschlag_id), None)
        if not v:
            raise BuchFehler("Vorschlag nicht gefunden")
        if v["status"] == "gebucht":
            return v
        if v["status"] != "offen":
            raise BuchFehler("Dieser Vorschlag wurde verworfen")
        treffer = duplikate(v["gesamt"], v["datum"])
        if treffer and not trotz_duplikat:
            raise BuchFehler("Mögliches Duplikat: " + json.dumps(treffer, ensure_ascii=False) +
                             ". Nur mit trotz_duplikat buchen, wenn der Nutzer das bestätigt hat.")
        v["buchung_id"] = buch.buchen("ausgabe", v["datum"], v["beschreibung"], v["zahlkonto"], v["positionen"])
        v["status"] = "gebucht"
        json_speichern(VORSCHLAEGE, liste)
    return v


def vorschlag_verwerfen(vorschlag_id):
    with lock:
        liste = vorschlaege_laden()
        v = next((x for x in liste if x["id"] == vorschlag_id and x["status"] == "offen"), None)
        if not v:
            raise BuchFehler("Kein offener Vorschlag mit dieser Nummer")
        v["status"] = "verworfen"
        json_speichern(VORSCHLAEGE, liste)


# ---------- Werkzeuge ----------

def zeile_aussen(z):
    return {"id": z["id"], "datum": z["datum"], "beschreibung": z["beschreibung"], "gesamt": euro(z["gesamt"]),
            "positionen": [{"kategorie": p["kategorie"], "betrag": euro(p["betrag"]), "memo": p["memo"]}
                           for p in z["positionen"]]}


def vorschlag_aussen(v):
    return {"vorschlag_id": v["id"], "status": v["status"], "datum": v["datum"], "beschreibung": v["beschreibung"],
            "zahlkonto": v["zahlkonto_name"], "gesamt": euro(v["gesamt"]), "duplikate": v["duplikate"],
            "buchung_id": v.get("buchung_id"),
            "positionen": [{"kategorie": p["kategorie"], "betrag": euro(p["betrag"]), "memo": p["memo"]}
                           for p in v["positionen"]]}


def w_kategorien(_):
    konten, _b = buch.lesen()
    frei = [k for k in konten.values() if not k["platzhalter"]]
    return {"kategorien": sorted(k["pfad"] for k in frei if k["typ"] == "EXPENSE"),
            "zahlkonten": sorted(k["pfad"] for k in frei if k["typ"] in buch.ZAHLKONTO_TYPEN)}


def w_vorschlagen(a):
    return vorschlag_aussen(vorschlagen(a))


def w_bestaetigen(a):
    return vorschlag_aussen(vorschlag_buchen(a.get("vorschlag_id"), a.get("trotz_duplikat") in (True, "true", "True")))


def w_verwerfen(a):
    vorschlag_verwerfen(a.get("vorschlag_id"))
    return {"ok": True}


def w_ausgaben(a):
    von = buch.tag(a["von"]).isoformat() if a.get("von") else None
    bis = buch.tag(a["bis"]).isoformat() if a.get("bis") else None
    zeilen = buch.ausgabezeilen(von, bis)
    suche = str(a.get("suche") or "").lower()
    if suche:
        zeilen = [z for z in zeilen if suche in z["beschreibung"].lower()
                  or any(suche in p["memo"].lower() for p in z["positionen"])]
    return {"anzahl": len(zeilen), "ausgaben": [zeile_aussen(z) for z in zeilen[-500:]]}


def w_stornieren(a):
    with lock:
        konten, buchungen = buch.lesen()
        b = next((x for x in buchungen if x["id"] == a.get("id")), None)
        typen = {konten[s["konto"]]["typ"] for s in b["splits"]} if b else set()
        if not b or "EXPENSE" not in typen or typen - {"EXPENSE", *buch.ZAHLKONTO_TYPEN}:
            raise BuchFehler("Keine stornierbare Ausgabe mit dieser Nummer")
        buch.loeschen(b["id"])
    return {"ok": True}


def monatssummen(monat):
    summen, anzahl = {}, 0
    for z in buch.ausgabezeilen(f"{monat}-01", f"{monat}-31"):
        anzahl += 1
        for p in z["positionen"]:
            summen[p["kategorie"]] = summen.get(p["kategorie"], 0) + p["betrag"]
    return summen, anzahl


def w_monat(a):
    monat = str(a.get("monat") or "")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", monat):
        raise BuchFehler("Monat im Format JJJJ-MM angeben")
    jahr, m = int(monat[:4]), int(monat[5:])
    vormonat = f"{jahr - 1}-12" if m == 1 else f"{jahr}-{m - 1:02d}"
    jetzt, anzahl = monatssummen(monat)
    davor, _ = monatssummen(vormonat)
    return {"monat": monat, "buchungen": anzahl, "gesamt": euro(sum(jetzt.values())),
            "vormonat": vormonat, "gesamt_vormonat": euro(sum(davor.values())),
            "kategorien": [{"kategorie": k, "betrag": euro(jetzt.get(k, 0)), "vormonat": euro(davor.get(k, 0))}
                           for k in sorted(set(jetzt) | set(davor), key=lambda k: -jetzt.get(k, 0))]}


def w_wiederkehrend(_):
    konten, _b = buch.lesen()
    liste = []
    for w in json_laden(WIEDERKEHREND, []):
        if w["art"] == "ausgabe" and w.get("aktiv", True):
            liste.append({"name": w["name"], "betrag": euro(w["betrag"]), "alle_monate": w["alle_monate"],
                          "tag": w["tag"], "naechste": w["naechste"],
                          "kategorie": konten.get(w["gegenkonto"], {}).get("pfad", "")})
    return {"wiederkehrend": liste}


def w_bericht(a):
    monat = str(a.get("monat") or "")
    text = str(a.get("text") or "").strip()
    if not re.fullmatch(r"\d{4}-((0[1-9]|1[0-2])|W(0[1-9]|[1-4]\d|5[0-3]))", monat) or not text:
        raise BuchFehler("Monat (JJJJ-MM) oder Woche (JJJJ-Www) und Text angeben")
    if len(text) > 20000:
        raise BuchFehler("Der Bericht ist zu lang")
    with lock:
        berichte = json_laden(BERICHTE, {})
        # Von den Wochenübersichten bleibt nur die neueste, Monatsberichte bleiben alle.
        if "W" in monat:
            berichte = {k: v for k, v in berichte.items() if "W" not in k}
        berichte[monat] = {"text": text, "erstellt": time.strftime("%Y-%m-%d %H:%M")}
        json_speichern(BERICHTE, berichte)
    return {"ok": True}


WERKZEUGE = {
    "kategorien_und_zahlkonten": w_kategorien,
    "ausgabe_vorschlagen": w_vorschlagen,
    "ausgabe_bestaetigen": w_bestaetigen,
    "vorschlag_verwerfen": w_verwerfen,
    "ausgaben_lesen": w_ausgaben,
    "ausgabe_stornieren": w_stornieren,
    "monatsauswertung": w_monat,
    "wiederkehrende_ausgaben": w_wiederkehrend,
    "monatsbericht_speichern": w_bericht,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def antwort(self, code, daten):
        roh = json.dumps(daten, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    def do_POST(self):
        if self.client_address[0] not in ERLAUBT:
            return self.antwort(403, {"ok": False, "fehler": "Nur für Home Assistant"})
        m = re.fullmatch(r"/werkzeug/(\w+)", self.path)
        werkzeug = WERKZEUGE.get(m[1]) if m else None
        if not werkzeug:
            return self.antwort(404, {"ok": False, "fehler": "Unbekanntes Werkzeug"})
        laenge = int(self.headers.get("Content-Length") or 0)
        if laenge > MAX_KOERPER:
            self.close_connection = True
            return self.antwort(400, {"ok": False, "fehler": "Anfrage zu groß"})
        # Fehler kommen mit Status 200, damit das Skript den Text an Claude weitergeben kann.
        try:
            ergebnis = werkzeug(json.loads(self.rfile.read(laenge) or b"{}"))
        except BuchFehler as ex:
            return self.antwort(200, {"ok": False, "fehler": str(ex)})
        except (KeyError, TypeError, AttributeError, ValueError):
            return self.antwort(200, {"ok": False, "fehler": "Ungültige Eingabe"})
        self.antwort(200, {"ok": True, **ergebnis})
