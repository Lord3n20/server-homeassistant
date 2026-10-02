"""Nokia Signal bridge: Signal for old J2ME phones.

signal-cli runs as a linked device of the user's Signal account. This app
keeps the chats in SQLite and offers a small HTTP API for the phone. The
phone cannot do modern TLS, so every request and answer is encrypted with
ChaCha20 and authenticated with HMAC-SHA256 under a key derived from the
shared phone key (see "Protocol" in README.md). An ingress page shows the
QR code for linking and the status.
"""
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import qrcode
import qrcode.image.svg
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from PIL import Image, ImageOps

DATA = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))
INGRESS_PORT = int(os.environ.get("INGRESS_PORT", "8099"))
SIGNAL_CLI = os.environ.get("SIGNAL_CLI", "/usr/local/bin/signal-cli")
SIGNAL_DIR = os.path.join(DATA, "signal")
OUT_DIR = os.path.join(DATA, "out")
THUMB_DIR = os.path.join(DATA, "thumbs")
DEVICE_NAME = "Nokia"

MAX_BODY = 8 * 1024 * 1024
MAX_SKEW = 600  # seconds a request timestamp may be off
PAGE = 30  # messages per page
IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp")

# The phone font has no colour emoji; the common ones become text smileys.
EMOJI = {
    "\U0001F600": ":D", "\U0001F603": ":D", "\U0001F604": ":D", "\U0001F601": ":D",
    "\U0001F606": "xD", "\U0001F602": "xD", "\U0001F923": "xD", "\U0001F605": "^^'",
    "\U0001F642": ":)", "\U0001F60A": ":)", "\U0001F607": "O:)", "\U0001F609": ";)",
    "\U0001F60D": "*_*", "\U0001F970": "<3", "\U0001F618": ":*", "\U0001F617": ":*",
    "\U0001F61B": ":P", "\U0001F61C": ";P", "\U0001F61D": "xP", "\U0001F60E": "B)",
    "\U0001F641": ":(", "☹": ":(", "\U0001F61E": ":(", "\U0001F622": ":'(",
    "\U0001F62D": ":'(", "\U0001F620": ">:(", "\U0001F621": ">:(", "\U0001F62E": ":O",
    "\U0001F632": ":O", "\U0001F631": ":O", "\U0001F610": ":|", "\U0001F611": ":|",
    "\U0001F644": "(Augenrollen)", "\U0001F914": "(hmm)", "\U0001F648": "(Affe)",
    "\U0001F62C": ":S", "\U0001F974": ":S", "\U0001F634": "(müde)",
    "❤": "<3", "\U0001F496": "<3", "\U0001F495": "<3", "\U0001F499": "<3",
    "\U0001F49A": "<3", "\U0001F49B": "<3", "\U0001F49C": "<3", "\U0001F5A4": "<3",
    "\U0001F494": "</3", "\U0001F44D": "(Y)", "\U0001F44E": "(N)", "\U0001F44C": "(ok)",
    "\U0001F64F": "(danke)", "\U0001F44F": "(Applaus)", "\U0001F44B": "(winkt)",
    "\U0001F4AA": "(stark)", "\U0001F525": "(Feuer)", "\U0001F389": "(Party)",
    "\U0001F381": "(Geschenk)", "\U0001F382": "(Kuchen)", "✅": "(ok)",
    "❌": "(x)", "\U0001F680": "(Rakete)", "\U0001F37A": "(Bier)", "\U0001F37B": "(Bier)",
    "☀": "(Sonne)", "\U0001F31E": "(Sonne)", "\U0001F327": "(Regen)",
}
ZERO_WIDTH = dict.fromkeys(map(ord, "︎️‍"), None)
SKIN_TONES = re.compile("[\U0001F3FB-\U0001F3FF]")


def log(msg):
    print("[nokia-signal] " + msg, flush=True)


# --- phone key and protocol ------------------------------------------------

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
        key = "".join(secrets.choice(alphabet) for _ in range(24))
        with open(path, "w") as f:
            f.write(key)
    log("Schlüssel für das Handy: " + key)
    return key


