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
import depot  # noqa: E402
import ausgaben  # noqa: E402
import bank  # noqa: E402

NS = ('xmlns:gnc="http://www.gnucash.org/XML/gnc" xmlns:act="http://www.gnucash.org/XML/act" '
      'xmlns:book="http://www.gnucash.org/XML/book" xmlns:cd="http://www.gnucash.org/XML/cd" '
      'xmlns:cmdty="http://www.gnucash.org/XML/cmdty" xmlns:slot="http://www.gnucash.org/XML/slot" '
      'xmlns:split="http://www.gnucash.org/XML/split" xmlns:trn="http://www.gnucash.org/XML/trn" '
      'xmlns:ts="http://www.gnucash.org/XML/ts"')
ROOT, AKTIVA, GIRO, DEPOT, AUSG, LEB, EIN, BAF, EK, DEPOTORDNER, VERR = [f"{i:032x}" for i in range(1, 12)]


def konto(kid, name, typ, eltern=None, platzhalter=False):
    slots = ("<act:slots><slot><slot:key>placeholder</slot:key><slot:value type=\"string\">true</slot:value></slot></act:slots>"
             if platzhalter else "")
    obj = f'<act:parent type="guid">{eltern}</act:parent>' if eltern else ""
    wa = "<act:commodity><cmdty:space>CURRENCY</cmdty:space><cmdty:id>EUR</cmdty:id></act:commodity>" if typ != "ROOT" else ""
    return (f'<gnc:account version="2.0.0"><act:name>{name}</act:name><act:id type="guid">{kid}</act:id>'
            f"<act:type>{typ}</act:type>{wa}{slots}{obj}</gnc:account>\n")


