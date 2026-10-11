#!/usr/bin/env python3
"""Minecraft console as a Home Assistant sidebar panel (Ingress).

Shows logs/latest.log live and sends commands with rcon-cli. Only answers
requests coming through Home Assistant's Ingress proxy, so the panel is as
protected as Home Assistant itself (admins only, see panel_admin).
"""
import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8099
INGRESS_IP = "172.30.32.2"
LOG = "/data/logs/latest.log"
FIRST_BYTES = 96 * 1024
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|§[0-9a-fk-or]")
# Every rcon-cli call (also our own) logs two of these lines.
NOISE = re.compile(r"\[RCON (Listener|Client)[^\]]*\]: Thread RCON Client")


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
    lines = [ln for ln in ANSI.sub("", data.decode("utf-8", "replace")).splitlines() if not NOISE.search(ln)]
    return {"pos": end, "text": "\n".join(lines), "reset": reset}


def run_command(cmd):
    try:
        r = subprocess.run(["rcon-cli", cmd], capture_output=True, text=True, timeout=30, cwd="/data")
    except subprocess.TimeoutExpired:
        return False, "Keine Antwort vom Server (Zeitlimit)."
    except OSError as e:
        return False, f"rcon-cli nicht startbar: {e}"
    out = ANSI.sub("", (r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        return False, out or "Server nicht erreichbar – läuft er noch oder startet gerade?"
    return True, out


PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Minecraft-Konsole</title>
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
<h1>Minecraft-Konsole</h1>
<button data-cmd="list">Spieler</button>
<button data-cmd="tps">TPS</button>
<button data-cmd="chunky progress">Chunky</button>
<button data-cmd="save-all">Speichern</button>
<label><input type="checkbox" id="quiet" checked>Chunky-Meldungen ausblenden</label>
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
    if (quiet.checked && /\\[Chunky\\] Task running/.test(ln)) continue;
    add(ln, /\\/(ERROR|FATAL)\\]|Exception/.test(ln) ? "e" : /\\/WARN\\]/.test(ln) ? "w" : "");
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
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
