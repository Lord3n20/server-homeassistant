"""Bankumsätze aus der Chromium-Erweiterung (Trade Republic, GLS, TF Bank) zum Einordnen im Panel.

Die Erweiterung liest die Umsätze aus dem angemeldeten Online-Banking und schickt sie
über einen Home-Assistant-Webhook an das Werkzeug bank_einreichen (ausgaben.py). Dort
werden sie nur abgelegt, die Antwort enthält nur die Anzahl. Gebucht wird erst, wenn
Enzo im Panel bestätigt. Es gibt hier keine Zugangsdaten.

Automatik (0.20.0, abschaltbar): Was sicher ist, erledigt die App selbst und zeigt es unter „Automatisch
erledigt“ mit „Rückgängig“: Umbuchungen zwischen den eigenen Banken (Gegenstück gefunden), Umsätze, die
genau so schon im Buch stehen, und bekannte Empfänger mit eindeutiger Kategorie. Den Rest fragt sie die
lokale KI (lokal_ki.py): Kategorie für neue Empfänger und ob ein ähnlicher Eintrag (z. B. ein Bon mit
Trinkgeld oder auf einem anderen Konto) derselbe Einkauf ist.
"""
import hashlib
import re
import time
from datetime import date, timedelta

import buch
from buch import BuchFehler, DATA, cent, json_laden, json_speichern, lock

BANK = DATA / "bank.json"
ALT_ZUGANG = DATA / "bank-zugang.json"  # vom FinTS-Abruf (0.11.0), wird entfernt
BANKEN = {"tr": "Trade Republic", "gls": "GLS Bank", "tf": "TF Bank"}
# Woran das Buchkonto ohne eigene Wahl erkannt wird (Teil des Kontopfads, klein geschrieben).
ERKENNUNG = {"tr": "trade republic", "gls": "gls", "tf": "tf bank"}
MAX_UMSAETZE = 3000
MAX_EINREICHEN = 2000
UEBERLAPPUNG = 5  # Tage vor dem letzten Abruf, die noch angenommen werden (spät gebuchte Kartenzahlungen)
PAAR_TAGE = 5  # so weit dürfen Ab- und Zugang einer Umbuchung zwischen zwei Banken auseinanderliegen
AEHNLICH_TAGE = 5  # Suchfenster für ähnliche Einträge im Buch
# Woran man im Namen oder Zweck erkennt, dass Geld zu einer der eigenen Banken geht.
HINWEISE = {"tr": ("trade republic",), "gls": ("gls",), "tf": ("tf bank", "tfbank")}
# Zahlungsdienste: dahinter stecken wechselnde Händler, also nie automatisch nach dem Namen buchen.
ZAHLDIENSTE = {"paypal", "klarna", "sumup", "stripe", "adyen", "sofort", "giropay", "wero", "zettle", "payone"}
# Wörter, die im Namensvergleich nichts sagen.
FUELLWOERTER = {"gmbh", "sagt", "danke", "markt", "und", "der", "die", "das", "kg", "co", "se", "europe", "deutschland",
                "sarl", "ltd", "inc", "filiale", "karte", "zahlung", "lastschrift", "kartenzahlung", "einkauf", "vom", "bei"}


def aufraeumen():
    ALT_ZUGANG.unlink(missing_ok=True)


def buchkonten(konten=None):
    """Bank → Konto-ID; ohne eigene Wahl das Zahlkonto, dessen Pfad den Banknamen enthält."""
    konten = konten or buch.lesen()[0]
    gewaehlt = json_laden(BANK, {}).get("konten", {})
    ergebnis = {}
    for kuerzel in BANKEN:
        kid = gewaehlt.get(kuerzel)
        if not kid or kid not in konten:
            passend = [k for k in konten.values() if k["typ"] in buch.ZAHLKONTO_TYPEN and not k["platzhalter"]
                       and ERKENNUNG[kuerzel] in k["pfad"].lower()]
            kid = passend[0]["id"] if len(passend) == 1 else ""
        ergebnis[kuerzel] = kid
    return ergebnis


def einstellungen_lesen():
    daten = json_laden(BANK, {})
    return {"banken": BANKEN, "konten": buchkonten(), "zuletzt": daten.get("zuletzt", {}),
            "automatik": daten.get("automatik", True)}


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
        if "automatik" in neu:
            daten["automatik"] = neu["automatik"] is True
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


def woerter(text):
    return {w for w in re.sub(r"[^a-z0-9äöüß]+", " ", text.lower()).split()
            if len(w) > 2 and not w.isdigit() and w not in FUELLWOERTER}


def haendler(name):
    """Schlüssel für denselben Händler: die Wörter ohne Füllwörter und Nummern, sortiert (leer bei Zahldiensten)."""
    w = woerter(name)
    return "" if w & ZAHLDIENSTE else " ".join(sorted(w))


