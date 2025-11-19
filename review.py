# review.py — konsistente, gut scannbare rechte Seite (Label inline), kompakte ALT-Ansicht
import re
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTextBrowser,
    QLabel, QLineEdit, QMessageBox, QWidget, QScrollArea,
    Qt, QCheckBox, QUrl, QTextOption, QComboBox, QToolButton,
    QMenu, QWidgetAction, QShortcut, QKeySequence, QApplication
)
from aqt import mw
from anki.notes import Note

# Imports aus deinen Modulen
from .config import TARGET_MODEL_NAME, FIELDS, TAG_NEW
from .parsing import parse_note_to_proposal, parse_with_llm
from .util import html_preview, normalize_combo_key, key_to_tag, find_similar_notes_fuzzy

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

class TargetModelDialog(QDialog):
    def __init__(self, parent, models, default_idx, initial_header=""):
        super().__init__(parent)
        self.setWindowTitle("MC-Mapper – Ziel auswählen")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("In welchen Notiztyp sollen die Karten übernommen werden?"))

        self.combo = QComboBox(self)
        names = [m["name"] for m in models]
        self.combo.addItems(names)
        if models:
            self.combo.setCurrentIndex(max(0, min(default_idx, len(models) - 1)))
        layout.addWidget(self.combo)

        layout.addWidget(QLabel("Feste Kopfzeile für neue Karten:"))
        self.headerEdit = QLineEdit(initial_header or "", self)
        layout.addWidget(self.headerEdit)

        btns = QHBoxLayout()
        btns.addStretch()
        ok = QPushButton("OK", self)
        cancel = QPushButton("Abbrechen", self)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addLayout(btns)

    def get_selection(self):
        return self.combo.currentIndex(), self.headerEdit.text()

