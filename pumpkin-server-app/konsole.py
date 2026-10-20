#!/usr/bin/env python3
"""Pumpkin console as a Home Assistant sidebar panel (Ingress), plus the add-on's helper jobs.

Shows logs/latest.log live and sends commands over RCON. Only answers requests
coming through Home Assistant's Ingress proxy, so the panel is as protected as
Home Assistant itself (admins only, see panel_admin).

In the background it makes whitelist and ops match the add-on options and
pre-generates the Overworld, Nether and End one after another (PumpkinPregen).
"""
import json
import os
import re
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8099
INGRESS_IP = "172.30.32.2"
DATA = "/data"
LOG = DATA + "/logs/latest.log"
FIRST_BYTES = 96 * 1024
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|§[0-9a-fk-or]")
RCON = ("127.0.0.1", 25575)
RCON_LOCK = threading.Lock()


def read_log(pos):
    """New log text from byte `pos` on (-1: the last part); restarts when the log was rotated."""
    try:
        size = os.path.getsize(LOG)
    except OSError:
        return {"pos": 0, "text": "", "reset": True}
    reset = pos < 0 or pos > size
    start = max(0, size - FIRST_BYTES) if pos < 0 else (0 if pos > size else pos)
    with open(LOG, "rb") as f:
        f.seek(start)
        data = f.read(size - start)
    if start and reset:
        data = data.split(b"\n", 1)[-1]  # begin at a whole line
    end = start + len(data)
    if data and not data.endswith(b"\n"):  # leave a half-written line for the next poll
        cut = data.rfind(b"\n") + 1
        end -= len(data) - cut
        data = data[:cut]
    return {"pos": end, "text": ANSI.sub("", data.decode("utf-8", "replace")).rstrip("\n"), "reset": reset}


def _packet(sock, req_id, kind, body):
    data = struct.pack("<ii", req_id, kind) + body.encode() + b"\0\0"
    sock.sendall(struct.pack("<i", len(data)) + data)