def aehnlich(a, b):
    return bool(woerter(a) & woerter(b))


def einordnen(umsatz, kontoid, konten, buchungen, verknuepft):
    """Kategorie vom letzten gleichen Empfänger und mögliche Buchung, die schon im Buch steht.

    Liefert dazu, ob die Kategorie sicher ist: die letzten (bis zu drei) gleichen Empfänger auf diesem Konto
    hatten alle dieselbe Ausgaben- oder Einnahmenkategorie.
    """
    name = umsatz["name"].lower()
    schluessel = haendler(umsatz["name"])
    auf_konto = sorted((b for b in buchungen if any(s["konto"] == kontoid for s in b["splits"])),
                       key=lambda b: b["datum"], reverse=True)

    def kategorien(passt):
        return [g for g in (_gegenkonto(b, kontoid) for b in auf_konto if passt(b)) if g and not konten[g]["platzhalter"]]
    gleiche = kategorien(lambda b: b["beschreibung"].lower() == name)
    # Sonst derselbe Händler mit anderer Filial- oder Kassennummer („REWE Markt 4711“ wie „REWE Markt 4712“);
    # sicher erst, wenn es mindestens zwei solche Buchungen gibt.
    genug = 1
    if not gleiche and schluessel:
        gleiche, genug = kategorien(lambda b: haendler(b["beschreibung"]) == schluessel), 2
    vorschlag = gleiche[0] if gleiche else ""
    sicher = (len(gleiche) >= genug and len(set(gleiche[:3])) == 1
              and konten[vorschlag]["typ"] in ("EXPENSE", "INCOME") and not woerter(umsatz["name"]) & ZAHLDIENSTE)
    d = date.fromisoformat(umsatz["datum"])
    von, bis = (d - timedelta(days=3)).isoformat(), (d + timedelta(days=3)).isoformat()
    duplikat = next((b for b in buchungen if von <= b["datum"] <= bis and b["id"] not in verknuepft
                     and any(s["konto"] == kontoid and s["betrag"] == umsatz["betrag"] for s in b["splits"])), None)
    return vorschlag, ({"id": duplikat["id"], "datum": duplikat["datum"], "beschreibung": duplikat["beschreibung"]}
                       if duplikat else None), sicher


