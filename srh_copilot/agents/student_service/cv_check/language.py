"""EN / DE detection for CV text.

The original used `langdetect`. That package is a 2021 source-only release
that no longer builds under current setuptools, and the decision here is
binary, so a stopword count is enough and needs no dependency. Deterministic
too, which the test suite likes.
"""

from __future__ import annotations

import re

_DE = {"und", "der", "die", "das", "mit", "für", "von", "bei", "im", "als", "seit", "bis", "sowie", "oder",
       "ausbildung", "berufserfahrung", "kenntnisse", "sprachen", "studium", "praktikum", "hochschule",
       "universität", "geboren", "lebenslauf", "abitur", "muttersprache", "verhandlungssicher", "fließend"}
_EN = {"and", "the", "with", "for", "from", "at", "in", "as", "since", "to", "or", "of",
       "education", "experience", "skills", "languages", "university", "internship", "bachelor", "master",
       "responsible", "developed", "managed", "native", "fluent", "summary", "profile"}

_WORD = re.compile(r"[a-zäöüß]+")


def detect_language(text: str) -> str:
    """Returns "de", "en" or "unknown"."""
    if not text or not text.strip():
        return "unknown"
    words = _WORD.findall(text[:4000].lower())
    if not words:
        return "unknown"
    de = sum(w in _DE for w in words)
    en = sum(w in _EN for w in words)
    if de == 0 and en == 0:
        return "unknown"
    return "de" if de > en else "en"


def language_label(code: str) -> str:
    return {"de": "German (DE)", "en": "English (EN)", "unknown": "Undetermined"}.get(code, "Unknown")