def testbuch():
    konten = (konto(ROOT, "Root Account", "ROOT") + konto(AKTIVA, "Aktiva", "ASSET", ROOT, True)
              + konto(GIRO, "Girokonto", "BANK", AKTIVA) + konto(VERR, "Verrechnungskonto", "BANK", AKTIVA) + konto(DEPOTORDNER, "Depot", "ASSET", AKTIVA, True) + konto(DEPOT, "GEHEIMDEPOT", "ASSET", DEPOTORDNER)
              + konto(AUSG, "Ausgaben", "EXPENSE", ROOT, True) + konto(LEB, "Lebensmittel", "EXPENSE", AUSG)
              + konto(EIN, "Einnahmen", "INCOME", ROOT, True) + konto(BAF, "GEHEIMEINKOMMEN", "INCOME", EIN)
              + konto(EK, "Eroeffnung", "EQUITY", ROOT))
    start = buch.buchung_xml("a" * 32, date(2026, 10, 1), "GEHEIMSTART",
                             [(GIRO, 987654, ""), (DEPOT, 123456, ""), (EK, -1111110, "")])
    return (f'<?xml version="1.0" encoding="utf-8" ?>\n<gnc-v2 {NS}>\n<gnc:count-data cd:type="book">1</gnc:count-data>\n'
            f'<gnc:book version="2.0.0">\n<book:id type="guid">{"b" * 32}</book:id>\n'
            f'<gnc:count-data cd:type="account">10</gnc:count-data>\n<gnc:count-data cd:type="transaction">1</gnc:count-data>\n'
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
        self.assertEqual(salden["Aktiva:Depot:GEHEIMDEPOT"], 123456 + 5000)

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

    def test_einnahme_buchen_nach_ok(self):
        e = {"datum": "2026-11-03", "beschreibung": "Lohn", "zahlkonto": "Aktiva:Girokonto",
             "kategorie": "Einnahmen:GEHEIMEINKOMMEN", "betrag": "300,00"}
        v, _ = self.rufe("einnahme_vorschlagen", **e)
        self.assertEqual((v["art"], v["gesamt"], v["duplikate"]), ("einnahme", 300.0, []))
        self.assertEqual(budget.monat("2026-11")["einnahmen"], 0)
        self.rufe("ausgabe_bestaetigen", vorschlag_id=v["vorschlag_id"])
        self.assertEqual(budget.monat("2026-11")["einnahmen"], 30000)
        self.assertEqual(buch.ausgabezeilen(), [])
        v2, _ = self.rufe("einnahme_vorschlagen", **e)
        self.assertEqual(len(v2["duplikate"]), 1)
        self.assertFalse(self.rufe("ausgabe_bestaetigen", vorschlag_id=v2["vorschlag_id"])[0]["ok"])
        # Kurze Namen und andere Schreibweise gehen auch.
        for kurzname in ("GEHEIMEINKOMMEN", "einnahmen:geheimeinkommen"):
            self.assertTrue(self.rufe("einnahme_vorschlagen", **{**e, "kategorie": kurzname, "datum": "2026-10-20"})[0]["ok"], kurzname)
        # Über ausgabe_vorschlagen mit Einnahmekategorie wird es eine Einnahme (falls das Handy das neue Skript nicht kennt).
        f = self.rufe("ausgabe_vorschlagen", **self.bon(positionen=[{"kategorie": "GEHEIMEINKOMMEN", "betrag": "3"}]))[0]
        self.assertEqual((f["ok"], f["art"]), (True, "einnahme"))
        for mehr in ({"kategorie": "Ausgaben:Lebensmittel"}, {"betrag": "-5"}, {"zahlkonto": "Aktiva:Depot:GEHEIMDEPOT"}):
            self.assertFalse(self.rufe("einnahme_vorschlagen", **{**e, **mehr})[0]["ok"], mehr)
        # Gebuchte Einnahmen bleiben unsichtbar.
        self.assertNotIn("Lohn", self.rufe("ausgaben_lesen")[1] + self.rufe("monatsauswertung", monat="2026-11")[1])

    def test_umbuchung_nach_ok(self):
        u = {"datum": "2026-11-03", "beschreibung": "Aufs Verrechnungskonto", "von": "Girokonto", "auf": "Verrechnungskonto", "betrag": "50"}
        v, roh = self.rufe("umbuchung_vorschlagen", **u)
        self.assertEqual((v["art"], v["gesamt"], v["zahlkonto"]), ("umbuchung", 50.0, "Aktiva:Girokonto"))
        self.rufe("ausgabe_bestaetigen", vorschlag_id=v["vorschlag_id"])
        _, buchungen = buch.lesen()
        b = next(x for x in buchungen if x["beschreibung"] == "Aufs Verrechnungskonto")
        self.assertEqual(sorted((s["konto"], s["betrag"]) for s in b["splits"]), sorted([(GIRO, -5000), (VERR, 5000)]))
        self.assertEqual(buch.ausgabezeilen(), [])
        self.assertEqual(len(self.rufe("umbuchung_vorschlagen", **u)[0]["duplikate"]), 1)
        # Auch über ausgabe_vorschlagen mit einem Zahlkonto als Kategorie.
        f = self.rufe("ausgabe_vorschlagen", **self.bon(positionen=[{"kategorie": "Verrechnungskonto", "betrag": "7"}]))[0]
        self.assertEqual((f["ok"], f["art"]), (True, "umbuchung"))
        for mehr in ({"auf": "Girokonto"}, {"auf": "Aktiva:Depot:GEHEIMDEPOT"}, {"betrag": "-5"}, {"auf": "Lebensmittel"}):
            self.assertFalse(self.rufe("umbuchung_vorschlagen", **{**u, **mehr})[0]["ok"], mehr)

    def test_positionen_als_json_text_wie_aus_dem_skript(self):
        text = json.dumps([{"kategorie": "Ausgaben:Lebensmittel", "betrag": "2,50", "memo": "Äpfel"},
                           {"kategorie": "Ausgaben:Lebensmittel", "betrag": "0,50"}])
        v, _ = self.rufe("ausgabe_vorschlagen", **self.bon(positionen=text, bonsumme=3.0))
        self.assertEqual(v["gesamt"], 3.0)

    def test_falsche_eingaben_abgelehnt(self):
        self.assertFalse(self.rufe("ausgabe_vorschlagen", **self.bon(bonsumme="9,99"))[0]["ok"])
        self.assertFalse(self.rufe("ausgabe_vorschlagen", **self.bon(positionen="kein json"))[0]["ok"])
        # Gemischt aus Einnahme und Ausgabe geht nicht.
        for mehr in ({"positionen": [{"kategorie": "Einnahmen:GEHEIMEINKOMMEN", "betrag": "3"}, {"kategorie": "Lebensmittel", "betrag": "1"}]},
                     {"zahlkonto": "Aktiva:Depot:GEHEIMDEPOT"},
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
        k = self.rufe("kategorien_und_zahlkonten")[0]
        self.assertEqual(k["einnahmekategorien"], ["Einnahmen:GEHEIMEINKOMMEN"])
        for geheim in ("9876", "1234", "1111", "855", "50.11", "saldo", "vermoegen", "GEHEIMDEPOT"):
            self.assertNotIn(geheim, json.dumps(k), geheim)
        gesamt = ""
        for name, a in (("ausgaben_lesen", {}), ("monatsauswertung", {"monat": "2026-11"}),
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

    def test_monats_und_wochenbericht(self):
        for kennung in ("2026-10", "2026-W45"):
            self.assertTrue(self.rufe("monatsbericht_speichern", monat=kennung, text="Bericht")[0]["ok"], kennung)
        self.rufe("monatsbericht_speichern", monat="2026-W46", text="Neu")
        self.assertEqual(sorted(buch.json_laden(ausgaben.BERICHTE, {})), ["2026-10", "2026-W46"])
        self.rufe("monatsbericht_speichern", monat="2026-10", text="B", vorschlaege='[{"titel": "Abo", "betrag": "17,00"}]')
        self.assertTrue(self.rufe("monatsbericht_speichern", monat="2026-10", text="B", vorschlaege='[{"titel": "Abo", "betrag": "17,00", "kategorie": "Lebensmittel"}]')[0]["ok"])
        self.assertEqual(buch.json_laden(ausgaben.BERICHTE, {})["2026-10"]["vorschlaege"][0]["kategorie"], "Ausgaben:Lebensmittel")
        self.assertFalse(self.rufe("monatsbericht_speichern", monat="2026-10", text="B", vorschlaege='[{"titel": "Abo", "betrag": "17,00", "kategorie": "Gibtsnicht"}]')[0]["ok"])
        self.rufe("monatsbericht_speichern", monat="2026-10", text="B", vorschlaege='[{"titel": "Abo", "betrag": "17,00"}]')
        self.assertEqual(buch.json_laden(ausgaben.BERICHTE, {})["2026-10"]["vorschlaege"], [{"titel": "Abo", "betrag": 1700}])
        for falsch in ("2026-13", "2026-W54", "2026-W00", "letzte Woche"):
            self.assertFalse(self.rufe("monatsbericht_speichern", monat=falsch, text="Bericht")[0]["ok"], falsch)

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


class Depot(Basis):
    def setUp(self):
        super().setUp()
        self.kurs = 12.0
        self.abrufe = []
        self.alt = depot.kurs_holen

        def holen(isin):
            self.abrufe.append(isin)
            return {"kurs": self.kurs, "vortag": 10.0, "quelle": "Test", "zeit": 1.0}
        depot.kurs_holen = holen
        depot.eintragen(DEPOT, {"isin": "IE00B4L5Y983", "stueck": "10,5", "kaufkurs": "10,00"})

    def tearDown(self):
        depot.kurs_holen = self.alt

    def test_gewinn_zum_kaufpreis(self):
        d = depot.uebersicht()
        p = d["positionen"][0]
        self.assertEqual((p["einstand"], p["wert"], p["gewinn"]), (10500, 12600, 2100))
        self.assertAlmostEqual(p["prozent"], 20.0)
        self.assertAlmostEqual(p["heute"], 20.0)
        self.assertEqual(d["gewinn"], 2100)

    def test_kurs_nur_einmal_pro_minute_und_letzter_bleibt(self):
        depot.uebersicht()
        depot.uebersicht()
        self.assertEqual(len(self.abrufe), 1)
        kurse = buch.json_laden(depot.KURSE, {})
        kurse["IE00B4L5Y983"]["abgerufen"] = 0
        buch.json_speichern(depot.KURSE, kurse)
        depot.kurs_holen = lambda isin: None
        self.assertEqual(depot.uebersicht()["positionen"][0]["kurs"], 12.0)

    def test_buchungen_werden_einmal_nachgezogen(self):
        morgen = date.fromordinal(date.today().toordinal() + 1)
        buch.buchen("umbuchung", morgen, "Sparplan", GIRO, [{"konto": DEPOT, "betrag": 6000}])
        depot.uebersicht()
        p = depot.uebersicht()["positionen"][0]
        self.assertEqual(p["einstand"], 16500)
        self.assertAlmostEqual(p["stueck"], 15.5)
        self.assertTrue(p["geschaetzt"])
        buch.buchen("umbuchung", morgen, "Verkauf", DEPOT, [{"konto": GIRO, "betrag": 9300}])
        p = depot.uebersicht()["positionen"][0]
        self.assertAlmostEqual(p["stueck"], 7.75)
        self.assertEqual(p["einstand"], 8250)

    def test_vermoegen_rechnet_mit_aktuellem_wert(self):
        depot.uebersicht()
        s = budget.stand()
        self.assertEqual(s["vermoegen"], 987654 + 12600)
        self.assertEqual(buch.lesen()[1][0]["splits"][1]["betrag"], 123456)

    def test_geldkurs_zuerst_sonst_mittelkurs(self):
        depot.kurs_holen = self.alt
        echt = depot.json_holen
        ls = [{"isin": "AT000000STR1", "instrumentId": 7}]
        chart = {"info": {"plotlines": [{"id": "previousDay", "value": 100.0}]},
                 "series": {"intraday": {"data": [[1793809140000, 103.2]]}}}

        def tradegate_ok(url):
            return {"bid": "1.102,80", "ask": "1.103,20", "last": "1.103,00", "close": 1101.5}
        depot.json_holen = tradegate_ok
        try:
            k = depot.kurs_holen("AT000000STR1")
            self.assertEqual((k["kurs"], k["vortag"], k["quelle"]), (1102.8, 1101.5, "Geldkurs Tradegate"))

            def ohne_tradegate(url):
                if "tradegate" in url:
                    return {"bid": 0, "close": 1}
                return ls if "search" in url else chart
            depot.json_holen = ohne_tradegate
            k = depot.kurs_holen("AT000000STR1")
            self.assertEqual((k["kurs"], k["quelle"]), (103.2, "Mittelkurs Lang & Schwarz"))
            self.assertEqual(k["zeit"], 1793809140 - 3600)

            def alles_kaputt(url):
                raise OSError("weg")
            depot.json_holen = alles_kaputt
            self.assertIsNone(depot.kurs_holen("AT000000STR1"))
        finally:
            depot.json_holen = echt

    def test_falsche_eingaben(self):
        for falsch in ({"isin": "ABC", "stueck": "1", "kaufkurs": "1"}, {"isin": "IE00B4L5Y983", "stueck": "0", "kaufkurs": "1"},
                       {"isin": "IE00B4L5Y983", "stueck": "x", "kaufkurs": "1"}, {"isin": "IE00B4L5Y983", "stueck": "1", "kaufkurs": "0"}):
            with self.assertRaises(buch.BuchFehler):
                depot.eintragen(DEPOT, falsch)
        with self.assertRaises(buch.BuchFehler):
            depot.eintragen(GIRO, {"isin": "IE00B4L5Y983", "stueck": "1", "kaufkurs": "1"})

    def test_claude_kommt_nicht_ans_depot(self):
        depot.uebersicht()
        url = starten(ausgaben.Handler)
        self.assertEqual(anfrage(f"{url}/api/depot")[0], 501)
        gesamt = ""
        for name in ausgaben.WERKZEUGE:
            if name in ("kategorien_und_zahlkonten", "ausgaben_lesen", "wiederkehrende_ausgaben"):
                gesamt += anfrage(f"{url}/werkzeug/{name}", {})[1]
        for geheim in ("IE00B4L5Y983", "12600", "GEHEIMDEPOT"):
            self.assertNotIn(geheim, gesamt)
        self.assertNotIn("depot", Path(ausgaben.__file__).read_text().replace("Depot", ""))


class Panel(Basis):
    def test_bericht_zahlen(self):
        buch.json_speichern(ausgaben.WIEDERKEHREND, [
            {"id": "y", "art": "ausgabe", "name": "Miete", "betrag": 20000, "konto": GIRO, "gegenkonto": LEB, "tag": 1, "alle_monate": 1, "naechste": "2030-01-01", "ende": "", "aktiv": True}])
        buch.buchen("ausgabe", "2026-09-30", "Davor", GIRO, [{"konto": LEB, "betrag": 500}])
        buch.buchen("ausgabe", "2026-10-02", "Miete", GIRO, [{"konto": LEB, "betrag": 20000}])
        buch.buchen("ausgabe", "2026-10-03", "Uhr Kauf", GIRO, [{"konto": LEB, "betrag": 18000}])
        buch.buchen("ausgabe", "2026-10-31", "Aldi Süd Weg (x)", GIRO, [{"konto": LEB, "betrag": 700}])
        buch.buchen("ausgabe", "2026-10-31", "Hopfen & Malz", GIRO, [{"konto": LEB, "betrag": 100}])
        buch.buchen("einnahme", "2026-10-30", "BAföG", GIRO, [{"konto": BAF, "betrag": 85500}])
        buch.json_speichern(ausgaben.BERICHTE, {"2026-10": {"text": "Kurz", "vorschlaege": [{"titel": "A", "betrag": 100}], "erstellt": "x"}})
        d = budget.bericht("2026-10")
        self.assertEqual((d["gesamt"], d["fix"], d["gross"], d["alltag"], d["vergleich"]), (38800, 20000, 18000, 800, 500))
        self.assertEqual(d["einnahmen"], 85500)
        self.assertEqual([q["betrag"] for q in d["einnahmequellen"]], [85500])
        self.assertEqual(len(d["verlauf"]), 31)
        self.assertEqual(d["verlauf"][30], 800)
        self.assertEqual(len(d["vergleich_verlauf"]), 30)
        k = {a["name"]: a for a in d["kuchen"]}
        self.assertEqual({n: a["betrag"] for n, a in k.items()}, {"Fest": 20000, "Alltag": 800, "Große Posten": 18000})
        self.assertEqual([h["name"] for h in k["Alltag"]["kategorien"][0]["haendler"]], ["Aldi Süd", "Weitere"])
        self.assertEqual(budget.kuchen_bauen({"A": {"K": {"Rest": -100, "X": 500}}}, 400)[0]["betrag"], 400)
        self.assertEqual(budget.kuchen_bauen({"A": {"K": {"Erstattung": -100, "X": 100}}}, 0), [])
        self.assertEqual(budget.stand()["vorschlaege"][0]["titel"], "A")
        self.assertEqual(d["trend"][-1]["ausgaben"], 38800)
        self.assertEqual(d["vorschlaege"][0]["titel"], "A")
        g = budget.bericht("gesamt")
        monate = budget.messmonate(*buch.lesen())
        self.assertEqual(monate[:2], ["2026-09", "2026-10"])
        self.assertEqual([t["titel"] for t in g["trend"]], monate)
        self.assertEqual((g["gesamt"], g["einnahmen"], g["vergleich"], g["verlauf"]), (38800 + 500, 85500, 0, []))
        self.assertEqual({k: g["trend"][1][k] for k in ("Fest", "Alltag", "Große Posten")}, {"Fest": 20000, "Alltag": 800, "Große Posten": 18000})
        buch.buchen("umbuchung", "2026-10-15", "Kauf", GIRO, [{"konto": DEPOT, "betrag": 5000}])
        self.assertEqual(budget.bericht("2026-10")["ins_depot"], 5000)
        self.assertEqual(budget.stand()["erspartes"], 123456 + 5000)
        self.assertEqual((g["vorschlaege"][0]["titel"], g["vorschlaege_aus"], g["text"]), ("A", "2026-10", ""))
        self.assertEqual([q["betrag"] for q in g["einnahmequellen"]], [85500])
        with self.assertRaises(buch.BuchFehler):
            budget.bericht("quatsch")

    def test_kategorien_mit_durchschnitt_der_messzeit(self):
        buch.buchen("ausgabe", "2026-08-05", "Aldi", GIRO, [{"konto": LEB, "betrag": 3000}])
        buch.buchen("ausgabe", "2026-10-05", "Aldi", GIRO, [{"konto": LEB, "betrag": 6000}])
        buch.buchen("ausgabe", "2026-10-06", "Aldi", GIRO, [{"konto": LEB, "betrag": 1000}])
        laufend = date.today().strftime("%Y-%m")
        buch.buchen("ausgabe", f"{laufend}-01", "Aldi", GIRO, [{"konto": LEB, "betrag": 99900}])
        m = budget.monat("2026-10")
        # August, September (leer) zählen mit, Oktober (gezeigt) und der laufende Monat nicht.
        self.assertEqual(m["schnitt_monate"][:2], ["2026-08", "2026-09"])
        self.assertNotIn("2026-10", m["schnitt_monate"])
        self.assertNotIn(laufend, m["schnitt_monate"])
        n = len(m["schnitt_monate"])
        self.assertEqual(m["kategorien"][0]["schnitt"], round(3000 / n))
        self.assertEqual(m["kategorien"][0]["betrag"], 7000)
        g = budget.monat("gesamt")
        self.assertEqual((g["ausgaben"], g["von"], g["buchungen"]), (3000 + 7000 + 99900, "2026-08", []))
        self.assertEqual(g["kategorien"][0]["schnitt"], round(10000 / len(g["schnitt_monate"])))
        aktuell = budget.monat(laufend)
        self.assertEqual(aktuell["kategorien"][0]["schnitt"], round(10000 / len(aktuell["schnitt_monate"])))

    def test_bericht_laufender_monat_endet_heute(self):
        heute = date.today()
        d = budget.bericht(heute.strftime("%Y-%m"))
        self.assertEqual(len(d["verlauf"]), heute.day)

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


class Bankumsaetze(Basis):
    """Umsätze aus der Chromium-Erweiterung: ablegen, einordnen, buchen; nach außen nur die Anzahl."""

    def umsaetze(self):
        return [{"datum": "2026-11-03", "betrag": "-12.50", "name": "REWE Markt", "zweck": "Karte"},
                {"datum": "2026-11-03", "betrag": "-12.50", "name": "REWE Markt", "zweck": "Karte"},
                {"datum": "2026-11-04", "betrag": 520, "name": "GEHEIMAMT", "zweck": "Foerderung"},
                {"datum": "2026-11-05", "betrag": "-100", "name": "Trade Republic", "zweck": "Sparen"},
                {"datum": "2026-11-06", "betrag": -3, "name": "Bäcker", "zweck": ""}]

    def test_einreichen_einordnen_und_buchen(self):
        buch.buchen("ausgabe", "2026-10-20", "REWE Markt", GIRO, [{"konto": LEB, "betrag": 800}])
        buch.buchen("ausgabe", "2026-11-06", "Bäcker vom Bon", GIRO, [{"konto": LEB, "betrag": 300}])
        bank.einstellungen_speichern({"konten": {"gls": GIRO}})
        url = starten(ausgaben.Handler)
        r, roh = json.loads(anfrage(f"{url}/werkzeug/bank_einreichen", {"bank": "gls", "umsaetze": self.umsaetze()})[1]), None
        self.assertEqual(r, {"ok": True, "neu": 5})
        roh = anfrage(f"{url}/werkzeug/bank_einreichen", {"bank": "gls", "umsaetze": self.umsaetze()})[1]
        self.assertEqual(json.loads(roh), {"ok": True, "neu": 0})  # zweimal geschickt, nichts doppelt
        self.assertFalse(json.loads(anfrage(f"{url}/werkzeug/bank_einreichen", {"bank": "x", "umsaetze": []})[1])["ok"])
        u = bank.offene()
        self.assertEqual([x["betrag"] for x in u], [-1250, -1250, 52000, -10000, -300])
        self.assertNotEqual(u[0]["id"], u[1]["id"])
        self.assertEqual((u[0]["vorschlag"], u[1]["vorschlag"]), (LEB, LEB))
        self.assertEqual(u[4]["duplikat"]["beschreibung"], "Bäcker vom Bon")
        bank.umsatz_buchen(u[0]["id"], LEB)
        bank.umsatz_buchen(u[2]["id"], BAF)
        bank.umsatz_buchen(u[3]["id"], VERR)
        bank.umsatz_ignorieren(u[4]["id"])
        with self.assertRaises(buch.BuchFehler):
            bank.umsatz_buchen(u[0]["id"], LEB)
        _, buchungen = buch.lesen()
        salden = {}
        for b in buchungen:
            for s in b["splits"]:
                salden[s["konto"]] = salden.get(s["konto"], 0) + s["betrag"]
        self.assertEqual((salden[VERR], salden[LEB], salden[BAF]), (10000, 800 + 300 + 1250, -52000))
        self.assertEqual(len(bank.offene()), 1)
        # Für Claude bleiben Einnahmen unsichtbar.
        self.assertNotIn("GEHEIMAMT", anfrage(f"{url}/werkzeug/ausgaben_lesen", {})[1])

    def test_ordergebuehr(self):
        def salden():
            werte = {}
            for b in buch.lesen()[1]:
                for sp in b["splits"]:
                    werte[sp["konto"]] = werte.get(sp["konto"], 0) + sp["betrag"]
            return werte
        url = starten(budget.Handler)
        kauf = {"art": "umbuchung", "datum": "2026-11-03", "beschreibung": "Kauf Apple", "konto": VERR,
                "positionen": [{"konto": DEPOT, "betrag": "100,00"}], "gebuehr": "1,00"}
        vorher = salden()
        code, roh = anfrage(f"{url}/api/buchungen", kauf)
        self.assertEqual(code, 400, roh)  # noch keine Gebühren-Kategorie: gar nichts buchen
        self.assertEqual(salden(), vorher)
        geb = buch.konto_anlegen("Ordergebühren", AUSG)
        self.assertEqual(anfrage(f"{url}/api/buchungen", kauf)[0], 200)
        jetzt = salden()
        self.assertEqual((jetzt[DEPOT] - vorher[DEPOT], jetzt[geb], jetzt[VERR] - vorher.get(VERR, 0)), (10000, 100, -10100))
        self.assertEqual(anfrage(f"{url}/api/buchungen", {**kauf, "art": "ausgabe", "positionen": [{"konto": LEB, "betrag": "1"}]})[0], 400)
        bank.einstellungen_speichern({"konten": {"gls": GIRO}})
        bank.einreichen("gls", self.umsaetze())
        u = bank.offene()
        with self.assertRaises(buch.BuchFehler):
            bank.umsatz_buchen(u[0]["id"], LEB, gebuehr=100)
        bank.umsatz_buchen(u[0]["id"], DEPOT, gebuehr=100)
        danach = salden()
        self.assertEqual((danach[DEPOT] - jetzt[DEPOT], danach[geb] - jetzt[geb]), (1150, 100))

    def test_kennung_der_bank_und_kontowahl(self):
        mit_id = [{"id": "tr-1", "datum": "2026-11-03", "betrag": -5, "name": "A"}]
        self.assertEqual(bank.einreichen("tr", mit_id), 1)
        self.assertEqual(bank.einreichen("tr", [{**mit_id[0], "name": "A geändert"}]), 0)
        # Nach einem Abruf zählt nur noch, was höchstens ein paar Tage vor ihm liegt.
        heute = date.today()
        alt = [{"datum": (heute - __import__("datetime").timedelta(days=d)).isoformat(), "betrag": -1, "name": f"Alt {d}"} for d in (30, 6, 4, 0)]
        self.assertEqual(bank.einreichen("tr", alt), 2)
        self.assertEqual(sorted(u["name"] for u in bank.offene() if u["name"].startswith("Alt")), ["Alt 0", "Alt 4"])
        for u in bank.offene():
            if u["name"].startswith("Alt"):
                bank.umsatz_ignorieren(u["id"])
        # Ohne Wahl wird das Zahlkonto genommen, dessen Name passt; hier gibt es keins.
        self.assertEqual(bank.buchkonten()["tr"], "")
        with self.assertRaises(buch.BuchFehler):
            bank.umsatz_buchen(bank.offene()[0]["id"], LEB)
        with self.assertRaises(buch.BuchFehler):
            bank.einstellungen_speichern({"konten": {"tr": LEB}})
        bank.einstellungen_speichern({"konten": {"tr": VERR}})
        bank.umsatz_buchen(bank.offene()[0]["id"], LEB)
        for kaputt in ([{"datum": "quatsch", "betrag": 1}], [{"datum": "2026-11-03", "betrag": "1,234"}], "keine Liste"):
            with self.assertRaises((buch.BuchFehler, ValueError)):
                bank.einreichen("tr", kaputt)
        url = starten(budget.Handler)
        g = json.loads(anfrage(f"{url}/api/bank")[1])
        self.assertEqual(g["konten"]["tr"], VERR)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1, exit=False)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
