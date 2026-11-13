"""Claude-Anbindung des Desktop-Programms: MCP-Dienst (stdio) und Anmeldung bei Claude Desktop.

Claude Desktop startet das Programm selbst mit `--mcp --schluessel …` und spricht über stdin/stdout
JSON-RPC (Model Context Protocol). Die Werkzeuge sind dieselben wie im Add-on (ausgaben.py): Claude sieht
nur Ausgaben, Kategorien und Kontonamen, bucht Ausgaben, Einnahmen und Umbuchungen erst als Vorschlag
und nach dem OK des Nutzers. Ohne gültigen Schlüssel (Anmeldung im Programm) antwortet jedes Werkzeug
nur mit einem Hinweis. Abmelden macht den Schlüssel ungültig.
"""
import json
import os
import secrets
import sys
import time
from pathlib import Path

import ausgaben
import buch
from buch import BuchFehler, DATA, json_laden, json_speichern, lock

ANMELDUNG = DATA / "claude.json"
SERVERNAME = "budget"
PROTOKOLLE = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

ANLEITUNG = (
    "Budget ist das Haushaltsbuch des Nutzers (GnuCash-Datei auf seinem Rechner). Du siehst nur Ausgaben, "
    "Kategorien und die Namen der Konten, nie Salden, Einnahmen oder das Depot. Buchen geht in zwei Schritten: "
    "erst *_vorschlagen, das Ergebnis dem Nutzer zeigen, erst nach seinem OK ausgabe_bestaetigen. Vor dem ersten "
    "Vorschlag kategorien_und_zahlkonten aufrufen. Kassenbons aus früheren Nachrichten dieses Chats (etwa vom "
    "Handy, wo es diese Werkzeuge nicht gibt) kannst du jetzt nachtragen; meldet ein Vorschlag Duplikate, ist der "
    "Bon wahrscheinlich schon gebucht.")

T = {"type": "string"}
BETRAG = {"type": ["string", "number"], "description": "Betrag in Euro, z. B. 12,34"}
DATUM = {"type": "string", "description": "Datum als JJJJ-MM-TT"}


def schema(eigenschaften=None, pflicht=()):
    return {"type": "object", "properties": eigenschaften or {}, "required": list(pflicht)}