KEY = load_key()


def subkey(label):
    return hashlib.sha256((label + "\n" + KEY).encode("utf-8")).digest()


UP_ENC, UP_MAC = subkey("up-enc"), subkey("up-mac")
DOWN_ENC, DOWN_MAC = subkey("down-enc"), subkey("down-mac")


def chacha(key, nonce, data):
    # cryptography wants the 32-bit block counter (0) in front of the nonce.
    c = Cipher(algorithms.ChaCha20(key, b"\0\0\0\0" + nonce), mode=None).encryptor()
    return c.update(data)


def seal(plain):
    nonce = os.urandom(12)
    ct = chacha(DOWN_ENC, nonce, plain)
    tag = hmac.new(DOWN_MAC, nonce + ct, hashlib.sha256).digest()[:16]
    return nonce + ct + tag


def unseal(frame):
    """Returns the plaintext, or None if the frame is not from the phone."""
    if len(frame) < 28:
        return None
    nonce, ct, tag = frame[:12], frame[12:-16], frame[-16:]
    good = hmac.new(UP_MAC, nonce + ct, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, good):
        return None
    return nonce, chacha(UP_ENC, nonce, ct)


_seen = {}
_seen_lock = threading.Lock()


def fresh_nonce(nonce):
    """False if this request was already handled (replayed)."""
    now = time.time()
    with _seen_lock:
        for n, t in list(_seen.items()):
            if now - t > 2 * MAX_SKEW:
                del _seen[n]
        if nonce in _seen:
            return False
        _seen[nonce] = now
        return True


def phone_text(s):
    """Text the phone can show: no tabs/newlines, emoji as smileys."""
    s = SKIN_TONES.sub("", (s or "").translate(ZERO_WIDTH))
    out = []
    for ch in s:
        if ch in EMOJI:
            out.append(EMOJI[ch])
        elif ord(ch) > 0xFFFF:
            out.append("[?]")
        elif ch == "\t":
            out.append(" ")
        elif ch in "\r\n":
            out.append("\x1e")  # the phone turns this back into a line break
        else:
            out.append(ch)
    # Emoji made of several characters (families, flags) become one [?].
    return re.sub(r"(\[\?\])+", "[?]", "".join(out))


def when(ts):
    t = datetime.fromtimestamp(ts / 1000)
    if t.date() == datetime.now().date():
        return t.strftime("%H:%M")
    return t.strftime("%d.%m. %H:%M")


