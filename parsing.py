import re
import json
import urllib.request
import urllib.error
import os
import base64
import mimetypes
from aqt import mw  # Zugriff auf Anki Settings & Media DB
from .util import strip_html_keep_media, normalize_option_text

# ---- Patterns
LETTER_ONLY   = re.compile(r'^\s*([a-eA-E])\s*$')
BIN_5_SPACED  = re.compile(r'\b([01])\s*([01])\s*([01])\s*([01])\s*([01])\b')
BIN_5_COMPACT = re.compile(r'\b[01]{5}\b')
LETTER_PLUS   = re.compile(r'^\s*([a-eA-E])(?:[\)\.\:\-])?\s+(.*)$')
STRICT_OPT_MARK  = re.compile(r'(?<![A-Za-z0-9])([a-eA-E])[\)\.\:\-]\s+', re.M)
LENIENT_OPT_MARK = re.compile(r'(?<![A-Za-z0-9])([a-eA-E])\s+', re.M)

def _field_names(note):
    return [f["name"] for f in note.model()["flds"]]

def _get(note, name):
    names = _field_names(note)
    return note[name] if name in names else ""

def _pick_best_sequence(txt: str):
    def collect(mark_pat):
        ms = list(mark_pat.finditer(txt))
        if not ms: return []
        runs = []
        expected = ['a', 'b', 'c', 'd', 'e']
        starts = [i for i, m in enumerate(ms) if m.group(1).lower() == 'a']
        for si in starts:
            seq, ei = [], 0
            for m in ms[si:]:
                ch = m.group(1).lower()
                if ch == expected[ei]:
                    seq.append(m); ei = min(ei + 1, 4)
                elif ch in expected[ei+1:]:
                    seq.append(m); ei = expected.index(ch)
                if len(seq) == 5: break
            if 3 <= len(seq) <= 5: runs.append(seq)
        return runs

    runs = collect(STRICT_OPT_MARK)
    if not runs: runs = collect(LENIENT_OPT_MARK)
    if not runs: return []
    runs.sort(key=lambda r: r[-1].end(), reverse=True)
    return runs[0]

def _parse_front_stream(html: str):
    txt = strip_html_keep_media(html)
    seq = _pick_best_sequence(txt)
    if not seq: return txt.strip(), []
    q = txt[:seq[0].start()].strip()
    opts = []
    for i, m in enumerate(seq):
        s = m.end()
        e = seq[i + 1].start() if i + 1 < len(seq) else len(txt)
        chunk = txt[s:e].strip()
        if chunk: opts.append(normalize_option_text(chunk))
    return q, opts[:5]