class Review(QDialog):
    def __init__(self, mw, note_ids):
        super().__init__(mw)
        self.mw = mw
        self.all_note_ids = list(note_ids) if note_ids else list(mw.col.find_notes("deck:current"))
        self.note_ids = list(self.all_note_ids)
        self.i = 0
        self.orig = None
        self.prop = {f: "" for f in FIELDS}
        self._prop_generated = False
        self._manual_override = False
        self._setting_fields = False
        self.field_editors = {}
        self.fixed_header = ""
        self._info_cache = {}
        self.setWindowTitle("MC-Mapper – Review")

        self.oldView = QTextBrowser()
        self.newView = QTextBrowser()
        for v in (self.oldView, self.newView):
            v.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            v.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.newView.document().setBaseUrl(QUrl.fromLocalFile(self.mw.col.media.dir() + "/"))

        self.info = QLabel("")

        # --- Navigation & Buttons ---
        self.btnPrev = QPushButton("◀ Zurück")
        self.btnNext = QPushButton("Weiter ▶")
        self.jumpEdit = QLineEdit(); self.jumpEdit.setPlaceholderText("zu # springen…"); self.jumpEdit.setFixedWidth(100)
        self.btnJump = QPushButton("Go")
        self.btnApply = QPushButton("Übernehmen")
        self.btnEdit = QPushButton("Bearbeiten anzeigen")
        
        self.btnAi = QPushButton("✨ AI-Fix")
        self.btnAi.setToolTip("Versucht, die Frage mit OpenAI zu parsen (Key in Add-on Konfiguration nötig)")
        
        self.btnAuto = QPushButton("🚀 Auto-Sicher")
        self.btnAuto.setToolTip("Übernimmt alle Karten, bei denen sich der Parser 100% sicher ist (Turbo-Modus)")

        # Connections
        self.btnPrev.clicked.connect(self.prev)
        self.btnNext.clicked.connect(self.next)
        self.btnJump.clicked.connect(self.jump_to)
        self.jumpEdit.returnPressed.connect(self.jump_to)
        self.btnApply.clicked.connect(self.apply_current)
        self.btnEdit.clicked.connect(self.toggle_edit_panel)
        self.btnAi.clicked.connect(self.on_ai_repair)
        self.btnAuto.clicked.connect(self.on_auto_accept)

        self.filterButton = QToolButton(self)
        self.filterButton.setText("Filter")
        self.filterButton.setToolTip("Filteroptionen anzeigen")
        self.filterButton.setPopupMode(QToolButton.InstantPopup)
        self.filterMenu = QMenu(self.filterButton)
        self.filterButton.setMenu(self.filterMenu)

        filtersHost = QWidget(self.filterMenu)
        filtersLayout = QVBoxLayout(filtersHost)
        filtersLayout.setContentsMargins(8, 6, 8, 6)
        filtersLayout.setSpacing(4)

        # --- FILTERS ---
        filter_specs = [
            (
                "Bereits migrierte Karten ausblenden",
                "Versteckt Karten, die das Add-on schon bearbeitet hat (Tag: migrated/by-mc-mapper).",
                "chkHideMigr",
            ),
            (
                "Nur Karten ohne eindeutige Lösung",
                "Zeigt nur Karten, bei denen keine oder keine eindeutige richtige Antwort erkannt wurde.",
                "chkNoCorrect",
            ),
        ]

        self._filter_checks = []
        for text, tooltip, attr in filter_specs:
            chk = QCheckBox(text, filtersHost)
            chk.setToolTip(tooltip)
            chk.stateChanged.connect(self._filters_changed)
            filtersLayout.addWidget(chk)
            setattr(self, attr, chk)
            self._filter_checks.append(chk)

        filtersLayout.addStretch(1)
        filtersAction = QWidgetAction(self.filterMenu)
        filtersAction.setDefaultWidget(filtersHost)
        self.filterMenu.addAction(filtersAction)

        top = QHBoxLayout()
        left = QVBoxLayout();  left.addWidget(QLabel("ALT"));           left.addWidget(self.oldView)
        right = QVBoxLayout(); self.newLabel = QLabel("NEU (Vorschlag)"); right.addWidget(self.newLabel); right.addWidget(self.newView)
        self.editPanel = self._build_edit_panel(); right.addWidget(self.editPanel)
        top.addLayout(left); top.addLayout(right)

        nav = QHBoxLayout()
        nav.addWidget(self.btnPrev); nav.addWidget(self.btnNext); nav.addSpacing(12)
        nav.addWidget(QLabel("Position:")); self.posLbl = QLabel(""); nav.addWidget(self.posLbl)
        nav.addSpacing(12); nav.addWidget(self.jumpEdit); nav.addWidget(self.btnJump)
        nav.addStretch()
        nav.addWidget(self.filterButton)
        nav.addSpacing(12)
        nav.addWidget(self.info)

        actions = QHBoxLayout()
        actions.addWidget(self.btnAuto)
        actions.addStretch()
        actions.addWidget(self.btnAi)
        actions.addWidget(self.btnEdit)
        actions.addWidget(self.btnApply)

        lay = QVBoxLayout(self); lay.addLayout(top); lay.addLayout(nav); lay.addLayout(actions)

        self.model = self._select_target_model()
        if not self.model:
            self.reject(); return
        self.newLabel.setText(f"NEU ({self.model['name']})")
        self.setWindowTitle(f"MC-Mapper – Review ({self.model['name']})")
        
        # Shortcuts
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self.apply_current)
        QShortcut(QKeySequence("Ctrl+Right"), self).activated.connect(self.next)
        QShortcut(QKeySequence("Ctrl+Left"), self).activated.connect(self.prev)
        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self.on_ai_repair)

        self._update_filter_button_text()
        self._apply_filter(reset_position=True)

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
        dlg = TargetModelDialog(self, models, default_idx, self.fixed_header)
        if not dlg.exec():
            return None
        idx, header = dlg.get_selection()
        if idx < 0 or idx >= len(models):
            return None
        self.fixed_header = header.strip()
        return models[idx]

    def _build_edit_panel(self):
        panel = QWidget(self)
        panel.setVisible(False)
        wrap = QVBoxLayout(panel)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(QLabel("Felder bearbeiten"))

        form_host = QWidget(panel)
        form_layout = QVBoxLayout(form_host)
        form_layout.setContentsMargins(0, 0, 0, 0)
        for field in FIELDS:
            row = QHBoxLayout()
            row.addWidget(QLabel(field, form_host))
            editor = QLineEdit(form_host)
            editor.textChanged.connect(lambda text, f=field: self._handle_field_changed(f, text))
            row.addWidget(editor)
            form_layout.addLayout(row)
            self.field_editors[field] = editor

        scroll = QScrollArea(panel)
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)
        wrap.addWidget(scroll)
        return panel

    def _set_edit_panel_visible(self, visible: bool):
        if self.editPanel.isVisible() == visible:
            return
        self.editPanel.setVisible(visible)
        self.btnEdit.setText("Bearbeiten ausblenden" if visible else "Bearbeiten anzeigen")
        if visible:
            self._sync_edit_fields()

    def toggle_edit_panel(self):
        self._set_edit_panel_visible(not self.editPanel.isVisible())

    def _sync_edit_fields(self):
        self._setting_fields = True
        try:
            for field, editor in self.field_editors.items():
                editor.setText(self.prop.get(field, ""))
        finally:
            self._setting_fields = False

    def _handle_field_changed(self, field: str, text: str):
        if self._setting_fields:
            return
        if not isinstance(self.prop, dict):
            self.prop = {f: "" for f in FIELDS}
        self.prop[field] = text
        self._manual_override = True
        self._update_preview()

    def _update_preview(self):
        if not self.prop:
            self.newView.setHtml("")
            return
        if not self._prop_generated and not self._manual_override:
            self.newView.setHtml("<i>Kein sicherer Vorschlag – bitte Bearbeiten…</i>")
        else:
            self.newView.setHtml(self._render_prop_html(self.prop))

    def _filters_changed(self, *_args):
        self._update_filter_button_text()
        self._apply_filter(reset_position=True)

    def _update_filter_button_text(self):
        active = sum(1 for chk in getattr(self, "_filter_checks", []) if chk.isChecked())
        self.filterButton.setText(f"Filter ({active})" if active else "Filter")

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
          .row.question-label .lab { color: rgb(0, 150, 255); }
          .row.opt-right { background: rgba(40, 167, 69, 0.12); border-left: 3px solid rgba(40, 167, 69, 0.6); border-radius: 4px; padding-left: 8px; }
          .row.opt-right .lab, .row.opt-right .val { color: #2aa158; }
          .row.opt-wrong .lab { color: #a0a0a0; }
          .row.opt-wrong .val { color: #bcbcbc; }
        </style>
        """
        rows = []
        def add_row(label, value, row_class=None):
            cls = "row"
            if row_class:
                cls += f" {row_class}"
            rows.append(f"<div class='{cls}'><span class='lab'>{label}:</span> <span class='val'>{_inline_html(value)}</span></div>")

        add_row("Frage",     prop.get("Frage",""), row_class="question-label")
        rows.append("<div class='sep'></div>")
        correct_key = "Antwort A"
        for k in ["Antwort A","Antwort B","Antwort C","Antwort D","Antwort E"]:
            cls = "opt-right" if k == correct_key else "opt-wrong"
            add_row(k, prop.get(k,""), row_class=cls)
        rows.append("<div class='sep'></div>")
        add_row("Kopfzeile",      prop.get("Kopfzeile",""))
        add_row("Eigene Notizen", prop.get("Eigene Notizen",""))
        add_row("Antwort",        prop.get("Antwort",""))

        return css + "<div class='wrap'><div class='card'>" + "".join(rows) + "</div></div>"

    def apply_all_filters(self):
        filtered = []
        config = mw.addonManager.getConfig(__name__) or {}
        
        for nid in self.all_note_ids:
            note = self.mw.col.get_note(nid)
            if note is None:
                continue
            tags = set(note.tags)
            
            if self.model:
                try:
                    if note.model().get("name") == self.model.get("name"):
                        continue
                except Exception:
                    pass

            if self.chkHideMigr.isChecked() and TAG_NEW in tags:
                continue
            
            info = self._get_note_info(nid, note)

            if self.chkNoCorrect.isChecked() and not info.get("no_correct"):
                continue
            
            filtered.append(nid)
        return filtered

    def _apply_filter(self, reset_position: bool = True):
        self.note_ids = self.apply_all_filters()
        if reset_position:
            self.i = 0
        self._clamp()
        self.load()

    def _build_note_info(self, nid: int, note, prop=None, warnings=None):
        if prop is None or warnings is None:
            prop, warnings = parse_note_to_proposal(note)
        warnings = warnings or []
        info = {
            "prop": prop,
            "warnings": warnings,
            "has_warnings": bool(warnings),
            "no_correct": any("Keine eindeutige richtige Antwort" in w for w in warnings),
            "key_tag": None,
            "has_duplicate": False,
            "is_fuzzy_duplicate": False,
        }
        
        config = mw.addonManager.getConfig(__name__) or {}
        dup_thresh = config.get("duplicate_threshold", 0.85)

        if prop:
            combo_key = normalize_combo_key(prop)
            key_tag = key_to_tag(combo_key)
            info["key_tag"] = key_tag
            
            hits = self.mw.col.find_notes(f'tag:"{key_tag}"') if key_tag else []
            
            is_fuzzy = False
            if not hits and prop.get("Frage"):
                fuzzy_hits = find_similar_notes_fuzzy(self.mw.col, prop["Frage"], threshold=dup_thresh)
                if fuzzy_hits:
                    hits = [h[0] for h in fuzzy_hits]
                    is_fuzzy = True

            info["has_duplicate"] = any(hid != nid for hid in hits)
            info["is_fuzzy_duplicate"] = is_fuzzy
            
        return info

    def _get_note_info(self, nid: int, note=None):
        cached = self._info_cache.get(nid)
        if cached is not None:
            return cached
        if note is None:
            try:
                note = self.mw.col.get_note(nid)
            except Exception:
                return {"prop": None, "warnings": [], "has_warnings": False, "no_correct": False, "key_tag": None, "has_duplicate": False}
        info = self._build_note_info(nid, note)
        self._info_cache[nid] = info
        return info

    def _clamp(self):
        self.i = max(0, min(self.i, len(self.note_ids)-1))
        self.btnPrev.setEnabled(self.i > 0); self.btnNext.setEnabled(self.i < len(self.note_ids)-1)
        total = len(self.note_ids); self.posLbl.setText(f"{(self.i+1) if total else 0}/{total}")

    def load(self):
        if not self.note_ids:
            self.orig = None
            self.prop = {f: "" for f in FIELDS}
            self.warnings = []
            self._prop_generated = False
            self._manual_override = False
            self.oldView.setHtml("<i>Keine Notizen in der aktuellen Auswahl/Filter.</i>")
            self.newView.setHtml("")
            self.info.setText("")
            self._sync_edit_fields()
            self._clamp()
            return

        self._clamp()
        nid = self.note_ids[self.i]
        self.orig = self.mw.col.get_note(nid)
        parsed_prop, warnings = parse_note_to_proposal(self.orig)
        self.warnings = warnings
        self._prop_generated = parsed_prop is not None
        self._manual_override = False

        if parsed_prop:
            prop = parsed_prop.copy()
        else:
            prop = {f: "" for f in FIELDS}
        for field in FIELDS:
            prop.setdefault(field, "")
        if self.fixed_header and not prop.get("Kopfzeile"):
            prop["Kopfzeile"] = self.fixed_header
        self.prop = prop

        self.oldView.setHtml(html_preview(self.orig, self.mw.col.media.dir()))
        
        info = self._build_note_info(nid, self.orig, parsed_prop, self.warnings)
        self._info_cache[nid] = info
        
        display_warnings = list(self.warnings)
        if info.get("is_fuzzy_duplicate"):
            display_warnings.append("⚠️ Ähnliche Frage gefunden (Fuzzy)")
            
        self.info.setText(" | ".join(display_warnings) if display_warnings else "")
        self._sync_edit_fields()
        self._update_preview()

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

    def on_ai_repair(self):
        if not self.orig: return
        
        raw_text = ""
        for f in self.orig.keys():
            raw_text += f"{f}: {self.orig[f]}\n"

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            prop, warnings = parse_with_llm(raw_text)
        finally:
            QApplication.restoreOverrideCursor()

        if not prop:
            QMessageBox.warning(self, "AI Error", "Konnte nicht parsen:\n" + "\n".join(warnings))
            return

        self.prop = prop
        self._manual_override = True
        self.warnings = ["✨ AI-Generated"] + warnings
        self._update_preview()
        self.info.setText(" | ".join(self.warnings))
        self._sync_edit_fields()

    def on_auto_accept(self):
        accepted = 0
        total = len(self.note_ids)
        
        if QMessageBox.question(self, "Auto-Accept", 
            "Soll ich versuchen, alle 100% sicheren Karten automatisch zu verarbeiten? (Warnung: Dubletten werden ignoriert)", 
            QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        self.mw.checkpoint("MC-Mapper Auto-Accept")
        self.mw.progress.start(immediate=True)
        
        ids_to_process = list(self.note_ids)
        
        try:
            for i, nid in enumerate(ids_to_process):
                self.mw.progress.update(label=f"Verarbeite {i+1}/{len(ids_to_process)}...", value=i, max=len(ids_to_process))
                
                info = self._get_note_info(nid)
                
                if (info["prop"] 
                    and not info["has_warnings"] 
                    and not info["no_correct"]):
                    
                    orig_note = self.mw.col.get_note(nid)
                    current_prop = info["prop"]
                    
                    if self.fixed_header and not current_prop.get("Kopfzeile"):
                        current_prop["Kopfzeile"] = self.fixed_header

                    n = Note(self.mw.col, self.model)
                    for f in FIELDS:
                        n[f] = _with_img_breaks_exact(current_prop.get(f, ""))
                    
                    try:
                        orig_cids = list(orig_note.cards())
                        deck_id = orig_cids[0].did if orig_cids else self.mw.col.decks.get_current_id()
                    except:
                        deck_id = self.mw.col.decks.get_current_id()
                    
                    self.mw.col.add_note(n, deck_id)

                    o_tags = set(orig_note.tags)
                    o_tags.add(TAG_NEW)
                    orig_note.tags = list(o_tags)
                    self.mw.col.update_note(orig_note)
                    
                    accepted += 1

            self.mw.col.save()
            self._info_cache.clear()
            self._apply_filter(reset_position=True)
                    
        finally:
            self.mw.progress.finish()
            
        QMessageBox.information(self, "Fertig", f"{accepted} von {total} Karten im Turbo-Modus verarbeitet!")

    def apply_current(self, suppress_dialogs=False):
        if not self.note_ids or not self.orig:
            return
        
        self.mw.checkpoint("MC-Mapper apply")
        self.mw.col.save()

        if not self._prop_generated and not self._manual_override:
            self._set_edit_panel_visible(True)
            return

        current_prop = {f: self.prop.get(f, "") for f in FIELDS}
        if self.fixed_header and not current_prop.get("Kopfzeile"):
            current_prop["Kopfzeile"] = self.fixed_header

        key_tag = key_to_tag(normalize_combo_key(current_prop))
        
        if not suppress_dialogs:
            dup_hits = self.mw.col.find_notes(f'tag:"{key_tag}"')
            if dup_hits:
                btn = QMessageBox.question(self, "Dublettenhinweis",
                    f"Eine identische Frage existiert bereits ({len(dup_hits)} Treffer). Trotzdem neu anlegen?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if btn != QMessageBox.Yes: return

        n = Note(self.mw.col, self.model)
        for f in FIELDS:
            n[f] = _with_img_breaks_exact(current_prop.get(f, ""))
        
        try:
            orig_cids = list(self.orig.cards()); deck_id = orig_cids[0].did if orig_cids else self.mw.col.decks.get_current_id()
        except Exception:
            deck_id = self.mw.col.decks.get_current_id()
        self.mw.col.add_note(n, deck_id)

        o_tags = set(self.orig.tags); o_tags.add(TAG_NEW); self.orig.tags = list(o_tags); self.mw.col.update_note(self.orig)

        old_ids = list(self.note_ids)
        old_index = self.i
        next_id = old_ids[old_index + 1] if old_index + 1 < len(old_ids) else None
        current_id = self.orig.id

        self.prop = current_prop
        self._info_cache.clear()
        self.note_ids = self.apply_all_filters()

        if next_id and next_id in self.note_ids:
            self.i = self.note_ids.index(next_id)
        elif current_id in self.note_ids:
            idx = self.note_ids.index(current_id)
            self.i = idx + 1 if idx + 1 < len(self.note_ids) else idx
        else:
            if self.note_ids:
                self.i = min(old_index, len(self.note_ids) - 1)
            else:
                self.i = 0

        self._clamp()
        self.load()

def run_review(mw, note_ids):
    Review(mw, note_ids).exec()