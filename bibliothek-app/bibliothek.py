"""Bibliothek: Bücher per ISBN oder Suche erfassen und als Website-Seite veröffentlichen.

Das Ingress-Panel (panel.html) spricht mit der kleinen JSON-API hier. Die
Buchdaten liegen in /data/buecher.json (kommt so mit ins HA-Backup), die Cover
in /data/cover/. Nach jeder Änderung schreibt die App eine fertige statische
Seite (ohne Cover) in den Website-Ordner unter /media, den die nginx-App
ausliefert. Die Website lädt dadurch nichts von fremden Servern.

Metadaten kommen von der Deutschen Nationalbibliothek (SRU, ohne Schlüssel)
und ersatzweise von Open Library.
"""
import html
import json
import os
import re
import secrets
import shutil
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.environ.get("BIBLIOTHEK_DATA", "/data"))
MEDIA = Path(os.environ.get("BIBLIOTHEK_MEDIA", "/media"))
PANEL = Path(__file__).with_name("panel.html")
PORT = int(os.environ.get("BIBLIOTHEK_PORT", "8099"))
# Nur das Ingress-Gateway des Supervisors darf zugreifen (lokal zum Testen auch 127.0.0.1).
ERLAUBT = {"172.30.32.2", "127.0.0.1", "::1"}
DEV = os.environ.get("BIBLIOTHEK_DEV") == "1"

BUECHER = DATA / "buecher.json"
COVER = DATA / "cover"
UA = "Bibliothek-HA-App/1.0 (privater Buchkatalog)"
STATUS = ("lese", "gelesen", "ungelesen")
STATUS_TITEL = {"lese": "Gerade am Lesen", "gelesen": "Gelesen", "ungelesen": "Noch ungelesen"}
FELDER = ("isbn", "titel", "untertitel", "autor", "verlag", "jahr", "seiten",
          "genre", "status", "oeffentlich", "notiz", "gelesen_am")
MAX_COVER = 3 * 1024 * 1024

lock = threading.Lock()
letzte_veroeffentlichung = {"zeit": None, "fehler": None}


# ---------- Speicher ----------

def optionen():
    try:
        return json.loads((DATA / "options.json").read_text())
    except (OSError, ValueError):
        return {}


def laden():
    try:
        return json.loads(BUECHER.read_text())
    except FileNotFoundError:
        return []


def speichern(buecher):
    tmp = BUECHER.with_suffix(".tmp")
    tmp.write_text(json.dumps(buecher, ensure_ascii=False, indent=1))
    os.replace(tmp, BUECHER)


# ---------- ISBN ----------

def isbn_normalisieren(text):
    """ISBN-10 oder -13 (mit Bindestrichen) → ISBN-13, sonst None."""
    s = re.sub(r"[^0-9Xx]", "", text or "").upper()
    if len(s) == 10 and re.fullmatch(r"\d{9}[\dX]", s):
        summe = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
        if summe % 11:
            return None
        s = "978" + s[:9]
        return s + str((10 - sum((1, 3)[i % 2] * int(c) for i, c in enumerate(s)) % 10) % 10)
    if len(s) == 13 and s.isdigit():
        if sum((1, 3)[i % 2] * int(c) for i, c in enumerate(s)) % 10:
            return None
        return s
    return None


# ---------- Netz ----------

def holen(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_COVER + 1), r.headers.get("Content-Type", "")


