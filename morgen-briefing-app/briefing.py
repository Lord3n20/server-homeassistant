"""Morgen-Briefing: a daily morning briefing without any AI.

Once a day (option `uhrzeit`, Europe/Berlin) it collects
  - today's events and the first one of tomorrow from CalDAV
    (Nextcloud expands recurring events itself),
  - mails of the last 2 days that look like they need an answer, each with its
    first real sentence as a summary, plus deadlines found in mails of the last
    7 days (IMAP, mailbox opened read-only: nothing is marked or moved),
renders them as one HTML page (served via Ingress) and sends a short push.
The only thing it changes in Home Assistant is the calendar alarm
(script.kalender_wecker_stellen), and only for an event before 12:00.

Everything is heuristics: a part whose data cannot be fetched is left out
with a short note instead of guessing.
"""
import base64
import email
import html
import imaplib
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
DATA = os.environ.get("BRIEFING_DATA", "/data")
OPTIONS_FILE = os.environ.get("BRIEFING_OPTIONS", os.path.join(DATA, "options.json"))
PAGE_FILE = os.path.join(DATA, "briefing.html")
INGRESS_IP = "172.30.32.2"
DEV = os.environ.get("BRIEFING_DEV") == "1"

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]

AUTOMATED_FROM_RE = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|newsletter|news|"
                               r"notifications?|benachrichtigung(en)?|mailer-daemon|postmaster|"
                               r"bounces?|marketing|alerts?|updates?)([+.-].*)?@", re.I)
GREETING_RE = re.compile(r"^\s*(hallo|hello|hi|hey|moin|servus|guten (morgen|tag|abend)|liebe[rs]?\b|"
                         r"sehr geehrte|dear|lieber)\b.{0,60}$", re.I)
SIGNOFF_RE = re.compile(r"^\s*(viele grüße|liebe grüße|lg\b|vg\b|beste grüße|mit freundlichen|"
                        r"freundliche grüße|grüße|gruß|best|cheers|regards|--\s*$)", re.I)
DEADLINE_KEY_RE = re.compile(r"(frist|deadline|stichtag|spätestens|spaetestens|bis zum|bis einschl|"
                             r"\bbis\b|fällig|faellig|zahlbar|zahlungsziel|anmeld|rückmeld|rueckmeld|"
                             r"abgabe|einreich|bewerbungsschluss|anmeldeschluss|einsendeschluss|"
                             r"\bdue\b|\buntil\b|no later than|\bbefore\b)", re.I)
SENTENCE_END_RE = re.compile(r"[.!?]\s+(?=[A-ZÄÖÜ])|\n\s*\n|\n(?=\s*[-•*]\s)")
MONATS_NAMEN = {
    "jan": 1, "januar": 1, "feb": 2, "februar": 2, "mär": 3, "märz": 3, "mar": 3, "maerz": 3,
    "apr": 4, "april": 4, "mai": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7, "aug": 8,
    "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10, "nov": 11,
    "november": 11, "dez": 12, "dezember": 12,
}
DATE_RE = re.compile(
    r"(?<![\d.])(?P<d>[0-3]?\d)\.(?P<m>[01]?\d)\.(?P<y>(?:20)?\d\d)?(?![\d])"
    r"|(?<!\d)(?P<d2>[0-3]?\d)\.?\s+(?P<mn>Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|"
    r"September|Oktober|November|Dezember|Jan|Feb|Mär|Apr|Jun|Jul|Aug|Sept|Sep|Okt|Nov|Dez)\.?"
    r"(?:\s+(?P<y2>20\d\d))?"
    r"|(?<!\d)(?P<iy>20\d\d)-(?P<im>[01]\d)-(?P<id>[0-3]\d)(?!\d)",
    re.I)

run_lock = threading.Lock()
status = {"laeuft": False, "letzter_fehler": ""}


def log(*args):
    print("[briefing]", *args, flush=True)


# --------------------------------------------------------------------------- config / HTTP

