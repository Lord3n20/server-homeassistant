"""Nokia bridge: a small plain-HTTP API for old J2ME phones.

The phone cannot talk TLS 1.2 to Home Assistant, so this app speaks plain
HTTP with a tab-separated text format and its own key. It only exposes the
entities and actions listed in ITEMS; the Home Assistant token never leaves
the app (inside HA it uses the Supervisor token).
"""
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# kind, entity_id, display name (None = friendly_name from HA)
#   H heading, R read-only, G read-only with 24 h graph, T toggle,
#   S select, L dimmable light, B button (radio), M media player, W weather
ITEMS = [
    ("H", None, "Status"),
    ("R", "input_boolean.zuhause", None),
    ("H", None, "Zimmer"),
    ("G", "sensor.arduino_temperature1", None),
    ("S", "select.avalon_nano_3s_arbeitsmodus", None),
    ("L", "light.wled", None),
    ("T", "switch.tapo_p300_1", None),
    ("T", "switch.zimmer_lampesofa", None),
    ("T", "input_boolean.licht_k1_bett", None),
    ("B", "counter.radiosender", "Radiosender"),
    ("M", "media_player.zimmer_enzos_verstarker_2", None),
    ("T", "switch.tapo_p300_2", "Verstärker"),
    ("H", None, "Draußen"),
    ("W", "weather.forecast_home", "Wetter"),
    ("H", None, "Claude"),
    ("R", "sensor.claude_usage_enzo_pro_session_usage", "Session"),
    ("R", "sensor.claude_usage_enzo_pro_week_usage", "Woche"),
    ("R", "sensor.claude_usage_enzo_pro_week_usage_pace", "Geschwindigkeit"),
]

# What the radio tile does on tap in the dashboard.
RADIO_AUTOMATIONS = [
    "automation.radio_an_k2",
    "automation.radio_an_wdr5_k2",
    "automation.radio_an_mex_k2",
    "automation.radio_aus_k2",
    "automation.radio_aus_k4",
]

MEDIA_ACTIONS = {
    "power": "toggle",
    "prev": "media_previous_track",
    "play": "media_play_pause",
    "next": "media_next_track",
}

CONDITIONS = {
    "clear-night": "Klar",
    "cloudy": "Bewölkt",
    "exceptional": "Unwetter",
    "fog": "Nebel",
    "hail": "Hagel",
    "lightning": "Gewitter",
    "lightning-rainy": "Gewitter, Regen",
    "partlycloudy": "Teilweise bewölkt",
    "pouring": "Starkregen",
    "rainy": "Regen",
    "snowy": "Schnee",
    "snowy-rainy": "Schneeregen",
    "sunny": "Sonnig",
    "windy": "Windig",
    "windy-variant": "Windig, bewölkt",
}

STATES = {
    "on": "Ein",
    "off": "Aus",
    "unavailable": "nicht verfügbar",
    "unknown": "unbekannt",
    "playing": "spielt",
    "paused": "pausiert",
    "idle": "bereit",
    "standby": "Standby",
    "buffering": "lädt",
}

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

if os.environ.get("SUPERVISOR_TOKEN"):
    HA_URL = "http://supervisor/core/api"
    HA_TOKEN = os.environ["SUPERVISOR_TOKEN"]
else:  # for testing outside Home Assistant
    HA_URL = os.environ["HA_URL"].rstrip("/") + "/api"
    HA_TOKEN = os.environ["HA_TOKEN"]

DATA = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))


def log(msg):
    print("[nokia-bridge] " + msg, flush=True)


def load_key():
    """Key from the app options, otherwise a generated one kept in /data."""
    try:
        with open(os.path.join(DATA, "options.json")) as f:
            key = (json.load(f).get("schluessel") or "").strip()
    except (OSError, ValueError):
        key = ""
    if key:
        return key
    path = os.path.join(DATA, "key.txt")
    try:
        with open(path) as f:
            key = f.read().strip()
    except OSError:
        key = ""
    if not key:
        # Only letters and digits that are easy to type on a phone keypad.
        alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
        key = "".join(secrets.choice(alphabet) for _ in range(20))
        with open(path, "w") as f:
            f.write(key)
    log("Schlüssel für das Handy: " + key)
    return key


KEY = load_key()


