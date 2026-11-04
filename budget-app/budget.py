"""Budget: Haushaltsbuch im GnuCash-Format mit Panel und Schnittstelle für Claude.

Das Ingress-Panel (panel.html) spricht mit der JSON-API hier und ist der einzige
Weg zu Salden, Einnahmen, Umbuchungen und Depot. Claude kommt über Skripte in Home
Assistant auf einen internen Port (ausgaben.py), der nur Ausgaben kennt. Wiederkehrende Buchungen liegen in
/data/wiederkehrend.json und werden fällig automatisch ins Buch geschrieben.
"""
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import buch
import ausgaben
from buch import BuchFehler, DATA, cent, json_laden, json_speichern, lock

PANEL = Path(__file__).with_name("panel.html")
PORT = int(os.environ.get("BUDGET_PORT", "8099"))
AUSGABEN_PORT = int(os.environ.get("BUDGET_AUSGABEN_PORT", "8098"))
# Nur das Ingress-Gateway des Supervisors darf zugreifen (lokal zum Testen auch 127.0.0.1).
ERLAUBT = {"172.30.32.2", "127.0.0.1", "::1"}
EINSTELLUNGEN = DATA / "einstellungen.json"
MAX_IMPORT = 20 * 1024 * 1024
MAX_JSON = 1024 * 1024
GROSSER_POSTEN = 5000  # Cent; ab hier zählt eine Ausgabe nicht mehr als Alltag


# ---------- Wiederkehrend ----------

def wiederkehrend_pruefen(daten, alt=None):
    art = daten.get("art")
    name = str(daten.get("name") or "").strip()
    if not name:
        raise BuchFehler("Name fehlt")
    betrag = cent(daten.get("betrag"))
    if betrag <= 0:
        raise BuchFehler("Der Betrag muss über 0 € liegen")
    tag_im_monat = int(daten.get("tag") or 0)
    alle = int(daten.get("alle_monate") or 0)
    if not 1 <= tag_im_monat <= 31 or alle not in (1, 2, 3, 6, 12):
        raise BuchFehler("Tag oder Rhythmus passt nicht")
    konten, _ = buch.lesen()
    buch.splits_bauen(konten, art, daten.get("konto"), [{"konto": daten.get("gegenkonto"), "betrag": betrag}])
    naechste = buch.tag(daten.get("naechste"))
    ende = buch.tag(daten["ende"]).isoformat() if daten.get("ende") else ""
    return {"id": (alt or {}).get("id") or secrets.token_hex(8), "art": art, "name": name, "betrag": betrag,
            "konto": daten["konto"], "gegenkonto": daten["gegenkonto"], "tag": tag_im_monat, "alle_monate": alle,
            "naechste": naechste.isoformat(), "ende": ende, "aktiv": daten.get("aktiv", True) is not False}


def faellige_buchen(heute=None):
    """Bucht alles Fällige; die Markierung im Memo verhindert Doppelte nach einem Absturz."""
    heute = heute or date.today()
    with lock:
        if not buch.vorhanden():
            return 0
        liste = json_laden(ausgaben.WIEDERKEHREND, [])
        gebucht = 0
        for w in liste:
            while w.get("aktiv", True) and w["naechste"] <= heute.isoformat() and (not w["ende"] or w["naechste"] <= w["ende"]):
                memo = f"Wiederkehrend {w['id']} {w['naechste']}"
                _, buchungen = buch.lesen()
                if not any(s["memo"] == memo for b in buchungen for s in b["splits"]):
                    buch.buchen(w["art"], w["naechste"], w["name"], w["konto"],
                                [{"konto": w["gegenkonto"], "betrag": w["betrag"], "memo": memo}])
                    gebucht += 1
                w["naechste"] = buch.monat_plus(date.fromisoformat(w["naechste"]), w["alle_monate"], w["tag"]).isoformat()
                json_speichern(ausgaben.WIEDERKEHREND, liste)
        return gebucht


def faellige_schleife():
    while True:
        try:
            faellige_buchen()
        except (BuchFehler, OSError) as ex:
            print(f"Wiederkehrende Buchungen: {ex}")
        time.sleep(3600)


