"""Pförtner vor colibri: lädt das Modell einmal herunter, startet die Engine nur bei Bedarf und hält RAM frei.

- Port 8100, nur aus dem App-Netz von Home Assistant (172.30.32.0/23), also z. B. von der Budget-App.
- POST /v1/chat/completions: wie OpenAI. Anfragen laufen nacheinander; die Engine startet beim ersten
  Aufruf und hört nach `leerlauf_minuten` ohne Anfrage wieder auf.
- GET /status: Modell, Download, ob die Engine läuft.
- Gedrosselt: wenige Threads, nice 19, ionice idle, nur `experten_je_schicht` Experten im RAM (der Rest
  kommt bei Bedarf von der SSD). Gestartet wird nur, wenn danach noch `ram_reserve_gb` frei bleiben;
  fällt der freie Speicher während des Laufs unter 60 % der Reserve, wird die Engine beendet.
"""
import ipaddress
import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATEI = "Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf"
QUELLE = f"https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/{DATEI}"
GROESSE = 13_210_000_000  # ungefähr, nur für die Fortschrittsanzeige
ORDNER = Path(os.environ.get("KI_ORDNER", "/share/colibri"))
GGUF = ORDNER / DATEI
MODELL = ORDNER / DATEI.replace(".gguf", ".colibri")
COLI = os.environ.get("KI_COLI", "/app/c/coli")
PORT = 8100
ENGINE_PORT = 8001
BEDARF_GB = 2.8  # gemessen: 2,5 GB RSS mit 4 Experten je Schicht, dazu das Gateway
NETZ = ipaddress.ip_network("172.30.32.0/23")
MAX_KOERPER = 2 * 1024 * 1024


def optionen():
    try:
        o = json.loads(Path("/data/options.json").read_text())
    except (OSError, ValueError):
        o = {}
    return {"reserve": float(o.get("ram_reserve_gb", 1.5)), "threads": int(o.get("threads", 2)),
            "cap": int(o.get("experten_je_schicht", 4)), "leerlauf": int(o.get("leerlauf_minuten", 10)) * 60}


OPT = optionen()
zustand = {"modell": "fehlt", "download": 0.0, "engine": "aus", "fehler": "", "anfragen": 0}
engine = {"proc": None, "id": "", "zuletzt": 0.0, "aktiv": 0, "abbruch": ""}
anfrage_lock = threading.Lock()   # eine Anfrage nach der anderen
engine_lock = threading.Lock()


def frei_gb():
    for zeile in Path("/proc/meminfo").read_text().splitlines():
        if zeile.startswith("MemAvailable:"):
            return int(zeile.split()[1]) / 1024 / 1024
    return 0.0


# ---------- Modell ----------

def herunterladen():
    ORDNER.mkdir(parents=True, exist_ok=True)
    if (MODELL / "config.json").exists() and GGUF.exists():
        zustand["modell"] = "bereit"
        return
    teil = GGUF.with_suffix(".part")
    while not GGUF.exists():
        zustand["modell"] = "lädt herunter"
        proc = subprocess.Popen(["curl", "-fsSL", "--retry", "5", "-C", "-", "-o", str(teil), QUELLE])
        while proc.poll() is None:
            zustand["download"] = round(min(teil.stat().st_size / GROESSE, 1) * 100 if teil.exists() else 0, 1)
            time.sleep(5)
        if proc.returncode == 0:
            teil.rename(GGUF)
        else:
            zustand["fehler"] = f"Download abgebrochen (curl {proc.returncode}), neuer Versuch in 10 min"
            time.sleep(600)
    zustand["modell"] = "wird eingerichtet"
    # Nur verlinken: die Gewichte bleiben in der GGUF, es wird nichts kopiert.
    r = subprocess.run(["python3", COLI, "import", str(GGUF), "--out", str(MODELL)], capture_output=True, text=True)
    if r.returncode:
        zustand["modell"] = "fehlerhaft"
        zustand["fehler"] = (r.stdout + r.stderr)[-500:]
        return
    zustand["modell"] = "bereit"
    zustand["fehler"] = ""


# ---------- Engine ----------

def _get(pfad, timeout=5):
    with urllib.request.urlopen(f"http://127.0.0.1:{ENGINE_PORT}{pfad}", timeout=timeout) as r:
        return json.load(r)


