"""Lokale KI (App „KI (colibri)“ im selben App-Netz): Einordnen im Reiter Bank und der lokale Monatsbericht.

Nichts davon verlässt den Server. Ohne BUDGET_KI_URL (Desktop-Programm, Tests) ist alles aus.

- Bank: Umsätze mit ki == "wartet" gehen in Paketen an die KI. Sie wählt die Kategorie (neue Empfänger) und
  sagt, ob ein ähnlicher Eintrag im Buch derselbe Einkauf ist. Danach erledigt bank.automatisch() den Rest.
- Monatsbericht: in der Nacht zum 1. (bis zum 7. nachgeholt) für den Vormonat. Anders als Claude bekommt die
  lokale KI auch Einnahmen, Kontostände, Depot und Sparziel (Enzo 20.11.). Er steht im Bericht unter „lokal“;
  ein Bericht von Claude bliebe daneben erhalten.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

import ausgaben
import bank
import buch
import depot
from buch import BuchFehler, DATA, json_laden, json_speichern, lock

URL = os.environ.get("BUDGET_KI_URL", "").rstrip("/")
SUCHE = os.environ.get("BUDGET_SUCHE_URL", "").rstrip("/")  # App „Suche (SearXNG)“
ZUSTAND = DATA / "ki.json"
RUECKMELDUNG = DATA / "rueckmeldung.json"  # 👍/👎 zu Sparvorschlägen: {monat: {titel: 1 | -1}}
MAX_HINWEISE = 2000


def bewerten(monat, titel, wert):
    """Enzos Rückmeldung zu einem Sparvorschlag; 0 nimmt sie zurück."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(monat)) or not str(titel).strip() or wert not in (1, -1, 0):
        raise BuchFehler("Monat, Titel und Bewertung (1, -1 oder 0) angeben")
    with lock:
        daten = json_laden(RUECKMELDUNG, {})
        je_monat = daten.setdefault(monat, {})
        if wert:
            je_monat[str(titel)[:200]] = wert
        else:
            je_monat.pop(str(titel)[:200], None)
        json_speichern(RUECKMELDUNG, {m: v for m, v in daten.items() if v})


def bewertungen(monat):
    return json_laden(RUECKMELDUNG, {}).get(monat, {})


def hinweise():
    return str(json_laden(DATA / "einstellungen.json", {}).get("ki_hinweise") or "").strip()
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