# ---------- Sicht fürs Panel ----------

def stand():
    konten, buchungen = buch.lesen()
    salden = {}
    for b in buchungen:
        for s in b["splits"]:
            salden[s["konto"]] = salden.get(s["konto"], 0) + s["betrag"]
    aktiv = [k for k in konten.values() if k["typ"] in buch.VERMOEGEN_TYPEN and k["waehrung"] == "EUR"]
    liste = [{"pfad": k["pfad"], "saldo": salden.get(k["id"], 0), "typ": k["typ"]}
             for k in sorted(aktiv, key=lambda k: k["pfad"]) if not k["platzhalter"]]
    vermoegen = sum(x["saldo"] for x in liste)
    e = json_laden(EINSTELLUNGEN, {})
    ziel = None
    if e.get("sparziel") and e.get("sparziel_datum"):
        z = date.fromisoformat(e["sparziel_datum"])
        heute = date.today()
        monate = max(1, (z.year - heute.year) * 12 + z.month - heute.month + (z.day >= heute.day))
        ziel = {"betrag": e["sparziel"], "datum": e["sparziel_datum"], "monate": monate,
                "fehlt": max(0, e["sparziel"] - vermoegen), "pro_monat": max(0, e["sparziel"] - vermoegen) // monate}
    monate = sorted({b["datum"][:7] for b in buchungen if any(konten[s["konto"]]["typ"] in ("EXPENSE", "INCOME") for s in b["splits"])})
    return {"konten": liste, "vermoegen": vermoegen, "ziel": ziel, "monate": monate}


def kontenliste():
    konten, _ = buch.lesen()
    return [{"id": k["id"], "pfad": k["pfad"], "typ": k["typ"], "platzhalter": k["platzhalter"]}
            for k in sorted(konten.values(), key=lambda k: k["pfad"]) if k["typ"] != "ROOT"]


def monat(m):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m or ""):
        raise BuchFehler("Monat im Format JJJJ-MM angeben")
    jahr, nr = int(m[:4]), int(m[5:])
    vor = f"{jahr - 1}-12" if nr == 1 else f"{jahr}-{nr - 1:02d}"
    konten, buchungen = buch.lesen()
    ausgaben, einnahmen, kurs, davor = {}, {}, 0, {}
    zeilen = []
    for b in buchungen:
        for s in b["splits"]:
            k = konten[s["konto"]]
            if b["datum"][:7] == vor and k["typ"] == "EXPENSE":
                davor[k["pfad"]] = davor.get(k["pfad"], 0) + s["betrag"]
            if b["datum"][:7] != m:
                continue
            if k["typ"] == "EXPENSE":
                ausgaben[k["pfad"]] = ausgaben.get(k["pfad"], 0) + s["betrag"]
            elif k["typ"] == "INCOME" and "Kursgewinn" in k["name"]:
                kurs -= s["betrag"]
            elif k["typ"] == "INCOME":
                einnahmen[k["pfad"]] = einnahmen.get(k["pfad"], 0) - s["betrag"]
        if b["datum"][:7] == m:
            art, konto, positionen, einfach = buch.einordnen(konten, b)
            zeilen.append({"id": b["id"], "datum": b["datum"], "beschreibung": b["beschreibung"], "art": art,
                           "konto": konto, "konto_name": konten[konto]["pfad"] if konto else "",
                           "bearbeitbar": einfach, "gesamt": sum(p["betrag"] for p in positionen),
                           "positionen": [{"konto": p["konto"], "konto_name": konten[p["konto"]]["pfad"],
                                           "betrag": p["betrag"], "memo": p["memo"]} for p in positionen]})
    zeilen.sort(key=lambda z: z["datum"], reverse=True)
    summe_ein, summe_aus = sum(einnahmen.values()), sum(ausgaben.values())
    return {"monat": m, "vormonat": vor, "einnahmen": summe_ein, "ausgaben": summe_aus, "kurs": kurs,
            "ausgaben_vormonat": sum(davor.values()), "kategorien": [
                {"pfad": p, "betrag": ausgaben.get(p, 0), "vormonat": davor.get(p, 0)}
                for p in sorted(set(ausgaben) | set(davor), key=lambda p: -ausgaben.get(p, 0))],
            "einnahmequellen": [{"pfad": p, "betrag": b} for p, b in sorted(einnahmen.items(), key=lambda x: -x[1])],
            "buchungen": zeilen}


