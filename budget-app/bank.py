"""Umsätze von der Bank per FinTS (PIN/TAN) abrufen, zuerst für die GLS Bank.

Abgerufene Umsätze landen als Liste im Panel; gebucht wird erst, wenn Enzo dort
bestätigt. Zugangsdaten liegen nur in /data (bank-zugang.json, nur für die App lesbar)
und gehen nie ans Panel zurück. Claude kommt hier nicht heran: ausgaben.py kennt
dieses Modul nicht.
Freigaben laufen über SecureGo plus (decoupled): Die Bank schickt eine Push-Anfrage,
der Abruf wartet bis zu fünf Minuten auf die Bestätigung in der App. Danach reicht
die Freigabe meist 90 Tage, der Zustand des Clients wird dafür gespeichert.
"""
import base64
import hashlib
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import buch
from buch import BuchFehler, DATA, json_laden, json_speichern, lock

BANK = DATA / "bank.json"
ZUGANG = DATA / "bank-zugang.json"
STANDARD = {"blz": "43060967", "server": "https://fints1.atruvia.de/cgi-bin/hbciservlet"}
WARTEN_FREIGABE = 300  # Sekunden
AUTO_STUNDE = 7
MAX_UMSAETZE = 3000

_abruf = threading.Lock()
_gestartet = threading.Event()


def _status(zustand, text=""):
    with lock:
        daten = json_laden(BANK, {})
        daten["status"] = {"zustand": zustand, "text": text, "zeit": time.strftime("%Y-%m-%d %H:%M")}
        json_speichern(BANK, daten)


def _zugang():
    return json_laden(ZUGANG, {})


def einstellungen_lesen():
    """Fürs Panel: alles außer der PIN."""
    z = _zugang()
    daten = json_laden(BANK, {})
    status = daten.get("status") or {"zustand": "bereit", "text": "", "zeit": ""}
    if status["zustand"] in ("laeuft", "freigabe") and not (_abruf.locked() or _gestartet.is_set()):
        status = {**status, "zustand": "fehler", "text": "Abruf wurde unterbrochen (Neustart)"}
    return {"login": z.get("login", ""), "pin_gesetzt": bool(z.get("pin")), "produkt_id": z.get("produkt_id", ""),
            "blz": z.get("blz", STANDARD["blz"]), "server": z.get("server", STANDARD["server"]),
            "iban": z.get("iban", ""), "buchkonto": z.get("buchkonto", ""), "ab": z.get("ab", ""),
            "automatisch": z.get("automatisch", False), "status": status,
            "letzter_abruf": daten.get("letzter_abruf", ""), "konten_bank": daten.get("konten_bank", [])}


def einstellungen_speichern(neu):
    with lock:
        z = _zugang()
        konten, _ = buch.lesen()
        buchkonto = str(neu.get("buchkonto") or "")
        if buchkonto and (buchkonto not in konten or konten[buchkonto]["typ"] not in buch.ZAHLKONTO_TYPEN):
            raise BuchFehler("Das Buchkonto muss ein Zahlkonto sein")
        ab = buch.tag(neu["ab"]).isoformat() if neu.get("ab") else ""
        server = str(neu.get("server") or STANDARD["server"]).strip()
        if not server.startswith("https://"):
            raise BuchFehler("Der Server muss mit https:// beginnen")
        blz = re.sub(r"\s", "", str(neu.get("blz") or STANDARD["blz"]))
        if not re.fullmatch(r"\d{8}", blz):
            raise BuchFehler("Die BLZ hat 8 Ziffern")
        alt = (z.get("login"), z.get("blz"), z.get("server"))
        z.update(login=str(neu.get("login") or "").strip(), produkt_id=str(neu.get("produkt_id") or "").strip(),
                 blz=blz, server=server, iban=re.sub(r"\s", "", str(neu.get("iban") or "")).upper(),
                 buchkonto=buchkonto, ab=ab, automatisch=neu.get("automatisch") is True)
        if neu.get("pin"):
            z["pin"] = str(neu["pin"])
        if alt != (z["login"], z["blz"], z["server"]):
            z.pop("client", None)  # anderer Zugang: Freigaben und Bankdaten neu holen
        json_speichern(ZUGANG, z)
        os.chmod(ZUGANG, 0o600)