# --- storage -----------------------------------------------------------------

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        # Bumped on every change from Signal; the phone long-polls on it.
        self.version = 1
        self.changed = threading.Condition()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS convs (
                id TEXT PRIMARY KEY, name TEXT, read_upto INTEGER DEFAULT 0,
                last_id INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS msgs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, conv TEXT, ts INTEGER,
                author TEXT, out INTEGER, body TEXT);
            CREATE INDEX IF NOT EXISTS msgs_conv ON msgs (conv, id);
            CREATE INDEX IF NOT EXISTS msgs_ts ON msgs (conv, author, ts);
            CREATE TABLE IF NOT EXISTS atts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, msg INTEGER, path TEXT,
                ctype TEXT, name TEXT);
            CREATE INDEX IF NOT EXISTS atts_msg ON atts (msg);
            CREATE TABLE IF NOT EXISTS names (addr TEXT PRIMARY KEY, name TEXT, prio INTEGER);
        """)
        self.db.commit()

    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    def set_name(self, addr, name, prio):
        """prio: 0 phone number, 1 profile name, 2 group name, 3 contact name (wins)."""
        if not addr or not name:
            return
        with self.lock:
            row = self.db.execute("SELECT prio FROM names WHERE addr=?", (addr,)).fetchone()
            if row is None or row[0] <= prio:
                self.db.execute("INSERT OR REPLACE INTO names VALUES (?,?,?)", (addr, name, prio))
                self.db.commit()

    def name(self, addr, fallback=None):
        row = self.q("SELECT name FROM names WHERE addr=?", (addr,))
        if row:
            return row[0][0]
        if fallback:
            return fallback
        return addr[2:] if addr.startswith("n:") else "Unbekannt"

    def add_msg(self, conv, ts, author, out, body, atts=()):
        with self.lock:
            dup = self.db.execute(
                "SELECT id FROM msgs WHERE conv=? AND author=? AND ts=?", (conv, author, ts)).fetchone()
            if dup:
                return dup[0]
            self.db.execute("INSERT OR IGNORE INTO convs (id) VALUES (?)", (conv,))
            mid = self.db.execute(
                "INSERT INTO msgs (conv, ts, author, out, body) VALUES (?,?,?,?,?)",
                (conv, ts, author, out, body)).lastrowid
            for path, ctype, name in atts:
                self.db.execute("INSERT INTO atts (msg, path, ctype, name) VALUES (?,?,?,?)",
                                (mid, path, ctype, name))
            self.db.execute("UPDATE convs SET last_id=? WHERE id=?", (mid, conv))
            if out:
                # Writing from any device means the chat has been seen.
                self.db.execute("UPDATE convs SET read_upto=? WHERE id=?", (mid, conv))
            self.db.commit()
        self.bump()
        return mid

    def bump(self):
        with self.changed:
            self.version += 1
            self.changed.notify_all()

    def wait(self, since, seconds):
        """Returns the version as soon as it differs from since, or after the timeout."""
        with self.changed:
            self.changed.wait_for(lambda: self.version != since, timeout=seconds)
            return self.version

    def find(self, conv, author, ts):
        row = self.q("SELECT id FROM msgs WHERE conv=? AND author=? AND ts=?", (conv, author, ts))
        return row[0][0] if row else None

    def mark_read(self, conv, upto):
        self.x("UPDATE convs SET read_upto=max(read_upto, ?) WHERE id=?", (upto, conv))


store = None


# --- signal-cli --------------------------------------------------------------

class Signal:
    """Runs signal-cli: `link` until an account exists, then `jsonRpc`."""

    def __init__(self):
        self.state = "startet"
        self.link_uri = None
        self.number = None
        self.error = ""
        self.proc = None
        self.wlock = threading.Lock()
        self.pending = {}
        self.next_id = 1
        self.stop_link = threading.Event()

    def account(self):
        try:
            with open(os.path.join(SIGNAL_DIR, "data", "accounts.json")) as f:
                accounts = json.load(f).get("accounts", [])
        except (OSError, ValueError):
            return None
        return accounts[0].get("number") if accounts else None

    def base_cmd(self):
        return [SIGNAL_CLI, "--config", SIGNAL_DIR]

    def run(self):
        quick_fails = 0
        while True:
            self.number = self.account()
            if not self.number:
                self.link()
                continue
            started = time.time()
            self.serve()
            quick_fails = quick_fails + 1 if time.time() - started < 60 else 0
            if quick_fails >= 3:
                self.state = "fehler"
                log("signal-cli stürzt wiederholt ab, warte 5 Minuten")
                time.sleep(300)
                quick_fails = 0
            else:
                time.sleep(10)

    def link(self):
        self.state = "koppeln"
        self.stop_link.clear()
        p = subprocess.Popen(self.base_cmd() + ["link", "-n", DEVICE_NAME],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.proc = p
        line = p.stdout.readline().strip()
        if line.startswith("sgnl://"):
            self.link_uri = line
            log("Warte auf Kopplung, QR-Code in der App-Oberfläche (Web-UI)")
        rest, err = p.communicate()
        self.link_uri = None
        if p.returncode == 0:
            log("Gekoppelt: " + rest.strip())
            open(os.path.join(DATA, "sync-needed"), "w").close()
        else:
            if err.strip() and not self.stop_link.is_set():
                log("Kopplung abgebrochen: " + err.strip().splitlines()[-1])
            time.sleep(2)

    def serve(self):
        self.state = "verbindet"
        p = subprocess.Popen(self.base_cmd() + ["-a", self.number, "jsonRpc"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, bufsize=1)
        self.proc = p
        threading.Thread(target=self.read_stderr, args=(p,), daemon=True).start()
        threading.Thread(target=self.after_start, daemon=True).start()
        for line in p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and msg["id"] in self.pending:
                slot = self.pending.pop(msg["id"])
                slot["reply"] = msg
                slot["event"].set()
            elif msg.get("method") == "receive":
                try:
                    handle_envelope(msg["params"]["envelope"])
                except Exception as e:  # never let one odd message stop receiving
                    log("Nachricht nicht verarbeitet: %r" % e)
        p.wait()
        for slot in list(self.pending.values()):
            slot["event"].set()
        self.pending.clear()
        if self.state != "entkoppelt":
            self.state = "getrennt"
        log("signal-cli beendet (Code %s)" % p.returncode)

    def read_stderr(self, p):
        for line in p.stderr:
            line = line.rstrip()
            if not line:
                continue
            self.error = line
            if "not registered" in line or "AuthorizationFailed" in line or "DeviceLimit" in line:
                self.state = "entkoppelt"
            log("signal-cli: " + line)

    def after_start(self):
        time.sleep(3)
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            self.rpc("version", {})
            self.state = "verbunden"
            flag = os.path.join(DATA, "sync-needed")
            if os.path.exists(flag):
                self.rpc("sendSyncRequest", {})
                os.remove(flag)
                time.sleep(20)  # give the primary a moment to send contacts and groups
            refresh_names()
        except Exception as e:
            log("Start-Abgleich fehlgeschlagen: %r" % e)

    def rpc(self, method, params, timeout=120):
        p = self.proc
        if p is None or p.poll() is not None or self.state in ("koppeln", "entkoppelt"):
            raise RuntimeError("Signal ist nicht verbunden")
        with self.wlock:
            rid = self.next_id
            self.next_id += 1
            slot = {"event": threading.Event(), "reply": None}
            self.pending[rid] = slot
            p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                                      "params": params}) + "\n")
            p.stdin.flush()
        if not slot["event"].wait(timeout):
            self.pending.pop(rid, None)
            raise RuntimeError("Signal antwortet nicht")
        reply = slot["reply"]
        if reply is None:
            raise RuntimeError("Signal wurde beendet")
        if "error" in reply:
            raise RuntimeError(reply["error"].get("message", "Signal-Fehler"))
        return reply.get("result")

    def relink(self):
        """Throws away the linked account and starts linking again."""
        self.state = "koppeln"
        self.stop_link.set()
        p = self.proc
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        # Only the account goes; received pictures stay viewable.
        shutil.rmtree(os.path.join(SIGNAL_DIR, "data"), ignore_errors=True)


signal = Signal()


def refresh_names():
    for c in signal.rpc("listContacts", {}) or []:
        addr = conv_of(c.get("uuid"), c.get("number"))
        prof = c.get("profile") or {}
        name = (c.get("nickName") or c.get("name")
                or " ".join(x for x in (c.get("givenName"), c.get("familyName")) if x))
        if name:
            store.set_name(addr, name, 3)
        else:
            store.set_name(addr, " ".join(x for x in (prof.get("givenName"), prof.get("familyName")) if x), 1)
    for g in signal.rpc("listGroups", {}) or []:
        store.set_name("g:" + g["id"], g.get("name"), 3)


def conv_of(uuid_, number):
    return "u:" + uuid_ if uuid_ else "n:" + (number or "?")


def attachments(items):
    base = os.path.join(SIGNAL_DIR, "attachments")
    out = []
    for a in items or []:
        if not a.get("id"):
            continue
        path = os.path.join(base, a["id"])
        out.append((path, a.get("contentType") or "", a.get("filename") or ""))
    return out


def describe(dm, atts):
    """Message text as the phone shows it, without the images."""
    parts = []
    quote = dm.get("quote")
    if quote and quote.get("text"):
        q = quote["text"].replace("\n", " ")
        parts.append("» " + (q[:60] + "…" if len(q) > 60 else q) + "\n")
    text = dm.get("message") or ""
    # Mentions are U+FFFC in the text; put the names back in, last first.
    for m in sorted(dm.get("mentions") or [], key=lambda m: -m.get("start", 0)):
        name = m.get("name") or store.name(conv_of(m.get("uuid"), m.get("number")))
        start = m.get("start", 0)
        text = text[:start] + "@" + name + text[start + m.get("length", 1):]
    if text:
        parts.append(text)
    for _, ctype, name in atts:
        if ctype.startswith("image/"):
            continue
        if ctype.startswith("audio/"):
            parts.append("[Sprachnachricht]")
        elif ctype.startswith("video/"):
            parts.append("[Video]")
        else:
            parts.append("[Datei%s]" % (": " + name if name else ""))
    if dm.get("sticker"):
        parts.append("[Sticker]")
    if dm.get("viewOnce"):
        parts.append("[Einmal-Ansicht]")
    if dm.get("pollCreate"):
        parts.append("[Umfrage: %s]" % (dm["pollCreate"].get("question") or ""))
    if dm.get("contacts"):
        parts.append("[Kontakt]")
    return " ".join(p for p in parts if p).replace("\n ", "\n")


def store_data(conv, author, out, dm, ts):
    """Stores one data message (incoming or sent from another device)."""
    if dm.get("reaction"):
        r = dm["reaction"]
        if r.get("isRemove"):
            return
        store.add_msg(conv, ts, author, out, "reagiert mit " + (r.get("emoji") or "?"))
        return
    if dm.get("remoteDelete"):
        mid = store.find(conv, author, dm["remoteDelete"].get("timestamp"))
        if mid:
            store.x("UPDATE msgs SET body='[gelöscht]' WHERE id=?", (mid,))
            store.x("DELETE FROM atts WHERE msg=?", (mid,))
            store.bump()
        return
    atts = attachments(dm.get("attachments"))
    body = describe(dm, atts)
    if not body and not atts:
        return  # profile key updates, expiration timers, group changes, …
    store.add_msg(conv, ts, author, out, body, atts)


def handle_envelope(env):
    author = conv_of(env.get("sourceUuid"), env.get("sourceNumber"))
    store.set_name(author, env.get("sourceNumber"), 0)
    store.set_name(author, env.get("sourceName"), 1)
    ts = env.get("timestamp") or int(time.time() * 1000)

    dm = env.get("dataMessage")
    edit = env.get("editMessage")
    if edit:
        dm = edit.get("dataMessage") or {}
    if dm is not None:
        g = dm.get("groupInfo")
        conv = "g:" + g["groupId"] if g else author
        if g:
            store.set_name(conv, g.get("groupName"), 2)
        if edit:
            mid = store.find(conv, author, edit.get("targetSentTimestamp"))
            if mid:
                store.x("UPDATE msgs SET body=? WHERE id=?",
                        (describe(dm, []) + " (bearbeitet)", mid))
                store.bump()
                return
        store_data(conv, author, 0, dm, dm.get("timestamp") or ts)
        return

    sync = env.get("syncMessage")
    if sync:
        sent = sync.get("sentMessage")
        if sent:
            g = sent.get("groupInfo")
            conv = "g:" + g["groupId"] if g else conv_of(sent.get("destinationUuid"),
                                                          sent.get("destinationNumber"))
            if not g:
                store.set_name(conv, sent.get("destinationNumber"), 0)
            store_data(conv, "self", 1, sent, sent.get("timestamp") or ts)
        for r in sync.get("readMessages") or []:
            sender = conv_of(r.get("senderUuid"), r.get("senderNumber"))
            row = store.q("SELECT conv, id FROM msgs WHERE author=? AND ts=?", (sender, r.get("timestamp")))
            if row:
                store.mark_read(row[0][0], row[0][1])
                store.bump()


# --- phone API ---------------------------------------------------------------

def need_signal():
    if signal.state == "koppeln":
        raise ValueError("Noch nicht mit Signal gekoppelt")
    if signal.state == "entkoppelt":
        raise ValueError("Signal hat das Gerät entkoppelt")


def cmd_chats():
    rows = store.q("""
        SELECT c.id, c.last_id, m.ts, m.out, m.body,
               (SELECT count(*) FROM msgs u WHERE u.conv=c.id AND u.id>c.read_upto AND u.out=0),
               (SELECT count(*) FROM atts a WHERE a.msg=m.id)
        FROM convs c JOIN msgs m ON m.id=c.last_id ORDER BY c.last_id DESC LIMIT 60""")
    lines = []
    for conv, _, ts, out, body, unread, natt in rows:
        preview = body or ("[Bild]" if natt else "")
        if out:
            preview = "Du: " + preview
        lines.append("\t".join([conv, phone_text(store.name(conv)), str(unread), when(ts),
                                phone_text(preview[:50])]))
    return "\n".join(lines)


def cmd_msgs(conv, after, before):
    after, before = int(after or 0), int(before or 0)
    if after:
        rows = store.q("SELECT id, ts, author, out, body FROM msgs WHERE conv=? AND id>? "
                       "ORDER BY id LIMIT ?", (conv, after, PAGE))
    else:
        rows = store.q("SELECT id, ts, author, out, body FROM msgs WHERE conv=? AND id<? "
                       "ORDER BY id DESC LIMIT ?", (conv, before or 1 << 62, PAGE))
        rows.reverse()
    group = conv.startswith("g:")
    lines = []
    for mid, ts, author, out, body in rows:
        imgs = [str(a) for a, ctype, path in store.q(
            "SELECT id, ctype, path FROM atts WHERE msg=?", (mid,))
            if ctype in IMAGE_TYPES and os.path.exists(path)]
        who = "Du" if out else (store.name(author) if group else "")
        lines.append("\t".join([str(mid), "1" if out else "0", phone_text(who), when(ts),
                                phone_text(body), ",".join(imgs)]))
    if rows:
        store.mark_read(conv, rows[-1][0])
    return "\n".join(lines)


def recipient(conv):
    kind, ident = conv.split(":", 1)
    return {"groupId": ident} if kind == "g" else {"recipient": [ident]}


def cmd_send(conv, text, atts=()):
    need_signal()
    if not store.q("SELECT 1 FROM convs WHERE id=?", (conv,)):
        raise ValueError("unbekannter Chat")
    params = recipient(conv)
    params["message"] = text
    if atts:
        params["attachments"] = [a[0] for a in atts]
    res = signal.rpc("send", params)
    ts = (res or {}).get("timestamp") or int(time.time() * 1000)
    return str(store.add_msg(conv, ts, "self", 1, text, atts))


def cmd_image(conv, data):
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
    except Exception:
        raise ValueError("kein gültiges Bild")
    fmt = (img.format or "JPEG").lower()
    ext = {"jpeg": "jpg"}.get(fmt, fmt)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s.%s" % (uuid.uuid4().hex, ext))
    with open(path, "wb") as f:
        f.write(data)
    return cmd_send(conv, "", [(path, Image.MIME.get(img.format, "image/jpeg"), "")])


def cmd_att(att, w, h):
    row = store.q("SELECT path, ctype FROM atts WHERE id=?", (int(att),))
    if not row or row[0][1] not in IMAGE_TYPES:
        raise ValueError("Bild nicht gefunden")
    w, h = max(16, min(1024, int(w))), max(16, min(1024, int(h)))
    os.makedirs(THUMB_DIR, exist_ok=True)
    cache = os.path.join(THUMB_DIR, "%d_%dx%d.jpg" % (int(att), w, h))
    if not os.path.exists(cache):
        try:
            img = Image.open(row[0][0])
            img = ImageOps.exif_transpose(img)
        except Exception:
            raise ValueError("Bild kann nicht gelesen werden")
        img.thumbnail((w, h))
        if img.mode != "RGB":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[3])
            img = bg
        img.save(cache, "JPEG", quality=70)
    with open(cache, "rb") as f:
        return f.read()


def dispatch(fields, payload):
    cmd = fields[0] if fields else ""
    if cmd == "ping":
        return "%s\t%s" % (signal.state, signal.number or "")
    need_signal()
    if cmd == "wait" and len(fields) >= 3:
        # Long poll: answers at once when something changed, else after the timeout.
        return str(store.wait(int(fields[1]), max(0, min(25, int(fields[2])))))
    if cmd == "chats":
        return cmd_chats()
    if cmd == "msgs" and len(fields) >= 4:
        return cmd_msgs(fields[1], fields[2], fields[3])
    if cmd == "send" and len(fields) >= 2:
        text = payload.decode("utf-8", "replace").strip()
        if not text:
            raise ValueError("leere Nachricht")
        return cmd_send(fields[1], text)
    if cmd == "img" and len(fields) >= 2:
        return cmd_image(fields[1], payload)
    if cmd == "att" and len(fields) >= 4:
        return cmd_att(fields[1], fields[2], fields[3])
    raise ValueError("unbekannter Befehl")


_fails = {"n": 0, "t": 0.0}
_fails_lock = threading.Lock()


def read_body(handler):
    if "chunked" in handler.headers.get("Transfer-Encoding", "").lower():
        data = bytearray()
        while True:
            size = int(handler.rfile.readline().split(b";")[0].strip() or b"0", 16)
            if size == 0:
                handler.rfile.readline()
                break
            if len(data) + size > MAX_BODY:
                raise ValueError("zu groß")
            data += handler.rfile.read(size)
            handler.rfile.readline()
        return bytes(data)
    n = int(handler.headers.get("Content-Length") or 0)
    if n > MAX_BODY:
        raise ValueError("zu groß")
    return handler.rfile.read(n)


class PhoneHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def reply(self, code, body, ctype="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def plain(self, code, text):
        self.reply(code, text.encode("utf-8"), "text/plain; charset=utf-8")

    def do_GET(self):
        self.plain(200, "Nokia-Signal-Brücke läuft.\n")

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path != "/x":
            return self.plain(404, "ERR\tunbekannte Adresse")
        try:
            frame = read_body(self)
        except (ValueError, OSError):
            return self.plain(413, "ERR\tAnfrage zu groß")
        opened = unseal(frame)
        if opened is None:
            with _fails_lock:
                if time.time() - _fails["t"] > 60:
                    _fails.update(n=0, t=time.time())
                _fails["n"] += 1
                slow = _fails["n"] > 5
            if slow:
                time.sleep(5)
            return self.plain(403, "ERR\tSchlüssel falsch")
        nonce, plain = opened
        try:
            (hlen,) = struct.unpack(">I", plain[:4])
            fields = plain[4:4 + hlen].decode("utf-8").split("\t")
            payload = plain[4 + hlen:]
            sent = int(fields.pop(0)) / 1000
        except (struct.error, ValueError, IndexError, UnicodeDecodeError):
            return self.reply(200, seal(b"ERR\tdefekte Anfrage\n"))
        if abs(time.time() - sent) > MAX_SKEW:
            # The phone corrects its clock offset and asks again.
            return self.reply(200, seal(b"ERR\tZEIT\t%d\n" % int(time.time() * 1000)))
        if not fresh_nonce(nonce):
            return self.reply(200, seal(b"ERR\tdoppelte Anfrage\n"))
        if fields and fields[0] != "wait":
            log("Handy: " + fields[0])
        try:
            res = dispatch(fields, payload)
        except (ValueError, RuntimeError) as e:
            return self.reply(200, seal(("ERR\t%s\n" % e).encode("utf-8")))
        except Exception as e:
            log("Fehler: %r" % e)
            return self.reply(200, seal(b"ERR\tinterner Fehler\n"))
        body = b"OK\n" + (res if isinstance(res, bytes) else res.encode("utf-8"))
        self.reply(200, seal(body))

    def log_message(self, fmt, *args):
        pass  # requests are logged in do_POST, without their content


# --- ingress page (inside Home Assistant) ------------------------------------

STATE_TEXT = {
    "startet": "startet …",
    "koppeln": "wartet auf Kopplung",
    "verbindet": "verbindet …",
    "verbunden": "verbunden",
    "getrennt": "getrennt, startet neu …",
    "entkoppelt": "vom Handy entkoppelt",
    "fehler": "signal-cli stürzt ab, siehe Protokoll",
}


class IngressHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def allowed(self):
        # Only Home Assistant's ingress proxy may open this page.
        if self.client_address[0] in ("172.30.32.2", "127.0.0.1"):
            return True
        self.send_error(403)
        return False

    def do_GET(self):
        if not self.allowed():
            return
        st = signal.state
        parts = ["<h1>Nokia-Signal</h1>",
                 "<p>Status: <b>%s</b>" % html.escape(STATE_TEXT.get(st, st))]
        if signal.number:
            parts.append(" &middot; Konto %s" % html.escape(signal.number))
        parts.append("</p>")
        if st == "koppeln":
            if signal.link_uri:
                svg = qrcode.make(signal.link_uri, image_factory=qrcode.image.svg.SvgPathImage,
                                  box_size=12).to_string(encoding="unicode")
                parts.append("<p>Am Pixel: Signal &rarr; Einstellungen &rarr; Verknüpfte Geräte "
                             "&rarr; <i>Gerät hinzufügen</i>, dann diesen Code scannen.</p>")
                parts.append('<div class="qr">%s</div>' % svg)
            else:
                parts.append("<p>QR-Code wird erzeugt …</p>")
        else:
            n = store.q("SELECT count(*) FROM msgs")[0][0]
            c = store.q("SELECT count(*) FROM convs")[0][0]
            parts.append("<p>%d Nachrichten in %d Chats gespeichert.</p>" % (n, c))
            if st in ("entkoppelt", "fehler", "getrennt") and signal.error:
                parts.append("<p><small>%s</small></p>" % html.escape(signal.error))
            parts.append('<form method="post" action="relink" onsubmit="return confirm('
                         "'Kopplung lösen und neu koppeln? Gespeicherte Chats bleiben erhalten.')\">"
                         '<button>Neu koppeln</button></form>')
        parts.append("<p>Schlüssel für das Handy: <code>%s</code></p>" % html.escape(KEY))
        # Reload while something is about to change, e.g. right after scanning.
        refresh = "<meta http-equiv=refresh content=5>" if st not in ("verbunden", "entkoppelt") else ""
        page = ("<!doctype html><meta charset=utf-8>" + refresh +
                "<meta name=viewport content='width=device-width'>"
                "<style>body{font-family:sans-serif;max-width:40em;margin:2em auto;padding:0 1em;"
                "color:var(--primary-text-color,#222)}.qr svg{width:300px;height:300px;"
                "background:#fff;padding:12px}</style>" + "".join(parts))
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.allowed():
            return
        if self.path.rstrip("/").endswith("relink"):
            log("Neu koppeln angefordert")
            threading.Thread(target=signal.relink, daemon=True).start()
        self.send_response(303)
        self.send_header("Location", "./")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


def main():
    global store
    os.makedirs(SIGNAL_DIR, exist_ok=True)
    store = Store(os.path.join(DATA, "chats.db"))
    threading.Thread(target=signal.run, daemon=True).start()
    ingress = ThreadingHTTPServer(("0.0.0.0", INGRESS_PORT), IngressHandler)
    threading.Thread(target=ingress.serve_forever, daemon=True).start()
    log("Lausche auf Port %d (Handy) und %d (Oberfläche)" % (PORT, INGRESS_PORT))
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), PhoneHandler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
