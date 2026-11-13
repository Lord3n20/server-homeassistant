"""Budget als Programm für macOS, Windows und Linux, ohne Home Assistant.

Derselbe Server wie im Add-on, aber nur auf 127.0.0.1 mit zufälligem Port und einem Sitzungs-Cookie, das nur
dieses Programm kennt (andere Webseiten im Browser kommen so nicht an die Daten). Ohne den Reiter Bank.
Claude kommt über Claude Desktop dran: das startet dieses Programm mit --mcp als MCP-Dienst (claude_mcp.py),
angemeldet wird unter Einstellungen → Claude. Das Fenster kommt von pywebview (macOS/Windows); fehlt es, öffnet sich der
Browser, und das Programm beendet sich, wenn das Panel ein paar Minuten nicht mehr gefragt hat.

Daten: macOS ~/Library/Application Support/Budget, Windows %APPDATA%\\Budget, Linux ~/.local/share/budget
(oder BUDGET_DATA). BUDGET_BROWSER=1 erzwingt den Browser.
"""
import http.cookies
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path


def datenordner():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Budget"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home()) / "Budget"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "budget"


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # buch.py & Co. liegen eine Ebene höher
os.environ.setdefault("BUDGET_DATA", str(datenordner()))
try:  # Zertifikate für die Kursabfrage, falls das gebündelte Python keine findet
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

import bank  # noqa: E402
import buch  # noqa: E402
import budget  # noqa: E402
import claude_mcp  # noqa: E402

KEKS = "budget_sitzung"
LEERLAUF = 300  # Sekunden ohne Anfrage, nach denen das Programm im Browser-Betrieb endet
INSTANZ = buch.DATA / "instanz.json"


class DesktopHandler(budget.Handler):
    token = ""
    browser = False
    zuletzt = time.monotonic()

    def keks_passt(self):
        try:
            keks = http.cookies.SimpleCookie(self.headers.get("Cookie") or "")
        except http.cookies.CookieError:
            return False
        return KEKS in keks and secrets.compare_digest(keks[KEKS].value.encode(), self.token.encode())

    def erlaubt(self):
        DesktopHandler.zuletzt = time.monotonic()
        if self.client_address[0] in ("127.0.0.1", "::1") and self.keks_passt():
            return True
        self.fehler(403, "Bitte Budget neu öffnen")
        return False

    def do_GET(self):
        teile = urllib.parse.urlsplit(self.path)
        if teile.path != "/start":
            return super().do_GET()
        t = urllib.parse.parse_qs(teile.query).get("t", [""])[0]
        if self.client_address[0] not in ("127.0.0.1", "::1") or not secrets.compare_digest(t.encode(), self.token.encode()):
            return self.fehler(403, "Bitte Budget neu öffnen")
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", f"{KEKS}={self.token}; Path=/; HttpOnly; SameSite=Strict")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def lesen(self):
        pfad = urllib.parse.urlsplit(self.path).path
        if pfad == "/api/puls":
            return self.antwort(200, {"ok": True})
        if pfad == "/api/claude":
            return self.antwort(200, claude_mcp.status())
        return super().lesen()

    def anlegen(self):
        pfad = urllib.parse.urlsplit(self.path).path
        if pfad == "/api/claude/anmelden":
            return self.antwort(200, claude_mcp.anmelden())
        if pfad == "/api/claude/abmelden":
            return self.antwort(200, claude_mcp.abmelden())
        return super().anlegen()

    def panel(self):
        text = budget.PANEL.read_text(encoding="utf-8")
        text = text.replace("Sie bleibt auf diesem Server, Claude bekommt nur Ausgaben zu sehen.",
                            "Sie bleibt auf diesem Rechner.")
        text = text.replace('<div data-seite="einstellungen">', '<div data-seite="einstellungen">' + CLAUDE_KARTE, 1)
        text = text.replace("</body>", CLAUDE_SKRIPT + "\n</body>", 1)
        zusatz = "<style>#bankReiter { display: none; } #clJson { white-space: pre-wrap; word-break: break-all; font-size: 0.8rem; }"
        zusatz += " #clAnweisung { width: 100%; min-height: 7em; font: inherit; }</style>"
        if self.browser:
            zusatz += '<script>setInterval(() => fetch("api/puls").catch(() => {}), 30000);</script>'
        return text.replace("</head>", zusatz + "\n</head>", 1).encode()


PROJEKT_ANWEISUNG = (
    "Kassenbons: Lies jeden Bon (Datum, Händler, Positionen, Summe, wie bezahlt). Hast du die Budget-Werkzeuge "
    "(Claude Desktop am Rechner), ordne die Kategorien zu, leg einen Vorschlag an und buche nach meinem OK. Hast du "
    "sie nicht (Handy, Web), schreib den Bon kurz als Liste und vermerke „noch nicht eingetragen“. Sage ich später am "
    "Rechner „eintragen“, trägst du alle Bons aus diesem Chat ein, die noch nicht eingetragen sind; meldet ein "
    "Vorschlag ein mögliches Duplikat, frag nach.")