# ---------- Umsätze ----------

def _text(x):
    return re.sub(r"\s+", " ", str(x or "")).strip()


def umsaetze_aus_bank(transaktionen):
    """Macht aus den FinTS-Umsätzen einfache Einträge mit fester Kennung (gleiche Umsätze am selben Tag zählen mit)."""
    liste, gesehen = [], {}
    for t in transaktionen:
        d = t.data if hasattr(t, "data") else t
        tag = d.get("date") or d.get("entry_date")
        if isinstance(tag, datetime):
            tag = tag.date()
        betrag = d.get("amount")
        betrag = getattr(betrag, "amount", betrag)
        cents = int((Decimal(str(betrag)) * 100).to_integral_value())
        name = _text(d.get("applicant_name") or d.get("posting_text") or "Unbekannt")
        zweck = _text(d.get("purpose"))
        schluessel = f"{tag.isoformat()}|{cents}|{name}|{zweck}"
        n = gesehen.get(schluessel, 0)
        gesehen[schluessel] = n + 1
        kennung = "u" + hashlib.sha1(f"{schluessel}|{n}".encode()).hexdigest()[:16]
        liste.append({"id": kennung, "datum": tag.isoformat(), "betrag": cents, "name": name, "zweck": zweck[:300]})
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


def uebernehmen(neue):
    """Fügt neue Umsätze hinzu; bekannte (gleiche Kennung) bleiben, wie sie sind."""
    with lock:
        z = _zugang()
        kontoid = z.get("buchkonto")
        daten = json_laden(BANK, {})
        liste = daten.get("umsaetze", [])
        bekannt = {u["id"] for u in liste}
        konten, buchungen = buch.lesen()
        verknuepft = {u.get("buchung_id") for u in liste} | {(u.get("duplikat") or {}).get("id") for u in liste}
        dazu = 0
        for u in sorted(neue, key=lambda u: u["datum"]):
            if u["id"] in bekannt or (z.get("ab") and u["datum"] < z["ab"]):
                continue
            vorschlag, duplikat = einordnen(u, kontoid, konten, buchungen, verknuepft)
            if duplikat:
                verknuepft.add(duplikat["id"])
            liste.append({**u, "status": "offen", "vorschlag": vorschlag, "duplikat": duplikat})
            dazu += 1
        daten["umsaetze"] = liste[-MAX_UMSAETZE:]
        json_speichern(BANK, daten)
    return dazu


def offene():
    return [u for u in json_laden(BANK, {}).get("umsaetze", []) if u["status"] == "offen"]


def umsatz_buchen(kennung, gegenkonto, beschreibung=None):
    with lock:
        z = _zugang()
        kontoid = z.get("buchkonto")
        daten = json_laden(BANK, {})
        u = next((x for x in daten.get("umsaetze", []) if x["id"] == kennung), None)
        if not u or u["status"] != "offen":
            raise BuchFehler("Kein offener Umsatz mit dieser Nummer")
        konten, _ = buch.lesen()
        g = konten.get(gegenkonto)
        if not kontoid or kontoid not in konten:
            raise BuchFehler("Erst in den Bank-Einstellungen das Buchkonto wählen")
        if not g or g["platzhalter"]:
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


# ---------- Abruf ----------

def _client(z):
    from fints.client import FinTS3PinTanClient
    alt = base64.b64decode(z["client"]) if z.get("client") else None
    return FinTS3PinTanClient(z["blz"], z["login"], z["pin"], z["server"], product_id=z["produkt_id"], from_data=alt)


def _warten(client, antwort):
    """Wartet auf die Freigabe in SecureGo plus; liefert die Antwort danach."""
    from fints.client import NeedTANResponse
    ende = time.time() + WARTEN_FREIGABE
    while isinstance(antwort, NeedTANResponse):
        if not antwort.decoupled:
            raise BuchFehler("Die Bank will eine TAN zum Eintippen; bitte in den Einstellungen SecureGo plus nutzen")
        _status("freigabe", "Bitte in SecureGo plus freigeben")
        if time.time() > ende:
            raise BuchFehler("Keine Freigabe in SecureGo plus innerhalb von fünf Minuten")
        time.sleep(3)
        antwort = client.send_tan(antwort, "")
    return antwort