WERKZEUGE = [
    ("kategorien_und_zahlkonten", "kategorien_und_zahlkonten",
     "Listet die gültigen Ausgaben- und Einnahmekategorien und die Namen der Zahlkonten, ohne Salden und Beträge. "
     "Vor dem ersten Vorschlag aufrufen.", schema(), True),
    ("ausgabe_vorschlagen", "ausgabe_vorschlagen",
     "Legt eine Ausgabe, etwa von einem Kassenbon, als Vorschlag an und prüft Summe und Duplikate. Bucht noch nicht: "
     "das Ergebnis dem Nutzer zeigen und erst nach seinem OK mit ausgabe_bestaetigen buchen. Einnahmen über "
     "einnahme_vorschlagen, Umbuchungen über umbuchung_vorschlagen.",
     schema({"datum": DATUM, "beschreibung": {"type": "string", "description": "Händler oder Zweck"},
             "zahlkonto": {"type": "string", "description": "Name des Zahlkontos aus kategorien_und_zahlkonten"},
             "positionen": {"type": "array", "description": "Positionen des Bons, je Kategorie zusammengefasst oder einzeln",
                            "items": schema({"kategorie": {"type": "string", "description": "z. B. Ausgaben:Lebensmittel"},
                                             "betrag": BETRAG, "memo": {"type": "string", "description": "Notiz, z. B. Äpfel"}},
                                            ("kategorie", "betrag"))},
             "bonsumme": {**BETRAG, "description": "Summe laut Bon, wird gegen die Positionen geprüft"}},
            ("datum", "beschreibung", "zahlkonto", "positionen")), False),
    ("einnahme_vorschlagen", "einnahme_vorschlagen",
     "Legt eine Einnahme (Lohn, BAföG o. Ä.) als Vorschlag an und prüft Duplikate. Bucht noch nicht; nach dem OK des "
     "Nutzers mit ausgabe_bestaetigen buchen. Gebuchte Einnahmen und Salden lassen sich nicht lesen.",
     schema({"datum": DATUM, "beschreibung": {"type": "string", "description": "Absender oder Zweck"},
             "zahlkonto": {"type": "string", "description": "Konto, auf dem das Geld eingeht"},
             "kategorie": {"type": "string", "description": "Einnahmekategorie aus kategorien_und_zahlkonten"},
             "betrag": BETRAG, "memo": T}, ("datum", "beschreibung", "zahlkonto", "kategorie", "betrag")), False),
    ("umbuchung_vorschlagen", "umbuchung_vorschlagen",
     "Legt eine Umbuchung zwischen zwei eigenen Zahlkonten als Vorschlag an und prüft Duplikate. Bucht noch nicht; "
     "nach dem OK des Nutzers mit ausgabe_bestaetigen buchen.",
     schema({"datum": DATUM, "von": {"type": "string", "description": "Zahlkonto, von dem das Geld abgeht"},
             "auf": {"type": "string", "description": "Zahlkonto, auf dem das Geld ankommt"}, "betrag": BETRAG,
             "beschreibung": {"type": "string", "description": "Zweck, z. B. Sparen"}, "memo": T},
            ("datum", "von", "auf", "betrag")), False),
    ("ausgabe_bestaetigen", "ausgabe_bestaetigen",
     "Bucht einen Vorschlag (Ausgabe, Einnahme oder Umbuchung), nachdem der Nutzer ihn bestätigt hat. Ein zweiter "
     "Aufruf bucht nicht noch einmal.",
     schema({"vorschlag_id": {"type": "string", "description": "Nummer aus dem Vorschlag"},
             "trotz_duplikat": {"type": "boolean", "description": "Nur true, wenn der Nutzer ein mögliches Duplikat ausdrücklich bestätigt hat"}},
            ("vorschlag_id",)), False),
    ("vorschlag_verwerfen", "vorschlag_verwerfen", "Verwirft einen offenen Vorschlag.",
     schema({"vorschlag_id": T}, ("vorschlag_id",)), False),
    ("ausgaben_lesen", "ausgaben_lesen",
     "Liest gebuchte Ausgaben, optional nach Zeitraum und Suchwort. Erstattungen erscheinen als negative Beträge. "
     "Salden und Einnahmen sind bewusst nicht verfügbar.",
     schema({"von": {"type": "string", "description": "Ab Datum JJJJ-MM-TT"}, "bis": {"type": "string", "description": "Bis Datum JJJJ-MM-TT"},
             "suche": {"type": "string", "description": "Suchwort in Beschreibung oder Notiz"}}), True),
    ("ausgabe_stornieren", "ausgabe_stornieren", "Entfernt eine gebuchte Ausgabe, wenn der Nutzer das verlangt.",
     schema({"id": {"type": "string", "description": "Nummer der Ausgabe aus ausgaben_lesen"}}, ("id",)), False),
    ("monatsauswertung", "monatsauswertung", "Ausgaben eines Monats je Kategorie im Vergleich zum Vormonat.",
     schema({"monat": {"type": "string", "description": "Monat als JJJJ-MM"}}, ("monat",)), True),
    ("wiederkehrende_ausgaben", "wiederkehrende_ausgaben", "Listet die im Programm eingestellten wiederkehrenden Ausgaben.",
     schema(), True),
    ("monatsbericht_speichern", "monatsbericht_speichern",
     "Speichert einen Monats- oder Wochenbericht, damit er im Budget-Programm erscheint.",
     schema({"monat": {"type": "string", "description": "Monat als JJJJ-MM oder Woche als JJJJ-Www, z. B. 2026-W45"},
             "text": {"type": "string", "description": "Nur zwei oder drei kurze Sätze, die Grafiken zeigt das Programm selbst"},
             "vorschlaege": {"type": "array", "description": "Sparvorschläge, Betrag pro Monat, Kategorie freiwillig",
                             "items": schema({"titel": T, "betrag": BETRAG, "kategorie": T}, ("titel", "betrag"))}},
            ("monat", "text")), False),
]


# ---------- Anmeldung ----------

def angemeldet(schluessel):
    gespeichert = json_laden(ANMELDUNG, {}).get("schluessel") or ""
    return bool(gespeichert and schluessel) and secrets.compare_digest(gespeichert.encode(), str(schluessel).encode())


def programm_befehl(schluessel):
    """Wie Claude Desktop dieses Programm als MCP-Dienst startet."""
    if getattr(sys, "frozen", False):
        befehl, args = os.environ.get("APPIMAGE") or sys.executable, []
    else:
        befehl, args = sys.executable, [str(Path(__file__).with_name("budget_desktop.py").resolve())]
    return {"command": befehl, "args": args + ["--mcp", "--schluessel", schluessel]}


def claude_konfigurationen():
    """Die Einstellungsdateien von Claude Desktop, die es auf diesem Rechner gibt (oder geben sollte)."""
    home = Path.home()
    if sys.platform == "darwin":
        ordner = [home / "Library" / "Application Support" / "Claude"]
    elif os.name == "nt":
        ordner = [Path(os.environ.get("APPDATA") or home) / "Claude"]
        lokal = Path(os.environ.get("LOCALAPPDATA") or home)
        ordner += [p / "LocalCache" / "Roaming" / "Claude" for p in (lokal / "Packages").glob("Claude_*")]
    else:
        ordner = [Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "Claude"]
    vorhanden = [o for o in ordner if o.is_dir()]
    return [o / "claude_desktop_config.json" for o in (vorhanden or ordner[:1])]


def konfig_aendern(pfad, eintrag):
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8")) if pfad.exists() else {}
    except ValueError:
        raise BuchFehler(f"Die Einstellungen von Claude Desktop sind nicht lesbar: {pfad}")
    server = daten.setdefault("mcpServers", {})
    if eintrag is None:
        if SERVERNAME not in server:
            return
        server.pop(SERVERNAME)
    else:
        server[SERVERNAME] = eintrag
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".budget.tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, pfad)