def bildtyp(daten):
    if daten[:3] == b"\xff\xd8\xff":
        return "jpg"
    if daten[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if daten[:4] == b"RIFF" and daten[8:12] == b"WEBP":
        return "webp"
    return None


def bild_holen(url):
    try:
        daten, _ = holen(url)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    # Platzhalterbilder (1×1 o. Ä.) sind winzig.
    if len(daten) > MAX_COVER or len(daten) < 1500 or not bildtyp(daten):
        return None
    return daten


# ---------- Deutsche Nationalbibliothek ----------

MARC = "{http://www.loc.gov/MARC21/slim}"

# DDC-Sachgruppen der DNB → Genre (das längste passende Präfix gewinnt)
SACHGRUPPEN = {
    "0": "Allgemeines", "004": "Informatik", "07": "Journalismus",
    "1": "Philosophie", "13": "Esoterik", "15": "Psychologie",
    "2": "Religion",
    "3": "Gesellschaft", "30": "Soziologie", "32": "Politik", "33": "Wirtschaft",
    "34": "Recht", "35": "Politik", "36": "Gesellschaft", "37": "Bildung", "39": "Kultur",
    "4": "Sprache",
    "5": "Naturwissenschaften", "51": "Mathematik", "53": "Physik", "54": "Chemie",
    "57": "Biologie", "58": "Biologie", "59": "Biologie",
    "6": "Technik", "61": "Medizin", "62": "Ingenieurwissenschaften", "64": "Haushalt",
    "65": "Wirtschaft",
    "7": "Kunst", "72": "Architektur", "77": "Fotografie", "78": "Musik", "79": "Sport",
    "8": "Literatur",
    "9": "Geschichte", "91": "Reisen", "92": "Biografie",
}


def genre_aus_ddc(werte):
    if "B" in werte:
        return "Belletristik"
    if "K" in werte:
        return "Kinder- und Jugendbuch"
    for w in werte:
        if re.match(r"\d", w):
            for laenge in range(len(w), 0, -1):
                if w[:laenge] in SACHGRUPPEN:
                    return SACHGRUPPEN[w[:laenge]]
    return ""


# GND-Schlagwörter der DNB (Wortteile, Kleinschreibung). Die Sachgruppe ordnet nach dem
# Gegenstand ein ("KI und Kunst" → Kunst); ein Philosophie-Schlagwort zeigt dagegen das Fach.
PHILOSOPHIE = ("philosoph", "ästhetik", "ethik", "metaphysik", "erkenntnistheorie",
               "ontologie", "logik", "moral", "stoa", "stoizismus", "utilitarismus", "existenzialismus",
               "absurde", "phänomenologie", "kritische theorie", "deontologie")
# Nur wenn die DNB keine Sachgruppe vergeben hat (oft bei älteren Ausgaben); erste passende Gruppe gewinnt.
SCHLAGWORT_GENRES = [
    ("Philosophie", PHILOSOPHIE),
    ("Soziologie", ("soziologie", "gesellschaft", "massenkultur",
                    "kulturindustrie", "massenpsychologie", "öffentliche meinung")),
    ("Politik", ("politik", "demokratie", "diktatur", "faschismus", "totalitarismus", "staat")),
    ("Wirtschaft", ("kapitalismus", "ökonomie", "wirtschaft")),
    ("Psychologie", ("psychologie",)),
    ("Religion", ("religion", "theologie", "christentum", "bibel")),
    ("Geschichte", ("geschichte",)),
]


def schlagwort_passt(schlagwoerter, teile):
    # Teilwort genügt: "Umweltethik" → ethik, "Geschichte 1949-1962" → geschichte
    return any(t in sw.lower() for sw in schlagwoerter for t in teile)


def genre_bestimmen(ddc, schlagwoerter):
    if "B" not in ddc and schlagwort_passt(schlagwoerter, PHILOSOPHIE):
        return "Philosophie"
    genre = genre_aus_ddc(ddc)
    if genre:
        return genre
    for name, teile in SCHLAGWORT_GENRES:
        if schlagwort_passt(schlagwoerter, teile):
            return name
    return ""


def name_umdrehen(name):
    """'Hesse, Hermann' → 'Hermann Hesse', 'Horkheimer, Max, (1895-1973)' → 'Max Horkheimer'"""
    name = unicodedata.normalize("NFC", name.replace("\x98", "").replace("\x9c", ""))
    name = re.sub(r"\(.*?\)", "", name)
    teile = [t.strip() for t in name.split(",") if t.strip(" /:;")]
    if len(teile) >= 2:
        return f"{teile[1]} {teile[0]}"
    return teile[0] if teile else ""


def aufraeumen(text):
    # DNB markiert Artikel zum Sortieren mit U+0098/U+009C und liefert Umlaute teils zerlegt (NFD).
    text = unicodedata.normalize("NFC", (text or "").replace("\x98", "").replace("\x9c", ""))
    return re.sub(r"\s*[/:;,.=]\s*$", "", text.strip()).strip()


def dnb_satz(rec):
    def felder(tag):
        return rec.findall(f"{MARC}datafield[@tag='{tag}']")

    def unter(feld, code):
        return [s.text or "" for s in feld.findall(f"{MARC}subfield[@code='{code}']")]

    def erstes(tag, code):
        for f in felder(tag):
            for w in unter(f, code):
                if w.strip():
                    return w
        return ""

    isbns = []
    for f in felder("020"):
        for w in unter(f, "a") + unter(f, "9"):
            n = isbn_normalisieren(w)
            if n and n not in isbns:
                isbns.append(n)

    autoren = []
    for tag in ("100", "700"):
        for f in felder(tag):
            rollen = unter(f, "4") + unter(f, "e")
            if tag == "700" and not any(r in ("aut", "Verfasser") for r in rollen):
                continue
            for n in unter(f, "a"):
                n = name_umdrehen(n)
                if n and n not in autoren:
                    autoren.append(n)
    if not autoren:
        autoren = [aufraeumen(erstes("245", "c"))] if erstes("245", "c") else []

    jahr = ""
    for tag in ("264", "260"):
        m = re.search(r"\d{4}", erstes(tag, "c"))
        if m:
            jahr = m.group()
            break
    m = re.search(r"(\d+)\s*S\.", erstes("300", "a"))
    ddc = [w for f in felder("082") + felder("083") for w in unter(f, "a")]
    schlagwoerter = [aufraeumen(w) for f in felder("650") + felder("689") for w in unter(f, "a")]

    return {
        "quelle": "DNB",
        "isbn": isbns[0] if isbns else "",
        "isbns": isbns,
        "titel": aufraeumen(erstes("245", "a")),
        "untertitel": aufraeumen(erstes("245", "b")),
        "autor": ", ".join(autoren),
        "verlag": aufraeumen(erstes("264", "b") or erstes("260", "b")),
        "jahr": jahr,
        "seiten": m.group(1) if m else "",
        "genre": genre_bestimmen(ddc, schlagwoerter),
    }


def dnb_suche(abfrage, anzahl=20):
    url = "https://services.dnb.de/sru/dnb?" + urllib.parse.urlencode({
        "version": "1.1", "operation": "searchRetrieve", "query": abfrage,
        "recordSchema": "MARC21-xml", "maximumRecords": str(anzahl)})
    daten, _ = holen(url, timeout=20)
    wurzel = ET.fromstring(daten)
    return [dnb_satz(r) for r in wurzel.iter(f"{MARC}record")]


# ---------- Open Library ----------

OL_GENRES = [
    ("fiction", "Belletristik"), ("novel", "Belletristik"), ("philosophy", "Philosophie"),
    ("psychology", "Psychologie"), ("sociology", "Soziologie"), ("political", "Politik"),
    ("history", "Geschichte"), ("economics", "Wirtschaft"), ("religion", "Religion"),
    ("science", "Naturwissenschaften"), ("biography", "Biografie"), ("poetry", "Lyrik"),
    ("drama", "Drama"), ("computer", "Informatik"),
]


def genre_aus_themen(themen):
    klein = " | ".join(themen).lower()
    for stichwort, genre in OL_GENRES:
        if stichwort in klein:
            return genre
    return ""


def ol_isbn(isbn):
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    daten = json.loads(holen(url)[0]).get(f"ISBN:{isbn}")
    if not daten:
        return None
    m = re.search(r"\d{4}", daten.get("publish_date", ""))
    return {
        "quelle": "Open Library",
        "isbn": isbn,
        "titel": daten.get("title", ""),
        "untertitel": daten.get("subtitle", ""),
        "autor": ", ".join(a["name"] for a in daten.get("authors", [])),
        "verlag": (daten.get("publishers") or [{}])[0].get("name", ""),
        "jahr": m.group() if m else "",
        "seiten": str(daten.get("number_of_pages", "") or ""),
        "genre": genre_aus_themen([s["name"] for s in daten.get("subjects", [])]),
        "cover_url": (daten.get("cover") or {}).get("large", ""),
    }


def ol_suche(titel, autor, frei):
    p = {"limit": "15", "fields": "title,subtitle,author_name,first_publish_year,isbn,"
         "cover_i,publisher,number_of_pages_median,subject"}
    if titel:
        p["title"] = titel
    if autor:
        p["author"] = autor
    if frei:
        p["q"] = frei
    daten = json.loads(holen("https://openlibrary.org/search.json?" + urllib.parse.urlencode(p))[0])
    treffer = []
    for d in daten.get("docs", []):
        isbns = [n for n in (isbn_normalisieren(i) for i in d.get("isbn", [])) if n]
        # deutsche Ausgaben zuerst anbieten
        isbns.sort(key=lambda n: not n.startswith("9783"))
        treffer.append({
            "quelle": "Open Library",
            "isbn": isbns[0] if isbns else "",
            "titel": d.get("title", ""),
            "untertitel": d.get("subtitle", ""),
            "autor": ", ".join(d.get("author_name", [])),
            "verlag": (d.get("publisher") or [""])[0],
            "jahr": str(d.get("first_publish_year", "") or ""),
            "seiten": str(d.get("number_of_pages_median", "") or ""),
            "genre": genre_aus_themen(d.get("subject", [])),
            "cover_url": f"https://covers.openlibrary.org/b/id/{d['cover_i']}-L.jpg" if d.get("cover_i") else "",
        })
    return treffer


# ---------- Nachschlagen ----------

def cover_url_dnb(isbn):
    return f"https://portal.dnb.de/opac/mvb/cover?isbn={isbn}"


def isbn_nachschlagen(isbn):
    treffer = None
    try:
        saetze = dnb_suche(f"num={isbn}", 5)
        treffer = next((s for s in saetze if isbn in s["isbns"]), None)
    except (urllib.error.URLError, OSError, ET.ParseError) as e:
        print(f"DNB: {e}")
    try:
        ol = ol_isbn(isbn)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"Open Library: {e}")
        ol = None
    if treffer is None and ol is None:
        return None
    if treffer is None:
        treffer = ol
    elif ol:
        for k in ("titel", "autor", "verlag", "jahr", "seiten", "genre"):
            treffer[k] = treffer[k] or ol[k]
    treffer["isbn"] = isbn
    treffer["cover_urls"] = [cover_url_dnb(isbn), f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"]
    if ol and ol.get("cover_url"):
        treffer["cover_urls"].append(ol["cover_url"])
    return treffer


def suchen(titel, autor, frei):
    teile = []
    if titel:
        teile.append(f'tit="{titel}"' if " " in titel else f"tit={titel}")
    if autor:
        teile.append(f'per="{autor}"' if " " in autor else f"per={autor}")
    if frei:
        teile.append(" and ".join(f"woe={w}" for w in frei.split()))
    ergebnisse, fehler = [], []
    if teile:
        try:
            for s in dnb_suche(" and ".join(teile) + " and mat=books"):
                if s["titel"]:
                    s["cover_url"] = cover_url_dnb(s["isbn"]) if s["isbn"] else ""
                    ergebnisse.append(s)
        except (urllib.error.URLError, OSError, ET.ParseError) as e:
            fehler.append(f"DNB: {e}")
    try:
        ergebnisse += ol_suche(titel, autor, frei)
    except (urllib.error.URLError, OSError, ValueError) as e:
        fehler.append(f"Open Library: {e}")
    # gleiche Ausgabe nur einmal
    gesehen, eindeutig = set(), []
    for e in ergebnisse:
        schluessel = e["isbn"] or (e["titel"].lower(), e["autor"].lower(), e["jahr"])
        if schluessel not in gesehen:
            gesehen.add(schluessel)
            e.pop("isbns", None)
            eindeutig.append(e)
    return eindeutig, fehler


# ---------- Bücher ----------

def neue_id(isbn):
    return isbn or "o" + secrets.token_hex(5)


def cover_speichern(buch, daten):
    typ = bildtyp(daten)
    if not typ:
        raise ValueError("Kein JPEG-, PNG- oder WebP-Bild")
    COVER.mkdir(parents=True, exist_ok=True)
    if buch.get("cover"):
        (COVER / buch["cover"]).unlink(missing_ok=True)
    name = f"{buch['id']}-{secrets.token_hex(3)}.{typ}"  # neuer Name, damit Browser nicht cachen
    (COVER / name).write_bytes(daten)
    buch["cover"] = name


def cover_suchen(buch, urls):
    for url in urls:
        if url:
            daten = bild_holen(url)
            if daten:
                cover_speichern(buch, daten)
                return True
    return False


def bereinigen(eingabe):
    buch = {}
    for k in FELDER:
        if k not in eingabe:
            continue
        v = eingabe[k]
        if k == "oeffentlich":
            buch[k] = bool(v)
        elif k == "status":
            if v not in STATUS:
                raise ValueError("Unbekannter Status")
            buch[k] = v
        elif k == "isbn":
            buch[k] = isbn_normalisieren(v) or "" if v else ""
            if v and not buch[k]:
                raise ValueError("ISBN ungültig")
        else:
            buch[k] = str(v or "").strip()[:500]
    return buch


def buch_anlegen(buecher, felder, cover_urls=()):
    neu = {"titel": "", "untertitel": "", "autor": "", "verlag": "", "jahr": "", "seiten": "",
           "genre": "", "status": "ungelesen", "oeffentlich": True, "notiz": "", "gelesen_am": "",
           "isbn": "", "cover": ""}
    neu.update(bereinigen(felder))
    if not neu["titel"]:
        raise ValueError("Titel fehlt")
    neu["id"] = neue_id(neu["isbn"])
    if any(b["id"] == neu["id"] for b in buecher):
        neu["id"] = neue_id("")
    neu["hinzugefuegt"] = date.today().isoformat()
    urls = list(cover_urls)
    if neu["isbn"]:
        urls += [cover_url_dnb(neu["isbn"]), f"https://covers.openlibrary.org/b/isbn/{neu['isbn']}-L.jpg?default=false"]
    cover_suchen(neu, urls)
    buecher.append(neu)
    return neu


def nachname(autor):
    erster = re.split(r",| und | & |;", autor or "")[0].strip()
    return erster.split()[-1].lower() if erster else "~"


def sortierschluessel(b):
    return (nachname(b.get("autor")), (b.get("titel") or "").lower())


# ---------- Website ----------

def website_ordner():
    teil = optionen().get("website_ordner", "website/bibliothek").strip("/")
    if ".." in teil.split("/"):
        raise ValueError("website_ordner ungültig")
    return MEDIA / teil, teil


def veroeffentlichen(buecher):
    ziel, teil = website_ordner()
    tiefe = len(teil.split("/")) - 1
    zurueck = "../" * tiefe if tiefe else "./"
    wurzel = MEDIA / teil.split("/")[0]
    try:
        m = re.search(r"style\.css\?v=(\w+)", (wurzel / "index.html").read_text())
        css = f"style.css?v={m.group(1)}" if m else "style.css"
    except OSError:
        css = "style.css"

    sichtbar = sorted((b for b in buecher if b.get("oeffentlich")), key=sortierschluessel)
    ziel.mkdir(parents=True, exist_ok=True)
    # Cover bleiben bewusst nur im Panel (Urheberrecht der Verlage).
    shutil.rmtree(ziel / "cover", ignore_errors=True)

    tmp = ziel / "index.html.tmp"
    tmp.write_text(seite(sichtbar, zurueck, css))
    os.replace(tmp, ziel / "index.html")


def e(text):
    return html.escape(str(text or ""), quote=True)


def karte(b):
    meta = " · ".join(x for x in (e(b.get("jahr")), e(b.get("verlag"))) if x)
    genre = f'<span class="tag-sm">{e(b["genre"])}</span>' if b.get("genre") else ""
    unter = f'<p class="buch-unter">{e(b["untertitel"])}</p>' if b.get("untertitel") else ""
    return f"""<li class="buch" data-genre="{e(b.get('genre'))}">
  <p class="buch-autor">{e(b.get('autor'))}</p>
  <h3>{e(b['titel'])}</h3>
  {unter}
  <p class="buch-meta">{genre}{meta}</p>
</li>"""


def seite(buecher, zurueck, css):
    genres = sorted({b["genre"] for b in buecher if b.get("genre")}, key=str.lower)
    abschnitte = []
    for status in STATUS:
        liste = [b for b in buecher if b.get("status") == status]
        if not liste:
            continue
        abschnitte.append(
            f'<section class="regal" id="{status}">\n'
            f'<h2>{STATUS_TITEL[status]} <span class="anzahl">{len(liste)}</span></h2>\n'
            f'<ul class="buecher">\n' + "\n".join(karte(b) for b in liste) + "\n</ul>\n</section>")
    if not abschnitte:
        abschnitte.append('<p class="article-meta">Hier stehen bald meine Bücher.</p>')
    knoepfe = "".join(f'<button type="button" data-genre="{e(g)}">{e(g)}</button>' for g in genres)
    filter_html = (f'<div class="genre-filter" hidden><button type="button" data-genre="" '
                   f'aria-pressed="true">Alle</button>{knoepfe}</div>') if len(genres) > 1 else ""
    anzahl = len(buecher)
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bibliothek – Enzo Brandsch</title>
  <meta name="description" content="Was neben meinem Lesesessel steht: gelesen, ungelesen und gerade am Lesen.">
  <meta name="color-scheme" content="dark">
  <meta name="theme-color" content="#030506">
  <link rel="icon" href="{zurueck}wappen-transparent.svg" type="image/svg+xml">
  <link rel="stylesheet" href="{zurueck}{css}">
  <style>
    .regal {{ margin: 0 0 3rem; }}
    .regal h2 {{ font-size: 1.6rem; margin: 0 0 1.2rem; }}
    .anzahl {{ font-family: var(--sans); font-size: 0.9rem; color: var(--muted); font-weight: 400; }}
    .buecher {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 1rem;
      grid-template-columns: repeat(auto-fill, minmax(min(100%, 21rem), 1fr)); }}
    .buch {{ padding: 1rem 1.2rem; border: 1px solid var(--line); border-radius: 12px;
      background: rgba(8, 18, 20, 0.7); min-width: 0; }}
    .buch h3 {{ margin: 0.1rem 0 0; font-size: 1.15rem; line-height: 1.3; }}
    .buch-autor, .buch-unter, .buch-meta {{ margin: 0; color: var(--muted); font-size: 0.88rem; }}
    .buch-unter {{ font-family: var(--serif); font-style: italic; }}
    .buch-meta {{ margin-top: 0.5rem; font-size: 0.8rem; }}
    .buch-meta .tag-sm {{ margin: 0 0.5rem 0 0; }}
    .genre-filter {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0 0 2.5rem; }}
    .genre-filter button {{ font: inherit; font-size: 0.85rem; padding: 0.25rem 0.8rem; cursor: pointer;
      border: 1px solid var(--line); border-radius: 999px; background: none; color: var(--muted); }}
    .genre-filter button[aria-pressed="true"] {{ background: var(--accent-soft); color: var(--accent); }}
  </style>
