# MC Mapper

**MC Mapper** is an Anki add-on that helps convert messy or legacy multiple-choice cards into a clean, structured note format.

## Features
- Automatically detects:
  - Question text and answer options (A–E)
  - Correct answer (from letters, bitstrings, or text)
  - Optional comments or notes
- Generates structured proposals for the target note type **“MC7-DZ”**
- Provides a visual review interface for checking and editing before applying
- Tags migrated notes with `migrated/by-mc-mapper`
- Detects duplicates using content-based hash tags (`MMKEY_…`)
- Optional KI-Normalisierung via OpenAI für konsistente Formatierung

## Usage
Select one or more notes in the browser, then choose **Tools → MC-Mapper…** to start reviewing and migrating cards.

### KI-Unterstützung (optional)
Für besonders saubere Ergebnisse kann eine OpenAI-Anbindung aktiviert werden:

1. Hinterlege deinen API-Schlüssel unter **Tools → MC-Mapper KI-Einstellungen…**.
2. Aktiviere die Option „KI-Unterstützung“ und wähle das gewünschte Modell (z. B. `gpt-4o-mini`).
3. Während des Reviews verfeinert die KI Frage- und Antworttexte, korrigiert Rechtschreibung und harmonisiert die Formatierung – ohne Inhalte zu verändern.

Die generierten Vorschläge bleiben vollständig editierbar. Für die Dublettenprüfung erstellt die KI zusätzlich eine normalisierte Signatur, die automatisch für den `MMKEY_…`-Abgleich genutzt wird.

## Compatibility
Works with Anki 2.1.35 and later (desktop).

---

Created to streamline the conversion of shared medical multiple-choice decks into a unified, high-quality format.