def _read(sock):
    def exact(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("RCON-Verbindung beendet")
            buf += chunk
        return buf

    (length,) = struct.unpack("<i", exact(4))
    data = exact(length)
    req_id, _kind = struct.unpack("<ii", data[:8])
    return req_id, data[8:-2].decode("utf-8", "replace")


def rcon(cmd):
    """Runs one command over RCON and returns its output; raises OSError if the server is not reachable."""
    with RCON_LOCK, socket.create_connection(RCON, timeout=30) as sock:
        _packet(sock, 1, 3, os.environ["RCON_PASSWORD"])
        if _read(sock)[0] == -1:
            raise ConnectionError("RCON-Passwort abgelehnt")
        _packet(sock, 2, 2, cmd)
        return ANSI.sub("", _read(sock)[1]).strip()


def run_command(cmd):
    try:
        return True, rcon(cmd)
    except socket.timeout:
        return False, "Keine Antwort vom Server (Zeitlimit)."
    except OSError:
        return False, "Server nicht erreichbar – läuft er noch oder startet gerade?"


def say(text):
    print(f"[Konsole] {text}", flush=True)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def sync_list(option, path, add, remove):
    """Makes whitelist.json / ops.json hold exactly the names from the add-on option."""
    wanted = {n.lower(): n for n in OPTIONS.get(option) or []}
    present = {e.get("name", "").lower(): e.get("name", "") for e in load_json(path, [])}
    for key, name in wanted.items():
        if key not in present:
            say(f"{add} {name}: {rcon(f'{add} {name}')}")
    for key, name in present.items():
        if key and key not in wanted:
            say(f"{remove} {name}: {rcon(f'{remove} {name}')}")


# Pregen order and option per dimension; radius in blocks around 0 0
DIMENSIONS = [
    ("minecraft:overworld", "pregen_oberwelt"),
    ("minecraft:the_nether", "pregen_nether"),
    ("minecraft:the_end", "pregen_end"),
]


def pregen():
    """Pre-generates one dimension after the other; finished radii are kept per world."""
    state_path = f"{DATA}/{OPTIONS.get('welt') or 'world'}/stierzunge-pregen.json"
    done = load_json(state_path, {})
    for dim, option in DIMENSIONS:
        radius = int(OPTIONS.get(option) or 0)
        if radius <= 0 or done.get(dim, 0) >= radius:
            continue
        start = f"pregen start {radius} 0 0 {dim.split(':')[1]}"
        out = rcon(start)
        say(f"Pregen {dim} r{radius}: {out}")
        if "Started pregenerating" not in out:
            continue
        while True:
            time.sleep(60)
            status = rcon("pregen status")
            if status.startswith("Pregeneration complete"):
                done[dim] = radius
                with open(state_path, "w") as f:
                    json.dump(done, f)
                say(f"Pregen {dim} fertig.")
                break
            if "cancelled" in status:
                say("Pregen von Hand abgebrochen – bis zum nächsten Neustart kein automatisches Pregen.")
                return
            if status.startswith("PumpkinPregen stopped") or status.startswith("No pregeneration"):
                say(f"Pregen {dim} gestoppt ({status}), neuer Versuch.")
                say(rcon(start))


def helper():
    """Waits for the server, then syncs whitelist/ops and runs the pregen."""
    while True:
        try:
            rcon("list")
            break
        except OSError:
            time.sleep(5)
    try:
        sync_list("whitelist", f"{DATA}/data/whitelist.json", "whitelist add", "whitelist remove")
        sync_list("ops", f"{DATA}/data/ops.json", "op", "deop")
        pregen()
    except OSError as e:
        say(f"Hintergrundaufgabe abgebrochen: {e}")


PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pumpkin-Konsole</title>
<style>
:root{--bg:#f6f7f9;--panel:#ffffff;--text:#1d2329;--muted:#66707a;--line:#dde1e6;--accent:#2e7d32;
--warn:#a15c00;--err:#c62828;--cmd:#1565c0;--term:#fbfcfd}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--panel:#1a1e23;--text:#e3e6ea;--muted:#8d96a0;
--line:#2b3138;--accent:#66bb6a;--warn:#ffb74d;--err:#ef5350;--cmd:#64b5f6;--term:#0d1013}}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--text);font:15px/1.4 system-ui,sans-serif;display:flex;flex-direction:column}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{font-size:17px;margin:0 auto 0 0;font-weight:600}
button{font:inherit;font-size:14px;padding:6px 12px;border-radius:8px;border:1px solid var(--line);
background:var(--panel);color:var(--text);cursor:pointer}
button:hover{border-color:var(--accent)}
label{font-size:14px;color:var(--muted);display:flex;gap:6px;align-items:center}
#log{flex:1;overflow:auto;margin:0;padding:10px 16px;background:var(--term);
font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word}
#log .w{color:var(--warn)} #log .e{color:var(--err)}
#log .c{color:var(--cmd);font-weight:600;margin-top:6px}
#log .r{color:var(--text);border-left:3px solid var(--cmd);padding-left:8px;margin-bottom:6px}
#log .f{color:var(--err);border-left:3px solid var(--err);padding-left:8px;margin-bottom:6px}
form{display:flex;gap:8px;padding:10px 16px calc(10px + env(safe-area-inset-bottom));border-top:1px solid var(--line);background:var(--panel)}
#cmd{flex:1;min-width:0;font:14px ui-monospace,Menlo,Consolas,monospace;padding:8px 10px;border-radius:8px;
border:1px solid var(--line);background:var(--term);color:var(--text)}
#cmd:focus{outline:2px solid var(--accent);outline-offset:-1px}
#send{background:var(--accent);border-color:var(--accent);color:#fff}
#jump{position:fixed;right:24px;bottom:76px;display:none}
</style></head><body>
<header>
<h1>Pumpkin-Konsole</h1>
<button data-cmd="list">Spieler</button>
<button data-cmd="tps">TPS</button>
<button data-cmd="pregen status">Pregen</button>
<button data-cmd="save-all">Speichern</button>
<label><input type="checkbox" id="quiet" checked>Pregen-Meldungen ausblenden</label>
</header>
<div id="log"></div>
<button id="jump">↓ Neueste</button>
<form id="f" autocomplete="off">
<input id="cmd" placeholder="Befehl ohne /, z. B. say Hallo" autocapitalize="off" spellcheck="false">
<button id="send">Senden</button>
</form>
<script>
const log = document.getElementById("log"), cmd = document.getElementById("cmd"),
      quiet = document.getElementById("quiet"), jump = document.getElementById("jump");
