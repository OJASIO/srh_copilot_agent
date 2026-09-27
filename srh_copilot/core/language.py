"""German or English? A small, deterministic guess for short chat messages.

Used to pick the language of text the code adds around a model answer (contact
block, disclaimer, guardrail messages). The model itself is told to answer in
the language of the question, so this only has to be right for the frame.
Words that exist in both languages ("in", "an", "was", "die", "am") are left out.
"""

from __future__ import annotations

import re

_DE = {
    "und", "der", "den", "dem", "des", "das", "ein", "eine", "einen", "einem", "einer", "ich", "mein", "meine",
    "meinen", "meinem", "mich", "mir", "wer", "wie", "welche", "welcher", "welches", "wann", "wo", "warum",
    "kann", "können", "muss", "müssen", "darf", "gibt", "es", "ist", "sind", "bin", "habe", "hat", "haben",
    "für", "von", "mit", "bei", "auf", "zu", "zum", "zur", "nicht", "auch", "oder", "wenn", "ob", "bekomme",
    "bekommen", "bewerben", "bewerbe", "bitte", "viel", "wieviel", "monatlich", "jetzt", "noch", "gilt",
    "hochschule", "studierende", "studium", "unterschreibt", "antrag", "stipendien", "frist", "fristen",
}
_EN = {
    "the", "a", "is", "are", "what", "which", "who", "how", "when", "where", "why", "can", "could", "do",
    "does", "did", "i", "my", "me", "you", "your", "for", "of", "to", "and", "or", "with", "there", "any",
    "apply", "please", "much", "many", "get", "need", "should", "would", "will", "about", "per", "month",
    "deadline", "deadlines", "scholarships", "funding", "student", "students", "now", "still", "it",
}
_WORD = re.compile(r"[a-zäöüß]+")


def today_text(lang: str = "en") -> str:
    """Today's date for prompts, so a model does not take a current date for the future."""
    from datetime import date

    d = date.today()
    months = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December")  # fixed names: strftime("%B") depends on the server locale
    return d.strftime("%d.%m.%Y") if lang == "de" else f"{d.day} {months[d.month - 1]} {d.year}"


def detect_language(text: str, default: str = "en") -> str:
    """Returns "de" or "en"."""
    words = _WORD.findall((text or "").lower())
    de = sum(w in _DE for w in words)
    en = sum(w in _EN for w in words)
    if de > en:
        return "de"
    if en > de:
        return "en"
    if re.search(r"[äöüß]", (text or "").lower()):
        return "de"
    return default