</head>
<body class="subpage">
  <div class="crest" aria-hidden="true"></div>

  <nav class="navbar" aria-label="Navigation">
    <a class="brand" href="{zurueck}index.html">
      <img src="{zurueck}wappen-transparent.svg" alt="" width="30" height="33">
      <span>Enzo Brandsch</span>
    </a>
    <ul>
      <li><a href="{zurueck}index.html#ueber">Über mich</a></li>
      <li><a href="{zurueck}index.html#werdegang">Werdegang</a></li>
      <li><a href="{zurueck}texte/index.html">Beiträge</a></li>
    </ul>
  </nav>

  <header class="page-head">
    <div class="page-head-inner">
      <a class="back" href="{zurueck}index.html">← Zur Startseite</a>
      <p class="eyebrow">Neben dem Lesesessel</p>
      <h1 class="article-title">Bibliothek</h1>
      <p class="article-meta">{anzahl} {"Buch" if anzahl == 1 else "Bücher"} aus meinem Regal – was ich gerade lese, was ich gelesen habe und was noch wartet.</p>
    </div>
  </header>

  <main class="article">
    {filter_html}
    {chr(10).join(abschnitte)}
  </main>

  <footer class="footer">
    <img src="{zurueck}wappen-transparent.svg" alt="" width="36" height="39">
    <p>© {date.today().year} Enzo Brandsch · Aachen · <a href="{zurueck}impressum.html">Impressum &amp; Datenschutz</a></p>
  </footer>
  <script>
    (function () {{
      var leiste = document.querySelector(".genre-filter");
      if (!leiste) return;
      leiste.hidden = false;
      leiste.addEventListener("click", function (ev) {{
        var k = ev.target.closest("button");
        if (!k) return;
        var g = k.getAttribute("data-genre");
        leiste.querySelectorAll("button").forEach(function (b) {{ b.setAttribute("aria-pressed", b === k); }});
        document.querySelectorAll(".buch").forEach(function (b) {{ b.hidden = g && b.getAttribute("data-genre") !== g; }});
        document.querySelectorAll(".regal").forEach(function (r) {{ r.hidden = !r.querySelector(".buch:not([hidden])"); }});
      }});
    }})();
  </script>