def _parse_structured_fields(note):
    names = _field_names(note)
    fmap = {n.lower(): n for n in names}
    q_key = (fmap.get("question") or fmap.get("frage") or fmap.get("front") or fmap.get("vorderseite") or (names[0] if names else None))
    if not q_key: return None
    question = strip_html_keep_media(_get(note, q_key))
    opt_values = [""] * 5
    for i in range(1, 6):
        chosen = None
        for cand in (f"q_{i}", f"q{i}", f"q-{i}", f"q {i}", f"option {i}", f"antwort {i}", f"answer {i}"):
            if cand in fmap: chosen = fmap[cand]; break
        if chosen: opt_values[i - 1] = normalize_option_text(_get(note, chosen))
    indexed_opts = [(idx, text) for idx, text in enumerate(opt_values) if text]
    if not indexed_opts: return None
    options = [text for _, text in indexed_opts]
    idx_lookup = {orig_idx: pos for pos, (orig_idx, _) in enumerate(indexed_opts)}
    sol_key = (fmap.get("answers") or fmap.get("solutions") or fmap.get("mc_solutions") or fmap.get("solution") or fmap.get("correct") or fmap.get("loesungen") or fmap.get("lösungen"))
    correct_idx, comment = None, ""
    if sol_key:
        sol_raw = strip_html_keep_media(_get(note, sol_key))
        selected_orig_idx = None
        m = BIN_5_SPACED.search(sol_raw)
        if m: bits = [int(x) for x in m.groups()]
        else:
            m2 = BIN_5_COMPACT.search(sol_raw)
            bits = [int(x) for x in m2.group(0)] if m2 else []
        if bits and bits.count(1) == 1: selected_orig_idx = bits.index(1)
        else:
            stripped = sol_raw.strip()
            m_letter = LETTER_ONLY.match(stripped)
            if m_letter: selected_orig_idx = "ABCDE".index(m_letter.group(1).upper())
            else:
                m_letter_plus = LETTER_PLUS.match(stripped)
                if m_letter_plus:
                    letter_idx = "ABCDE".index(m_letter_plus.group(1).upper())
                    tail = normalize_option_text(m_letter_plus.group(2))
                    if tail:
                        for orig_idx, text in indexed_opts:
                            if normalize_option_text(text) == tail: selected_orig_idx = orig_idx; break
                    if selected_orig_idx is None: selected_orig_idx = letter_idx
                else:
                    sol_norm = normalize_option_text(sol_raw)
                    for orig_idx, text in indexed_opts:
                        if normalize_option_text(text) == sol_norm: selected_orig_idx = orig_idx; break
        if selected_orig_idx is not None: correct_idx = idx_lookup.get(selected_orig_idx)
    for cand in ("comment", "kommentar", "extra 1", "extra", "notes"):
        if cand in fmap: comment = strip_html_keep_media(_get(note, fmap[cand])); break
    return question, options, correct_idx, comment

def _detect_from_back(back_html: str, options: list[str]):
    raw = strip_html_keep_media(back_html)
    if not raw: return None, ""
    parts = re.split(r"\r?\n", raw, maxsplit=1)
    first = parts[0] if parts else raw
    rest_comment = parts[1] if len(parts) > 1 else ""
    m = LETTER_ONLY.match(first)
    if m: return "ABCDE".index(m.group(1).upper()), rest_comment
    m = BIN_5_SPACED.search(raw)
    if m:
        bits = [int(x) for x in m.groups()]
        if bits.count(1) == 1: return bits.index(1), rest_comment
    m2 = BIN_5_COMPACT.search(raw)
    if m2:
        bits = [int(x) for x in m2.group(0)]
        if bits.count(1) == 1: return bits.index(1), rest_comment
    m = LETTER_PLUS.match(first)
    if m:
        letter_idx = "ABCDE".index(m.group(1).upper())
        tail = normalize_option_text(m.group(2))
        if tail:
            for i, o in enumerate(options):
                if normalize_option_text(o) == tail: return i, rest_comment
        return letter_idx, rest_comment
    first_norm = normalize_option_text(first)
    for i, o in enumerate(options):
        if normalize_option_text(o) == first_norm: return i, rest_comment
    return None, rest_comment

def parse_note_to_proposal(note):
    warnings = []
    sf = _parse_structured_fields(note)
    if sf: q, opts, correct, comment = sf
    else:
        names = _field_names(note)
        front = _get(note, names[0]) if names else ""
        back  = _get(note, names[1]) if len(names) > 1 else ""
        q, opts = _parse_front_stream(front)
        correct, comment = _detect_from_back(back, opts)

    if not q or not opts:
        warnings.append("Keine Frage/Optionen erkannt – Bearbeiten nötig")
        return None, warnings

    prop = {"Frage": q, "Kopfzeile": "", "Eigene Notizen": "", "Antwort": comment or ""}
    ordered = [""] * 5
    if correct is not None and 0 <= correct < len(opts):
        correct_text = opts[correct]
        others = [o for i, o in enumerate(opts) if i != correct]
        ordered = [correct_text] + others + [""] * (5 - (1 + len(others)))
    else:
        warnings.append("Keine eindeutige richtige Antwort – Bearbeiten")
        for i, o in enumerate(opts[:5]): ordered[i] = o

    for i, name in enumerate(["Antwort A", "Antwort B", "Antwort C", "Antwort D", "Antwort E"]):
        prop[name] = ordered[i]
    return prop, warnings

