# review.py — konsistente, gut scannbare rechte Seite (Label inline), kompakte ALT-Ansicht
import re
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTextBrowser,
    QLabel, QLineEdit, QMessageBox, QWidget, QScrollArea,
    Qt, QCheckBox, QUrl, QTextOption, QInputDialog
)
from aqt import mw
from anki.notes import Note

from .config import TARGET_MODEL_NAME, FIELDS, TAG_NEW
from .parsing import parse_note_to_proposal
from .settings import get_settings
from .ai import AISettings as AIConfig, enhance_proposal
from .util import html_preview, normalize_combo_key, key_to_tag, strip_html_keep_media

def _with_img_breaks_exact(html: str) -> str:
    if not html:
        return ""
    def repl(m):
        return "<br>" + m.group(1) + "<br>"
    html = re.sub(r"(?is)(?:\s*(?:<br\s*/?>\s*)+)?(<img\b[^>]*>)(?:\s*(?:<br\s*/?>\s*)+)?", repl, html)
    html = re.sub(r"(?is)(<br\s*/?>\s*){3,}", r"<br><br>", html)
    return html

def _inline_html(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\r\n","\n").replace("\r","\n")
    s = re.sub(r"\n{2,}", "\n", s)
    return s.replace("\n","<br>")

class EditForm(QDialog):
    def __init__(self, parent, proposal, orig_note):
        super().__init__(parent)
        self.setWindowTitle("MC-Mapper – Bearbeiten")
        self.values = proposal.copy()

        left = QVBoxLayout()
        left.addWidget(QLabel("ALT (Original)"))
        self.oldView = QTextBrowser()
        self.oldView.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.oldView.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.oldView.setHtml(html_preview(orig_note, parent.mw.col.media.dir()))
        left.addWidget(self.oldView)

        formLay = QVBoxLayout()
        self.edits = {}
        for f in FIELDS:
            row = QHBoxLayout()
            row.addWidget(QLabel(f, self))
            le = QLineEdit(self.values.get(f, ""))
            self.edits[f] = le
            row.addWidget(le)
            formLay.addLayout(row)
        formWrap = QWidget(); formWrap.setLayout(formLay)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(formWrap)

        right = QVBoxLayout(); right.addWidget(QLabel("NEU (Ziel-Felder)")); right.addWidget(scroll)
        ok = QPushButton("OK"); cancel = QPushButton("Abbrechen")
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns = QHBoxLayout(); btns.addWidget(ok); btns.addWidget(cancel)

        top = QHBoxLayout(); top.addLayout(left,1); top.addLayout(right,1)
        lay = QVBoxLayout(self); lay.addLayout(top); lay.addLayout(btns)

    def get_values(self):
        for f, le in self.edits.items():
            self.values[f] = le.text()
        return self.values

class Review(QDialog):
    def __init__(self, mw, note_ids):
        super().__init__(mw)
        self.mw = mw
        self.all_note_ids = list(note_ids) if note_ids else list(mw.col.find_notes("deck:current"))
        self.note_ids = list(self.all_note_ids)
        self.i = 0
        self.setWindowTitle("MC-Mapper – Review")
        self.ai_meta = {}
        self.ai_status = "disabled"

        self.oldView = QTextBrowser()
        self.newView = QTextBrowser()
        for v in (self.oldView, self.newView):
            v.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            v.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.newView.document().setBaseUrl(QUrl.fromLocalFile(self.mw.col.media.dir() + "/"))

        self.info = QLabel("")

        self.btnPrev = QPushButton("◀ Zurück")
        self.btnNext = QPushButton("Weiter ▶")
        self.jumpEdit = QLineEdit(); self.jumpEdit.setPlaceholderText("zu # springen…"); self.jumpEdit.setFixedWidth(100)
        self.btnJump = QPushButton("Go")
        self.btnApply = QPushButton("Übernehmen")
        self.btnEdit = QPushButton("Bearbeiten…")

        self.btnPrev.clicked.connect(self.prev)
        self.btnNext.clicked.connect(self.next)
        self.btnJump.clicked.connect(self.jump_to)
        self.jumpEdit.returnPressed.connect(self.jump_to)
        self.btnApply.clicked.connect(self.apply_current)
        self.btnEdit.clicked.connect(self.edit_current)

        self.chkHideMigr = QCheckBox('„migrated/by-mc-mapper“ ausblenden')
        self.chkHideMigr.setToolTip("ALT-Karten, die bereits markiert wurden, im Review ausblenden.")
        self.chkHideMigr.stateChanged.connect(self._apply_filter)

        top = QHBoxLayout()
        left = QVBoxLayout();  left.addWidget(QLabel("ALT"));           left.addWidget(self.oldView)
        right = QVBoxLayout(); self.newLabel = QLabel("NEU (Vorschlag)"); right.addWidget(self.newLabel); right.addWidget(self.newView)
        top.addLayout(left); top.addLayout(right)

        nav = QHBoxLayout()
        nav.addWidget(self.btnPrev); nav.addWidget(self.btnNext); nav.addSpacing(12)
        nav.addWidget(QLabel("Position:")); self.posLbl = QLabel(""); nav.addWidget(self.posLbl)
        nav.addSpacing(12); nav.addWidget(self.jumpEdit); nav.addWidget(self.btnJump)
        nav.addStretch(); nav.addWidget(self.chkHideMigr); nav.addSpacing(12); nav.addWidget(self.info)

        actions = QHBoxLayout(); actions.addStretch(); actions.addWidget(self.btnEdit); actions.addWidget(self.btnApply)

        lay = QVBoxLayout(self); lay.addLayout(top); lay.addLayout(nav); lay.addLayout(actions)

        self.model = self._select_target_model()
        if not self.model:
            self.reject(); return
        self.newLabel.setText(f"NEU ({self.model['name']})")
        self.setWindowTitle(f"MC-Mapper – Review ({self.model['name']})")

        self._apply_filter()
        self.load()

    def _select_target_model(self):
        models = []
        for m in self.mw.col.models.all():
            field_names = {f["name"] for f in m.get("flds", [])}
            if all(f in field_names for f in FIELDS):
                models.append(m)

        if not models:
            QMessageBox.critical(self, "Kein passender Notiztyp", "Es wurde kein Notiztyp gefunden, der alle MC-Felder enthält.")
            return None

        names = [m["name"] for m in models]
        default_idx = next((i for i, name in enumerate(names) if name == TARGET_MODEL_NAME), 0)
        choice, ok = QInputDialog.getItem(
            self,
            "Ziel-Notiztyp auswählen",
            "In welchen Notiztyp sollen die Karten übernommen werden?",
            names,
            default_idx,
            False,
        )
        if not ok:
            return None
        try:
            idx = names.index(choice)
        except ValueError:
            return None
        return models[idx]

    def _render_prop_html(self, prop: dict) -> str:
        css = """
        <style>
          .wrap { font-family: Segoe UI, Arial; font-size:12px; line-height:1.35; }
          .card { border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 10px; }
          .row  { margin: 4px 0 6px; }
          .lab  { font-weight:700; display:inline-block; min-width:110px; }
          .val  { display:inline; }
          .sep  { margin: 8px 0; border-top: 1px dashed rgba(255,255,255,0.15); }
          img   { max-width:100%; height:auto; display:block; margin:6px 0; }
        </style>
        """
        rows = []
        def add_row(label, value):
            rows.append(f"<div class='row'><span class='lab'>{label}:</span> <span class='val'>{_inline_html(value)}</span></div>")

        add_row("Frage",     prop.get("Frage",""))
        rows.append("<div class='sep'></div>")
        for k in ["Antwort A","Antwort B","Antwort C","Antwort D","Antwort E"]:
            add_row(k, prop.get(k,""))
        rows.append("<div class='sep'></div>")
        add_row("Kopfzeile",      prop.get("Kopfzeile",""))
        add_row("Eigene Notizen", prop.get("Eigene Notizen",""))
        add_row("Antwort",        prop.get("Antwort",""))

        return css + "<div class='wrap'><div class='card'>" + "".join(rows) + "</div></div>"

    def _apply_filter(self):
        if not self.chkHideMigr.isChecked():
            self.note_ids = list(self.all_note_ids)
        else:
            self.note_ids = [nid for nid in self.all_note_ids if TAG_NEW not in self.mw.col.get_note(nid).tags]
        self.i = 0; self._clamp()

    def _clamp(self):
        self.i = max(0, min(self.i, len(self.note_ids)-1))
        self.btnPrev.setEnabled(self.i > 0); self.btnNext.setEnabled(self.i < len(self.note_ids)-1)
        total = len(self.note_ids); self.posLbl.setText(f"{(self.i+1) if total else 0}/{total}")

    def _build_ai_context(self, note):
        context = {"notiztyp": note.model().get("name", ""), "felder": {}}
        try:
            for fld in note.model().get("flds", []):
                name = fld.get("name", "")
                if not name:
                    continue
                try:
                    context["felder"][name] = strip_html_keep_media(note[name])
                except Exception:
                    context["felder"][name] = ""
        except Exception:
            pass
        context["tags"] = list(getattr(note, "tags", []))
        context["guid"] = getattr(note, "guid", "")
        return context

    def load(self):
        if not self.note_ids:
            self.oldView.setHtml("<i>Keine Notizen in der aktuellen Auswahl/Filter.</i>")
            self.newView.setHtml(""); self.info.setText(""); self._clamp(); return

        self._clamp()
        nid = self.note_ids[self.i]
        self.orig = self.mw.col.get_note(nid)
        self.prop, self.warnings = parse_note_to_proposal(self.orig)
        self.ai_meta = {}
        self.ai_status = "disabled"

        addon_settings = get_settings()
        ai_config = AIConfig(
            enabled=addon_settings.enable_ai,
            api_key=addon_settings.openai_api_key,
            model=addon_settings.openai_model,
            temperature=addon_settings.openai_temperature,
        )

        if self.prop and ai_config.enabled:
            context = self._build_ai_context(self.orig)
            self.mw.progress.start(label="KI-Überarbeitung…", immediate=True)
            try:
                ai_result = enhance_proposal(self.prop, context=context, settings=ai_config)
            finally:
                self.mw.progress.finish()

            if ai_result.proposal:
                self.prop = ai_result.proposal
                self.ai_status = "enabled"
            if ai_result.meta:
                self.ai_meta = ai_result.meta
                dup = self.ai_meta.get("duplicate_signature")
                if dup:
                    self.prop["_duplicate_signature"] = dup
            if ai_result.warnings:
                self.warnings.extend(ai_result.warnings)
                self.ai_status = "warning"
        elif self.prop:
            # Fallback-Signatur ohne KI, falls benötigt
            self.prop["_duplicate_signature"] = normalize_combo_key(self.prop)

        if self.prop and "_duplicate_signature" not in self.prop:
            self.prop["_duplicate_signature"] = normalize_combo_key(self.prop)

        self.oldView.setHtml(html_preview(self.orig, self.mw.col.media.dir()))
        self.newView.setHtml(self._render_prop_html(self.prop) if self.prop else "<i>Kein sicherer Vorschlag – bitte Bearbeiten…</i>")
        info_parts = []
        if self.warnings:
            info_parts.extend(self.warnings)
        if self.ai_meta.get("notes"):
            info_parts.append(f"KI: {self.ai_meta['notes']}")
        elif self.ai_status == "enabled":
            info_parts.append("KI aktiv")
        elif self.ai_status == "warning" and addon_settings.enable_ai:
            info_parts.append("KI mit Warnungen")
        self.info.setText(" | ".join(info_parts))

    def next(self):
        if self.i < len(self.note_ids)-1:
            self.i += 1; self.load()

    def prev(self):
        if self.i > 0:
            self.i -= 1; self.load()

    def jump_to(self):
        try: idx = int(self.jumpEdit.text())
        except Exception: return
        if 1 <= idx <= len(self.note_ids):
            self.i = idx-1; self.load()

    def apply_current(self):
        if not self.prop:
            self.edit_current()
            if not self.prop: return

        dup_key = self.prop.get("_duplicate_signature") or normalize_combo_key(self.prop)
        key_tag = key_to_tag(dup_key)
        dup_hits = self.mw.col.find_notes(f'tag:"{key_tag}"')
        if dup_hits:
            btn = QMessageBox.question(self, "Dublettenhinweis",
                f"Eine ähnliche Frage existiert bereits ({len(dup_hits)} Treffer). Trotzdem neu anlegen?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if btn != QMessageBox.Yes: return

        self.mw.checkpoint("MC-Mapper apply")
        n = Note(self.mw.col, self.model)
        for f in FIELDS:
            n[f] = _with_img_breaks_exact(self.prop.get(f, ""))  # exakt 1 <br> vor/nach IMG
        new_tags = set(n.tags)
        new_tags.add(key_tag)
        n.tags = list(new_tags)

        try:
            orig_cids = list(self.orig.cards()); deck_id = orig_cids[0].did if orig_cids else self.mw.col.decks.get_current_id()
        except Exception:
            deck_id = self.mw.col.decks.get_current_id()
        self.mw.col.add_note(n, deck_id)

        # ALT markieren und ggf. aus Liste entfernen
        o_tags = set(self.orig.tags); o_tags.add(TAG_NEW); self.orig.tags = list(o_tags); self.mw.col.update_note(self.orig)

        if self.chkHideMigr.isChecked():
            try: self.note_ids.remove(self.orig.id)
            except ValueError: pass
            if self.i >= len(self.note_ids): self.i = max(0, len(self.note_ids)-1)
        else:
            if self.i < len(self.note_ids)-1:
                self.i += 1
        self.load()

    def edit_current(self):
        dlg = EditForm(self, self.prop or {f:"" for f in FIELDS}, self.orig)
        if dlg.exec():
            self.prop = dlg.get_values()
            self.prop["_duplicate_signature"] = normalize_combo_key(self.prop)
            self.newView.setHtml(self._render_prop_html(self.prop))

def run_review(mw, note_ids):
    Review(mw, note_ids).exec()