</body>
</html>
"""


def nach_aenderung(buecher):
    speichern(buecher)
    try:
        veroeffentlichen(buecher)
        letzte_veroeffentlichung.update(zeit=date.today().isoformat(), fehler=None)
    except (OSError, ValueError) as ex:
        print(f"Veröffentlichen fehlgeschlagen: {ex}")
        letzte_veroeffentlichung["fehler"] = str(ex)


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Bibliothek"

    def log_message(self, fmt, *args):
        if DEV:
            super().log_message(fmt, *args)

    def antwort(self, code, daten=None, typ="application/json; charset=utf-8", roh=None):
        body = roh if roh is not None else json.dumps(daten, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if "json" in typ or "html" in typ else "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def fehler(self, code, text):
        self.antwort(code, {"fehler": text})

    def koerper(self, roh=False):
        laenge = int(self.headers.get("Content-Length") or 0)
        if laenge > MAX_COVER:
            raise ValueError("Zu groß")
        daten = self.rfile.read(laenge)
        return daten if roh else json.loads(daten or b"{}")

    def erlaubt(self):
        if self.client_address[0] in ERLAUBT or DEV:
            return True
        self.fehler(403, "Nur über Home Assistant erreichbar")
        return False

    def pfad(self):
        return urllib.parse.urlsplit(self.path)

    def do_GET(self):
        if not self.erlaubt():
            return
        p = self.pfad()
        if p.path in ("/", "/index.html"):
            return self.antwort(200, typ="text/html; charset=utf-8", roh=PANEL.read_bytes())
        if p.path == "/api/buecher":
            with lock:
                buecher = laden()
            return self.antwort(200, {"buecher": buecher, "veroeffentlicht": letzte_veroeffentlichung,
                                      "website_ordner": optionen().get("website_ordner", "website/bibliothek")})
        if p.path == "/api/suche":
            q = urllib.parse.parse_qs(p.query)
            titel, autor, frei = (q.get(k, [""])[0].strip() for k in ("titel", "autor", "frei"))
            if not (titel or autor or frei):
                return self.fehler(400, "Titel oder Autor angeben")
            treffer, fehler = suchen(titel, autor, frei)
            return self.antwort(200, {"treffer": treffer, "fehler": fehler})
        m = re.fullmatch(r"/cover/([\w.-]+)", p.path)
        if m and (COVER / m.group(1)).is_file():
            datei = COVER / m.group(1)
            typ = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[datei.suffix[1:]]
            return self.antwort(200, typ=typ, roh=datei.read_bytes())
        self.fehler(404, "Nicht gefunden")

    def do_POST(self):
        if not self.erlaubt():
            return
        p = self.pfad().path
        try:
            if p == "/api/isbn":
                isbn = isbn_normalisieren(self.koerper().get("isbn"))
                if not isbn:
                    return self.fehler(400, "Das ist keine gültige ISBN")
                with lock:
                    vorhanden = next((b for b in laden() if b.get("isbn") == isbn), None)
                if vorhanden:
                    return self.antwort(200, {"buch": vorhanden, "neu": False})
                treffer = isbn_nachschlagen(isbn)
                if not treffer:
                    return self.fehler(404, "Zu dieser ISBN nichts gefunden – über die Suche oder von Hand eintragen")
                with lock:
                    buecher = laden()
                    buch = buch_anlegen(buecher, treffer, treffer.get("cover_urls", []))
                    nach_aenderung(buecher)
                return self.antwort(200, {"buch": buch, "neu": True})
            if p == "/api/buecher":
                felder = self.koerper()
                with lock:
                    buecher = laden()
                    if felder.get("isbn"):
                        n = isbn_normalisieren(felder["isbn"])
                        vorhanden = next((b for b in buecher if n and b.get("isbn") == n), None)
                        if vorhanden:
                            return self.antwort(200, {"buch": vorhanden, "neu": False})
                    url = felder.get("cover_url", "")
                    urls = [url] if url.startswith("https://") else []
                    buch = buch_anlegen(buecher, felder, urls)
                    nach_aenderung(buecher)
                return self.antwort(200, {"buch": buch, "neu": True})
            m = re.fullmatch(r"/api/buecher/(\w+)/cover-suchen", p)
            if m:
                with lock:
                    buecher = laden()
                    buch = next((b for b in buecher if b["id"] == m.group(1)), None)
                    if not buch:
                        return self.fehler(404, "Buch nicht gefunden")
                    if not buch.get("isbn"):
                        return self.fehler(400, "Ohne ISBN kann ich kein Cover suchen – lade eins hoch")
                    isbn = buch["isbn"]
                    gefunden = cover_suchen(buch, [cover_url_dnb(isbn), f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"])
                    if gefunden:
                        nach_aenderung(buecher)
                if not gefunden:
                    return self.fehler(404, "Kein Cover gefunden")
                return self.antwort(200, {"buch": buch})
            if p == "/api/veroeffentlichen":
                with lock:
                    nach_aenderung(laden())
                return self.antwort(200, {"veroeffentlicht": letzte_veroeffentlichung})
        except ValueError as ex:
            return self.fehler(400, str(ex))
        self.fehler(404, "Nicht gefunden")

    def do_PATCH(self):
        if not self.erlaubt():
            return
        m = re.fullmatch(r"/api/buecher/(\w+)", self.pfad().path)
        if not m:
            return self.fehler(404, "Nicht gefunden")
        try:
            aenderung = bereinigen(self.koerper())
        except ValueError as ex:
            return self.fehler(400, str(ex))
        with lock:
            buecher = laden()
            buch = next((b for b in buecher if b["id"] == m.group(1)), None)
            if not buch:
                return self.fehler(404, "Buch nicht gefunden")
            if aenderung.get("status") == "gelesen" and buch.get("status") != "gelesen" and "gelesen_am" not in aenderung:
                aenderung["gelesen_am"] = date.today().isoformat()
            if "titel" in aenderung and not aenderung["titel"]:
                return self.fehler(400, "Titel darf nicht leer sein")
            buch.update(aenderung)
            nach_aenderung(buecher)
        self.antwort(200, {"buch": buch})

    def do_PUT(self):
        if not self.erlaubt():
            return
        m = re.fullmatch(r"/api/buecher/(\w+)/cover", self.pfad().path)
        if not m:
            return self.fehler(404, "Nicht gefunden")
        try:
            daten = self.koerper(roh=True)
            with lock:
                buecher = laden()
                buch = next((b for b in buecher if b["id"] == m.group(1)), None)
                if not buch:
                    return self.fehler(404, "Buch nicht gefunden")
                cover_speichern(buch, daten)
                nach_aenderung(buecher)
        except ValueError as ex:
            return self.fehler(400, str(ex))
        self.antwort(200, {"buch": buch})

    def do_DELETE(self):
        if not self.erlaubt():
            return
        m = re.fullmatch(r"/api/buecher/(\w+)", self.pfad().path)
        if not m:
            return self.fehler(404, "Nicht gefunden")
        with lock:
            buecher = laden()
            buch = next((b for b in buecher if b["id"] == m.group(1)), None)
            if not buch:
                return self.fehler(404, "Buch nicht gefunden")
            buecher.remove(buch)
            if buch.get("cover"):
                (COVER / buch["cover"]).unlink(missing_ok=True)
            nach_aenderung(buecher)
        self.antwort(200, {"ok": True})


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    COVER.mkdir(exist_ok=True)
    with lock:
        nach_aenderung(laden())
    print(f"Bibliothek läuft auf Port {PORT}, Website-Ordner: {website_ordner()[0]}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