def _beispiele(konten, buchungen, umsaetze=(), anzahl=40):
    """Frühere Zuordnungen (Beschreibung → Kategorie), je Beschreibung einmal.

    Zuerst die, deren Wörter zu den gefragten Umsätzen passen (z. B. derselbe Händler mit anderer Nummer),
    dann die neuesten.
    """
    gesucht = set()
    for u in umsaetze:
        gesucht |= bank.woerter(f"{u['name']} {u['zweck']}")
    gesehen, passend, neueste = set(), [], []
    for b in sorted(buchungen, key=lambda b: b["datum"], reverse=True):
        art, _, pos, einfach = buch.einordnen(konten, b)
        name = b["beschreibung"].strip()
        if art in ("ausgabe", "einnahme") and einfach and len(pos) == 1 and name.lower() not in gesehen:
            gesehen.add(name.lower())
            zeile = f"{name[:60]} -> {konten[pos[0]['konto']]['pfad']}"
            (passend if bank.woerter(name) & gesucht else neueste).append(zeile)
    return (passend[:anzahl // 2] + neueste)[:anzahl]


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
              + "\n".join(_beispiele(konten, buchungen, umsaetze)) +
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
    posten = sorted(zeilen, key=lambda z: -z["gesamt"])[:40]
    # Was hinter den Kategorien steckt: je Kategorie die Händler mit Summe und Anzahl, damit die KI
    # Unterschiede erklären kann statt zu raten.
    je_kat = {}
    for z in zeilen:
        for p in z["positionen"]:
            h = je_kat.setdefault(p["kategorie"], {}).setdefault(z["beschreibung"].strip()[:40], [0, 0])
            h[0] += p["betrag"]
            h[1] += 1
    davor_summe = {}
    for d in davor:
        for z in buch.ausgabezeilen(f"{d}-01", f"{d}-31"):
            for p in z["positionen"]:
                k = davor_summe.setdefault(p["kategorie"], {})
                k[z["beschreibung"].strip()[:40]] = k.get(z["beschreibung"].strip()[:40], 0) + p["betrag"] / 3
    fest = ausgaben.w_wiederkehrend({})["wiederkehrend"]
    teile = [f"Monat {monat}: {m['buchungen']} Ausgaben, gesamt {m['gesamt']:.2f} EUR (Vormonat {m['gesamt_vormonat']:.2f} EUR).",
             "Kategorien (Monat | Vormonat | Ø der 3 Monate davor):"]
    teile += [f"- {k['kategorie']}: {k['betrag']:.2f} | {k['vormonat']:.2f} | {schnitt.get(k['kategorie'], 0) / 100:.2f}"
              for k in m["kategorien"]]
    teile.append("Händler je Kategorie (Summe, Anzahl, Ø pro Monat in den 3 Monaten davor):")
    for k in m["kategorien"]:
        haendler = sorted((je_kat.get(k["kategorie"]) or {}).items(), key=lambda x: -x[1][0])[:6]
        if haendler:
            teile.append(f"- {k['kategorie']}: " + "; ".join(
                f"{n} {v[0] / 100:.2f} ({v[1]}x, Ø {davor_summe.get(k['kategorie'], {}).get(n, 0) / 100:.2f})" for n, v in haendler))
    alt = (json_laden(ausgaben.BERICHTE, {}).get(davor[0]) or {})
    alte_vorschlaege = alt.get("vorschlaege") or (alt.get("lokal") or {}).get("vorschlaege") or []
    if alte_vorschlaege:
        teile.append("Sparvorschläge aus dem Bericht zum Vormonat (prüfe, ob sie gewirkt haben):")
        teile += [f"- {v['titel']}: {v['betrag'] / 100:.2f} EUR ({v.get('kategorie') or 'ohne Kategorie'})" for v in alte_vorschlaege]
    teile.append("Größte Posten:")
    teile += [f"- {z['datum']} {z['beschreibung'][:50]}: {z['gesamt'] / 100:.2f} EUR ({', '.join(sorted({p['kategorie'] for p in z['positionen']}))})"
              for z in posten]
    if fest:
        teile.append("Feste wiederkehrende Ausgaben:")
        teile += [f"- {w['name']}: {w['betrag']:.2f} EUR alle {w['alle_monate']} Monat(e) ({w['kategorie']})" for w in fest]
    # Nur für die lokale KI (bleibt auf dem Server): Einnahmen, Sparquote, Kontostände, Sparziel.
    import budget  # erst hier: budget importiert dieses Modul
    b, st = budget.bericht(monat), budget.stand()
    quote = f"{(b['einnahmen'] - b['gesamt']) / b['einnahmen'] * 100:.0f} %" if b["einnahmen"] > 0 else "-"
    quellen = ", ".join(f"{q['name']} {q['betrag'] / 100:.2f}" for q in b["einnahmequellen"]) or "-"
    teile.append(f"Einnahmen im Monat: {b['einnahmen'] / 100:.2f} EUR ({quellen}); "
                 f"ins Depot: {b['ins_depot'] / 100:.2f} EUR; Sparquote: {quote}.")
    # Rückblick über ein Jahr: Ausgaben, Einnahmen und die größten Kategorien je Monat.
    rueckblick = [f"{jahr - (mon - k <= 0)}-{(mon - k - 1) % 12 + 1:02d}" for k in range(12, 0, -1)]
    zeilen_rb = []
    for r in rueckblick:
        summen, anzahl = ausgaben.monatssummen(r)
        if not anzahl:
            continue
        try:
            ein = budget.monat(r)["einnahmen"]
        except (BuchFehler, KeyError, ValueError):
            ein = 0
        groesste = ", ".join(f"{k.split(':', 1)[-1]} {v / 100:.0f}" for k, v in sorted(summen.items(), key=lambda x: -x[1])[:5])
        zeilen_rb.append(f"- {r}: Ausgaben {sum(summen.values()) / 100:.2f}, Einnahmen {ein / 100:.2f} EUR ({groesste})")
    if zeilen_rb:
        teile.append("Rückblick der letzten 12 Monate (Ausgaben, Einnahmen, größte Kategorien in EUR):")
        teile += zeilen_rb
    berichte = json_laden(ausgaben.BERICHTE, {})
    fruehere = [(k, (v.get("lokal") or {}).get("text") or v.get("text")) for k, v in sorted(berichte.items())
                if "W" not in k and k < monat]
    fruehere = [(k, t) for k, t in fruehere if t][-3:]
    if fruehere:
        teile.append("Deine früheren Fazits (wiederhole keine Tipps und keine Rezepte daraus):")
        teile += [f"- {k}: {t[:600]}" for k, t in fruehere]
    eigene = hinweise()
    if eigene:
        teile.append(f"Enzos Hinweise an dich (beachte sie):\n{eigene}")
    gut, schlecht = [], []
    for m, je in sorted(json_laden(RUECKMELDUNG, {}).items())[-12:]:
        for titel, wert in je.items():
            (gut if wert > 0 else schlecht).append(f"{titel} ({m})")
    if gut:
        teile.append("Sparvorschläge, die Enzo gut fand (in diese Richtung weiterdenken): " + "; ".join(gut))
    if schlecht:
        teile.append("Sparvorschläge, die Enzo unpassend fand (so etwas nicht mehr vorschlagen): " + "; ".join(schlecht))
    teile.append("Kontostände heute:")
    teile += [f"- {k['pfad']}: {k['saldo'] / 100:.2f} EUR" for k in st["konten"]]
    teile.append(f"Vermögen: {st['vermoegen'] / 100:.2f} EUR, davon im Depot (zählt als erspart): {st['erspartes'] / 100:.2f} EUR.")
    try:
        dep = depot.uebersicht(warten=False)
    except (OSError, BuchFehler, ValueError, KeyError):
        dep = {"positionen": []}
    mit_wert = [z for z in dep["positionen"] if "wert" in z]
    if mit_wert:
        gesamt = sum(z["wert"] for z in mit_wert) or 1
        teile.append(f"Depot (Kurs von Tradegate/L&S): Wert {dep['wert'] / 100:.2f} EUR, Einstand {dep['einstand'] / 100:.2f} EUR"
                     + (f", {dep['prozent']:+.1f} %" if dep.get("prozent") is not None else "") + ". Positionen:")
        teile += [f"- {z['name']} ({z['isin']}): {z['wert'] / 100:.2f} EUR, {z['wert'] / gesamt * 100:.0f} % des Depots, "
                  f"{z['prozent']:+.1f} % seit Kauf" for z in sorted(mit_wert, key=lambda z: -z["wert"])]
    if st["ziel"]:
        z = st["ziel"]
        teile.append(f"Sparziel: {z['betrag'] / 100:.2f} EUR Vermögen bis {z['datum']}, fehlt {z['fehlt'] / 100:.2f} EUR, "
                     f"also {z['pro_monat'] / 100:.2f} EUR pro Monat.")
    return "\n".join(teile)


# Vorgabe für die Anweisung; in der App unter Konfiguration → „bericht_anweisung“ änderbar.
ANWEISUNG = 'Du schreibst Enzos Monatsbericht für sein Budget-Panel (er ist Student in Aachen). Sprich ihn mit du an. Das Panel zeichnet Verlauf, Kategorien, Händler und größte Posten selbst: wiederhole diese Zahlen nicht, sondern sag, was die Grafiken nicht von selbst zeigen. Fazit: höchstens drei kurze Sätze mit der wichtigsten Erkenntnis, auch zu Einnahmen, Sparquote, Kontoständen, Depot und Sparziel; wenn im Monat auffällig wenig gebucht ist, sag das. Danach eine eigene Zeile mit einem fleischfreien Rezepttipp in einem Satz. Dazu 2 bis 3 konkrete, realistische Sparvorschläge, Titel mit höchstens vier Wörtern, Betrag in Euro pro Monat. Regeln: ehrlich, ohne Vorwürfe. Fahrradteile nicht kommentieren. Einzelne Fast-Food-Besuche nicht hochrechnen und nicht als Muster werten. Fleisch fürs Wochenende ist gewollt und kein Sparpotenzial. Das Claude-Abo nie als Sparvorschlag. Die Wochenzeitung der Freitag ist gekündigt: nicht als Sparvorschlag nennen. Einkäufe bei dm sind bewusst, dort kein Sparpotenzial sehen. Getränke kosten wegen der Studentenverbindung mehr als üblich, Essen weniger. Bargeldabhebungen sind normale Ausgaben. Miete nicht als Sparpotenzial. Zum Depot darfst du Tipps geben (Aufteilung, Kosten, Sparrate, Risiko, Notgroschen), sachlich begründet und ohne Kursprognosen.'
# Fest angehängt, damit die App die Antwort lesen kann.
FORMAT = ("Antworte nur mit JSON in dieser Form: "
          '{"fazit": "...", "vorschlaege": [{"titel": "...", "betrag": 12.5, "kategorie": "Ausgaben:Lebensmittel"}]} '
          "– Betrag in Euro pro Monat, die Kategorie exakt wie in den Daten oder leer.")
OPTIONEN = DATA / "options.json"  # von Home Assistant geschrieben


def anweisung():
    eigene = str(json_laden(OPTIONEN, {}).get("bericht_anweisung") or "").strip()
    return eigene or ANWEISUNG


def _bericht_lesen(antwort):
    try:
        roh = json.loads(antwort[antwort.index("{"):antwort.rindex("}") + 1])
        text = str(roh["fazit"]).strip()
        vorschlaege = [{"titel": str(v["titel"]), "betrag": v["betrag"], "kategorie": str(v.get("kategorie") or "")}
                       for v in roh.get("vorschlaege") or [] if v.get("titel") and v.get("betrag") is not None]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise OSError("KI: Bericht nicht lesbar")
    if not text:
        raise OSError("KI: Bericht leer")
    return text, vorschlaege


PRUEFUNG = ("Du prüfst den Entwurf eines Monatsberichts gegen die Daten und die Anweisung. Korrigiere jede Zahl, die "
            "nicht zu den Daten passt, streiche Aussagen, die die Daten nicht belegen, und alles, was gegen die Regeln der "
            "Anweisung verstößt. Sparvorschläge müssen realistisch sein (nicht mehr als die Ausgaben der Kategorie). "
            "Ist der Entwurf gut, gib ihn unverändert zurück.")


def suchen(anfrage, anzahl=3):
    """Die ersten Treffer (Titel, Auszug, Adresse) über die eigene SearXNG-App; ohne sie oder bei Fehlern leer."""
    if not SUCHE:
        return []
    url = f"{SUCHE}/search?" + urllib.parse.urlencode({"q": anfrage, "format": "json", "language": "de-DE"})
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            treffer = json.load(r).get("results") or []
    except (OSError, ValueError):
        return []
    def kurz(text, laenge):
        return " ".join(str(text or "").split())[:laenge]
    return [f"{kurz(t.get('title'), 120)}: {kurz(t.get('content'), 300)} ({kurz(t.get('url'), 100)})"
            for t in treffer[:anzahl] if t.get("content")]


def netz_fakten():
    """Aktuelles aus dem Netz für den Bericht. In die Suche gehen nur allgemeine Fragen und die ISINs, nie Beträge."""
    if not SUCHE:
        return ""
    heute = date.today()
    fragen = [f"Tagesgeld Zinsen Vergleich {heute.year}", "EZB Leitzins aktuell", f"Inflationsrate Deutschland {heute.year}"]
    try:
        positionen = [z for z in depot.uebersicht(warten=False)["positionen"] if z.get("isin")]
    except (OSError, BuchFehler, ValueError, KeyError):
        positionen = []
    fragen += [f"{z['isin']} {z['name']} TER Fondsgröße" for z in positionen[:8]]
    teile = []
    for f in fragen:
        treffer = suchen(f)
        if treffer:
            teile.append(f"Suche „{f}“:")
            teile += [f"- {t}" for t in treffer]
    if not teile:
        return ""
    return (f"Aus dem Netz (Suchergebnisse vom {heute.isoformat()}, können ungenau oder veraltet sein; nur nutzen, "
            "wenn sie zueinander passen, und dann die Quelle nennen):\n" + "\n".join(teile))


def bericht_erstellen(monat):
    """Zwei Durchgänge: Entwurf, dann Prüfung gegen die Daten. Darf zusammen Stunden dauern."""
    system = anweisung() + "\n\n" + FORMAT
    daten = bericht_eingabe(monat)
    netz = netz_fakten()
    if netz:
        daten += "\n\n" + netz
    text, vorschlaege = _bericht_lesen(frage(system, daten, max_tokens=6000, denken=True, timeout=10 * 3600))
    entwurf = json.dumps({"fazit": text, "vorschlaege": vorschlaege}, ensure_ascii=False)
    try:
        text, vorschlaege = _bericht_lesen(frage(
            PRUEFUNG + "\n\nAnweisung:\n" + anweisung() + "\n\n" + FORMAT,
            f"Daten:\n{daten}\n\nEntwurf:\n{entwurf}", max_tokens=6000, denken=True, timeout=10 * 3600))
    except OSError as ex:
        print(f"KI (Bericht): Prüfung fehlgeschlagen, Entwurf bleibt ({ex})")
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