def load_options():
    with open(OPTIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def http(url, data=None, headers=None, method=None, timeout=20, auth=None):
    """Returns (status, body bytes). Raises only on network errors."""
    h = dict(headers or {})
    if auth:
        h["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class HA:
    """Home Assistant core API: inside HAOS via the Supervisor, for testing via HA_URL/HA_TOKEN."""

    def __init__(self):
        tok = os.environ.get("SUPERVISOR_TOKEN")
        if tok:
            self.base, self.token = "http://supervisor/core/api", tok
        else:
            self.base, self.token = os.environ.get("HA_URL", "").rstrip("/") + "/api", os.environ.get("HA_TOKEN", "")
        self.h = {"Authorization": "Bearer " + self.token}

    def call(self, domain, service, data):
        if os.environ.get("BRIEFING_DRY") == "1":
            log(f"(Trockenlauf) {domain}.{service} {json.dumps(data, ensure_ascii=False)}")
            return
        st, body = http(f"{self.base}/services/{domain}/{service}", data=data, headers=self.h,
                        method="POST", timeout=20)
        if st not in (200, 201):
            raise RuntimeError(f"{domain}.{service}: HTTP {st} {body[:200]!r}")


def ingress_path():
    """Frontend path of this app's panel, for the push notification."""
    tok = os.environ.get("SUPERVISOR_TOKEN")
    if not tok:
        return "/"
    try:
        st, body = http("http://supervisor/addons/self/info", headers={"Authorization": "Bearer " + tok}, timeout=10)
        return "/hassio/ingress/" + json.loads(body)["data"]["slug"]
    except Exception as e:  # noqa: BLE001
        log("Ingress-Pfad unbekannt:", e)
        return "/"


def esc(s):
    return html.escape(str(s or ""), quote=True)


def hm(dt):
    return dt.strftime("%H:%M")


# --------------------------------------------------------------------------- calendar (CalDAV)

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}


def list_calendars(opt):
    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:displayname/><d:resourcetype/></d:prop></d:propfind>')
    st, raw = http(opt["caldav_url"], data=body, method="PROPFIND", timeout=30,
                   headers={"Depth": "1", "Content-Type": "application/xml"},
                   auth=(opt["caldav_benutzer"], opt["caldav_passwort"]))
    if st != 207:
        raise RuntimeError(f"HTTP {st}" + (" (Benutzer/App-Passwort prüfen)" if st == 401 else ""))
    base = urllib.parse.urlsplit(opt["caldav_url"])
    excl = {x.strip().lower() for x in opt.get("kalender_ausschliessen", [])}
    cals = []
    for r in ET.fromstring(raw).findall("d:response", NS):
        if r.find(".//d:resourcetype/c:calendar", NS) is None:
            continue
        href = r.findtext("d:href", "", NS)
        name = r.findtext(".//d:displayname", "", NS) or href
        key = href.rstrip("/").rsplit("/", 1)[-1].lower()
        if key in excl or name.strip().lower() in excl:
            continue
        cals.append((f"{base.scheme}://{base.netloc}{href}", name))
    return cals


def unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text).replace("\r", "").split("\n")


