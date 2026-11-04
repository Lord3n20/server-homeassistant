"""Prüft Buchhaltung, Dauerbuchungen und dass die Schnittstelle für Claude nur Ausgaben herausgibt.

Aufruf: python3 test_budget.py
"""
import gzip
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["BUDGET_DATA"] = str(TMP)

import buch  # noqa: E402
import budget  # noqa: E402
import ausgaben  # noqa: E402

NS = ('xmlns:gnc="http://www.gnucash.org/XML/gnc" xmlns:act="http://www.gnucash.org/XML/act" '
      'xmlns:book="http://www.gnucash.org/XML/book" xmlns:cd="http://www.gnucash.org/XML/cd" '
      'xmlns:cmdty="http://www.gnucash.org/XML/cmdty" xmlns:slot="http://www.gnucash.org/XML/slot" '
      'xmlns:split="http://www.gnucash.org/XML/split" xmlns:trn="http://www.gnucash.org/XML/trn" '
      'xmlns:ts="http://www.gnucash.org/XML/ts"')
ROOT, AKTIVA, GIRO, DEPOT, AUSG, LEB, EIN, BAF, EK = [f"{i:032x}" for i in range(1, 10)]


def konto(kid, name, typ, eltern=None, platzhalter=False):
    slots = ("<act:slots><slot><slot:key>placeholder</slot:key><slot:value type=\"string\">true</slot:value></slot></act:slots>"
             if platzhalter else "")
    obj = f'<act:parent type="guid">{eltern}</act:parent>' if eltern else ""
    wa = "<act:commodity><cmdty:space>CURRENCY</cmdty:space><cmdty:id>EUR</cmdty:id></act:commodity>" if typ != "ROOT" else ""
    return (f'<gnc:account version="2.0.0"><act:name>{name}</act:name><act:id type="guid">{kid}</act:id>'
            f"<act:type>{typ}</act:type>{wa}{slots}{obj}</gnc:account>\n")


def testbuch():
    konten = (konto(ROOT, "Root Account", "ROOT") + konto(AKTIVA, "Aktiva", "ASSET", ROOT, True)
              + konto(GIRO, "Girokonto", "BANK", AKTIVA) + konto(DEPOT, "GEHEIMDEPOT", "ASSET", AKTIVA)
              + konto(AUSG, "Ausgaben", "EXPENSE", ROOT, True) + konto(LEB, "Lebensmittel", "EXPENSE", AUSG)
              + konto(EIN, "Einnahmen", "INCOME", ROOT, True) + konto(BAF, "GEHEIMEINKOMMEN", "INCOME", EIN)
              + konto(EK, "Eroeffnung", "EQUITY", ROOT))
    start = buch.buchung_xml("a" * 32, date(2026, 10, 1), "GEHEIMSTART",
                             [(GIRO, 987654, ""), (DEPOT, 123456, ""), (EK, -1111110, "")])
    return (f'<?xml version="1.0" encoding="utf-8" ?>\n<gnc-v2 {NS}>\n<gnc:count-data cd:type="book">1</gnc:count-data>\n'
            f'<gnc:book version="2.0.0">\n<book:id type="guid">{"b" * 32}</book:id>\n'
            f'<gnc:count-data cd:type="account">9</gnc:count-data>\n<gnc:count-data cd:type="transaction">1</gnc:count-data>\n'
            f"{konten}{start}</gnc:book></gnc-v2>\n")