# --- AI & Vision Logic ---

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def parse_with_llm(text_content: str) -> tuple[dict, list]:
    # Lade Config dynamisch
    config = mw.addonManager.getConfig(__name__) or {}
    api_key = config.get("openai_api_key", "").strip()
    # Standard-Modell, aber wir prüfen gleich, ob wir Vision brauchen
    model = config.get("openai_model", "gpt-4o-mini") 

    if not api_key:
        return None, ["Kein API Key konfiguriert!"]

    # 1. Bilder im Text suchen (src="...")
    media_dir = mw.col.media.dir()
    image_refs = re.findall(r'src="([^"]+)"', text_content)
    
    # 2. Content zusammenbauen (Text + Bilder)
    content_payload = []
    
    # -- UPDATE: Instruktion gegen Buchstaben-Referenzen --
    prompt_text = (
        "Du bist ein Assistent für Medizinstudenten. Analysiere diese Multiple-Choice-Frage (Text + Bilder). "
        "Extrahiere Frage, Optionen (A-E) und Lösung.\n"
        "WICHTIG: Referenziere in der Erklärung ('Antwort') NIEMALS die Buchstaben (A, B, C...), "
        "da die Antworten in der App gemischt werden. "
        "Schreibe stattdessen immer den vollständigen Text der Antwortoption aus "
        "(z.B. statt 'B ist falsch' schreibe 'Die Hypertonie ist falsch, weil...').\n"
        "Antworte AUSSCHLIESSLICH als JSON: {'Frage': '...', 'Antwort A': '...', ... 'Antwort E': '...', 'Antwort': 'Erklärung (OHNE Buchstaben)', 'Correct': 'A'}.\n"
        "Hier ist der Inhalt:\n" + text_content
    )

    content_payload.append({
        "type": "text", 
        "text": prompt_text
    })

    has_images = False
    for img_fname in image_refs:
        # Filename bereinigen (manchmal URL-encoded)
        img_fname = urllib.parse.unquote(img_fname)
        full_path = os.path.join(media_dir, img_fname)
        
        if os.path.exists(full_path):
            mime_type, _ = mimetypes.guess_type(full_path)
            if mime_type and mime_type.startswith('image'):
                try:
                    base64_img = encode_image(full_path)
                    content_payload.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_img}"
                        }
                    })
                    has_images = True
                except Exception:
                    pass # Wenn ein Bild kaputt ist, ignorieren wir es
    
    # Wenn Bilder dabei sind, erzwingen wir ein Vision-fähiges Modell
    if has_images and "gpt-4" not in model:
        model = "gpt-4o-mini"

    data = {
        "model": model,
        "messages": [{"role": "user", "content": content_payload}],
        "temperature": 0.0
    }

    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(data).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
        )
        with urllib.request.urlopen(req) as response:
            res = json.load(response)
            content = res["choices"][0]["message"]["content"]
            try:
                content = content.replace("```json", "").replace("```", "").strip()
                js = json.loads(content)
            except json.JSONDecodeError:
                return None, ["AI-Antwort war kein valides JSON"]

            prop = {
                "Frage": js.get("Frage", ""),
                "Kopfzeile": "",
                "Eigene Notizen": "",
                "Antwort": js.get("Antwort", "")
            }
            opts = ["Antwort A", "Antwort B", "Antwort C", "Antwort D", "Antwort E"]
            correct_letter = (js.get("Correct") or "").upper().strip()
            raw_opts = [js.get(k, "") for k in opts]
            
            if correct_letter in "ABCDE" and len(correct_letter) == 1:
                idx = "ABCDE".index(correct_letter)
                correct_text = raw_opts[idx]
                raw_opts.pop(idx)
                raw_opts.insert(0, correct_text)
            else:
                return None, ["AI konnte keine Lösung identifizieren"]

            for i, k in enumerate(opts):
                prop[k] = raw_opts[i] if i < len(raw_opts) else ""

            return prop, []

    except Exception as e:
        return None, [f"AI Request Error: {str(e)}"]