def split_prop(line):
    """'DTSTART;TZID=Europe/Berlin:2026...' -> ('DTSTART', {'TZID': ...}, value)."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def ics_text(v):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v).strip()


def ics_time(value, params):
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d").date()
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(TZ)
    dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    try:
        tz = ZoneInfo(params["TZID"]) if "TZID" in params else TZ
    except Exception:  # noqa: BLE001 - unknown/Windows TZ names
        tz = TZ
    return dt.replace(tzinfo=tz).astimezone(TZ)


def parse_events(ics, cal_name):
    events, cur, depth = [], None, 0
    for line in unfold(ics):
        if line == "BEGIN:VEVENT":
            cur, depth = {}, 0
            continue
        if cur is None:
            continue
        if line.startswith("BEGIN:"):
            depth += 1  # VALARM etc.
            continue
        if line.startswith("END:") and depth:
            depth -= 1
            continue
        if line == "END:VEVENT":
            events.append(cur)
            cur = None
            continue
        if depth:
            continue
        p = split_prop(line)
        if p:
            cur.setdefault(p[0], (p[1], p[2]))
    out = []
    for e in events:
        if "DTSTART" not in e:
            continue
        start = ics_time(e["DTSTART"][1], e["DTSTART"][0])
        allday = not isinstance(start, datetime)
        if "DTEND" in e:
            end = ics_time(e["DTEND"][1], e["DTEND"][0])
        elif "DURATION" in e:
            m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", e["DURATION"][1].strip())
            w, d, h, mi, s = (int(x or 0) for x in m.groups()) if m else (0, 0, 0, 0, 0)
            end = start + timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
        else:
            end = start + timedelta(days=1) if allday else start
        if allday and isinstance(end, datetime):
            end = end.date()
        if allday and end <= start:
            end = start + timedelta(days=1)
        title = ics_text(e.get("SUMMARY", ({}, ""))[1]) or "(ohne Titel)"
        out.append({
            "titel": title,
            "start": start, "ende": end, "ganztags": allday,
            "ort": ics_text(e.get("LOCATION", ({}, ""))[1]),
            "abgesagt": (e.get("STATUS", ({}, ""))[1].strip().upper() == "CANCELLED"
                         or bool(re.match(r"(abgesagt|cancel+ed|entfällt)\b", title, re.I))),
            "kalender": cal_name,
        })
    return out


def fetch_events(opt, day_from, day_to):
    """All events overlapping [day_from, day_to) from every non-excluded calendar."""
    t0 = datetime.combine(day_from, datetime.min.time(), TZ).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    t1 = datetime.combine(day_to, datetime.min.time(), TZ).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    body = (f'<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            f'<d:prop><c:calendar-data><c:expand start="{t0}" end="{t1}"/></c:calendar-data></d:prop>'
            f'<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range start="{t0}" end="{t1}"/></c:comp-filter></c:comp-filter></c:filter>'
            f'</c:calendar-query>')
    events, fehler = [], []
    for url, name in list_calendars(opt):
        st, raw = http(url, data=body, method="REPORT", timeout=30,
                       headers={"Depth": "1", "Content-Type": "application/xml"},
                       auth=(opt["caldav_benutzer"], opt["caldav_passwort"]))
        if st != 207:
            fehler.append(f"Kalender „{name}“: HTTP {st}")
            continue
        for cd in ET.fromstring(raw).iter("{urn:ietf:params:xml:ns:caldav}calendar-data"):
            events.extend(parse_events(cd.text or "", name))
    return events, fehler


def events_on(events, day):
    d0 = datetime.combine(day, datetime.min.time(), TZ)
    d1 = d0 + timedelta(days=1)
    out = []
    for e in events:
        if e["ganztags"]:
            if e["start"] <= day < e["ende"]:
                out.append(e)
        elif e["start"] < d1 and (e["ende"] > d0 or e["start"] >= d0):
            out.append(e)
    out.sort(key=lambda e: (not e["ganztags"], e["start"] if not e["ganztags"] else d0, e["titel"]))
    return out


def set_alarm(ha, today_events, now):
    """Wake 90 min before the first event before 12:00, if that is still ahead."""
    morning = [e for e in today_events if not e["ganztags"] and not e["abgesagt"]
               and e["start"].date() == now.date() and e["start"].hour < 12]
    if not morning:
        return None
    first = min(morning, key=lambda e: e["start"])
    wake = first["start"] - timedelta(minutes=90)
    if wake <= now:
        return None
    ha.call("script", "kalender_wecker_stellen", {"weckzeit": wake.strftime("%Y-%m-%d %H:%M")})
    return f"Wecker {hm(wake)} – {first['titel']} {hm(first['start'])}"


# --------------------------------------------------------------------------- mail (IMAP)

IMAP_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def mail_text(msg):
    """Plain text of the mail without quoted replies."""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        text = part.get_content()
        if part.get_content_subtype() == "html":
            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
            text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    except Exception:  # noqa: BLE001 - broken charsets etc.
        return ""
    lines = []
    for line in text.splitlines():
        if re.match(r"\s*(Am .{5,120} schrieb|On .{5,120} wrote|-{3,}\s*(Original|Ursprüngliche)|"
                    r"_{10,}|Von:\s|From:\s)", line):
            break
        if not line.lstrip().startswith(">"):
            lines.append(line)
    return re.sub(r"[ \t ]+", " ", "\n".join(lines))


def summary(text, limit=220):
    """First real sentence(s): skips greeting, stops at the sign-off."""
    kept = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if SIGNOFF_RE.match(s):
            break
        if not kept and GREETING_RE.match(s):
            continue
        kept.append(s)
        if sum(len(k) for k in kept) > limit:
            break
    s = " ".join(kept)
    s = re.sub(r"^(hallo|hi|hey|moin|liebe[rs]?|lieber|sehr geehrte[rs]?)\b[^,!]{0,40}[,!]\s*", "", s, flags=re.I)
    if len(s) <= limit:
        return s
    cut = s[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?](\s|$)", cut)]
    return cut[:ends[-1]].strip() if ends and ends[-1] > 60 else cut.rsplit(" ", 1)[0] + " …"


def is_newsletter(msg):
    prec = str(msg.get("Precedence", "")).lower()
    return bool(msg.get("List-Unsubscribe") or msg.get("List-Id") or prec in ("bulk", "list", "junk"))


def is_automated(msg, addr):
    auto = str(msg.get("Auto-Submitted", "no")).lower()
    return bool(is_newsletter(msg) or auto not in ("", "no") or msg.get("X-Autoreply")
                or msg.get("X-Auto-Response-Suppress") or msg.get("Feedback-ID")
                or AUTOMATED_FROM_RE.match(addr or ""))


def find_deadlines(text, today):
    found = []
    for m in DATE_RE.finditer(text):
        try:
            if m.group("d"):
                d, mo, y = int(m.group("d")), int(m.group("m")), m.group("y")
            elif m.group("d2"):
                d, mo, y = int(m.group("d2")), MONATS_NAMEN[m.group("mn").lower()], m.group("y2")
            else:
                d, mo, y = int(m.group("id")), int(m.group("im")), m.group("iy")
            year = int(y) + (2000 if y and len(y) == 2 else 0) if y else today.year
            when = date(year, mo, d)
        except (ValueError, KeyError):
            continue
        if not y and when < today:
            continue
        if not (today <= when <= today + timedelta(days=365)):
            continue
        # only the sentence the date is in counts
        a = max(0, m.start() - 120)
        cuts = list(SENTENCE_END_RE.finditer(text, a, m.start()))
        a = cuts[-1].end() if cuts else a
        if not DEADLINE_KEY_RE.search(text[max(a, m.start() - 80):m.start()]):
            continue
        nxt = SENTENCE_END_RE.search(text, m.end(), m.end() + 80)
        z = nxt.start() + 1 if nxt else min(len(text), m.end() + 60)
        snippet = re.sub(r"\s+", " ", text[a:z]).strip()
        found.append((when, snippet[:180]))
    return found


def fetch_mail(opt, now):
    """Returns (needs_you, other_count, deadlines)."""
    today = now.date()
    since = today - timedelta(days=7)
    me = opt["imap_benutzer"].lower()
    imap = imaplib.IMAP4_SSL(opt["imap_host"], timeout=30)
    try:
        imap.login(opt["imap_benutzer"], opt["imap_passwort"])
        imap.select("INBOX", readonly=True)
        typ, data = imap.search(None, "SINCE", f"{since.day:02d}-{IMAP_MON[since.month - 1]}-{since.year}")
        ids = data[0].split() if typ == "OK" and data and data[0] else []
        needs, others, deadlines = [], 0, []
        for mid in ids[-300:]:
            typ, data = imap.fetch(mid, "(FLAGS RFC822.SIZE)")
            meta = data[0].decode() if data and isinstance(data[0], bytes) else str(data[0])
            size = int((re.search(r"RFC822\.SIZE (\d+)", meta) or [0, 0])[1])
            flags = (re.search(r"FLAGS \(([^)]*)\)", meta) or [0, ""])[1]
            what = "(BODY.PEEK[])" if size < 1_500_000 else "(BODY.PEEK[HEADER])"
            typ, data = imap.fetch(mid, what)
            raw = next((x[1] for x in data if isinstance(x, tuple)), b"")
            msg = email.message_from_bytes(raw, policy=policy.default)
            try:
                name, addr = getaddresses([str(msg.get("From", ""))])[0]
            except Exception:  # noqa: BLE001
                name, addr = "", ""
            addr = addr.lower()
            if addr == me:
                continue
            try:
                when = parsedate_to_datetime(str(msg["Date"])).astimezone(TZ)
            except Exception:  # noqa: BLE001
                when = now
            subject = str(msg.get("Subject", "") or "(ohne Betreff)").strip()
            sender = name or addr
            text = mail_text(msg)
            if now - when <= timedelta(days=2):
                if "\\Answered" not in flags and not is_automated(msg, addr):
                    needs.append({"von": sender, "betreff": subject, "zeit": when,
                                  "ungelesen": "\\Seen" not in flags, "kurz": summary(text)})
                else:
                    others += 1
            if not is_newsletter(msg):
                for d, snip in find_deadlines(subject + "\n" + text, today):
                    deadlines.append({"datum": d, "betreff": subject, "von": sender, "text": snip})
        needs.sort(key=lambda x: (not x["ungelesen"], -x["zeit"].timestamp()))
        seen, uniq = set(), []
        for dl in sorted(deadlines, key=lambda x: x["datum"]):
            k = (dl["datum"], dl["betreff"])
            if k not in seen:
                seen.add(k)
                uniq.append(dl)
        return needs[:12], others, uniq[:10]
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- rendering

CSS = """
:root{--bg:#f6f4ef;--card:#fff;--fg:#1d1d1f;--mut:#6b6b70;--line:#e4e1da;--acc:#9c2b23;--hl:#fff4d6}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--fg:#ececef;--mut:#9a9aa2;
--line:#2e2e33;--acc:#e0786f;--hl:#3a3220}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:680px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.2rem 0 0}h2{font-size:.8rem;letter-spacing:.08em;text-transform:uppercase;
color:var(--mut);margin:0 0 .5rem}.sub{color:var(--mut);margin:0 0 1rem}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:0 0 12px}
ul{list-style:none;margin:0;padding:0}li{padding:7px 0;border-top:1px solid var(--line)}li:first-child{border-top:0}
.t{font-variant-numeric:tabular-nums;font-weight:600;margin-right:.4rem}.m{color:var(--mut);font-size:.9rem}
.dot{color:var(--acc)}.hl{background:var(--hl);border-radius:6px;padding:6px 8px}
.alarm{font-weight:600;margin:0 0 12px}.x{text-decoration:line-through;color:var(--mut)}
button{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
footer{color:var(--mut);font-size:.85rem;padding:4px 2px 24px}
"""


def render_event(e):
    head = ('<span class="t">ganztags</span>' if e["ganztags"]
            else f'<span class="t">{hm(e["start"])}–{hm(e["ende"])}</span>')
    title = (f'<span class="x">{esc(e["titel"])}</span> <span class="m">(abgesagt)</span>'
             if e["abgesagt"] else esc(e["titel"]))
    ort = f'<div class="m">{esc(e["ort"])}</div>' if e["ort"] else ""
    return f"<li>{head}{title}{ort}</li>"


def render(b):
    now, today = b["jetzt"], b["jetzt"].date()
    out = [f'<!doctype html><html lang="de"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>Morgen-Briefing</title><style>{CSS}</style></head><body><main>'
           f'<h1>Guten Morgen, Enzo</h1>'
           f'<p class="sub">{WOCHENTAGE[today.weekday()]}, {today.day}. {MONATE[today.month - 1]} {today.year}</p>']
    if b.get("wecker"):
        out.append(f'<p class="alarm">⏰ {esc(b["wecker"])}</p>')

    if b.get("termine") is not None:
        body = "".join(render_event(e) for e in b["termine"]) or '<li class="m">Heute keine Termine</li>'
        t = b.get("morgen")
        nxt = (f"Morgen zuerst: {hm(t['start'])} {t['titel']}" + (f" ({t['ort']})" if t["ort"] else "")
               if t else "Morgen keine Termine")
        out.append(f'<section><h2>Termine heute</h2><ul>{body}</ul>'
                   f'<div class="m" style="margin-top:8px">{esc(nxt)}</div></section>')

    if b.get("mails") is not None:
        rows = []
        for m in b["mails"]:
            dot = ' <span class="dot">●</span>' if m["ungelesen"] else ""
            kurz = f'<div class="m">{esc(m["kurz"])}</div>' if m["kurz"] else ""
            rows.append(f'<li><b>{esc(m["von"])}</b>{dot} <span class="m">{m["zeit"].strftime("%d.%m. %H:%M")}</span>'
                        f'<div>{esc(m["betreff"])}</div>{kurz}</li>')
        if not rows:
            rows.append('<li class="m">Nichts Offenes in den letzten 2 Tagen</li>')
        rest = (f'<div class="m" style="margin-top:8px">+ {b["andere"]} weitere (Newsletter, Benachrichtigungen, '
                f'schon beantwortet)</div>' if b.get("andere") else "")
        out.append(f'<section><h2>Mails, die dich brauchen</h2><ul>{"".join(rows)}</ul>{rest}</section>')

    if b.get("fristen"):
        rows = []
        for f in b["fristen"]:
            soon = (f["datum"] - today).days <= 3
            rows.append(f'<li{" class=hl" if soon else ""}><span class="t">{f["datum"].strftime("%d.%m.")}</span>'
                        f'{esc(f["betreff"])} <span class="m">– {esc(f["von"])}</span>'
                        f'<div class="m">{esc(f["text"])}</div></li>')
        out.append(f'<section><h2>Fristen aus Mails</h2><ul>{"".join(rows)}</ul></section>')

    notes = "".join(f"<div>{esc(n)}</div>" for n in b["hinweise"])
    out.append(f'<footer>Erstellt {now.strftime("%d.%m. %H:%M")}{notes}'
               f'<form method="post" action="neu" style="margin-top:10px"><button>Neu erstellen</button></form>'
               f'</footer></main></body></html>')
    return "".join(out)


def push_text(b):
    parts = []
    if b.get("termine") is not None:
        timed = [e for e in b["termine"] if not e["ganztags"] and not e["abgesagt"]]
        if timed:
            parts.append(f"{len(timed)} Termin(e), erster {hm(timed[0]['start'])} {timed[0]['titel']}")
        else:
            parts.append("keine Termine")
    if b.get("mails"):
        parts.append(f"{len(b['mails'])} Mail(s) brauchen dich")
    soon = [f for f in b.get("fristen") or [] if (f["datum"] - b["jetzt"].date()).days <= 3]
    if soon:
        parts.append(f"{len(soon)} Frist(en) in 3 Tagen")
    if b.get("wecker"):
        parts.append(b["wecker"])
    return " · ".join(parts) or "Briefing ist fertig"


# --------------------------------------------------------------------------- one run

def build(push=False):
    if not run_lock.acquire(blocking=False):
        return
    status["laeuft"] = True
    try:
        opt = load_options()
        now = datetime.now(TZ)
        today = now.date()
        ha = HA()
        b = {"jetzt": now, "hinweise": []}
        notes = b["hinweise"]

        def step(name, fn):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                log(f"{name}: {e}")
                if DEV:
                    traceback.print_exc()
                notes.append(f"{name} nicht abrufbar: {e}")
                return None

        res = step("Kalender", lambda: fetch_events(opt, today, today + timedelta(days=2)))
        if res is not None:
            events, cal_err = res
            notes.extend(cal_err)
            b["termine"] = events_on(events, today)
            first = [e for e in events_on(events, today + timedelta(days=1))
                     if not e["ganztags"] and not e["abgesagt"]]
            b["morgen"] = first[0] if first else None
            b["wecker"] = step("Kalender-Wecker", lambda: set_alarm(ha, b["termine"], now))

        if opt.get("imap_passwort"):
            mail = step("Mail", lambda: fetch_mail(opt, now))
            if mail:
                b["mails"], b["andere"], b["fristen"] = mail
        else:
            notes.append("Mail: kein IMAP-Passwort eingetragen")

        tmp = PAGE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(render(b))
        os.replace(tmp, PAGE_FILE)
        log(f"Briefing erstellt ({len(notes)} Hinweis(e))")
        if push and opt.get("push_dienst"):
            path = ingress_path()
            step("Push", lambda: ha.call("notify", opt["push_dienst"], {
                "title": "Morgen-Briefing", "message": push_text(b),
                "data": {"url": path, "clickAction": path, "tag": "morgen-briefing"}}))
        status["letzter_fehler"] = ""
    except Exception as e:  # noqa: BLE001
        status["letzter_fehler"] = str(e)
        log("Fehler:", e)
        traceback.print_exc()
    finally:
        status["laeuft"] = False
        run_lock.release()


def page_is_from_today():
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(PAGE_FILE), TZ)
        return mtime.date() == datetime.now(TZ).date()
    except OSError:
        return False


def scheduler():
    if not page_is_from_today():
        build(push=False)
    last_run = None
    while True:
        try:
            hh, mm = (int(x) for x in load_options()["uhrzeit"].split(":"))
        except Exception:  # noqa: BLE001
            hh, mm = 5, 0
        now = datetime.now(TZ)
        if (now.hour, now.minute) == (hh, mm) and last_run != now.date():
            last_run = now.date()
            build(push=True)
        time.sleep(20)


# --------------------------------------------------------------------------- web (Ingress)

class Handler(BaseHTTPRequestHandler):
    def _allowed(self):
        return DEV or self.client_address[0] == INGRESS_IP

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/gesund"):
            return self._send(200, "ok", "text/plain")
        if not self._allowed():
            return self._send(403, "nur über Home Assistant", "text/plain")
        try:
            with open(PAGE_FILE, encoding="utf-8") as f:
                page = f.read()
        except OSError:
            page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                    '<body style="font-family:system-ui;padding:16px">Noch kein Briefing – wird erstellt, '
                    'gleich neu laden.' + (f"<p>Letzter Fehler: {esc(status['letzter_fehler'])}" if status["letzter_fehler"] else ""))
        if status["laeuft"]:
            page = page.replace("<main>", '<main><p class="m">Wird gerade neu erstellt …</p>', 1)
        self._send(200, page)

    def do_POST(self):  # noqa: N802
        if not self._allowed():
            return self._send(403, "nur über Home Assistant", "text/plain")
        if self.path.rstrip("/").endswith("/neu"):
            threading.Thread(target=build, daemon=True).start()
            time.sleep(1)
            return self._send(303, "", extra={"Location": "./"})
        self._send(404, "nicht gefunden", "text/plain")

    def log_message(self, *args):
        pass


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "einmal":
        build(push="--push" in sys.argv)
        return
    threading.Thread(target=scheduler, daemon=True).start()
    log("Bereit auf Port 8080")
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