def bericht(kennung):
    """Alle Zahlen für die Grafiken eines Monats (JJJJ-MM) oder der letzten 7 Tage ("woche")."""
    heute = date.today()
    zeilen = buch.ausgabezeilen()
    konten, buchungen = buch.lesen()
    dauer = {(konten[w["gegenkonto"]]["pfad"], w["betrag"]) for w in json_laden(ausgaben.WIEDERKEHREND, [])
             if w["art"] == "ausgabe" and w["gegenkonto"] in konten}

    def ist_fix(z):
        return any(p["memo"].startswith("Wiederkehrend ") or (p["kategorie"], p["betrag"]) in dauer for p in z["positionen"])

    def im(von, bis):
        return [z for z in zeilen if von.isoformat() <= z["datum"] <= bis.isoformat()]

    def summe(liste):
        return sum(z["gesamt"] for z in liste)

    def je_tag(liste, von, tage):
        werte = [0] * tage
        for z in liste:
            werte[(date.fromisoformat(z["datum"]) - von).days] += z["gesamt"]
        return werte

    if kennung == "woche":
        von, bis = heute - timedelta(days=6), heute
        vor_von, vor_bis = von - timedelta(days=7), von - timedelta(days=1)
        titel, vergleich_titel = f"{von.strftime('%d.%m.')} bis {bis.strftime('%d.%m.')}", "7 Tage davor"
        tage, vor_tage = 7, 7
        trend = []
        for i in range(7, -1, -1):
            ende = heute - timedelta(days=7 * i)
            trend.append({"titel": ende.strftime("%d.%m."), "ausgaben": summe(im(ende - timedelta(days=6), ende))})
    else:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", kennung or ""):
            raise BuchFehler("Zeitraum nicht lesbar")
        von = date.fromisoformat(f"{kennung}-01")
        bis = buch.monat_plus(von, 0, 31)
        vor_von = buch.monat_plus(von, -1, 1)
        vor_bis = von - timedelta(days=1)
        titel, vergleich_titel = kennung, vor_von.strftime("%Y-%m")
        tage, vor_tage = (min(bis, max(heute, von)) - von).days + 1, (vor_bis - vor_von).days + 1
        einnahmen = {}
        for b in buchungen:
            for s in b["splits"]:
                k = konten[s["konto"]]
                if k["typ"] == "INCOME" and "Kursgewinn" not in k["name"]:
                    einnahmen[b["datum"][:7]] = einnahmen.get(b["datum"][:7], 0) - s["betrag"]
        trend = []
        for i in range(5, -1, -1):
            start = buch.monat_plus(von, -i, 1)
            schluessel = start.strftime("%Y-%m")
            trend.append({"titel": schluessel, "ausgaben": summe(im(start, buch.monat_plus(start, 0, 31))),
                          "einnahmen": einnahmen.get(schluessel, 0)})

    jetzt, davor = im(von, bis), im(vor_von, vor_bis)
    fix = [z for z in jetzt if ist_fix(z)]
    gross = [z for z in jetzt if not ist_fix(z) and z["gesamt"] >= GROSSER_POSTEN]
    kategorien, vor_kategorien, haendler = {}, {}, {}
    for z in jetzt:
        for p in z["positionen"]:
            kategorien[p["kategorie"]] = kategorien.get(p["kategorie"], 0) + p["betrag"]
        if not ist_fix(z):
            woerter = z["beschreibung"].split("(")[0].split()
            name = " ".join(woerter[:3] if woerter[1:2] == ["&"] else woerter[:2]) or z["beschreibung"]
            haendler[name] = haendler.get(name, 0) + z["gesamt"]
    for z in davor:
        for p in z["positionen"]:
            vor_kategorien[p["kategorie"]] = vor_kategorien.get(p["kategorie"], 0) + p["betrag"]

    berichte = json_laden(ausgaben.BERICHTE, {})
    wochen = sorted(k for k in berichte if "W" in k)
    text = berichte.get(wochen[-1] if kennung == "woche" and wochen else kennung) or {}
    return {
        "kennung": kennung, "titel": titel, "vergleich_titel": vergleich_titel, "von": von.isoformat(),
        "gesamt": summe(jetzt), "vergleich": summe(davor), "fix": summe(fix), "gross": summe(gross),
        "alltag": summe(jetzt) - summe(fix) - summe(gross),
        "einnahmen": trend[-1].get("einnahmen"),
        "verlauf": je_tag(jetzt, von, (bis - von).days + 1)[:tage],
        "vergleich_verlauf": je_tag(davor, vor_von, vor_tage),
        "kategorien": [{"pfad": k, "betrag": kategorien.get(k, 0), "vergleich": vor_kategorien.get(k, 0)}
                       for k in sorted(set(kategorien) | set(vor_kategorien), key=lambda k: -kategorien.get(k, 0))],
        "haendler": [{"name": n, "betrag": b} for n, b in sorted(haendler.items(), key=lambda x: -x[1])[:6] if b > 0],
        "posten": [{"datum": z["datum"], "beschreibung": z["beschreibung"], "betrag": z["gesamt"]}
                   for z in sorted(jetzt, key=lambda z: -z["gesamt"])[:5]],
        "trend": trend, "text": text.get("text", ""), "vorschlaege": text.get("vorschlaege", []),
    }


