# config.py — zentrale Konfiguration für MC-Mapper

# Standard-Ziel-Notiztyp (Vorauswahl, kann beim Start geändert werden)
TARGET_MODEL_NAME = "MC7-DZ"

# Ziel-Feldreihenfolge
FIELDS = [
    "Frage", "Antwort A", "Antwort B", "Antwort C", "Antwort D", "Antwort E",
    "Kopfzeile", "Eigene Notizen", "Antwort"
]

# Tags
TAG_NEW = "migrated/by-mc-mapper"       # markiert ALT-Karten nach Migration
TAG_HASH_PREFIX = "MMKEY_"              # Hash-Tag-Präfix für Dublettenwarnung
