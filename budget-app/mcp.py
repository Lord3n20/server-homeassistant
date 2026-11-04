"""Claude-Connector: MCP über HTTP mit OAuth, ausschließlich für Ausgaben.

Dieses Modul darf nur Ausgabenzeilen, Kategorien und die Namen der Zahlkonten
herausgeben. Salden, Einnahmen, Umbuchungen und Depot bleiben im Panel; darum
gibt es hier weder ein Werkzeug noch eine Antwort, die sie enthalten könnte.
Geschrieben wird in zwei Schritten: erst Vorschlag, nach dem OK Buchung.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.parse
from datetime import date, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler

import buch
from buch import BuchFehler, DATA, cent, json_laden, json_speichern, lock

VORSCHLAEGE = DATA / "vorschlaege.json"
BERICHTE = DATA / "berichte.json"
WIEDERKEHREND = DATA / "wiederkehrend.json"
OAUTH = DATA / "oauth.json"
MAX_KOERPER = 1024 * 1024
PROTOKOLLE = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
WEITERLEITUNGEN = ("https://claude.ai/", "https://claude.com/")
ACCESS_DAUER = 3600
REFRESH_DAUER = 90 * 86400
CODE_DAUER = 300
MIN_PASSWORT = 12
MAX_FEHLVERSUCHE = 5
SPERRE = 900

ANLEITUNG = ("Haushaltsbuch, nur Ausgaben. Salden und Einnahmen sind bewusst nicht verfügbar; "
             "frag nicht danach. Vor dem Buchen immer ausgabe_vorschlagen, dem Nutzer das Ergebnis "
             "zeigen und erst nach seinem OK ausgabe_bestaetigen.")

fehlversuche = {"anzahl": 0, "bis": 0.0}


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
        for p in a.get("positionen") or []:
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
        vorschlag = {"id": secrets.token_hex(8), "status": "offen", "erstellt": time.strftime("%Y-%m-%d %H:%M"),
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
    return vorschlag_aussen(vorschlag_buchen(a.get("vorschlag_id"), a.get("trotz_duplikat") is True))


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
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", monat) or not text:
        raise BuchFehler("Monat (JJJJ-MM) und Text angeben")
    if len(text) > 20000:
        raise BuchFehler("Der Bericht ist zu lang")
    with lock:
        berichte = json_laden(BERICHTE, {})
        berichte[monat] = {"text": text, "erstellt": time.strftime("%Y-%m-%d %H:%M")}
        json_speichern(BERICHTE, berichte)
    return {"ok": True}


def schema(eigenschaften, pflicht=()):
    return {"type": "object", "properties": eigenschaften, "required": list(pflicht), "additionalProperties": False}


TEXT = {"type": "string"}
WERKZEUGE = {
    "kategorien_und_zahlkonten": (w_kategorien, "Listet die gültigen Ausgabenkategorien und die Namen der Zahlkonten, ohne Salden.",
                                  schema({})),
    "ausgabe_vorschlagen": (w_vorschlagen, "Legt eine Ausgabe als Vorschlag an und prüft Summe und Duplikate. Bucht noch nicht.",
                            schema({"datum": {**TEXT, "description": "JJJJ-MM-TT"}, "beschreibung": TEXT,
                                    "zahlkonto": TEXT,
                                    "bonsumme": {**TEXT, "description": "Summe laut Bon, wird gegen die Positionen geprüft"},
                                    "positionen": {"type": "array", "minItems": 1, "items": schema(
                                        {"kategorie": TEXT, "betrag": {**TEXT, "description": "Euro, z. B. 12,34"}, "memo": TEXT},
                                        ("kategorie", "betrag"))}},
                                   ("datum", "beschreibung", "zahlkonto", "positionen"))),
    "ausgabe_bestaetigen": (w_bestaetigen, "Bucht einen Vorschlag, nachdem der Nutzer ihn bestätigt hat.",
                            schema({"vorschlag_id": TEXT, "trotz_duplikat": {"type": "boolean"}}, ("vorschlag_id",))),
    "vorschlag_verwerfen": (w_verwerfen, "Verwirft einen offenen Vorschlag.", schema({"vorschlag_id": TEXT}, ("vorschlag_id",))),
    "ausgaben_lesen": (w_ausgaben, "Liest gebuchte Ausgaben, optional nach Zeitraum und Suchwort. Erstattungen erscheinen als negative Beträge.",
                       schema({"von": TEXT, "bis": TEXT, "suche": TEXT})),
    "ausgabe_stornieren": (w_stornieren, "Entfernt eine gebuchte Ausgabe, wenn der Nutzer das verlangt.", schema({"id": TEXT}, ("id",))),
    "monatsauswertung": (w_monat, "Ausgaben eines Monats je Kategorie im Vergleich zum Vormonat.", schema({"monat": TEXT}, ("monat",))),
    "wiederkehrende_ausgaben": (w_wiederkehrend, "Listet die im Panel eingestellten wiederkehrenden Ausgaben.", schema({})),
    "monatsbericht_speichern": (w_bericht, "Speichert die Monatsbewertung, damit sie im Panel erscheint.",
                                schema({"monat": TEXT, "text": TEXT}, ("monat", "text"))),
}


def rpc(anfrage):
    """Beantwortet eine JSON-RPC-Nachricht; None heißt: keine Antwort nötig."""
    methode, kennung = anfrage.get("method"), anfrage.get("id")
    params = anfrage.get("params") or {}
    if kennung is None:
        return None

    def ok(ergebnis):
        return {"jsonrpc": "2.0", "id": kennung, "result": ergebnis}

    if methode == "initialize":
        gewuenscht = params.get("protocolVersion")
        return ok({"protocolVersion": gewuenscht if gewuenscht in PROTOKOLLE else PROTOKOLLE[0],
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "budget", "version": "0.1.0"},
                   "instructions": ANLEITUNG})
    if methode == "ping":
        return ok({})
    if methode == "tools/list":
        return ok({"tools": [{"name": n, "description": w[1], "inputSchema": w[2]} for n, w in WERKZEUGE.items()]})
    if methode == "tools/call":
        werkzeug = WERKZEUGE.get(params.get("name"))
        if not werkzeug:
            return {"jsonrpc": "2.0", "id": kennung, "error": {"code": -32602, "message": "Unbekanntes Werkzeug"}}
        try:
            text, fehler = json.dumps(werkzeug[0](params.get("arguments") or {}), ensure_ascii=False), False
        except BuchFehler as ex:
            text, fehler = str(ex), True
        except (KeyError, TypeError, AttributeError, ValueError):
            text, fehler = "Ungültige Eingabe", True
        return ok({"content": [{"type": "text", "text": text}], "isError": fehler})
    return {"jsonrpc": "2.0", "id": kennung, "error": {"code": -32601, "message": "Unbekannte Methode"}}


# ---------- OAuth ----------

def einstellung():
    o = json_laden(DATA / "options.json", {})
    return (o.get("oeffentliche_url") or "").rstrip("/"), o.get("connector_passwort") or ""


def streuwert(text):
    return hashlib.sha256(text.encode()).hexdigest()


def oauth_laden():
    daten = json_laden(OAUTH, {})
    for feld in ("clients", "codes", "access", "refresh"):
        daten.setdefault(feld, {})
    jetzt = time.time()
    for feld in ("codes", "access", "refresh"):
        daten[feld] = {k: v for k, v in daten[feld].items() if v["bis"] > jetzt}
    return daten


def tokens_ausgeben(daten, client_id):
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    jetzt = time.time()
    daten["access"][streuwert(access)] = {"client": client_id, "bis": jetzt + ACCESS_DAUER}
    daten["refresh"][streuwert(refresh)] = {"client": client_id, "bis": jetzt + REFRESH_DAUER}
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_DAUER, "refresh_token": refresh}


def token_gueltig(kopfzeile):
    if not (kopfzeile or "").startswith("Bearer "):
        return False
    with lock:
        return streuwert(kopfzeile[7:].strip()) in oauth_laden()["access"]


def anmeldeseite(params, hinweis=""):
    felder = "".join(f'<input type="hidden" name="{escape(k)}" value="{escape(v)}">' for k, v in params.items())
    return (f'<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>Budget-Connector</title><style>body{{font:16px system-ui;max-width:24rem;margin:3rem auto;padding:0 1rem}}"
            f"input[type=password],button{{font:inherit;width:100%;padding:.6rem;margin-top:.6rem;box-sizing:border-box}}</style>"
            f"<h1>Budget-Connector</h1><p>Claude bekommt Zugriff auf deine Ausgaben, nicht auf Salden und Einnahmen.</p>"
            f'<p role="alert">{escape(hinweis)}</p><form method="post" action="authorize">{felder}'
            f'<input type="password" name="passwort" autocomplete="current-password" aria-label="Passwort" autofocus>'
            f"<button>Freigeben</button></form></html>")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def antwort(self, code, daten=None, typ="application/json; charset=utf-8", kopf=None):
        roh = b"" if daten is None else daten.encode() if isinstance(daten, str) else json.dumps(daten, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Cache-Control", "no-store")
        if daten is not None:
            self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(roh)))
        for k, v in (kopf or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(roh)

    def koerper(self):
        laenge = int(self.headers.get("Content-Length") or 0)
        if laenge > MAX_KOERPER:
            self.close_connection = True
            raise BuchFehler("Anfrage zu groß")
        return self.rfile.read(laenge)

    def formular(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.koerper().decode()).items()}

    def do_GET(self):
        pfad = urllib.parse.urlsplit(self.path)
        url, _ = einstellung()
        if pfad.path.startswith("/.well-known/oauth-protected-resource"):
            return self.antwort(200, {"resource": f"{url}/mcp", "authorization_servers": [url]})
        if pfad.path.startswith("/.well-known/oauth-authorization-server"):
            return self.antwort(200, {
                "issuer": url, "authorization_endpoint": f"{url}/authorize", "token_endpoint": f"{url}/token",
                "registration_endpoint": f"{url}/register", "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"]})
        if pfad.path == "/authorize":
            params = {k: v[0] for k, v in urllib.parse.parse_qs(pfad.query).items() if k != "passwort"}
            fehler = self.autorisierung_pruefen(params)
            if fehler:
                return self.antwort(400, fehler, "text/plain; charset=utf-8")
            return self.seite(200, anmeldeseite(params))
        if pfad.path == "/mcp":
            return self.antwort(405, {"fehler": "Nur POST"}, kopf={"Allow": "POST"})
        self.antwort(404, {"fehler": "Nicht gefunden"})

    def seite(self, code, html):
        self.antwort(code, html, "text/html; charset=utf-8", {
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                       "form-action 'self' https://claude.ai https://claude.com; frame-ancestors 'none'"})

    def autorisierung_pruefen(self, p):
        with lock:
            client = oauth_laden()["clients"].get(p.get("client_id", ""))
        if not client or p.get("redirect_uri") not in client["redirect_uris"]:
            return "Unbekannter Client oder unbekannte Rücksprungadresse"
        if p.get("response_type") != "code" or p.get("code_challenge_method") != "S256" or not p.get("code_challenge"):
            return "Es wird der Code-Ablauf mit PKCE (S256) gebraucht"
        return None

    def do_POST(self):
        try:
            pfad = urllib.parse.urlsplit(self.path).path
            if pfad == "/register":
                return self.registrieren()
            if pfad == "/authorize":
                return self.freigeben()
            if pfad == "/token":
                return self.token()
            if pfad == "/mcp":
                return self.mcp()
            self.antwort(404, {"fehler": "Nicht gefunden"})
        except (BuchFehler, ValueError) as ex:
            self.antwort(400, {"error": "invalid_request", "error_description": str(ex)})

    def registrieren(self):
        daten = json.loads(self.koerper())
        uris = daten.get("redirect_uris") or []
        if not uris or not all(isinstance(u, str) and u.startswith(WEITERLEITUNGEN) for u in uris):
            return self.antwort(400, {"error": "invalid_redirect_uri"})
        client_id = secrets.token_urlsafe(16)
        with lock:
            o = oauth_laden()
            o["clients"][client_id] = {"redirect_uris": uris, "erstellt": time.time()}
            o["clients"] = dict(sorted(o["clients"].items(), key=lambda x: x[1]["erstellt"])[-20:])
            json_speichern(OAUTH, o)
        self.antwort(201, {"client_id": client_id, "redirect_uris": uris, "token_endpoint_auth_method": "none",
                           "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})

    def freigeben(self):
        p = self.formular()
        eingabe = p.pop("passwort", "")
        fehler = self.autorisierung_pruefen(p)
        if fehler:
            return self.antwort(400, fehler, "text/plain; charset=utf-8")
        _, passwort = einstellung()
        if len(passwort) < MIN_PASSWORT:
            return self.seite(503, anmeldeseite(p, f"In der App ist noch kein Passwort mit mindestens {MIN_PASSWORT} Zeichen eingestellt."))
        with lock:
            if time.time() < fehlversuche["bis"]:
                return self.seite(429, anmeldeseite(p, "Zu viele Fehlversuche. Bitte später noch einmal."))
            if not hmac.compare_digest(eingabe.encode(), passwort.encode()):
                fehlversuche["anzahl"] += 1
                if fehlversuche["anzahl"] >= MAX_FEHLVERSUCHE:
                    fehlversuche.update(anzahl=0, bis=time.time() + SPERRE)
                return self.seite(401, anmeldeseite(p, "Falsches Passwort."))
            fehlversuche["anzahl"] = 0
            code = secrets.token_urlsafe(32)
            o = oauth_laden()
            o["codes"][streuwert(code)] = {"client": p["client_id"], "redirect": p["redirect_uri"],
                                           "challenge": p["code_challenge"], "bis": time.time() + CODE_DAUER}
            json_speichern(OAUTH, o)
        ziel = {"code": code}
        if p.get("state"):
            ziel["state"] = p["state"]
        trenner = "&" if "?" in p["redirect_uri"] else "?"
        self.antwort(302, kopf={"Location": p["redirect_uri"] + trenner + urllib.parse.urlencode(ziel)})

    def token(self):
        p = self.formular()
        with lock:
            o = oauth_laden()
            if p.get("grant_type") == "authorization_code":
                eintrag = o["codes"].pop(streuwert(p.get("code", "")), None)
                pruef = base64.urlsafe_b64encode(hashlib.sha256(p.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if (not eintrag or eintrag["client"] != p.get("client_id") or eintrag["redirect"] != p.get("redirect_uri")
                        or not hmac.compare_digest(pruef, eintrag["challenge"])):
                    json_speichern(OAUTH, o)
                    return self.antwort(400, {"error": "invalid_grant"})
            elif p.get("grant_type") == "refresh_token":
                eintrag = o["refresh"].pop(streuwert(p.get("refresh_token", "")), None)
                if not eintrag or eintrag["client"] != p.get("client_id"):
                    json_speichern(OAUTH, o)
                    return self.antwort(400, {"error": "invalid_grant"})
            else:
                return self.antwort(400, {"error": "unsupported_grant_type"})
            tokens = tokens_ausgeben(o, eintrag["client"])
            json_speichern(OAUTH, o)
        self.antwort(200, tokens)

    def mcp(self):
        if not token_gueltig(self.headers.get("Authorization")):
            url, _ = einstellung()
            self.koerper()
            return self.antwort(401, {"error": "unauthorized"}, kopf={
                "WWW-Authenticate": f'Bearer resource_metadata="{url}/.well-known/oauth-protected-resource"'})
        anfrage = json.loads(self.koerper())
        if not isinstance(anfrage, dict):
            return self.antwort(400, {"error": "Nur einzelne Nachrichten"})
        ergebnis = rpc(anfrage)
        if ergebnis is None:
            return self.antwort(202)
        self.antwort(200, ergebnis)