def starten(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def anfrage(url, daten=None, methode=None):
    roh = None if daten is None else json.dumps(daten).encode()
    req = urllib.request.Request(url, data=roh, method=methode)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as ex:
        with ex:
            return ex.code, ex.read().decode()


class Basis(unittest.TestCase):
    def setUp(self):
        for f in TMP.iterdir():
            shutil.rmtree(f) if f.is_dir() else f.unlink()
        buch.importieren(testbuch().encode())


class Buchhaltung(Basis):
    def test_ausgabe_mit_splits_geht_auf_und_zaehlt(self):
        buch.buchen("ausgabe", "2026-11-03", "HIT & Co", GIRO, [{"konto": LEB, "betrag": 1050, "memo": "A<B"}])
        konten, buchungen = buch.lesen()
        self.assertEqual(len(buchungen), 2)
        text = buch.BUCH.read_text()
        ET.fromstring(text)
        self.assertIn('cd:type="transaction">2<', text)
        self.assertEqual(budget.stand()["vermoegen"], 1111110 - 1050)

    def test_einnahme_und_umbuchung_vorzeichen(self):
        buch.buchen("einnahme", "2026-10-30", "BAföG", GIRO, [{"konto": BAF, "betrag": 85500}])
        buch.buchen("umbuchung", "2026-10-31", "Sparen", GIRO, [{"konto": DEPOT, "betrag": 5000}])
        m = budget.monat("2026-10")
        self.assertEqual(m["einnahmen"], 85500)
        self.assertEqual(m["ausgaben"], 0)
        salden = {k["pfad"]: k["saldo"] for k in budget.stand()["konten"]}
        self.assertEqual(salden["Aktiva:Girokonto"], 987654 + 85500 - 5000)
        self.assertEqual(salden["Aktiva:GEHEIMDEPOT"], 123456 + 5000)

    def test_falsche_konten_abgelehnt(self):
        for art, k, p in (("ausgabe", GIRO, BAF), ("einnahme", GIRO, LEB), ("ausgabe", GIRO, AUSG), ("umbuchung", GIRO, GIRO)):
            with self.assertRaises(buch.BuchFehler):
                buch.buchen(art, "2026-11-01", "x", k, [{"konto": p, "betrag": 100}])

    def test_aendern_und_loeschen(self):
        kid = buch.buchen("ausgabe", "2026-11-03", "alt", GIRO, [{"konto": LEB, "betrag": 100}])
        neu = buch.buchen("ausgabe", "2026-11-03", "neu", GIRO, [{"konto": LEB, "betrag": 200}], ersetzt=kid)
        _, b = buch.lesen()
        self.assertEqual([x["beschreibung"] for x in b if x["id"] != "a" * 32], ["neu"])
        buch.loeschen(neu)
        self.assertIn('cd:type="transaction">1<', buch.BUCH.read_text())
        self.assertTrue(list(buch.VERSIONEN.iterdir()))

    def test_import_gepackt_und_kaputt(self):
        self.assertEqual(buch.importieren(gzip.compress(testbuch().encode()))[1], 1)
        with self.assertRaises(buch.BuchFehler):
            buch.importieren(testbuch().replace("-1111110/100", "-1/100").encode())

    def test_betraege(self):
        self.assertEqual(buch.cent("12,50"), 1250)
        self.assertEqual(buch.cent("1.234,50"), 123450)
        self.assertEqual(buch.cent(12.5), 1250)
        for falsch in ("1,234", "abc", "NaN"):
            with self.assertRaises(buch.BuchFehler):
                buch.cent(falsch)

    def test_zeitumstellung(self):
        self.assertTrue(buch.zeitstempel(date(2026, 10, 24)).endswith("+0200"))
        self.assertTrue(buch.zeitstempel(date(2026, 10, 25)).endswith("+0100"))
        self.assertEqual(buch.lokales_datum("2026-10-06 22:30:00 +0000"), date(2026, 10, 7))


class Wiederkehrend(Basis):
    def eintrag(self, **mehr):
        return budget.wiederkehrend_pruefen({"art": "ausgabe", "name": "Miete", "betrag": "200,00", "konto": GIRO, "gegenkonto": LEB,
                                             "tag": 31, "alle_monate": 1, "naechste": "2026-08-31", **mehr})

    def test_nachholen_ohne_doppelte_und_monatsende(self):
        buch.json_speichern(ausgaben.WIEDERKEHREND, [self.eintrag()])
        self.assertEqual(budget.faellige_buchen(date(2026, 10, 31)), 3)
        self.assertEqual(budget.faellige_buchen(date(2026, 10, 31)), 0)
        daten = [z["datum"] for z in buch.ausgabezeilen()]
        self.assertEqual(daten, ["2026-08-31", "2026-09-30", "2026-10-31"])
        self.assertEqual(buch.json_laden(ausgaben.WIEDERKEHREND, [])[0]["naechste"], "2026-11-30")

    def test_absturz_zwischen_buch_und_zaehler(self):
        w = self.eintrag(naechste="2026-10-31")
        buch.json_speichern(ausgaben.WIEDERKEHREND, [w])
        budget.faellige_buchen(date(2026, 10, 31))
        buch.json_speichern(ausgaben.WIEDERKEHREND, [w])
        self.assertEqual(budget.faellige_buchen(date(2026, 10, 31)), 0)

    def test_pause_und_ende(self):
        buch.json_speichern(ausgaben.WIEDERKEHREND, [self.eintrag(aktiv=False), self.eintrag(ende="2026-08-31")])
        self.assertEqual(budget.faellige_buchen(date(2026, 12, 31)), 1)


class Schnittstelle(Basis):
    def setUp(self):
        super().setUp()
        self.url = starten(ausgaben.Handler)

    def rufe(self, name, **argumente):
        code, roh = anfrage(f"{self.url}/werkzeug/{name}", argumente)
        self.assertEqual(code, 200)
        return json.loads(roh), roh

    def bon(self, **mehr):
        return {"datum": "2026-11-03", "beschreibung": "Aldi", "zahlkonto": "Aktiva:Girokonto",
                "positionen": [{"kategorie": "Ausgaben:Lebensmittel", "betrag": "3,00"}], **mehr}

    def test_vorschlag_erst_nach_ok_und_nicht_doppelt(self):
        v, _ = self.rufe("ausgabe_vorschlagen", **self.bon(bonsumme="3,00"))
        self.assertTrue(v["ok"])
        self.assertEqual(buch.ausgabezeilen(), [])
        self.rufe("ausgabe_bestaetigen", vorschlag_id=v["vorschlag_id"])
        self.rufe("ausgabe_bestaetigen", vorschlag_id=v["vorschlag_id"])
        self.assertEqual(len(buch.ausgabezeilen()), 1)
        v2, _ = self.rufe("ausgabe_vorschlagen", **self.bon(datum="2026-11-04"))
        self.assertEqual(len(v2["duplikate"]), 1)
        self.assertFalse(self.rufe("ausgabe_bestaetigen", vorschlag_id=v2["vorschlag_id"])[0]["ok"])
        self.assertTrue(self.rufe("ausgabe_bestaetigen", vorschlag_id=v2["vorschlag_id"], trotz_duplikat=True)[0]["ok"])
        self.assertEqual(len(buch.ausgabezeilen()), 2)

    def test_positionen_als_json_text_wie_aus_dem_skript(self):
        text = json.dumps([{"kategorie": "Ausgaben:Lebensmittel", "betrag": "2,50", "memo": "Äpfel"},
                           {"kategorie": "Ausgaben:Lebensmittel", "betrag": "0,50"}])
        v, _ = self.rufe("ausgabe_vorschlagen", **self.bon(positionen=text, bonsumme=3.0))
        self.assertEqual(v["gesamt"], 3.0)

    def test_falsche_eingaben_abgelehnt(self):
        self.assertFalse(self.rufe("ausgabe_vorschlagen", **self.bon(bonsumme="9,99"))[0]["ok"])
        self.assertFalse(self.rufe("ausgabe_vorschlagen", **self.bon(positionen="kein json"))[0]["ok"])
        for mehr in ({"positionen": [{"kategorie": "Einnahmen:GEHEIMEINKOMMEN", "betrag": "3"}]},
                     {"zahlkonto": "Aktiva:GEHEIMDEPOT"},
                     {"positionen": [{"kategorie": "Ausgaben:Lebensmittel", "betrag": "-3,00"}]},
                     {"datum": "2099-01-01"}):
            self.assertFalse(self.rufe("ausgabe_vorschlagen", **self.bon(**mehr))[0]["ok"], mehr)

    def test_keine_saldo_oder_einnahmedaten_nach_aussen(self):
        buch.buchen("einnahme", "2026-11-01", "GEHEIMEINGANG", GIRO, [{"konto": BAF, "betrag": 85577, "memo": "GEHEIMMEMO"}])
        buch.buchen("umbuchung", "2026-11-01", "GEHEIMSPARPLAN", GIRO, [{"konto": DEPOT, "betrag": 5011}])
        buch.buchen("ausgabe", "2026-11-02", "Erlaubt", GIRO, [{"konto": LEB, "betrag": 300}])
        buch.json_speichern(ausgaben.WIEDERKEHREND, [
            {"id": "x", "art": "einnahme", "name": "GEHEIMDAUER", "betrag": 85599, "konto": GIRO, "gegenkonto": BAF, "tag": 1, "alle_monate": 1, "naechste": "2030-01-01", "ende": "", "aktiv": True},
            {"id": "y", "art": "ausgabe", "name": "Abo", "betrag": 1700, "konto": GIRO, "gegenkonto": LEB, "tag": 1, "alle_monate": 1, "naechste": "2030-01-01", "ende": "", "aktiv": True}])
        gesamt = ""
        for name, a in (("kategorien_und_zahlkonten", {}), ("ausgaben_lesen", {}), ("monatsauswertung", {"monat": "2026-11"}),
                        ("wiederkehrende_ausgaben", {}), ("ausgabe_vorschlagen", self.bon(beschreibung="B"))):
            gesamt += self.rufe(name, **a)[1]
        self.assertIn("Erlaubt", gesamt)
        self.assertIn("Abo", gesamt)
        for geheim in ("GEHEIM", "9876", "1234", "1111", "855", "50.11", "saldo", "vermoegen"):
            self.assertNotIn(geheim, gesamt, geheim)
        # Auch Einnahmen und Umbuchungen lassen sich nicht stornieren.
        _, buchungen = buch.lesen()
        for b in buchungen:
            if b["beschreibung"].startswith("GEHEIM"):
                self.assertFalse(self.rufe("ausgabe_stornieren", id=b["id"])[0]["ok"])

    def test_panel_wege_gibt_es_hier_nicht(self):
        for pfad in ("/werkzeug/stand", "/api/stand", "/api/export", "/"):
            self.assertEqual(anfrage(f"{self.url}{pfad}", {})[0], 404, pfad)
            self.assertEqual(anfrage(f"{self.url}{pfad}")[0], 501, pfad)

    def test_fremde_adresse_abgewiesen(self):
        alt = ausgaben.ERLAUBT
        ausgaben.ERLAUBT = {"172.30.32.1"}
        try:
            self.assertEqual(anfrage(f"{self.url}/werkzeug/ausgaben_lesen", {})[0], 403)
        finally:
            ausgaben.ERLAUBT = alt


class Panel(Basis):
    def test_api_ablauf(self):
        url = starten(budget.Handler)
        code, roh = anfrage(f"{url}/api/buchungen", {"art": "ausgabe", "datum": "2026-11-03", "beschreibung": "Bäcker", "konto": GIRO,
                                                         "positionen": [{"konto": LEB, "betrag": "2,40"}]})
        self.assertEqual(code, 200)
        kid = json.loads(roh)["id"]
        m = json.loads(anfrage(f"{url}/api/monat?m=2026-11")[1])
        self.assertEqual(m["ausgaben"], 240)
        self.assertTrue(m["buchungen"][0]["bearbeitbar"])
        self.assertEqual(anfrage(f"{url}/api/buchungen/{kid}", methode="DELETE")[0], 200)
        self.assertEqual(anfrage(f"{url}/api/buchungen", {"art": "ausgabe", "beschreibung": "x"})[0], 400)
        self.assertIn("Budget", anfrage(f"{url}/")[1])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1, exit=False)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