def warnung():
    pfad = os.environ.get("APPIMAGE") or sys.executable
    if sys.platform == "darwin" and pfad.startswith("/Volumes/"):
        return "Budget läuft noch aus dem DMG. Erst in „Programme“ ziehen und von dort öffnen, sonst findet Claude es später nicht."
    if getattr(sys, "frozen", False):
        return "Claude startet Budget von diesem Ort aus. Wird das Programm verschoben, hier neu anmelden."
    return ""


def status():
    a = json_laden(ANMELDUNG, {})
    eintrag = {"mcpServers": {SERVERNAME: programm_befehl(a["schluessel"])}} if a.get("schluessel") else None
    return {"angemeldet": bool(eintrag), "seit": a.get("seit", ""), "zuletzt": a.get("zuletzt", ""),
            "konfig": [str(p) for p in claude_konfigurationen()], "warnung": warnung(), "eintrag": eintrag}


def anmelden():
    schluessel = secrets.token_urlsafe(32)
    eintrag = programm_befehl(schluessel)
    with lock:
        for pfad in claude_konfigurationen():
            konfig_aendern(pfad, eintrag)
        json_speichern(ANMELDUNG, {"schluessel": schluessel, "seit": time.strftime("%Y-%m-%d %H:%M")})
    return status()


def abmelden():
    with lock:
        ANMELDUNG.unlink(missing_ok=True)
        for pfad in claude_konfigurationen():
            if pfad.exists():
                konfig_aendern(pfad, None)
    return status()


# ---------- MCP über stdio ----------

def werkzeug_ausfuehren(schluessel, name, argumente):
    if not angemeldet(schluessel):
        return True, ("Budget ist nicht mit Claude verbunden. Der Nutzer muss im Budget-Programm unter "
                      "Einstellungen → Claude „Mit Claude Desktop verbinden“ wählen und Claude Desktop neu starten.")
    funktion = ausgaben.WERKZEUGE.get(next((intern for n, intern, *_ in WERKZEUGE if n == name), ""))
    if not funktion:
        return True, f"Unbekanntes Werkzeug: {name}"
    try:
        ergebnis = funktion(argumente or {})
    except BuchFehler as ex:
        return True, str(ex)
    except (KeyError, TypeError, AttributeError, ValueError):
        return True, "Ungültige Eingabe"
    with lock:
        a = json_laden(ANMELDUNG, {})
        if a.get("schluessel") == schluessel:
            json_speichern(ANMELDUNG, {**a, "zuletzt": time.strftime("%Y-%m-%d %H:%M")})
    return False, json.dumps({"ok": True, **ergebnis}, ensure_ascii=False)


def bearbeiten(nachricht, schluessel):
    """Eine JSON-RPC-Nachricht; liefert die Antwort oder None (bei Benachrichtigungen)."""
    if not isinstance(nachricht, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Ungültige Anfrage"}}
    methode, kennung, p = nachricht.get("method"), nachricht.get("id"), nachricht.get("params") or {}
    if kennung is None:
        return None
    if methode == "initialize":
        gewuenscht = p.get("protocolVersion")
        ergebnis = {"protocolVersion": gewuenscht if gewuenscht in PROTOKOLLE else PROTOKOLLE[1],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "Budget", "version": json_laden(Path(__file__).with_name("version.json"), {}).get("version", "dev")},
                    "instructions": ANLEITUNG}
    elif methode == "ping":
        ergebnis = {}
    elif methode == "tools/list":
        ergebnis = {"tools": [{"name": n, "description": beschreibung, "inputSchema": eingabe,
                               "annotations": {"readOnlyHint": lesend, "destructiveHint": n == "ausgabe_stornieren"}}
                              for n, _, beschreibung, eingabe, lesend in WERKZEUGE]}
    elif methode == "tools/call":
        fehler, text = werkzeug_ausfuehren(schluessel, p.get("name"), p.get("arguments"))
        ergebnis = {"content": [{"type": "text", "text": text}], "isError": fehler}
    else:
        return {"jsonrpc": "2.0", "id": kennung, "error": {"code": -32601, "message": f"Unbekannte Methode: {methode}"}}
    return {"jsonrpc": "2.0", "id": kennung, "result": ergebnis}


def dienst(schluessel):
    """Liest Zeilen von stdin und schreibt Antworten nach stdout, bis Claude Desktop die Verbindung schließt."""
    eingang, ausgang = sys.stdin.buffer, sys.stdout.buffer
    for zeile in eingang:
        if not zeile.strip():
            continue
        try:
            nachricht = json.loads(zeile)
        except ValueError:
            antworten = [{"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Kein JSON"}}]
        else:
            liste = nachricht if isinstance(nachricht, list) else [nachricht]
            antworten = [a for a in (bearbeiten(n, schluessel) for n in liste) if a]
        for a in antworten:
            ausgang.write(json.dumps(a, ensure_ascii=False).encode() + b"\n")
        ausgang.flush()