def starten():
    """Engine starten, falls sie nicht läuft. Wirft RuntimeError mit lesbarem Grund."""
    with engine_lock:
        p = engine["proc"]
        if p and p.poll() is None:
            return
        if zustand["modell"] != "bereit":
            raise RuntimeError(f"Modell {zustand['modell']}")
        frei = frei_gb()
        if frei < BEDARF_GB + OPT["reserve"]:
            raise RuntimeError(f"Zu wenig freier RAM ({frei:.1f} GB frei, nötig {BEDARF_GB + OPT['reserve']:.1f} GB)")
        env = {**os.environ, "OMP_NUM_THREADS": str(OPT["threads"]), "CAP_RAISE": "0", "PYTHONUNBUFFERED": "1"}
        befehl = ["ionice", "-c3", "nice", "-n", "19", "python3", COLI, "serve", "--model", str(MODELL),
                  "--cap", str(OPT["cap"]), "--ctx", "16384", "--host", "127.0.0.1", "--port", str(ENGINE_PORT)]
        log = open("/tmp/engine.log", "ab")
        engine["proc"] = subprocess.Popen(befehl, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        engine["abbruch"] = ""
        engine["zuletzt"] = time.time()
        zustand["engine"] = "startet"
        ende = time.time() + 600
        while time.time() < ende:
            if not engine["proc"] or engine["proc"].poll() is not None:
                zustand["engine"] = "aus"
                raise RuntimeError("Engine ist beim Start beendet worden (siehe Log der App)")
            try:
                engine["id"] = _get("/v1/models")["data"][0]["id"]
                zustand["engine"] = "läuft"
                engine["zuletzt"] = time.time()
                print(f"Engine läuft ({frei_gb():.1f} GB frei)")
                return
            except (OSError, ValueError, KeyError, IndexError):
                time.sleep(2)
        stoppen("Start dauerte zu lange")
        raise RuntimeError("Engine startet nicht")


def stoppen(grund):
    p = engine["proc"]
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(30)
        except (OSError, subprocess.TimeoutExpired):
            os.killpg(p.pid, signal.SIGKILL)
        print(f"Engine beendet: {grund}")
    engine["proc"] = None
    zustand["engine"] = "aus"


def waechter():
    while True:
        time.sleep(3)
        p = engine["proc"]
        if not p:
            continue
        if p.poll() is not None:
            engine["proc"] = None
            zustand["engine"] = "aus"
            continue
        if frei_gb() < OPT["reserve"] * 0.6:
            engine["abbruch"] = f"Abgebrochen: freier RAM unter {OPT['reserve'] * 0.6:.1f} GB"
            stoppen(engine["abbruch"])
        elif zustand["engine"] == "läuft" and not engine["aktiv"] and time.time() - engine["zuletzt"] > OPT["leerlauf"]:
            stoppen("Leerlauf")


def chat(koerper):
    with anfrage_lock:
        starten()
        engine["aktiv"] += 1
        try:
            koerper["model"] = engine["id"]
            daten = json.dumps(koerper).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{ENGINE_PORT}/v1/chat/completions", daten,
                                         {"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=8 * 3600) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as ex:
                return ex.code, ex.read()
            except OSError:
                if engine["abbruch"]:
                    raise RuntimeError(engine["abbruch"])
                raise RuntimeError("Engine nicht erreichbar")
        finally:
            engine["aktiv"] -= 1
            engine["zuletzt"] = time.time()
            zustand["anfragen"] += 1


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def antwort(self, code, roh):
        if not isinstance(roh, bytes):
            roh = json.dumps(roh, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    def erlaubt(self):
        try:
            return ipaddress.ip_address(self.client_address[0]) in NETZ or self.client_address[0] == "127.0.0.1"
        except ValueError:
            return False

    def do_GET(self):
        if not self.erlaubt():
            return self.antwort(403, {"fehler": "Nur aus dem App-Netz"})
        if self.path == "/status":
            return self.antwort(200, {**zustand, "frei_gb": round(frei_gb(), 1), "reserve_gb": OPT["reserve"]})
        self.antwort(404, {"fehler": "Nicht gefunden"})

    def do_POST(self):
        if not self.erlaubt():
            return self.antwort(403, {"fehler": "Nur aus dem App-Netz"})
        if self.path != "/v1/chat/completions":
            return self.antwort(404, {"fehler": "Nicht gefunden"})
        laenge = int(self.headers.get("Content-Length") or 0)
        if laenge > MAX_KOERPER:
            return self.antwort(413, {"fehler": "Anfrage zu groß"})
        try:
            koerper = json.loads(self.rfile.read(laenge) or b"{}")
            code, roh = chat(koerper)
        except ValueError:
            return self.antwort(400, {"fehler": "Kein JSON"})
        except RuntimeError as ex:
            return self.antwort(503, {"fehler": str(ex)})
        self.antwort(code, roh)


def main():
    print(f"KI-Pförtner auf Port {PORT}: Reserve {OPT['reserve']} GB, {OPT['threads']} Threads, "
          f"{OPT['cap']} Experten je Schicht, Leerlauf {OPT['leerlauf'] // 60} min")
    threading.Thread(target=herunterladen, daemon=True).start()
    threading.Thread(target=waechter, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