def positionen_lesen(daten):
    return [{"konto": p.get("konto"), "betrag": cent(p.get("betrag")), "memo": str(p.get("memo") or "").strip()}
            for p in daten.get("positionen") or []]


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def antwort(self, code, daten=None, typ="application/json; charset=utf-8", roh=None, kopf=None):
        roh = roh if roh is not None else json.dumps(daten, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(roh)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (kopf or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(roh)

    def fehler(self, code, text):
        self.antwort(code, {"fehler": text})

    def koerper(self, grenze=MAX_JSON):
        laenge = int(self.headers.get("Content-Length") or 0)
        if laenge > grenze:
            raise BuchFehler("Die Datei ist zu groß")
        return self.rfile.read(laenge)

    def erlaubt(self):
        if self.client_address[0] in ERLAUBT:
            return True
        self.fehler(403, "Nur über das Home-Assistant-Panel erreichbar")
        return False

    def ausfuehren(self, arbeit):
        if not self.erlaubt():
            return
        try:
            arbeit()
        except (BuchFehler, ValueError, KeyError, TypeError) as ex:
            self.fehler(400, str(ex) if isinstance(ex, BuchFehler) else "Ungültige Eingabe")

    def do_GET(self):
        self.ausfuehren(self.lesen)

    def do_POST(self):
        self.ausfuehren(self.anlegen)

    def do_PUT(self):
        self.ausfuehren(self.aendern)

    def do_DELETE(self):
        self.ausfuehren(self.entfernen)

    def lesen(self):
        teile = urllib.parse.urlsplit(self.path)
        pfad, q = teile.path, urllib.parse.parse_qs(teile.query)
        if pfad == "/":
            return self.antwort(200, typ="text/html; charset=utf-8", roh=PANEL.read_bytes())
        if pfad == "/api/status":
            return self.antwort(200, {"buch": buch.vorhanden()})
        if pfad == "/api/stand":
            return self.antwort(200, stand())
        if pfad == "/api/konten":
            return self.antwort(200, {"konten": kontenliste()})
        if pfad == "/api/monat":
            return self.antwort(200, monat(q.get("m", [""])[0]))
        if pfad == "/api/wiederkehrend":
            return self.antwort(200, {"liste": json_laden(ausgaben.WIEDERKEHREND, [])})
        if pfad == "/api/vorschlaege":
            return self.antwort(200, {"liste": [v for v in ausgaben.vorschlaege_laden() if v["status"] == "offen"]})
        if pfad == "/api/bericht":
            return self.antwort(200, bericht(q.get("k", ["woche"])[0]))
        if pfad == "/api/einstellungen":
            return self.antwort(200, json_laden(EINSTELLUNGEN, {}))
        if pfad == "/api/export":
            return self.antwort(200, typ="application/octet-stream", roh=buch.BUCH.read_bytes(), kopf={
                "Content-Disposition": 'attachment; filename="haushaltsbuch.gnucash"'})
        self.fehler(404, "Nicht gefunden")

    def anlegen(self):
        pfad = urllib.parse.urlsplit(self.path).path
        if pfad == "/api/import":
            konten, buchungen = buch.importieren(self.koerper(MAX_IMPORT))
            faellige_buchen()
            return self.antwort(200, {"konten": konten, "buchungen": buchungen})
        daten = json.loads(self.koerper() or b"{}")
        if pfad == "/api/buchungen":
            kennung = buch.buchen(daten.get("art"), daten.get("datum"), daten.get("beschreibung"),
                                  daten.get("konto"), positionen_lesen(daten))
            return self.antwort(200, {"id": kennung})
        if pfad == "/api/konten":
            return self.antwort(200, {"id": buch.konto_anlegen(daten.get("name"), daten.get("eltern"))})
        if pfad == "/api/wiederkehrend":
            with lock:
                liste = json_laden(ausgaben.WIEDERKEHREND, [])
                liste.append(wiederkehrend_pruefen(daten))
                json_speichern(ausgaben.WIEDERKEHREND, liste)
            return self.antwort(200, {"gebucht": faellige_buchen()})
        m = re.fullmatch(r"/api/vorschlaege/(\w+)/(buchen|verwerfen)", pfad)
        if m and m[2] == "buchen":
            ausgaben.vorschlag_buchen(m[1], daten.get("trotz_duplikat") is True)
            return self.antwort(200, {"ok": True})
        if m:
            ausgaben.vorschlag_verwerfen(m[1])
            return self.antwort(200, {"ok": True})
        self.fehler(404, "Nicht gefunden")

    def aendern(self):
        pfad = urllib.parse.urlsplit(self.path).path
        daten = json.loads(self.koerper() or b"{}")
        m = re.fullmatch(r"/api/buchungen/(\w+)", pfad)
        if m:
            kennung = buch.buchen(daten.get("art"), daten.get("datum"), daten.get("beschreibung"),
                                  daten.get("konto"), positionen_lesen(daten), ersetzt=m[1])
            return self.antwort(200, {"id": kennung})
        m = re.fullmatch(r"/api/wiederkehrend/(\w+)", pfad)
        if m:
            with lock:
                liste = json_laden(ausgaben.WIEDERKEHREND, [])
                i = next((i for i, w in enumerate(liste) if w["id"] == m[1]), None)
                if i is None:
                    raise BuchFehler("Eintrag nicht gefunden")
                liste[i] = wiederkehrend_pruefen(daten, liste[i])
                json_speichern(ausgaben.WIEDERKEHREND, liste)
            return self.antwort(200, {"gebucht": faellige_buchen()})
        if pfad == "/api/einstellungen":
            ziel = cent(daten["sparziel"]) if daten.get("sparziel") not in (None, "") else 0
            termin = buch.tag(daten["sparziel_datum"]).isoformat() if daten.get("sparziel_datum") else ""
            json_speichern(EINSTELLUNGEN, {"sparziel": ziel, "sparziel_datum": termin})
            return self.antwort(200, {"ok": True})
        self.fehler(404, "Nicht gefunden")

    def entfernen(self):
        pfad = urllib.parse.urlsplit(self.path).path
        m = re.fullmatch(r"/api/buchungen/(\w+)", pfad)
        if m:
            buch.loeschen(m[1])
            return self.antwort(200, {"ok": True})
        m = re.fullmatch(r"/api/wiederkehrend/(\w+)", pfad)
        if m:
            with lock:
                liste = json_laden(ausgaben.WIEDERKEHREND, [])
                json_speichern(ausgaben.WIEDERKEHREND, [w for w in liste if w["id"] != m[1]])
            return self.antwort(200, {"ok": True})
        self.fehler(404, "Nicht gefunden")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=faellige_schleife, daemon=True).start()
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", AUSGABEN_PORT), ausgaben.Handler).serve_forever, daemon=True).start()
    print(f"Budget läuft: Panel auf Port {PORT}, Ausgaben-Schnittstelle auf Port {AUSGABEN_PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
