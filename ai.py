"""Tools zur optionalen KI-gestützten Verfeinerung von Vorschlägen."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List

from .config import FIELDS

try:  # neue OpenAI-Bibliothek (>=1.0)
    from openai import OpenAI  # type: ignore
    _HAS_OPENAI_CLIENT = True
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore
    _HAS_OPENAI_CLIENT = False

try:  # Legacy-Bibliothek (<1.0)
    import openai as openai_legacy  # type: ignore
    _HAS_OPENAI_LEGACY = True
except Exception:  # pragma: no cover - optional dependency
    openai_legacy = None  # type: ignore
    _HAS_OPENAI_LEGACY = False

PROMPT_SYSTEM = (
    "Du bist ein hilfreicher Assistent für Anki-Karten im MC-Mapper. "
    "Du erhältst Rohdaten einer Multiple-Choice-Frage und einen bereits extrahierten "
    "Vorschlag. Bringe alle Felder in eine einheitliche, saubere Schreibweise: korrekte "
    "Rechtschreibung, konsistente Groß-/Kleinschreibung, sichere Formatierung. "
    "Verändere niemals die inhaltliche Aussage. Entferne keine Antwortoptionen und "
    "füge keine neuen Inhalte hinzu. Nutze keine Markdown-Listen; arbeite mit einfachem Text. "
    "Falls dir die Angaben unplausibel erscheinen, gib sie trotzdem unverändert zurück."
)

PROMPT_INSTRUCTIONS = (
    "Bitte gib ausschließlich ein JSON-Objekt mit folgenden Schlüsseln zurück: {keys}. "
    "Optional kannst du zusätzlich `duplicate_signature` (String) und `notes` (String) anfügen. "
    "`duplicate_signature` sollte ein stark normalisierter Schlüssel (Frage + Antworten) sein, "
    "der zur Dublettenprüfung genutzt werden kann. `notes` dient ausschließlich für Hinweise."
)


@dataclass
class AISettings:
    enabled: bool
    api_key: str
    model: str
    temperature: float


@dataclass
class AIResult:
    proposal: Dict[str, str] | None
    meta: Dict[str, Any]
    warnings: List[str]


def _normalize_signature(text: str) -> str:
    text = text or ""
    text = re.sub(r"\s+", " ", text, flags=re.MULTILINE)
    text = text.strip().lower()
    return text


def _collect_default_signature(proposal: Dict[str, str]) -> str:
    ordered_values = [proposal.get(field, "") for field in FIELDS]
    joined = " || ".join(_normalize_signature(v) for v in ordered_values)
    return joined


def _call_openai(settings: AISettings, payload: str) -> str:
    if _HAS_OPENAI_CLIENT:
        client = OpenAI(api_key=settings.api_key)  # type: ignore[call-arg]
        if hasattr(client, "chat") and hasattr(client.chat, "completions"):
            response = client.chat.completions.create(  # type: ignore[attr-defined]
                model=settings.model,
                temperature=settings.temperature,
                messages=[
                    {"role": "system", "content": PROMPT_SYSTEM},
                    {"role": "user", "content": payload},
                ],
            )
            return response.choices[0].message.content  # type: ignore[index]
        if hasattr(client, "responses"):
            response = client.responses.create(  # type: ignore[attr-defined]
                model=settings.model,
                temperature=settings.temperature,
                input=[
                    {"role": "system", "content": [{"type": "text", "text": PROMPT_SYSTEM}]},
                    {"role": "user", "content": [{"type": "text", "text": payload}]},
                ],
            )
            if hasattr(response, "output_text"):
                return response.output_text  # type: ignore[attr-defined]
            parts: List[str] = []
            for item in getattr(response, "output", []):
                if item.get("type") == "message":
                    for c in item.get("content", []):
                        if c.get("type") == "text":
                            parts.append(c.get("text", ""))
            return "\n".join(parts)
        raise RuntimeError("Die installierte openai-Bibliothek unterstützt keine Chat- oder Responses-API.")

    if _HAS_OPENAI_LEGACY:
        openai_legacy.api_key = settings.api_key  # type: ignore[assignment]
        completion = openai_legacy.ChatCompletion.create(  # type: ignore[attr-defined]
            model=settings.model,
            temperature=settings.temperature,
            messages=[
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": payload},
            ],
        )
        return completion["choices"][0]["message"]["content"]

    raise RuntimeError(
        "Die openai-Python-Bibliothek ist nicht installiert. Bitte `pip install openai` ausführen."
    )


def enhance_proposal(
    proposal: Dict[str, str] | None,
    *,
    context: Dict[str, Any],
    settings: AISettings,
) -> AIResult:
    if not proposal:
        return AIResult(proposal=None, meta={}, warnings=["Kein Vorschlag vorhanden. KI übersprungen."])

    if not settings.enabled:
        return AIResult(proposal=proposal, meta={}, warnings=[])

    if not settings.api_key.strip():
        return AIResult(
            proposal=proposal,
            meta={},
            warnings=["Keine OpenAI-API hinterlegt. KI deaktiviert."],
        )

    payload_dict = {
        "hinweis": PROMPT_INSTRUCTIONS.format(keys=", ".join(FIELDS)),
        "quelle": context,
        "vorschlag": proposal,
    }
    payload = json.dumps(payload_dict, ensure_ascii=False, indent=2)

    try:
        response_text = _call_openai(settings, payload)
    except Exception as exc:  # pragma: no cover - API Fehler
        return AIResult(
            proposal=proposal,
            meta={},
            warnings=[f"KI-Anfrage fehlgeschlagen: {exc}"],
        )

    if not response_text:
        return AIResult(
            proposal=proposal,
            meta={},
            warnings=["KI lieferte keine Antwort."],
        )

    response_text = response_text.strip()
    # Extrahiere erstes JSON-Objekt aus Antwort
    json_str = response_text
    if "{" in response_text and not response_text.startswith("{"):
        json_str = response_text[response_text.find("{") : response_text.rfind("}") + 1]

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as exc:
        return AIResult(
            proposal=proposal,
            meta={"raw_response": response_text},
            warnings=[f"KI-Antwort konnte nicht interpretiert werden ({exc})."],
        )

    improved = {}
    for field in FIELDS:
        raw_value = parsed.get(field)
        if raw_value is None:
            improved[field] = proposal.get(field, "")
        else:
            improved[field] = str(raw_value)

    warnings: List[str] = []
    for field in FIELDS:
        original = (proposal.get(field, "") or "").strip()
        new_val = (improved.get(field, "") or "").strip()
        if not original and new_val:
            warnings.append(f"KI hat in '{field}' neuen Inhalt ergänzt – Original beibehalten.")
            improved[field] = ""
        elif original and not new_val:
            warnings.append(f"KI hat Inhalt aus '{field}' entfernt – Original beibehalten.")
            improved[field] = original
        else:
            norm_orig = _normalize_signature(original)
            norm_new = _normalize_signature(new_val)
            if norm_orig and norm_new:
                # Verhindert drastische Änderungen (>50% Differenz)
                max_len = max(len(norm_orig), 1)
                delta = abs(len(norm_new) - len(norm_orig)) / max_len
                if delta > 0.5:
                    warnings.append(f"KI-Änderung in '{field}' ungewöhnlich groß – Original beibehalten.")
                    improved[field] = original

    meta: Dict[str, Any] = {}
    duplicate_signature = parsed.get("duplicate_signature")
    if isinstance(duplicate_signature, str) and duplicate_signature.strip():
        meta["duplicate_signature"] = duplicate_signature.strip()
    else:
        meta["duplicate_signature"] = _collect_default_signature(improved)

    notes = parsed.get("notes")
    if isinstance(notes, str) and notes.strip():
        meta["notes"] = notes.strip()

    return AIResult(proposal=improved, meta=meta, warnings=warnings)