def aehnliche(umsatz, kontoid, konten, buchungen, verknuepft, ausser=None):
    """Einträge im Buch, die derselbe Einkauf sein könnten: andere Summe (Trinkgeld, Pfand) oder anderes Zahlkonto.

    Die KI entscheidet danach, ob einer davon wirklich passt.
    """
    d = date.fromisoformat(umsatz["datum"])
    von, bis = (d - timedelta(days=AEHNLICH_TAGE)).isoformat(), (d + timedelta(days=AEHNLICH_TAGE)).isoformat()
    spanne = max(150, abs(umsatz["betrag"]) // 8)
    treffer = []
    for b in buchungen:
        if not (von <= b["datum"] <= bis) or b["id"] in verknuepft or b["id"] == ausser:
            continue
        zahl = [s for s in b["splits"] if konten[s["konto"]]["typ"] in buch.ZAHLKONTO_TYPEN]
        rest = [s for s in b["splits"] if konten[s["konto"]]["typ"] in ("EXPENSE", "INCOME")]
        if len(zahl) != 1 or not rest:
            continue
        betrag = zahl[0]["betrag"]
        abstand = abs(betrag - umsatz["betrag"])
        if (betrag < 0) != (umsatz["betrag"] < 0) or abstand > spanne:
            continue
        anderes_konto = zahl[0]["konto"] != kontoid
        if not (aehnlich(b["beschreibung"], umsatz["name"] + " " + umsatz["zweck"]) or (anderes_konto and abstand == 0)):
            continue
        treffer.append((abstand, {"id": b["id"], "datum": b["datum"], "beschreibung": b["beschreibung"],
                                  "betrag": betrag, "konto": konten[zahl[0]["konto"]]["pfad"]}))
    return [t for _, t in sorted(treffer, key=lambda t: t[0])[:3]]


def einreichen(bank, roh):
    """Neue Umsätze ablegen und, was sicher ist, gleich erledigen; liefert nur die Anzahl.

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
        verknuepft = _verknuepft(liste)
        dazu = 0
        for u in sorted(neue, key=lambda u: u["datum"]):
            if u["id"] in bekannt or u["datum"] < grenze:
                continue
            vorschlag, duplikat, sicher = einordnen(u, kontoid, konten, buchungen, verknuepft) if kontoid else ("", None, False)
            if duplikat:
                verknuepft.add(duplikat["id"])
            kandidaten = aehnliche(u, kontoid, konten, buchungen, verknuepft) if kontoid and not duplikat else []
            liste.append({**u, "status": "offen", "vorschlag": vorschlag, "sicher": sicher, "duplikat": duplikat,
                          "kandidaten": kandidaten, "ki": "wartet" if kontoid and not duplikat and (kandidaten or not sicher) else ""})
            bekannt.add(u["id"])
            dazu += 1
        daten["umsaetze"] = liste[-MAX_UMSAETZE:]
        daten.setdefault("zuletzt", {})[bank] = time.strftime("%Y-%m-%d %H:%M")
        if daten.get("automatik", True):
            automatisch(daten)
        json_speichern(BANK, daten)
    return dazu


def _verknuepft(liste):
    ids = set()
    for u in liste:
        ids |= {u.get("buchung_id"), (u.get("duplikat") or {}).get("id"), (u.get("ki_duplikat") or {}).get("id")}
    return ids - {None}


def _nennt(u, kuerzel):
    text = f"{u['name']} {u['zweck']}".lower()
    return any(h in text for h in HINWEISE[kuerzel])


def paare(liste, konten_je_bank):
    """Ab- und Zugang zwischen zwei eigenen Banken (gleicher Betrag, wenige Tage auseinander).

    Liefert (abgang, zugang, sicher); sicher heißt, Name oder Zweck nennt die andere Bank.
    """
    offen = [u for u in liste if u["status"] == "offen" and konten_je_bank.get(u["bank"])]
    vergeben, ergebnis = set(), []
    for ab in sorted((u for u in offen if u["betrag"] < 0), key=lambda u: u["datum"]):
        d = date.fromisoformat(ab["datum"])
        passend = [zu for zu in offen if zu["betrag"] == -ab["betrag"] and zu["bank"] != ab["bank"] and zu["id"] not in vergeben
                   and abs((date.fromisoformat(zu["datum"]) - d).days) <= PAAR_TAGE]
        if not passend:
            continue
        zu = min(passend, key=lambda z: abs((date.fromisoformat(z["datum"]) - d).days))
        vergeben |= {ab["id"], zu["id"]}
        ergebnis.append((ab, zu, _nennt(ab, zu["bank"]) or _nennt(zu, ab["bank"])))
    return ergebnis


def automatisch(daten):
    """Erledigt, was sicher ist (läuft unter dem Buch-Lock, nach dem Einreichen und nach der KI)."""
    konten, buchungen = buch.lesen()
    je_bank = buchkonten(konten)
    liste = daten.get("umsaetze", [])
    for ab, zu, sicher in paare(liste, je_bank):
        ab["paar"], zu["paar"] = zu["id"], ab["id"]
        if sicher and not (ab.get("kein_auto") or zu.get("kein_auto")):
            bid = buch.buchen("umbuchung", ab["datum"], ab["name"], je_bank[ab["bank"]],
                              [{"konto": je_bank[zu["bank"]], "betrag": -ab["betrag"], "memo": ab["zweck"][:120]}])
            for u in (ab, zu):
                u.update(status="gebucht", buchung_id=bid, ki="",
                         auto=f"Umbuchung {BANKEN[ab['bank']]} → {BANKEN[zu['bank']]}")
    for u in liste:
        if u["status"] != "offen" or u.get("kein_auto") or u.get("paar"):
            continue
        dup = u.get("duplikat")
        if dup and abs((date.fromisoformat(dup["datum"]) - date.fromisoformat(u["datum"])).days) <= 2:
            u.update(status="ignoriert", auto=f"Stand schon im Buch: {dup['beschreibung']}")
            continue
        if dup or u.get("ki") == "wartet" or u.get("ki_duplikat") or u.get("kandidaten") and u.get("ki") != "fertig":
            continue
        ziel, grund = (u["vorschlag"], "Bekannter Empfänger") if u.get("sicher") else (u.get("ki_vorschlag"), "Kategorie von der KI")
        if ziel and ziel in konten and konten[ziel]["typ"] in ("EXPENSE", "INCOME") and not konten[ziel]["platzhalter"]:
            _buchen(u, konten, je_bank.get(u["bank"]), ziel)
            u["auto"] = f"{grund}: {konten[ziel]['pfad'].split(':', 1)[-1]}"


def offene():
    return [u for u in json_laden(BANK, {}).get("umsaetze", []) if u["status"] == "offen"]


def umsatz_buchen(kennung, gegenkonto, beschreibung=None, gebuehr=0):
    """gebuehr (Cent): Teil des Abgangs, der als Ordergebühr eigene Ausgabe wird (nur bei Umbuchung, z. B. Aktienkauf)."""
    with lock:
        daten = json_laden(BANK, {})
        liste = daten.get("umsaetze", [])
        u = next((x for x in liste if x["id"] == kennung), None)
        if not u or u["status"] != "offen":
            raise BuchFehler("Kein offener Umsatz mit dieser Nummer")
        konten, _ = buch.lesen()
        je_bank = buchkonten(konten)
        _buchen(u, konten, je_bank.get(u.get("bank", "gls")), gegenkonto, beschreibung, gebuehr)
        # Umbuchung auf die Bank des Gegenstücks: das Gegenstück ist damit auch gebucht.
        partner = next((x for x in liste if x["id"] == u.get("paar") and x["status"] == "offen"), None)
        if partner and je_bank.get(partner["bank"]) == gegenkonto:
            partner.update(status="gebucht", buchung_id=u["buchung_id"])
        json_speichern(BANK, daten)
    return u["buchung_id"]


def _buchen(u, konten, kontoid, gegenkonto, beschreibung=None, gebuehr=0):
    g = konten.get(gegenkonto)
    if not kontoid:
        raise BuchFehler(f"Erst das Buchkonto für {BANKEN[u['bank']]} wählen")
    if not g or g["platzhalter"] or g["id"] == kontoid:
        raise BuchFehler("Bitte eine Kategorie wählen")
    text = _text(beschreibung) or u["name"]
    memo = u["zweck"][:120]
    if gebuehr and not (g["typ"] in buch.VERMOEGEN_TYPEN and u["betrag"] < 0):
        raise BuchFehler("Die Ordergebühr gibt es nur bei Abgängen aufs Depot oder ein anderes Konto")
    if gebuehr and gebuehr >= -u["betrag"]:
        raise BuchFehler("Die Gebühr ist größer als der Umsatz")
    if gebuehr:
        buch.gebuehrenkonto(konten)
    if g["typ"] == "EXPENSE":
        art, konto, pos = "ausgabe", kontoid, [{"konto": g["id"], "betrag": -u["betrag"], "memo": memo}]
    elif g["typ"] == "INCOME":
        art, konto, pos = "einnahme", kontoid, [{"konto": g["id"], "betrag": u["betrag"], "memo": memo}]
    elif g["typ"] in buch.VERMOEGEN_TYPEN:
        # Geht Geld ab, fließt es vom Buchkonto aufs andere; kommt Geld an, umgekehrt.
        if u["betrag"] < 0:
            art, konto, pos = "umbuchung", kontoid, [{"konto": g["id"], "betrag": -u["betrag"] - gebuehr, "memo": memo}]
        else:
            art, konto, pos = "umbuchung", g["id"], [{"konto": kontoid, "betrag": u["betrag"], "memo": memo}]
    else:
        raise BuchFehler("Diese Kategorie passt nicht")
    u["buchung_id"] = buch.buchen(art, u["datum"], text, konto, pos)
    if gebuehr:
        u["gebuehr_id"] = buch.gebuehr_buchen(u["datum"], kontoid, gebuehr, text)
    u["status"] = "gebucht"


def umsatz_ignorieren(kennung):
    """Für Umsätze, die schon im Buch stehen (z. B. per Bon) oder nicht ins Buch sollen."""
    with lock:
        daten = json_laden(BANK, {})
        u = next((x for x in daten.get("umsaetze", []) if x["id"] == kennung and x["status"] == "offen"), None)
        if not u:
            raise BuchFehler("Kein offener Umsatz mit dieser Nummer")
        u["status"] = "ignoriert"
        json_speichern(BANK, daten)


def automatisch_erledigt(anzahl=40):
    """Was die Automatik zuletzt erledigt hat (fürs Panel, mit „Rückgängig“)."""
    liste = [u for u in json_laden(BANK, {}).get("umsaetze", []) if u.get("auto") and u["status"] != "offen"]
    return sorted(liste, key=lambda u: u["datum"], reverse=True)[:anzahl]


def umsatz_zuruecksetzen(kennung):
    """Nimmt eine automatische Erledigung zurück: Buchung raus, Umsatz wieder offen, ohne neue Automatik."""
    with lock:
        daten = json_laden(BANK, {})
        liste = daten.get("umsaetze", [])
        u = next((x for x in liste if x["id"] == kennung and x.get("auto") and x["status"] != "offen"), None)
        if not u:
            raise BuchFehler("Nichts zum Zurücknehmen")
        betroffen = [x for x in liste if x is u or (u.get("buchung_id") and x.get("buchung_id") == u["buchung_id"])]
        for bid in {u.get("buchung_id"), u.get("gebuehr_id")} - {None}:
            buch.loeschen(bid)
        for x in betroffen:
            for feld in ("buchung_id", "gebuehr_id", "auto"):
                x.pop(feld, None)
            x.update(status="offen", kein_auto=True)
        json_speichern(BANK, daten)
