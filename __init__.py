from aqt import mw, gui_hooks
from aqt.qt import QAction
from .review import run_review
from .settings import open_settings_dialog

# ---- Tools-Menü ----
def _run_review_from_tools():
    note_ids = []
    w = mw.app.activeWindow()
    try:
        if hasattr(w, "selectedNotes"):
            note_ids = w.selectedNotes()
    except Exception:
        pass
    run_review(mw, note_ids)

tools_review = QAction("MC-Mapper…", mw)
tools_review.triggered.connect(_run_review_from_tools)
mw.form.menuTools.addAction(tools_review)

tools_ai = QAction("MC-Mapper KI-Einstellungen…", mw)
tools_ai.triggered.connect(lambda: open_settings_dialog(mw))
mw.form.menuTools.addAction(tools_ai)

# ---- Browser-Menüs ----
def _ensure_browser_menu_actions(browser):
    if not any(a.text() == "MC-Mapper…" for a in browser.form.menuEdit.actions()):
        act_review = QAction("MC-Mapper…", browser)
        act_review.triggered.connect(lambda: run_review(mw, browser.selectedNotes()))
        browser.form.menuEdit.addAction(act_review)

def on_browser_menus_did_init(browser):
    _ensure_browser_menu_actions(browser)

def on_browser_context_menu(browser, menu):
    _ensure_browser_menu_actions(browser)
    review_action = next((a for a in browser.form.menuEdit.actions() if a.text() == "MC-Mapper…"), None)
    if review_action and not any(a.text() == "MC-Mapper…" for a in menu.actions()):
        menu.addAction(review_action)

# ---- Hooks ----
gui_hooks.browser_menus_did_init.append(on_browser_menus_did_init)
gui_hooks.browser_will_show_context_menu.append(on_browser_context_menu)
