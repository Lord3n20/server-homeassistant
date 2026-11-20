"""Lokale KI (App „KI (colibri)“ im selben App-Netz): Einordnen im Reiter Bank und der lokale Monatsbericht.

Nichts davon verlässt den Server. Ohne BUDGET_KI_URL (Desktop-Programm, Tests) ist alles aus.

- Bank: Umsätze mit ki == "wartet" gehen in Paketen an die KI. Sie wählt die Kategorie (neue Empfänger) und
  sagt, ob ein ähnlicher Eintrag im Buch derselbe Einkauf ist. Danach erledigt bank.automatisch() den Rest.
- Monatsbericht: in der Nacht zum 1. (bis zum 7. nachgeholt) für den Vormonat, mit denselben Daten wie
  Claude (nur Ausgaben). Er steht im Bericht unter „lokal“; ein Bericht von Claude bleibt daneben erhalten.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

import ausgaben
import bank
import buch
from buch import BuchFehler, DATA, json_laden, json_speichern, lock

URL = os.environ.get("BUDGET_KI_URL", "").rstrip("/")
ZUSTAND = DATA / "ki.json"
PAKET = 12
UMBUCHUNG = "Umbuchung (eigenes Konto, Depot, Sparen)"
zustand = {"bank": "", "bericht": ""}
_bank_lauf = threading.Lock()


def an():
    return bool(URL)


def frage(system, nutzer, max_tokens=300, denken=False, timeout=3600):
    koerper = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": nutzer}],
               "max_tokens": max_tokens, "temperature": 0, "enable_thinking": denken,
               "chat_template_kwargs": {"enable_thinking": denken}}
    req = urllib.request.Request(f"{URL}/v1/chat/completions", json.dumps(koerper).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = json.load(r)["choices"][0]["message"]["content"] or ""
    except urllib.error.HTTPError as ex:
        try:
            grund = json.loads(ex.read()).get("fehler") or ex.reason
        except ValueError:
            grund = ex.reason
        raise OSError(f"KI: {grund}")
    except (KeyError, IndexError, ValueError):
        raise OSError("KI: unerwartete Antwort")
    return re.sub(r"(?s)<think>.*?</think>", "", text).replace("<think>", "").strip()


# ---------- Bank ----------

def _kategorien(konten):
    return sorted(k["pfad"] for k in konten.values() if k["typ"] in ("EXPENSE", "INCOME") and not k["platzhalter"])


def _beispiele(konten, buchungen, anzahl=40):
    """Frühere Zuordnungen (Beschreibung → Kategorie), die neuesten zuerst, je Beschreibung einmal."""
    gesehen, zeilen = set(), []
    for b in sorted(buchungen, key=lambda b: b["datum"], reverse=True):
        art, _, pos, einfach = buch.einordnen(konten, b)
        name = b["beschreibung"].strip()
        if art in ("ausgabe", "einnahme") and einfach and len(pos) == 1 and name.lower() not in gesehen:
            gesehen.add(name.lower())
            zeilen.append(f"{name[:60]} -> {konten[pos[0]['konto']]['pfad']}")
            if len(zeilen) >= anzahl:
                break
    return zeilen


def _zeile(u):
    return f"{u['datum']} | {u['name']} | Zweck: {u['zweck'][:100] or '-'} | {u['betrag'] / 100:.2f} EUR"


def kategorien_fragen(umsaetze, konten, buchungen):
    """{umsatz_id: konto_id} für die Umsätze, bei denen die KI eine Kategorie aus der Liste nennt."""
    if not umsaetze:
        return {}
    pfade = _kategorien(konten)
    system = ("Du ordnest Bankumsätze eines Studenten aus Aachen genau einer Kategorie zu. Negative Beträge sind "
              "Ausgaben, positive Einnahmen oder Erstattungen (dann die Ausgabenkategorie des Kaufs). Kategorien:\n"
              + "\n".join(f"- {p}" for p in pfade) + f"\n- {UMBUCHUNG}\n\nFrühere Zuordnungen:\n"
              + "\n".join(_beispiele(konten, buchungen)) +
              "\n\nAntworte je Umsatz mit genau einer Zeile 'Nummer: Kategorie', die Kategorie exakt wie in der Liste, sonst nichts.")
    nutzer = "\n".join(f"{i + 1}. {_zeile(u)}" for i, u in enumerate(umsaetze))
    antwort = frage(system, nutzer, max_tokens=40 * len(umsaetze) + 40)
    nach_pfad = {p.lower(): next(k["id"] for k in konten.values() if k["pfad"] == p) for p in pfade}
    ergebnis = {}
    for m in re.finditer(r"(?m)^\W*(\d+)\s*[:.)-]\s*(.+?)\s*$", antwort):
        i, text = int(m[1]) - 1, m[2].strip().strip("*'\"").lower()
        if 0 <= i < len(umsaetze) and text in nach_pfad:
            ergebnis[umsaetze[i]["id"]] = nach_pfad[text]
        elif 0 <= i < len(umsaetze):
            # Ohne „Ausgaben:“ davor oder nur das letzte Stück: eindeutig zuordnen, sonst nichts.
            passend = [kid for p, kid in nach_pfad.items() if p.endswith(":" + text) or p == text]
            if len(passend) == 1:
                ergebnis[umsaetze[i]["id"]] = passend[0]
    return ergebnis


def duplikate_fragen(umsaetze):
    """{umsatz_id: kandidat} für Umsätze, deren Einkauf laut KI schon im Buch steht."""
    if not umsaetze:
        return {}
    system = ("Du prüfst, ob ein Bankumsatz schon im Haushaltsbuch steht, z. B. per Kassenbon eingetragen. "
              "Derselbe Einkauf kann im Buch einen etwas anderen Betrag (Trinkgeld, Pfand), ein anderes Datum "
              "(ein paar Tage) oder ein anderes Zahlkonto haben. Zwei verschiedene Einkäufe im selben Laden sind "
              "kein Treffer. Antworte je Umsatz mit genau einer Zeile 'Nummer: Buchstabe' der passenden Buchung "
              "oder 'Nummer: keine', sonst nichts.")
    teile = []
    for i, u in enumerate(umsaetze):
        teile.append(f"{i + 1}. Umsatz: {_zeile(u)}")
        for j, k in enumerate(u["kandidaten"]):
            teile.append(f"   {'abc'[j]}) {k['datum']} | {k['beschreibung']} | {k['betrag'] / 100:.2f} EUR | {k['konto']}")
    antwort = frage(system, "\n".join(teile), max_tokens=12 * len(umsaetze) + 20)
    ergebnis = {}
    for m in re.finditer(r"(?mi)^\W*(\d+)\s*[:.)-]\s*([abc]|keine)\b", antwort):
        i = int(m[1]) - 1
        if 0 <= i < len(umsaetze) and m[2].lower() != "keine":
            j = "abc".index(m[2].lower())
            if j < len(umsaetze[i]["kandidaten"]):
                ergebnis[umsaetze[i]["id"]] = umsaetze[i]["kandidaten"][j]
    return ergebnis


def bank_einordnen():
    """Arbeitet alle wartenden Umsätze in Paketen ab. Läuft höchstens einmal gleichzeitig."""
    if not an() or not _bank_lauf.acquire(blocking=False):
        return
    try:
        while True:
            daten = json_laden(bank.BANK, {})
            wartend = [u for u in daten.get("umsaetze", []) if u["status"] == "offen" and u.get("ki") == "wartet"][:PAKET]
            if not wartend:
                zustand["bank"] = ""
                return
            zustand["bank"] = f"KI ordnet {len(wartend)} Umsätze ein …"
            konten, buchungen = buch.lesen()
            kategorie = kategorien_fragen([u for u in wartend if not u.get("sicher")], konten, buchungen)
            doppelt = duplikate_fragen([u for u in wartend if u.get("kandidaten")])
            with lock:
                daten = json_laden(bank.BANK, {})
                nach_id = {u["id"]: u for u in daten.get("umsaetze", [])}
                for alt in wartend:
                    u = nach_id.get(alt["id"])
                    if not u or u["status"] != "offen":
                        continue
                    u["ki"] = "fertig"
                    if alt["id"] in kategorie:
                        u["ki_vorschlag"] = kategorie[alt["id"]]
                    if alt["id"] in doppelt:
                        k = doppelt[alt["id"]]
                        u["ki_duplikat"] = {"id": k["id"], "datum": k["datum"], "beschreibung": k["beschreibung"]}
                if daten.get("automatik", True):
                    bank.automatisch(daten)
                json_speichern(bank.BANK, daten)
    except (OSError, BuchFehler) as ex:
        zustand["bank"] = f"{ex} – neuer Versuch später"
        print(f"KI (Bank): {ex}")
    finally:
        _bank_lauf.release()


def bank_anstossen():
    if an():
        threading.Thread(target=bank_einordnen, daemon=True).start()


# ---------- Monatsbericht ----------

def _vormonat(heute):
    erster = heute.replace(day=1) - timedelta(days=1)
    return f"{erster.year}-{erster.month:02d}"


def bericht_eingabe(monat):
    """Dieselben Daten wie Claude (nur Ausgaben): Summen, Vormonat, Ø der drei Monate davor, Posten, Festes."""
    m = ausgaben.w_monat({"monat": monat})
    jahr, mon = int(monat[:4]), int(monat[5:])
    davor = [f"{jahr - (mon - k <= 0)}-{(mon - k - 1) % 12 + 1:02d}" for k in (1, 2, 3)]
    schnitt = {}
    for d in davor:
        summen, _ = ausgaben.monatssummen(d)
        for k, v in summen.items():
            schnitt[k] = schnitt.get(k, 0) + v / 3
    zeilen = buch.ausgabezeilen(f"{monat}-01", f"{monat}-31")
    posten = sorted(zeilen, key=lambda z: -z["gesamt"])[:60]
    fest = ausgaben.w_wiederkehrend({})["wiederkehrend"]
    teile = [f"Monat {monat}: {m['buchungen']} Ausgaben, gesamt {m['gesamt']:.2f} EUR (Vormonat {m['gesamt_vormonat']:.2f} EUR).",
             "Kategorien (Monat | Vormonat | Ø der 3 Monate davor):"]
    teile += [f"- {k['kategorie']}: {k['betrag']:.2f} | {k['vormonat']:.2f} | {schnitt.get(k['kategorie'], 0) / 100:.2f}"
              for k in m["kategorien"]]
    teile.append("Größte Posten:")
    teile += [f"- {z['datum']} {z['beschreibung'][:50]}: {z['gesamt'] / 100:.2f} EUR ({', '.join(sorted({p['kategorie'] for p in z['positionen']}))})"
              for z in posten]
    if fest:
        teile.append("Feste wiederkehrende Ausgaben:")
        teile += [f"- {w['name']}: {w['betrag']:.2f} EUR alle {w['alle_monate']} Monat(e) ({w['kategorie']})" for w in fest]
    return "\n".join(teile)


# Vorgabe für die Anweisung; in der App unter Konfiguration → „bericht_anweisung“ änderbar.
ANWEISUNG = ("Du bist ein nüchterner, freundlicher Finanzcoach für einen Studenten. Du bekommst nur seine Ausgaben. "
             "Schreibe auf Deutsch ein kurzes Fazit zum Monat (3 bis 5 Sätze, konkrete Zahlen, Vergleich mit Vormonat und "
             "Durchschnitt) und 2 bis 4 konkrete Sparvorschläge mit einem realistischen Betrag pro Monat in Euro. "
             "Schlage nichts vor, was er nicht beeinflussen kann (Miete).")
# Fest angehängt, damit die App die Antwort lesen kann.
FORMAT = ("Antworte nur mit JSON in dieser Form: "
          '{"fazit": "...", "vorschlaege": [{"titel": "...", "betrag": 12.5, "kategorie": "Ausgaben:Lebensmittel"}]} '
          "– Betrag in Euro pro Monat, die Kategorie exakt wie in den Daten oder leer.")
OPTIONEN = DATA / "options.json"  # von Home Assistant geschrieben


def anweisung():
    eigene = str(json_laden(OPTIONEN, {}).get("bericht_anweisung") or "").strip()
    return eigene or ANWEISUNG


def bericht_erstellen(monat):
    system = anweisung() + "\n\n" + FORMAT
    antwort = frage(system, bericht_eingabe(monat), max_tokens=6000, denken=True, timeout=10 * 3600)
    try:
        roh = json.loads(antwort[antwort.index("{"):antwort.rindex("}") + 1])
        text = str(roh["fazit"]).strip()
        vorschlaege = [{"titel": str(v["titel"]), "betrag": v["betrag"], "kategorie": str(v.get("kategorie") or "")}
                       for v in roh.get("vorschlaege") or [] if v.get("titel") and v.get("betrag") is not None]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OSError("KI: Bericht nicht lesbar")
    try:
        ausgaben.bericht_speichern(monat, text, vorschlaege, lokal=True)
    except BuchFehler:
        # Eine erfundene Kategorie: dann die Vorschläge ohne Kategorie.
        ausgaben.bericht_speichern(monat, text, [{**v, "kategorie": ""} for v in vorschlaege], lokal=True)


def bericht_faellig(jetzt):
    if jetzt.day > 7:
        return None
    monat = _vormonat(jetzt.date())
    if (json_laden(ausgaben.BERICHTE, {}).get(monat) or {}).get("lokal"):
        return None
    versuch = json_laden(ZUSTAND, {}).get("bericht_versuch", {})
    if versuch.get("monat") == monat and time.time() - versuch.get("zeit", 0) < 6 * 3600:
        return None
    # Nachts (ab 1 Uhr), damit Bankumsätze vom Monatsende noch drin sind; ab dem 2. auch tagsüber.
    if jetzt.day == 1 and jetzt.hour < 1:
        return None
    return monat


def schleife():
    while True:
        try:
            if an():
                bank_anstossen()
                monat = bericht_faellig(datetime.now())
                if monat and buch.vorhanden():
                    json_speichern(ZUSTAND, {**json_laden(ZUSTAND, {}), "bericht_versuch": {"monat": monat, "zeit": time.time()}})
                    zustand["bericht"] = f"Monatsbericht {monat} läuft"
                    bericht_erstellen(monat)
                    zustand["bericht"] = ""
                    print(f"Lokaler Monatsbericht {monat} fertig")
        except (OSError, BuchFehler) as ex:
            zustand["bericht"] = f"Monatsbericht: {ex}"
            print(f"KI (Bericht): {ex}")
        time.sleep(1800)


def status():
    return " · ".join(t for t in (zustand["bank"], zustand["bericht"]) if t)
