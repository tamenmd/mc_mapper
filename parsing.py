import re
from .util import strip_html_keep_media, normalize_option_text

# ---- Patterns
LETTER_ONLY   = re.compile(r'^\s*([a-eA-E])\s*$')
BIN_5_SPACED  = re.compile(r'\b([01])\s*([01])\s*([01])\s*([01])\s*([01])\b')
BIN_5_COMPACT = re.compile(r'\b[01]{5}\b')
LETTER_PLUS   = re.compile(r'^\s*([a-eA-E])(?:[\)\.\:\-])?\s+(.*)$')

# Strict: benötigt Trenner (a)/a./a:/a-)
STRICT_OPT_MARK  = re.compile(r'(?<![A-Za-z0-9])([a-eA-E])[\)\.\:\-]\s+', re.M)
# Lenient Fallback: Buchstabe + Leerraum
LENIENT_OPT_MARK = re.compile(r'(?<![A-Za-z0-9])([a-eA-E])\s+', re.M)


def _field_names(note):
    return [f["name"] for f in note.model()["flds"]]


def _get(note, name):
    names = _field_names(note)
    return note[name] if name in names else ""


def _pick_best_sequence(txt: str):
    """
    Finde Kandidaten a..e Sequenz; Präferenz:
      1) STRICT Pattern
      2) Sequence am weitesten hinten im Text
      3) Länge 3..5
    """
    def collect(mark_pat):
        ms = list(mark_pat.finditer(txt))
        if not ms:
            return []
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
                if len(seq) == 5:
                    break
            if 3 <= len(seq) <= 5:
                runs.append(seq)
        return runs

    runs = collect(STRICT_OPT_MARK)
    if not runs:
        runs = collect(LENIENT_OPT_MARK)
    if not runs:
        return []
    runs.sort(key=lambda r: r[-1].end(), reverse=True)
    return runs[0]


def _parse_front_stream(html: str):
    txt = strip_html_keep_media(html)
    seq = _pick_best_sequence(txt)
    if not seq:
        return txt.strip(), []

    q = txt[:seq[0].start()].strip()
    opts = []
    for i, m in enumerate(seq):
        s = m.end()
        e = seq[i + 1].start() if i + 1 < len(seq) else len(txt)
        chunk = txt[s:e].strip()
        if chunk:
            opts.append(normalize_option_text(chunk))
    return q, opts[:5]


def _parse_structured_fields(note):
    """
    Structured MC: Frage/Question, Q_1..Q_5 (oder q1/q_1/Option 1..5), Answers = 10000 / '1 0 0 0 0'.
    """
    names = _field_names(note)
    fmap = {n.lower(): n for n in names}

    # question field
    q_key = (
        fmap.get("question") or fmap.get("frage")
        or fmap.get("front") or fmap.get("vorderseite")
        or (names[0] if names else None)
    )
    if not q_key:
        return None
    question = strip_html_keep_media(_get(note, q_key))

    # options
    opt_keys = []
    for i in range(1, 6):
        for cand in (
            f"q_{i}", f"q{i}", f"q-{i}", f"q {i}",
            f"option {i}", f"antwort {i}", f"answer {i}"
        ):
            if cand in fmap:
                opt_keys.append(fmap[cand]); break
    if not opt_keys:
        return None
    options = [normalize_option_text(_get(note, k)) for k in opt_keys]
    options = [o for o in options if o]

    # solution bitstring / letter
    sol_key = (
        fmap.get("answers") or fmap.get("solutions") or fmap.get("mc_solutions")
        or fmap.get("solution") or fmap.get("correct") or fmap.get("loesungen") or fmap.get("lösungen")
    )
    correct_idx, comment = None, ""
    if sol_key:
        sol_raw_html = _get(note, sol_key)
        sol_raw = strip_html_keep_media(sol_raw_html)

        # spaced „1 0 0 0 0“
        m = BIN_5_SPACED.search(sol_raw)
        if m:
            bits = [int(x) for x in m.groups()]
        else:
            # compact „10000“
            m2 = BIN_5_COMPACT.search(sol_raw)
            bits = [int(x) for x in m2.group(0)] if m2 else []

        if bits and bits.count(1) == 1:
            correct_idx = bits.index(1)

    # optional comment
    for cand in ("comment", "kommentar", "extra 1", "extra", "notes"):
        if cand in fmap:
            comment = strip_html_keep_media(_get(note, fmap[cand])); break

    return question, options, correct_idx, comment


def _detect_from_back(back_html: str, options: list[str]):
    raw = strip_html_keep_media(back_html)
    if not raw:
        return None, ""
    # Splitting auf „erste Zeile + Rest“ anhand bereinigtem Text
    parts = re.split(r"\r?\n", raw, maxsplit=1)
    first = parts[0] if parts else raw
    rest_comment = parts[1] if len(parts) > 1 else ""

    m = LETTER_ONLY.match(first)
    if m:
        return "ABCDE".index(m.group(1).upper()), rest_comment

    # Bitstring (spaced oder compact)
    m = BIN_5_SPACED.search(raw)
    if m:
        bits = [int(x) for x in m.groups()]
        if bits.count(1) == 1:
            return bits.index(1), rest_comment
    m2 = BIN_5_COMPACT.search(raw)
    if m2:
        bits = [int(x) for x in m2.group(0)]
        if bits.count(1) == 1:
            return bits.index(1), rest_comment

    m = LETTER_PLUS.match(first)
    if m:
        letter_idx = "ABCDE".index(m.group(1).upper())
        tail = normalize_option_text(m.group(2))
        if tail:
            for i, o in enumerate(options):
                if normalize_option_text(o) == tail:
                    return i, rest_comment
        return letter_idx, rest_comment

    first_norm = normalize_option_text(first)
    for i, o in enumerate(options):
        if normalize_option_text(o) == first_norm:
            return i, rest_comment

    return None, rest_comment


def parse_note_to_proposal(note):
    warnings = []

    # 1) structured type first
    sf = _parse_structured_fields(note)
    if sf:
        q, opts, correct, comment = sf
    else:
        # 2) classic front/back
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
        for i, o in enumerate(opts[:5]):
            ordered[i] = o

    for i, name in enumerate(["Antwort A", "Antwort B", "Antwort C", "Antwort D", "Antwort E"]):
        prop[name] = ordered[i]

    return prop, warnings
