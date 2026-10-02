"""Rings a phone number over SIP and hangs up before anyone answers.

Minimal SIP client over UDP (RFC 3261) without audio: REGISTER, INVITE,
wait while it rings, CANCEL, unregister. Made for a FritzBox IP phone
("Telefoniegeräte → Neues Gerät → LAN/WLAN (IP-Telefon)"), works with any
registrar that uses digest auth. An unanswered call costs nothing.
Run by hand to test: python3 sipcall.py SERVER USER PASSWORD NUMBER [SECONDS]
"""

import hashlib
import random
import re
import socket
import sys
import time
import uuid


def _md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _rand():
    return uuid.uuid4().hex[:16]


class SipError(Exception):
    pass


class Call:
    def __init__(self, server, user, password, port=5060):
        self.server = server
        self.user = user
        self.password = password
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1)
        self.sock.connect((socket.gethostbyname(server), port))
        self.local, self.lport = self.sock.getsockname()
        self.cseq = random.randint(1, 1000)

    def close(self):
        self.sock.close()

    # --- messages -----------------------------------------------------------

    def _send(self, method, uri, to, call_id, ftag, branch, cseq, extra=(), body=""):
        lines = [
            "%s %s SIP/2.0" % (method, uri),
            "Via: SIP/2.0/UDP %s:%d;branch=%s;rport" % (self.local, self.lport, branch),
            "Max-Forwards: 70",
            "From: <sip:%s@%s>;tag=%s" % (self.user, self.server, ftag),
            "To: %s" % to,
            "Call-ID: %s" % call_id,
            "CSeq: %d %s" % (cseq, method),
            "Contact: <sip:%s@%s:%d>" % (self.user, self.local, self.lport),
            "User-Agent: nokia-signal",
        ]
        lines += list(extra)
        if body:
            lines.append("Content-Type: application/sdp")
        lines.append("Content-Length: %d" % len(body.encode("utf-8")))
        self.sock.send(("\r\n".join(lines) + "\r\n\r\n" + body).encode("utf-8"))

    def _recv(self, call_id, until):
        """Next response for this call, or None when until has passed."""
        while time.time() < until:
            try:
                data = self.sock.recv(65535).decode("utf-8", "replace")
            except socket.timeout:
                continue
            head = data.split("\r\n\r\n", 1)[0]
            first, _, rest = head.partition("\r\n")
            if not first.startswith("SIP/2.0 "):
                continue  # a request from the server (OPTIONS, NOTIFY …) – ignore
            hdr = {}
            for line in rest.split("\r\n"):
                k, _, v = line.partition(":")
                hdr.setdefault(k.strip().lower(), v.strip())
            if hdr.get("call-id", hdr.get("i")) != call_id:
                continue
            code = int(first.split()[1])
            cseq = hdr.get("cseq", "0 X").split()
            return code, first[12:].strip(), hdr, int(cseq[0]), cseq[1]
        return None

    def _auth(self, hdr, method, uri):
        key = "proxy-authenticate" if "proxy-authenticate" in hdr else "www-authenticate"
        ch = dict(re.findall(r'(\w+)="?([^",]*)"?', hdr[key]))
        ha1 = _md5("%s:%s:%s" % (self.user, ch.get("realm", ""), self.password))
        ha2 = _md5("%s:%s" % (method, uri))
        parts = 'username="%s", realm="%s", nonce="%s", uri="%s", algorithm=MD5' % (
            self.user, ch.get("realm", ""), ch.get("nonce", ""), uri)
        if "auth" in ch.get("qop", "").split(","):
            cnonce = _rand()
            resp = _md5("%s:%s:00000001:%s:auth:%s" % (ha1, ch["nonce"], cnonce, ha2))
            parts += ', qop=auth, nc=00000001, cnonce="%s"' % cnonce
        else:
            resp = _md5("%s:%s:%s" % (ha1, ch.get("nonce", ""), ha2))
        parts += ', response="%s"' % resp
        if "opaque" in ch:
            parts += ', opaque="%s"' % ch["opaque"]
        name = "Proxy-Authorization" if key == "proxy-authenticate" else "Authorization"
        return "%s: Digest %s" % (name, parts)

    # --- register -----------------------------------------------------------

    def register(self, expires):
        uri = "sip:" + self.server
        to = "<sip:%s@%s>" % (self.user, self.server)
        call_id, ftag, extra = _rand() + "@" + self.local, _rand(), ["Expires: %d" % expires]
        for _ in range(2):
            self.cseq += 1
            self._send("REGISTER", uri, to, call_id, ftag, "z9hG4bK" + _rand(), self.cseq, extra)
            while True:
                r = self._recv(call_id, time.time() + 5)
                if r is None:
                    raise SipError("keine Antwort vom SIP-Server " + self.server)
                code, reason, hdr, _, _ = r
                if code < 200:
                    continue
                if code in (401, 407):
                    extra = ["Expires: %d" % expires, self._auth(hdr, "REGISTER", uri)]
                    break
                if code < 300:
                    return
                raise SipError("Anmeldung: %d %s" % (code, reason))
        raise SipError("Anmeldung abgelehnt (Benutzer/Passwort?)")

    # --- call ---------------------------------------------------------------

    def ring(self, number, seconds):
        """Lets number ring for about seconds; returns a short result text."""
        uri = "sip:%s@%s" % (number, self.server)
        to = "<%s>" % uri
        call_id, ftag = _rand() + "@" + self.local, _rand()
        sdp = ("v=0\r\no=- %d 1 IN IP4 %s\r\ns=-\r\nc=IN IP4 %s\r\nt=0 0\r\n"
               "m=audio %d RTP/AVP 8 0\r\na=rtpmap:8 PCMA/8000\r\na=rtpmap:0 PCMU/8000\r\na=sendrecv\r\n"
               % (int(time.time()), self.local, self.local, self.lport + 2))
        extra = []
        start = time.time()
        for _ in range(2):
            self.cseq += 1
            branch = "z9hG4bK" + _rand()
            self._send("INVITE", uri, to, call_id, ftag, branch, self.cseq, extra, sdp)
            cancelled = False
            ringing = False
            while True:
                deadline = start + seconds if not cancelled else time.time() + 5
                r = self._recv(call_id, deadline)
                if r is None:
                    if cancelled:
                        return "abgebrochen (keine Bestätigung)"
                    # Time is up: stop ringing.
                    self._send("CANCEL", uri, to, call_id, ftag, branch, self.cseq)
                    cancelled = True
                    continue
                code, reason, hdr, cseq, method = r
                if method != "INVITE":
                    continue  # 200 for the CANCEL
                if code < 200:
                    ringing = ringing or code in (180, 183)
                    continue
                to_tag = hdr.get("to", hdr.get("t", to))
                if code < 300:
                    # Answered (person or mailbox): acknowledge and hang up at once.
                    m = re.search(r"<([^>]+)>", hdr.get("contact", hdr.get("m", "")))
                    target = m.group(1) if m else uri
                    self._send("ACK", target, to_tag, call_id, ftag, "z9hG4bK" + _rand(), cseq)
                    self.cseq += 1
                    self._send("BYE", target, to_tag, call_id, ftag, "z9hG4bK" + _rand(), self.cseq)
                    self._recv(call_id, time.time() + 3)
                    return "angenommen und aufgelegt"
                # Every final error answer to an INVITE gets an ACK in the same transaction.
                self._send("ACK", uri, to_tag, call_id, ftag, branch, cseq)
                if code in (401, 407) and not extra:
                    extra = [self._auth(hdr, "INVITE", uri)]
                    break
                if code == 487:
                    return "geklingelt" if ringing else "abgebrochen, bevor es klingelte"
                raise SipError("Anruf: %d %s" % (code, reason))
        raise SipError("Anruf abgelehnt (Benutzer/Passwort?)")


def ring(server, user, password, number, seconds=15):
    """Registers, rings number, unregisters. Returns a result text, raises SipError."""
    number = re.sub(r"[^0-9+*#]", "", number)
    call = Call(server, user, password)
    try:
        call.register(120)
        try:
            return call.ring(number, seconds)
        finally:
            try:
                call.register(0)
            except SipError:
                pass
    finally:
        call.close()


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    print(ring(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4],
               int(sys.argv[5]) if len(sys.argv) > 5 else 15))
