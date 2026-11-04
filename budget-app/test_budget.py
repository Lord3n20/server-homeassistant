"""Prüft Buchhaltung, Dauerbuchungen, OAuth und dass der Connector nur Ausgaben herausgibt.

Aufruf: python3 test_budget.py
"""
import base64
import gzip
import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["BUDGET_DATA"] = str(TMP)

import buch  # noqa: E402
import budget  # noqa: E402
import mcp  # noqa: E402

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


class KeinRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def anfrage(url, daten=None, kopf=None, methode=None, form=False):
    roh = None
    if daten is not None:
        roh = urllib.parse.urlencode(daten).encode() if form else json.dumps(daten).encode()
    req = urllib.request.Request(url, data=roh, headers=kopf or {}, method=methode)
    try:
        with urllib.request.build_opener(KeinRedirect).open(req) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode(), dict(ex.headers)


class Basis(unittest.TestCase):
    def setUp(self):
        for f in TMP.iterdir():
            shutil.rmtree(f) if f.is_dir() else f.unlink()
        buch.importieren(testbuch().encode())
        (TMP / "options.json").write_text(json.dumps({"oeffentliche_url": "https://budget.test", "connector_passwort": "ein-langes-passwort"}))
        mcp.fehlversuche.update(anzahl=0, bis=0.0)


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
        buch.json_speichern(mcp.WIEDERKEHREND, [self.eintrag()])
        self.assertEqual(budget.faellige_buchen(date(2026, 10, 31)), 3)
        self.assertEqual(budget.faellige_buchen(date(2026, 10, 31)), 0)
        daten = [z["datum"] for z in buch.ausgabezeilen()]
        self.assertEqual(daten, ["2026-08-31", "2026-09-30", "2026-10-31"])
        self.assertEqual(buch.json_laden(mcp.WIEDERKEHREND, [])[0]["naechste"], "2026-11-30")

    def test_absturz_zwischen_buch_und_zaehler(self):
        w = self.eintrag(naechste="2026-10-31")
        buch.json_speichern(mcp.WIEDERKEHREND, [w])
        budget.faellige_buchen(date(2026, 10, 31))
        buch.json_speichern(mcp.WIEDERKEHREND, [w])
        self.assertEqual(budget.faellige_buchen(date(2026, 10, 31)), 0)

    def test_pause_und_ende(self):
        buch.json_speichern(mcp.WIEDERKEHREND, [self.eintrag(aktiv=False), self.eintrag(ende="2026-08-31")])
        self.assertEqual(budget.faellige_buchen(date(2026, 12, 31)), 1)