let pos = -1, hist = [], hi = 0;
try { hist = JSON.parse(localStorage.getItem("mc-hist") || "[]"); quiet.checked = localStorage.getItem("mc-quiet") !== "0"; } catch (e) {}
hi = hist.length;
const atBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 40;
function add(text, cls) {
  const keep = atBottom(), d = document.createElement("div");
  d.textContent = text; if (cls) d.className = cls;
  log.appendChild(d);
  while (log.childElementCount > 4000) log.firstChild.remove();
  if (keep) log.scrollTop = log.scrollHeight;
}
function addLines(text) {
  for (const ln of text.split("\\n")) {
    if (!ln) continue;
    if (quiet.checked && /PumpkinPregen|forceload|Force ?load/i.test(ln)) continue;
    add(ln, /\\[(ERROR|FATAL)\\]|panicked/.test(ln) ? "e" : /\\[WARN\\]/.test(ln) ? "w" : "");
  }
}
async function poll() {
  try {
    const r = await fetch("log?pos=" + pos, {cache: "no-store"});
    const j = await r.json();
    if (j.reset && pos !== -1) add("— Log neu begonnen (Server-Neustart) —", "c");
    pos = j.pos; if (j.text) addLines(j.text);
  } catch (e) {}
  setTimeout(poll, 1500);
}
async function send(c) {
  c = c.trim().replace(/^\\//, ""); if (!c) return;
  if (hist[hist.length - 1] !== c) { hist.push(c); hist = hist.slice(-100); }
  hi = hist.length; try { localStorage.setItem("mc-hist", JSON.stringify(hist)); } catch (e) {}
  add("> " + c, "c"); log.scrollTop = log.scrollHeight;
  try {
    const r = await fetch("befehl", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({cmd: c})});
    const j = await r.json();
    add(j.text || "(keine Ausgabe)", j.ok ? "r" : "f");
  } catch (e) { add("Konsole nicht erreichbar: " + e, "f"); }
}
document.getElementById("f").onsubmit = e => { e.preventDefault(); send(cmd.value); cmd.value = ""; };
document.querySelectorAll("[data-cmd]").forEach(b => b.onclick = () => send(b.dataset.cmd));
cmd.onkeydown = e => {
  if (e.key === "ArrowUp" && hi > 0) { cmd.value = hist[--hi]; e.preventDefault(); }
  else if (e.key === "ArrowDown") { hi = Math.min(hist.length, hi + 1); cmd.value = hist[hi] || ""; e.preventDefault(); }
};
quiet.onchange = () => { try { localStorage.setItem("mc-quiet", quiet.checked ? "1" : "0"); } catch (e) {} };
log.onscroll = () => { jump.style.display = atBottom() ? "none" : "block"; };
jump.onclick = () => { log.scrollTop = log.scrollHeight; };
poll(); cmd.focus();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self):
        if self.client_address[0] == INGRESS_IP:
            return True
        self._send(403, "nur über Home Assistant", "text/plain")
        return False

    def do_GET(self):  # noqa: N802
        if not self._allowed():
            return
        path, _, query = self.path.partition("?")
        if path.endswith("/log"):
            m = re.search(r"(?:^|&)pos=(-?\d+)", query)
            return self._send(200, read_log(int(m.group(1)) if m else -1))
        self._send(200, PAGE, "text/html")

    def do_POST(self):  # noqa: N802
        if not self._allowed():
            return
        if not self.path.split("?")[0].endswith("/befehl"):
            return self._send(404, {"ok": False, "text": "unbekannt"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            c = str(json.loads(self.rfile.read(min(n, 4096)) or b"{}").get("cmd", "")).strip()
        except ValueError:
            c = ""
        if not c or "\n" in c:
            return self._send(400, {"ok": False, "text": "Kein gültiger Befehl."})
        ok, text = run_command(c)
        self._send(200, {"ok": ok, "text": text})


if __name__ == "__main__":
    with open(DATA + "/options.json") as f:
        OPTIONS = json.load(f)
    threading.Thread(target=helper, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