CLAUDE_KARTE = f"""
      <section class="card" id="claudeKarte">
        <h2>Claude</h2>
        <p class="gedaempft" id="clStatus"></p>
        <p class="gedaempft" id="clWarn"></p>
        <div><button class="primaer" id="clAn">Mit Claude Desktop verbinden</button> <button id="clAb" hidden>Abmelden</button></div>
        <details style="margin-top:10px"><summary>Kassenbons vom Handy</summary>
          <p class="gedaempft">Claude auf dem Handy kommt nicht an Budget. Leg in Claude ein Projekt „Budget“ an, gib ihm diese
          Anweisung und lad die Bons dort im Chat hoch. Am Rechner denselben Chat in Claude Desktop öffnen und „eintragen“ schreiben.</p>
          <textarea id="clAnweisung" readonly>{PROJEKT_ANWEISUNG}</textarea></details>
        <details id="clJsonBox" hidden><summary>Für andere MCP-Programme</summary><pre id="clJson"></pre></details>
      </section>"""

CLAUDE_SKRIPT = """<script>
const claudeLaden = sicher(async () => {
  const s = await api("api/claude");
  $("#clStatus").textContent = s.angemeldet
    ? `Verbunden seit ${s.seit}${s.zuletzt ? `, zuletzt benutzt ${s.zuletzt}` : ""}. Fehlt Budget in Claude Desktop, Claude Desktop einmal ganz beenden und neu öffnen.`
    : "Nicht verbunden. Claude sieht danach nur Ausgaben, Kategorien und Kontonamen, keine Salden und keine Einnahmen. Gebucht wird erst nach deinem OK.";
  $("#clAn").textContent = s.angemeldet ? "Neu verbinden" : "Mit Claude Desktop verbinden";
  $("#clAb").hidden = !s.angemeldet;
  $("#clWarn").textContent = s.warnung || "";
  $("#clJsonBox").hidden = !s.eintrag;
  $("#clJson").textContent = s.eintrag ? JSON.stringify(s.eintrag, null, 2) : "";
});
$("#clAn").onclick = sicher(async () => {
  await api("api/claude/anmelden", json("POST", {}));
  toast("Verbunden. Claude Desktop jetzt ganz beenden und neu öffnen.");
  await claudeLaden();
});
$("#clAb").onclick = sicher(async () => {
  await api("api/claude/abmelden", json("POST", {}));
  toast("Abgemeldet");
  await claudeLaden();
});
claudeLaden();
</script>"""


def laufende_instanz():
    """Adresse eines schon laufenden Budget mit denselben Daten, sonst None."""
    try:
        info = json.loads(INSTANZ.read_text())
        basis = f"http://127.0.0.1:{int(info['port'])}"
        req = urllib.request.Request(basis + "/api/status", headers={"Cookie": f"{KEKS}={info['token']}"})
        with urllib.request.urlopen(req, timeout=2) as r:
            if r.status == 200:
                return f"{basis}/start?t={info['token']}"
    except (OSError, ValueError, KeyError):
        pass
    return None


def browser_oeffnen(url):
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        # PyInstaller biegt die Bibliothekspfade um; der Browser soll die des Systems nehmen.
        alt = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        os.environ.pop("LD_LIBRARY_PATH", None)
        if alt:
            os.environ["LD_LIBRARY_PATH"] = alt
    if not webbrowser.open(url):
        print(f"Budget läuft unter {url}")


def beenden(code=0):
    with buch.lock:  # nicht mitten im Schreiben aufhören
        INSTANZ.unlink(missing_ok=True)
        os._exit(code)


def fenster(url):
    """True, wenn ein eigenes Fenster lief (und geschlossen wurde)."""
    if os.environ.get("BUDGET_BROWSER"):
        return False
    try:
        import webview
    except ImportError:
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window("Budget", url, width=1500, height=920, min_size=(380, 500))
        webview.start()
        return True
    except Exception as ex:  # z. B. fehlende WebView2-Laufzeit unter Windows
        print(f"Kein Fenster möglich ({ex}), nehme den Browser")
        return False


def main():
    buch.DATA.mkdir(parents=True, exist_ok=True)
    if "--mcp" in sys.argv:
        i = sys.argv.index("--schluessel") + 1 if "--schluessel" in sys.argv else 0
        return claude_mcp.dienst(sys.argv[i] if 0 < i < len(sys.argv) else "")
    schon = laufende_instanz()
    if schon:
        browser_oeffnen(schon)
        return
    DesktopHandler.token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", 0), DesktopHandler)
    port = server.server_address[1]
    budget.json_speichern(INSTANZ, {"port": port, "token": DesktopHandler.token, "pid": os.getpid()})
    threading.Thread(target=budget.faellige_schleife, daemon=True).start()
    bank.aufraeumen()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/start?t={DesktopHandler.token}"
    if fenster(url):
        beenden()
    DesktopHandler.browser = True
    DesktopHandler.zuletzt = time.monotonic()
    browser_oeffnen(url)
    try:
        while time.monotonic() - DesktopHandler.zuletzt < LEERLAUF:
            time.sleep(10)
    except KeyboardInterrupt:
        pass
    beenden()


if __name__ == "__main__":
    main()