class Connector(Basis):
    def setUp(self):
        super().setUp()
        self.url = starten(mcp.Handler)

    def token(self):
        verifier = "v" * 50
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        ruecksprung = "https://claude.ai/api/mcp/auth_callback"
        _, roh, _ = anfrage(f"{self.url}/register", {"redirect_uris": [ruecksprung]})
        client = json.loads(roh)["client_id"]
        p = {"client_id": client, "redirect_uri": ruecksprung, "response_type": "code", "code_challenge": challenge,
             "code_challenge_method": "S256", "state": "s1"}
        code, _, kopf = anfrage(f"{self.url}/authorize", {**p, "passwort": "ein-langes-passwort"}, form=True)
        self.assertEqual(code, 302)
        ziel = urllib.parse.urlsplit(kopf["Location"])
        q = urllib.parse.parse_qs(ziel.query)
        self.assertEqual(q["state"], ["s1"])
        code, roh, _ = anfrage(f"{self.url}/token", {"grant_type": "authorization_code", "code": q["code"][0], "client_id": client,
                                                      "redirect_uri": ruecksprung, "code_verifier": verifier}, form=True)
        self.assertEqual(code, 200)
        self.client = client
        return json.loads(roh)

    def rufe(self, token, name, **argumente):
        code, roh, _ = anfrage(f"{self.url}/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": argumente}},
                               {"Authorization": f"Bearer {token}"})
        self.assertEqual(code, 200)
        return json.loads(roh)["result"], roh

    def test_ohne_token_abgewiesen(self):
        code, _, kopf = anfrage(f"{self.url}/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(code, 401)
        self.assertIn("resource_metadata", kopf["WWW-Authenticate"])

    def test_fremde_ruecksprungadresse_abgewiesen(self):
        code, _, _ = anfrage(f"{self.url}/register", {"redirect_uris": ["https://boese.example/cb"]})
        self.assertEqual(code, 400)

    def test_falsches_passwort_sperrt(self):
        _, roh, _ = anfrage(f"{self.url}/register", {"redirect_uris": ["https://claude.ai/cb"]})
        p = {"client_id": json.loads(roh)["client_id"], "redirect_uri": "https://claude.ai/cb", "response_type": "code",
             "code_challenge": "x", "code_challenge_method": "S256"}
        for _ in range(mcp.MAX_FEHLVERSUCHE):
            self.assertEqual(anfrage(f"{self.url}/authorize", {**p, "passwort": "falsch"}, form=True)[0], 401)
        self.assertEqual(anfrage(f"{self.url}/authorize", {**p, "passwort": "ein-langes-passwort"}, form=True)[0], 429)

    def test_refresh_rotiert(self):
        t = self.token()
        daten = {"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": self.client}
        self.assertEqual(anfrage(f"{self.url}/token", daten, form=True)[0], 200)
        self.assertEqual(anfrage(f"{self.url}/token", daten, form=True)[0], 400)

    def test_vorschlag_erst_nach_ok_und_nicht_doppelt(self):
        t = self.token()["access_token"]
        r, _ = self.rufe(t, "ausgabe_vorschlagen", datum="2026-11-03", beschreibung="Aldi", zahlkonto="Aktiva:Girokonto", bonsumme="3,00",
                         positionen=[{"kategorie": "Ausgaben:Lebensmittel", "betrag": "2,50"}, {"kategorie": "Ausgaben:Lebensmittel", "betrag": "0,50"}])
        v = json.loads(r["content"][0]["text"])
        self.assertEqual(buch.ausgabezeilen(), [])
        self.rufe(t, "ausgabe_bestaetigen", vorschlag_id=v["vorschlag_id"])
        self.rufe(t, "ausgabe_bestaetigen", vorschlag_id=v["vorschlag_id"])
        self.assertEqual(len(buch.ausgabezeilen()), 1)
        r, _ = self.rufe(t, "ausgabe_vorschlagen", datum="2026-11-04", beschreibung="Aldi", zahlkonto="Aktiva:Girokonto",
                         positionen=[{"kategorie": "Ausgaben:Lebensmittel", "betrag": "3,00"}])
        v2 = json.loads(r["content"][0]["text"])
        self.assertEqual(len(v2["duplikate"]), 1)
        r, _ = self.rufe(t, "ausgabe_bestaetigen", vorschlag_id=v2["vorschlag_id"])
        self.assertTrue(r["isError"])

    def test_falsche_bonsumme_und_einnahme_abgelehnt(self):
        t = self.token()["access_token"]
        r, _ = self.rufe(t, "ausgabe_vorschlagen", datum="2026-11-03", beschreibung="x", zahlkonto="Aktiva:Girokonto", bonsumme="9,99",
                         positionen=[{"kategorie": "Ausgaben:Lebensmittel", "betrag": "3,00"}])
        self.assertTrue(r["isError"])
        for kat, zk in (("Einnahmen:GEHEIMEINKOMMEN", "Aktiva:Girokonto"), ("Ausgaben:Lebensmittel", "Aktiva:GEHEIMDEPOT")):
            r, _ = self.rufe(t, "ausgabe_vorschlagen", datum="2026-11-03", beschreibung="x", zahlkonto=zk,
                             positionen=[{"kategorie": kat, "betrag": "3,00"}])
            self.assertTrue(r["isError"])
        r, _ = self.rufe(t, "ausgabe_vorschlagen", datum="2026-11-03", beschreibung="Erstattung", zahlkonto="Aktiva:Girokonto",
                         positionen=[{"kategorie": "Ausgaben:Lebensmittel", "betrag": "-3,00"}])
        self.assertTrue(r["isError"])

    def test_keine_saldo_oder_einnahmedaten_nach_aussen(self):
        t = self.token()["access_token"]
        buch.buchen("einnahme", "2026-11-01", "GEHEIMEINGANG", GIRO, [{"konto": BAF, "betrag": 85577, "memo": "GEHEIMMEMO"}])
        buch.buchen("umbuchung", "2026-11-01", "GEHEIMSPARPLAN", GIRO, [{"konto": DEPOT, "betrag": 5011}])
        buch.buchen("ausgabe", "2026-11-02", "Erlaubt", GIRO, [{"konto": LEB, "betrag": 300}])
        buch.json_speichern(mcp.WIEDERKEHREND, [
            {"id": "x", "art": "einnahme", "name": "GEHEIMDAUER", "betrag": 85599, "konto": GIRO, "gegenkonto": BAF, "tag": 1, "alle_monate": 1, "naechste": "2030-01-01", "ende": "", "aktiv": True},
            {"id": "y", "art": "ausgabe", "name": "Abo", "betrag": 1700, "konto": GIRO, "gegenkonto": LEB, "tag": 1, "alle_monate": 1, "naechste": "2030-01-01", "ende": "", "aktiv": True}])
        antworten = []
        for name, a in (("kategorien_und_zahlkonten", {}), ("ausgaben_lesen", {}), ("monatsauswertung", {"monat": "2026-11"}),
                        ("wiederkehrende_ausgaben", {}),
                        ("ausgabe_vorschlagen", {"datum": "2026-11-03", "beschreibung": "B", "zahlkonto": "Aktiva:Girokonto", "positionen": [{"kategorie": "Ausgaben:Lebensmittel", "betrag": "1"}]})):
            _, roh = self.rufe(t, name, **a)
            antworten.append(roh)
        gesamt = "".join(antworten)
        self.assertIn("Erlaubt", gesamt)
        self.assertIn("Abo", gesamt)
        for geheim in ("GEHEIM", "9876", "1234", "1111", "855", "50.11", "saldo", "vermoegen"):
            self.assertNotIn(geheim, gesamt, geheim)
        _, liste, _ = anfrage(f"{self.url}/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {"Authorization": f"Bearer {t}"})
        self.assertEqual(len(json.loads(liste)["result"]["tools"]), len(mcp.WERKZEUGE))

    def test_panel_wege_gibt_es_am_connector_nicht(self):
        for pfad in ("/api/stand", "/api/export", "/", "/api/monat?m=2026-11"):
            self.assertEqual(anfrage(f"{self.url}{pfad}")[0], 404, pfad)


class Panel(Basis):
    def test_api_ablauf(self):
        url = starten(budget.Handler)
        code, roh, _ = anfrage(f"{url}/api/buchungen", {"art": "ausgabe", "datum": "2026-11-03", "beschreibung": "Bäcker", "konto": GIRO,
                                                         "positionen": [{"konto": LEB, "betrag": "2,40"}]})
        self.assertEqual(code, 200)
        kid = json.loads(roh)["id"]
        m = json.loads(anfrage(f"{url}/api/monat?m=2026-11")[1])
        self.assertEqual(m["ausgaben"], 240)
        self.assertTrue(m["buchungen"][0]["bearbeitbar"])
        self.assertEqual(anfrage(f"{url}/api/buchungen/{kid}", methode="DELETE")[0], 200)
        self.assertEqual(anfrage(f"{url}/api/buchungen", {"art": "ausgabe", "beschreibung": "x"})[0], 400)
        self.assertIn(b"Budget", anfrage(f"{url}/")[1].encode())


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1, exit=False)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