def ha(method, path, body=None):
    req = urllib.request.Request(
        HA_URL + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + HA_TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def call(domain, service, data):
    ha("POST", "/services/%s/%s" % (domain, service), data)


def fmt_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = str(value)
    # Raw sensor noise like 25.0005874633789 is shown with one decimal.
    if "." in text and len(text.split(".")[1]) > 2:
        text = "%.1f" % number
    return text.replace(".", ",")


def state_text(kind, st):
    s = st["state"]
    a = st.get("attributes", {})
    if s in ("unavailable", "unknown"):
        return STATES[s]
    if kind == "L":
        if s == "on" and a.get("brightness") is not None:
            return "%d %%" % round(a["brightness"] * 100 / 255)
        return STATES.get(s, s)
    if kind == "M":
        text = STATES.get(s, s)
        title = a.get("media_title")
        return text + (": " + title if title and s != "off" else "")
    if kind == "W":
        cond = CONDITIONS.get(s, s)
        unit = a.get("temperature_unit", "°C")
        text = "%s, %s %s" % (cond, fmt_number(a.get("temperature")), unit)
        if a.get("humidity") is not None:
            text += ", %s %%" % fmt_number(a["humidity"])
        return text
    unit = a.get("unit_of_measurement")
    return fmt_number(s) + (" " + unit if unit else "") if unit else STATES.get(s, s)


def clean(text):
    return str(text).replace("\t", " ").replace("\n", " ")


def page_states():
    lines = ["OK"]
    for i, (kind, eid, name) in enumerate(ITEMS):
        if kind == "H":
            lines.append("%d\tH\t%s\t\t" % (i, name))
            continue
        try:
            st = ha("GET", "/states/" + eid)
        except urllib.error.HTTPError:
            lines.append("%d\tR\t%s\tnicht gefunden\t" % (i, name or eid))
            continue
        a = st.get("attributes", {})
        label = name or a.get("friendly_name") or eid
        extra = ";".join(a.get("options", [])) if kind == "S" else ""
        lines.append("\t".join([str(i), kind, clean(label), clean(state_text(kind, st)), clean(extra)]))
    return "\n".join(lines)


_forecast = {"t": 0, "text": ""}


def page_weather():
    if time.time() - _forecast["t"] > 600:
        eid = next(e for k, e, _ in ITEMS if k == "W")
        res = ha("POST", "/services/weather/get_forecasts?return_response",
                 {"entity_id": eid, "type": "daily"})
        days = res["service_response"][eid]["forecast"]
        lines = ["OK"]
        for d in days[:5]:
            day = datetime.fromisoformat(d["datetime"].replace("Z", "+00:00")).astimezone()
            lines.append("\t".join([
                WEEKDAYS[day.weekday()],
                fmt_number(d.get("temperature", "")),
                fmt_number(d.get("templow", "")),
                CONDITIONS.get(d.get("condition"), d.get("condition", "")),
            ]))
        _forecast.update(t=time.time(), text="\n".join(lines))
    return _forecast["text"]


def page_graph(i):
    kind, eid, _ = ITEMS[i]
    if kind != "G":
        raise ValueError("kein Verlauf")
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)
    path = "/history/period/%s?filter_entity_id=%s&end_time=%s&minimal_response&no_attributes" % (
        urllib.parse.quote(start.isoformat()), eid, urllib.parse.quote(end.isoformat()))
    hist = ha("GET", path)
    points = []
    for p in hist[0] if hist else []:
        try:
            t = datetime.fromisoformat(p["last_changed"].replace("Z", "+00:00"))
            points.append((t, float(p["state"])))
        except (KeyError, ValueError):
            pass
    if not points:
        raise ValueError("keine Daten")
    # 48 half-hour buckets, averaged; empty buckets keep the previous value.
    n = 48
    sums, counts = [0.0] * n, [0] * n
    for t, v in points:
        b = min(n - 1, max(0, int((t - start).total_seconds() / (86400 / n))))
        sums[b] += v
        counts[b] += 1
    values, last = [], points[0][1]
    for b in range(n):
        if counts[b]:
            last = sums[b] / counts[b]
        values.append(int(round(last * 10)))
    return "OK\n" + ",".join(str(v) for v in values)


def do_action(i, cmd, value):
    kind, eid, _ = ITEMS[i]
    domain = eid.split(".")[0]
    if kind == "T" and cmd == "toggle":
        call(domain, "toggle", {"entity_id": eid})
    elif kind == "S" and cmd == "select":
        options = ha("GET", "/states/" + eid)["attributes"].get("options", [])
        if value not in options:
            raise ValueError("unbekannte Option")
        call("select", "select_option", {"entity_id": eid, "option": value})
    elif kind == "L" and cmd == "toggle":
        call("light", "toggle", {"entity_id": eid})
    elif kind == "L" and cmd == "bright":
        pct = max(0, min(100, int(value)))
        if pct == 0:
            call("light", "turn_off", {"entity_id": eid})
        else:
            call("light", "turn_on", {"entity_id": eid, "brightness_pct": pct})
    elif kind == "M" and cmd in MEDIA_ACTIONS:
        call("media_player", MEDIA_ACTIONS[cmd], {"entity_id": eid})
    elif kind == "B" and cmd == "press":
        call("automation", "trigger", {"entity_id": RADIO_AUTOMATIONS, "skip_condition": False})
    else:
        raise ValueError("Aktion nicht erlaubt")
    return "OK"


_fails = {"n": 0, "t": 0.0}
_fails_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def reply(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if not hmac.compare_digest(q.get("k", "").encode(), KEY.encode()):
            with _fails_lock:
                if time.time() - _fails["t"] > 60:
                    _fails.update(n=0, t=time.time())
                _fails["n"] += 1
                slow = _fails["n"] > 5
            if slow:
                time.sleep(5)
            return self.reply(403, "ERR\tSchlüssel falsch")
        try:
            if url.path == "/s":
                text = page_states()
            elif url.path == "/w":
                text = page_weather()
            elif url.path == "/g":
                text = page_graph(int(q.get("i", "-1")))
            elif url.path == "/a":
                i = int(q.get("i", "-1"))
                if not 0 <= i < len(ITEMS):
                    raise ValueError("unbekannter Eintrag")
                text = do_action(i, q.get("c", ""), q.get("v", ""))
            else:
                return self.reply(404, "ERR\tunbekannte Adresse")
        except (ValueError, IndexError, StopIteration) as e:
            return self.reply(400, "ERR\t" + str(e))
        except (urllib.error.URLError, OSError, KeyError) as e:
            log("Fehler bei Home Assistant: %r" % e)
            return self.reply(502, "ERR\tHome Assistant antwortet nicht")
        self.reply(200, text)

    def log_message(self, fmt, *args):
        # Never write the key to the log.
        log("%s %s" % (self.address_string(), urllib.parse.urlsplit(self.path).path))


if __name__ == "__main__":
    log("Lausche auf Port %d" % PORT)
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