def _tan_verfahren(client):
    if not client.get_current_tan_mechanism():
        client.fetch_tan_mechanisms()
        verfahren = client.get_tan_mechanisms()
        # SecureGo plus bevorzugen, sonst das erste angebotene Verfahren
        wahl = next((k for k, p in verfahren.items() if "securego" in str(getattr(p, "name", "")).lower()),
                    next(iter(verfahren), None))
        if wahl:
            client.set_tan_mechanism(wahl)
    if client.selected_tan_medium is None and client.is_tan_media_required():
        medien = client.get_tan_media()[1]
        if medien:
            client.set_tan_medium(medien[0])
        else:
            client.selected_tan_medium = ""


def abrufen():
    """Ein Abruf: Konto suchen, Umsätze seit dem letzten Abruf holen, übernehmen."""
    if not _abruf.acquire(blocking=False):
        raise BuchFehler("Es läuft schon ein Abruf")
    try:
        z = _zugang()
        if not all(z.get(k) for k in ("login", "pin", "produkt_id", "buchkonto")):
            raise BuchFehler("Login, PIN, Produkt-ID und Buchkonto eintragen")
        _status("laeuft", "Verbinde mit der Bank")
        daten = json_laden(BANK, {})
        heute = date.today()
        if daten.get("letzter_abruf"):
            start = date.fromisoformat(daten["letzter_abruf"]) - timedelta(days=10)
        else:
            start = date.fromisoformat(z["ab"]) if z.get("ab") else heute - timedelta(days=30)
        start = max(start, heute - timedelta(days=360))
        client = _client(z)
        _tan_verfahren(client)
        with client:
            if client.init_tan_response:
                _warten(client, client.init_tan_response)
            _status("laeuft", "Hole Umsätze")
            konten = _warten(client, client.get_sepa_accounts())
            if not konten:
                raise BuchFehler("Die Bank meldet kein Konto")
            konto = next((k for k in konten if k.iban == z.get("iban")), None) if z.get("iban") else konten[0]
            if not konto:
                raise BuchFehler("Konto mit dieser IBAN nicht gefunden")
            umsaetze = _warten(client, client.get_transactions(konto, start, heute))
        with lock:
            z = _zugang()
            z["client"] = base64.b64encode(client.deconstruct(including_private=True)).decode()
            json_speichern(ZUGANG, z)
            os.chmod(ZUGANG, 0o600)
        dazu = uebernehmen(umsaetze_aus_bank(umsaetze or []))
        with lock:
            daten = json_laden(BANK, {})
            daten["letzter_abruf"] = heute.isoformat()
            daten["konten_bank"] = [k.iban[:4] + " … " + k.iban[-4:] for k in konten]
            json_speichern(BANK, daten)
        _status("bereit", f"{dazu} neue Umsätze" if dazu != 1 else "1 neuer Umsatz")
        return dazu
    except BuchFehler as ex:
        _status("fehler", str(ex))
        raise
    except Exception as ex:  # Fehler der Bank oder der Verbindung: als Text ins Panel
        text = re.sub(r"\s+", " ", str(ex))[:300] or type(ex).__name__
        _status("fehler", f"Bank: {text}")
        raise BuchFehler(f"Bank: {text}")
    finally:
        _abruf.release()


def im_hintergrund():
    if _abruf.locked() or _gestartet.is_set():
        raise BuchFehler("Es läuft schon ein Abruf")
    _gestartet.set()
    _status("laeuft", "Starte")
    threading.Thread(target=_sicher_abrufen, daemon=True).start()


def _sicher_abrufen():
    try:
        abrufen()
    except BuchFehler:
        pass
    finally:
        _gestartet.clear()


def schleife():
    """Einmal am Tag ab 7 Uhr, wenn automatisch eingeschaltet."""
    while True:
        try:
            z = _zugang()
            daten = json_laden(BANK, {})
            heute = date.today().isoformat()
            if z.get("automatisch") and datetime.now().hour >= AUTO_STUNDE and daten.get("auto_versuch") != heute:
                daten["auto_versuch"] = heute
                json_speichern(BANK, daten)
                _sicher_abrufen()
        except OSError as ex:
            print(f"Bankabruf: {ex}")
        time.sleep(600)
