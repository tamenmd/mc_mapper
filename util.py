# util.py — Konsistente, kompakte Previews mit Medien (mit bs4-Fallback)
import re
import hashlib
from html import unescape
from pathlib import Path

# bs4 optional
try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except Exception:
    _HAS_BS4 = False

from .config import TAG_HASH_PREFIX

# --- Normalisierung von Feldnamen (robust ggü. Umlauten/Interpunktion) ---
def _norm_name(s: str) -> str:
    s = (s or "").lower()
    s = (s
         .replace("ä", "a")
         .replace("ö", "o")
         .replace("ü", "u")
         .replace("ß", "ss"))
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s

# Kandidatenlisten
FRONT_KEYS = {"vorderseite", "front", "question", "frage"}
BACK_KEYS  = {"rueckseite", "ruckseite", "back", "antwort", "answer"}


def _clean_ws_multiline(txt: str) -> str:
    txt = re.sub(r"[ \t\u00a0]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)  # max. zwei Umbrüche
    return txt.strip()


def _strip_html_simple(html: str, *, preview: bool = False, media_dir: str | None = None) -> str:
    """
    Fallback ohne BeautifulSoup:
      - <br> -> \n
      - <img src="..."> bleibt erhalten (bei preview=True ggf. in file:// umschreiben)
      - sonstiges HTML entfernt
    """
    if not html:
        return ""
    s = html

    # <br> -> \n
    s = re.sub(r'(?i)<br\s*/?>', '\n', s)

    # IMG: src extrahieren und ggf. auf file:// umschreiben
    def _img_repl(m):
        src = (m.group(1) or "").strip()
        if preview and media_dir and not src.lower().startswith(("http://","https://","file://")):
            src = Path(media_dir, src).absolute().as_uri()
        return f'<img src="{src}">'

    s = re.sub(r'(?is)<img\b[^>]*src="([^"]+)"[^>]*>', _img_repl, s)

    # Restliches HTML weg
    s = re.sub(r'(?is)<[^>]+>', ' ', s)

    # Normalisieren
    txt = unescape(s).replace('\r', '')
    txt = re.sub(r'[ \t]+', ' ', txt)
    txt = re.sub(r'\n{2,}', '\n', txt)
    return _clean_ws_multiline(txt)


def sanitize_keep_img(html: str, *, preview: bool = False, media_dir: str | None = None) -> str:
    """
    Entfernt HTML, behält aber <img src="...">.
    - <br> -> '\n'
    - Blockelemente fügen EINEN '\n' an (kompakt)
    - preview=True: src -> file:// Pfade (QTextBrowser)
    - Keine künstlichen \n um <img> (Spacing macht CSS)
    """
    if not html:
        return ""
    if not _HAS_BS4:
        return _strip_html_simple(html, preview=preview, media_dir=media_dir)

    soup = BeautifulSoup(html, "html.parser")

    for t in soup(["script", "style"]):
        t.decompose()

    for br in soup.find_all("br"):
        br.replace_with("\n")

    for blk in soup.find_all(["p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"]):
        blk.insert_after("\n")

    for im in soup.find_all("img"):
        src = (im.get("src") or "").strip()
        if not src:
            im.decompose()
            continue
        if preview and media_dir and not src.lower().startswith(("http://", "https://", "file://")):
            im["src"] = Path(media_dir, src).absolute().as_uri()

    for tag in list(soup.find_all(True)):
        if tag.name == "img":
            continue
        tag.unwrap()

    txt = unescape(str(soup))
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)
    txt = _clean_ws_multiline(txt)
    return txt


def strip_html_keep_media(html: str) -> str:
    if not html:
        return ""
    txt = sanitize_keep_img(html, preview=False, media_dir=None)
    # Für Feldspeicher: kompakt auf einer Zeile (Q/A-Felder)
    txt = txt.replace("\n", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def normalize_option_text(s: str) -> str:
    if s is None:
        return ""
    txt = strip_html_keep_media(s)
    txt = re.sub(r"^\s*([a-eA-E])(?:[\)\.\:\-])?\s+", "", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def find_model_by_name(col, name: str):
    for m in col.models.all():
        if m["name"] == name:
            return m
    return None


def _field(note, name: str) -> str:
    try:
        return note[name] or ""
    except Exception:
        return ""


def html_preview(note, media_dir: str | None = None):
    """
    ALT-Ansicht: kompakte Zeilen, Label inline (z. B. „Vorderseite: …“).
    Bilder werden blockig mit moderatem Abstand dargestellt.
    """
    all_names = [f["name"] for f in note.model()["flds"]]
    # Feldreihenfolge: bevorzugt Vorderseite/Rückseite zuerst, dann Rest
    front = [n for n in all_names if _norm_name(n) in FRONT_KEYS]
    back  = [n for n in all_names if _norm_name(n) in BACK_KEYS]
    others = [n for n in all_names if n not in front + back]
    ordered = front + back + others

    def _prep(val: str) -> str:
        txt = sanitize_keep_img(val, preview=True, media_dir=media_dir)
        return (txt or "(leer)").replace("\n", "<br>")

    rows = []
    for nm in ordered:
        html = _prep(_field(note, nm))
        rows.append(f"<div class='row'><span class='lbl'>{nm}:</span> <span class='val'>{html}</span></div>")

    css = """
    <style>
      .wrap { font-family: Segoe UI, Arial; font-size:12px; line-height:1.35; }
      .row  { margin: 4px 0 6px; }
      .lbl  { font-weight:700; display:inline-block; min-width:110px; }
      .val  { display:inline; }
      img   { max-width: 100%; height: auto; display:block; margin:6px 0; }
      i     { color:#888; }
    </style>
    """
    return css + "<div class='wrap'>" + "".join(rows) + "</div>"


def normalize_combo_key(prop: dict) -> str:
    parts = [
        prop.get("Frage", ""),
        prop.get("Antwort A", ""),
        prop.get("Antwort B", ""),
        prop.get("Antwort C", ""),
        prop.get("Antwort D", ""),
        prop.get("Antwort E", ""),
    ]
    txt = "||".join(re.sub(r"\s+", " ", p).strip() for p in parts)
    return txt.lower()


def key_to_tag(key: str) -> str:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{TAG_HASH_PREFIX}{h}"
