"""Konfigurations- und Einstellungsdialog für MC-Mapper."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

ADDON_KEY = __package__ or "mc_mapper"

DEFAULTS: Dict[str, Any] = {
    "enable_ai": False,
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "openai_temperature": 0.0,
}

AVAILABLE_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1-mini",
    "o4-mini",
]


@dataclass
class AddonSettings:
    enable_ai: bool
    openai_api_key: str
    openai_model: str
    openai_temperature: float


def get_settings() -> AddonSettings:
    conf = dict(DEFAULTS)
    stored = mw.addonManager.getConfig(ADDON_KEY) or {}
    conf.update(stored)
    return AddonSettings(
        enable_ai=bool(conf.get("enable_ai")),
        openai_api_key=str(conf.get("openai_api_key", "")),
        openai_model=str(conf.get("openai_model", DEFAULTS["openai_model"])),
        openai_temperature=float(conf.get("openai_temperature", 0.0)),
    )


def store_settings(settings: AddonSettings) -> None:
    data = {
        "enable_ai": settings.enable_ai,
        "openai_api_key": settings.openai_api_key,
        "openai_model": settings.openai_model,
        "openai_temperature": settings.openai_temperature,
    }
    mw.addonManager.writeConfig(ADDON_KEY, data)


class AISettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MC-Mapper – KI-Einstellungen")
        self.setModal(True)

        self._settings = get_settings()

        self.chk_enable = QCheckBox("KI-Unterstützung aktivieren")
        self.chk_enable.setChecked(self._settings.enable_ai)

        self.txt_key = QLineEdit(self._settings.openai_api_key)
        self.txt_key.setEchoMode(QLineEdit.Password)
        self.txt_key.setPlaceholderText("sk-...")

        self.btn_toggle = QPushButton("Anzeigen")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.toggled.connect(self._toggle_key_visibility)

        key_row = QHBoxLayout()
        key_row.addWidget(self.txt_key)
        key_row.addWidget(self.btn_toggle)

        self.cmb_model = QComboBox()
        self.cmb_model.addItems(AVAILABLE_MODELS)
        if self._settings.openai_model in AVAILABLE_MODELS:
            self.cmb_model.setCurrentText(self._settings.openai_model)
        else:
            self.cmb_model.insertItem(0, self._settings.openai_model)
            self.cmb_model.setCurrentIndex(0)

        self.txt_temperature = QLineEdit(str(self._settings.openai_temperature))
        self.txt_temperature.setPlaceholderText("0.0")

        form = QFormLayout()
        form.addRow(self.chk_enable)
        form.addRow(QLabel("OpenAI API Key:"), key_row)
        form.addRow(QLabel("Modell:"), self.cmb_model)
        form.addRow(QLabel("Temperatur (0–1):"), self.txt_temperature)

        info = QLabel(
            "Die KI bereinigt Formatierung und Rechtschreibung, ohne Inhalte zu verändern.\n"
            "Der API-Schlüssel wird lokal in der Anki-Konfiguration gespeichert."
        )
        info.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(info)
        layout.addWidget(buttons)

    def _toggle_key_visibility(self, checked: bool) -> None:
        self.txt_key.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self.btn_toggle.setText("Verbergen" if checked else "Anzeigen")

    def _on_accept(self) -> None:
        try:
            temperature = float(self.txt_temperature.text().strip() or 0.0)
        except ValueError:
            temperature = 0.0
        temperature = min(max(temperature, 0.0), 1.0)

        self._settings = AddonSettings(
            enable_ai=self.chk_enable.isChecked(),
            openai_api_key=self.txt_key.text().strip(),
            openai_model=self.cmb_model.currentText().strip() or DEFAULTS["openai_model"],
            openai_temperature=temperature,
        )
        store_settings(self._settings)
        self.accept()


def open_settings_dialog(parent=None) -> None:
    dlg = AISettingsDialog(parent)
    dlg.exec()
