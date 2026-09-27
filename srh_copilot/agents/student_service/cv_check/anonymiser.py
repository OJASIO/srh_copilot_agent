"""Anonymisation of CV text before it is sent to a language model.

Layers, in this order:
  1. Contact data: email, phone (shared detectors in core/pii.py, including
     "0151/1234567" and "(06221) 123456"), LinkedIn, GitHub, personal websites
  2. Date and place of birth, by label ("Geburtsdatum:", "Geburtsort:", "DOB", "geb.")
     with the label kept, or a German "* 01.01.1999" line
  3. Personal details by label, value masked, label kept: nationality, marital
     status, religion, children, gender, age, residence or work permit, parents,
     ID and tax numbers
  4. Addresses anywhere in the text (labelled lines and street patterns), and a
     postal code when a town follows it
  5. The name: largest font on page one (PDF) or the first lines (DOCX), skipping
     titles such as LEBENSLAUF, cross-checked against the email address and any
     "Name:" label. Every part of the name is masked, so a signature line such as
     "E. Musterfrau" is caught too.
  6. Websites that contain a part of the name

Placeholders stay in the text so the reviewer can see that the information
exists; the prompt tells the model to treat them as present. Labels stay too,
because "Familienstand: [PERSONAL DETAIL REMOVED]" is still useful feedback
material (German CVs no longer need it), while the value is nobody's business.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agents.student_service.cv_check.extractor import is_title_line, line_name_candidates, pdf_name_candidates
from core.pii import EMAIL, find_phones, replace_spans

_LINKEDIN = re.compile(r"(https?://)?([a-z]{2,3}\.)?linkedin\.com/in/[\w\-%]+/?", re.IGNORECASE)
_GITHUB_REPO = re.compile(r"(https?://)?(www\.)?github\.com/([a-zA-Z0-9\-]+)/([a-zA-Z0-9\-_\.]+)")
_GITHUB_PROFILE = re.compile(r"(https?://)?(www\.)?github\.com/[a-zA-Z0-9\-]+/?")
_PORTFOLIO = re.compile(
    r"(https?://)?(www\.)?[a-zA-Z0-9\-]+\.(github\.io|me|dev|portfolio|site|web\.app|streamlit\.app)(/[^\s]*)?"
)
_URL = re.compile(r"(?:https?://|www\.)[^\s|,;]+|\b[\w\-]+\.(?:de|com|net|org|io|me|dev|eu|info|at|ch)(?:/[^\s|,;]*)?\b")

_SEP = r"[ \t]*[:|\t][ \t]*"  # label separator on the same line: colon, table cell or tab
_LINE_START = r"(^|[|\u2022;]\s*|\s{2,})"  # a label starts a line or a table cell

# Birth data: the label stays and only the value is masked, so the reviewer can tell
# "Geburtsdatum: [DOB REMOVED]" from "Geburtsort: [BIRTHPLACE REMOVED]" (two identical
# placeholders made the model report a missing birthplace). Longest labels first.
_BIRTHPLACE_LABELS = r"Place of Birth|Geburtsort|Birthplace|Geboren in"
_DOB_LABELS = (r"Date and Place of Birth|Date & Place of Birth|Geburtsdatum und -ort|Geburtsdatum/-ort|"
               r"Date of Birth|Geburtsdatum|Geburtstag|Geboren am|Birth date|Birthdate|D\.O\.B\.|DOB|"
               + _BIRTHPLACE_LABELS)
# Everyday words ("Born to code") count only when a separator, a date or am/on/in follows.
_DOB_WEAK = r"(?:Geboren|geb\.|Born)(?=[ \t]*(?:[:|\t]|(?:am|on|in)\b|\d))"
_DOB = re.compile(rf"(?<![\w])({_DOB_LABELS}|{_DOB_WEAK})(?![\w])([ \t]*[:|\t]?[ \t]*)([^\n|]+)",
                  re.IGNORECASE)
_BIRTHPLACE = re.compile(rf"(?i)^(?:{_BIRTHPLACE_LABELS})$")
_DOB_STAR = re.compile(r"(?m)^\s*\*\s*\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}[^\n|]*")

_PERSONAL_LABELS = (
    r"Staatsangehörigkeit|Staatsbürgerschaft|Nationalität|Nationality|Citizenship|"
    r"Familienstand|Personenstand|Marital status|Civil status|"
    r"Konfession|Religionszugehörigkeit|Religion|Glaubensbekenntnis|Religious affiliation|"
    r"Kinder|Children|Geschlecht|Gender|Sex|Alter|Age|"
    r"Aufenthaltstitel|Aufenthaltserlaubnis|Aufenthaltsstatus|Residence permit|Residence status|"
    r"Visa status|Visum|Visa|Work permit|Arbeitserlaubnis|"
    r"Steuer-ID|Steuernummer|Steueridentifikationsnummer|Tax ID|Sozialversicherungsnummer|"
    r"Social security number|SSN|Personalausweisnummer|Personalausweis|Ausweisnummer|"
    r"Passport number|Passport|Reisepassnummer|Reisepass|ID number|"
    r"Schwerbehinderung|Behinderung|Disability|Gesundheitszustand|Gesundheit|Health|"
    r"Eltern|Vater|Mutter|Parents|Father|Mother|Geschwister|Siblings|Ehepartner|Spouse"
)
_PERSONAL = re.compile(rf"(?mi){_LINE_START}({_PERSONAL_LABELS}){_SEP}([^\n|;\u2022]+)")

_ADDRESS_LABELS = r"Anschrift|Adresse|Postanschrift|Wohnanschrift|Address|Home address|Wohnort|Wohnhaft in|Wohnhaft"
_ADDRESS_LABELLED = re.compile(rf"(?mi){_LINE_START}({_ADDRESS_LABELS}){_SEP}([^\n|;\u2022]+)")
_NAME_LABELS = (r"Name|Vor- und Nachname|Vor-und Nachname|Vollständiger Name|Full name|Vorname|Nachname|"
                r"Familienname|Surname|First name|Last name|Given name")
_NAME_LABELLED = re.compile(rf"(?mi)^\s*(?:{_NAME_LABELS}){_SEP}([^\n|;\u2022]+)")

_LABEL_ONLY = {
    "dob": re.compile(rf"(?i)^\s*(?:{_DOB_LABELS}|Geboren|geb\.|Born)\s*:?\s*$"),
    "personal": re.compile(rf"(?i)^\s*(?:{_PERSONAL_LABELS})\s*:?\s*$"),
    "address": re.compile(rf"(?i)^\s*(?:{_ADDRESS_LABELS})\s*:?\s*$"),
    "name": re.compile(rf"(?i)^\s*(?:{_NAME_LABELS})\s*:?\s*$"),
}
_TOKEN = {"dob": "[DOB REMOVED]", "personal": "[PERSONAL DETAIL REMOVED]", "address": "[ADDRESS REMOVED]"}
BIRTHPLACE_TOKEN = "[BIRTHPLACE REMOVED]"

# Street names. Case-sensitive on purpose, and "ring" not after a vowel, so that
# "Engineering 2024" or "Monitoring 24/7" are not taken for an address. House
# numbers have at most 3 digits, so years are never matched. Spaces only, never a
# line break: an address does not swallow the name on the line above.
_HOUSE_NO = r"\d{1,3}[ \t]?[a-zA-Z]?(?:[ \t]?[-/][ \t]?\d{1,3}[a-zA-Z]?)?(?![\d/])\b"
_STREET_SUFFIX = (r"(?:stra(?:ß|ss)e|str\.|weg|gasse|allee|platz|(?<![aeiouäöüAEIOUÄÖÜ])ring|damm|ufer|"
                  r"anlage|chaussee|steig|pfad|markt|graben|wall|"
                  r"-(?:Straße|Strasse|Str\.|Weg|Gasse|Allee|Platz|Ring|Damm|Ufer|Anlage|Chaussee|Markt))")
_STREET_WORD = r"(?:Straße|Strasse|Str\.|Weg|Gasse|Allee|Platz|Ring|Damm|Ufer|Anlage|Chaussee|Markt|Graben)"
_STREETS = [
    re.compile(rf"\b[A-ZÄÖÜ][\wäöüß.\-]*?{_STREET_SUFFIX}[ \t]*{_HOUSE_NO}"),  # Hauptstraße 12, Ludwig-Guttmann-Str. 6
    re.compile(rf"\b(?:[A-ZÄÖÜ][\wäöüß.\-]*[ \t]+){{1,3}}{_STREET_WORD}[ \t]+{_HOUSE_NO}"),  # Bergheimer Straße 5
    re.compile(r"\b\d{1,4}[A-Za-z]?[ \t]+(?:[A-Z][\w\-]*[ \t]+){1,3}(?:Street|St\.|Road|Rd\.|Avenue|Ave\.|Lane|Drive|"
               r"Way|Close|Court|Boulevard|Place)\b"),  # 221B Baker Street
]
# "Am Grauen Stein 27", "Platz der Deutschen Einheit 1": too loose for the whole text,
# so only used in the header and in a personal-data section.
_STREET_PREPOSITION = re.compile(
    rf"\b(?:Am|An der|An den|Im|In der|In den|Auf dem|Auf der|Zum|Zur|Hinter der|Unter den|Platz der|Platz des)[ \t]+"
    rf"[A-ZÄÖÜ][\wäöüß\-]*(?:[ \t]+[A-ZÄÖÜ][\wäöüß\-]*){{0,2}}[ \t]+{_HOUSE_NO}")
# Postal code: five digits before a capitalised word. German nouns are capitalised
# too ("50000 Datensätze"), so outside the header and personal-data sections a
# postal code also needs address context: right after a masked street, "D-" or "PLZ".
_PLZ = re.compile(r"(?<![\w\-])(?:D-)?\d{5}(?=\s+[A-ZÄÖÜ][a-zäöüß])")
_PLZ_IN_ADDRESS = re.compile(r"(\[ADDRESS REMOVED\],?\s*|\bD-|\bPLZ:?\s*)(\d{5})(?=\s+[A-ZÄÖÜ][a-zäöüß])")

SECTION_MARKERS = [
    "BERUFSERFAHRUNG", "WORK EXPERIENCE", "EXPERIENCE", "PROFESSIONAL EXPERIENCE", "PRAKTISCHE ERFAHRUNG",
    "AUSBILDUNG", "EDUCATION", "KOMPETENZEN", "SKILLS", "TECHNICAL SKILLS", "KENNTNISSE", "SCHULISCHE",
    "STUDIUM", "STUDIENPROJEKTE", "ACADEMIC PROJECTS", "PROFESSIONAL SUMMARY", "SUMMARY", "PROFIL",
    "ZERTIFIKATE", "HOBBYS", "P R O F I L", "S T U D I E N", "K E N N T N I S S E", "P R A K T I S C H E",
    "A U S B I L D U N G",
]
PERSONAL_SECTIONS = [
    "PERSÖNLICHE DATEN", "PERSONAL DETAILS", "PERSONAL INFORMATION", "KONTAKT", "CONTACT", "ÜBER MICH", "ABOUT ME",
]

_PARTICLES = {"von", "van", "der", "den", "de", "del", "da", "di", "zu", "zum", "ten", "ter", "al", "el", "bin",
              "le", "la", "dr", "prof", "msc", "bsc", "mba", "phd", "dipl", "ing", "med", "mr", "mrs", "ms", "herr",
              "frau", "b.sc", "m.sc", "m.a", "b.a"}
_PLACEHOLDER_WORDS = {"per", "email", "phone", "plz", "dob", "removed", "address", "personal", "detail", "website",
                      "linkedin", "github", "user"}


@dataclass
class AnonymisationResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _zones(text: str) -> list[tuple[int, int]]:
    """Header (before the first content section) and personal-data sections, as spans."""
    upper = text.upper()
    header_end = len(text)
    for marker in SECTION_MARKERS:
        idx = upper.find(marker)
        if idx != -1 and idx < header_end:
            header_end = idx
    zones = [(0, header_end)]
    for marker in PERSONAL_SECTIONS:
        start = upper.find(marker)
        while start != -1:
            end = len(text)
            for content_marker in SECTION_MARKERS:
                cidx = upper.find(content_marker, start + len(marker))
                if cidx != -1 and cidx < end:
                    end = cidx
            zones.append((start, end))
            start = upper.find(marker, start + len(marker))
    return zones


def _in_zones(text: str, rx: re.Pattern) -> list[tuple[int, int]]:
    """Non-overlapping match spans of `rx` inside the header and personal-data sections."""
    spans = sorted({m.span() for z0, z1 in _zones(text) for m in rx.finditer(text, z0, z1)})
    out: list[tuple[int, int]] = []
    for span in spans:
        if not out or span[0] >= out[-1][1]:
            out.append(span)
    return out


def _email_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for m in EMAIL.finditer(text):
        local = m.group(0).split("@", 1)[0]
        tokens |= {t.lower() for t in re.split(r"[._\-+\d]+", local) if len(t) >= 3}
    return tokens


def detect_names(text: str, filename: str = "", data: bytes | None = None) -> list[str]:
    """Names to mask: the best layout candidate plus anything behind a "Name:" label."""
    names: list[str] = []
    for m in _NAME_LABELLED.finditer(text):
        value = m.group(1).strip()
        if value and not is_title_line(value):
            names.append(value)
    lines = text.split("\n")
    for i, line in enumerate(lines[:-1]):
        if _LABEL_ONLY["name"].match(line) and 0 < len(lines[i + 1].strip()) <= 60:
            names.append(lines[i + 1].strip())

    candidates: list[str] = []
    if data is not None and filename.lower().endswith(".pdf"):
        candidates = pdf_name_candidates(data)
    if not candidates:
        candidates = line_name_candidates(text)
    email_tokens = _email_tokens(text)
    best = ""
    for cand in candidates[:5]:
        if any(w.lower() in email_tokens for w in cand.split()):
            best = cand
            break
    if not best and candidates:
        best = candidates[0]
    if best:
        names.insert(0, best)
    out: list[str] = []
    for n in names:
        n = re.sub(r"\s+", " ", n).strip(" ,;")
        if n and n not in out:
            out.append(n)
    return out


def _name_tokens(name: str) -> list[str]:
    tokens = []
    for raw in re.split(r"[\s,]+", name):
        raw = raw.strip(".,;:()")
        parts = [raw] + (raw.split("-") if "-" in raw else [])
        for tok in parts:
            if (len(tok) >= 3 and re.fullmatch(r"[^\W\d_][\w'\u2019-]*", tok)
                    and tok.lower() not in _PARTICLES and tok.lower() not in _PLACEHOLDER_WORDS):
                tokens.append(tok)
    return sorted(set(tokens), key=len, reverse=True)  # longest first: "Müller-Lüdenscheidt" before "Müller"


def _mask_names(text: str, names: list[str]) -> tuple[str, int]:
    count = 0
    for name in names:
        words = name.split()
        if not words:
            continue
        full = r"\s+".join(re.escape(w) for w in words)
        text, n = re.subn(rf"(?<![\w]){full}(?![\w])", "[PER]", text, flags=re.IGNORECASE)
        count += n
        for tok in _name_tokens(name):
            variants = {tok, tok.upper(), tok.capitalize()}
            pattern = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
            text, n = re.subn(rf"(?<![\w\[])(?:{pattern})(?![\w\]])", "[PER]", text)
            count += n
    return text, count


def anonymise(text: str, *, filename: str = "", data: bytes | None = None) -> AnonymisationResult:
    counts: dict[str, int] = {}

    def add(key: str, n: int) -> None:
        if n:
            counts[key] = counts.get(key, 0) + n

    names = detect_names(text, filename, data)
    name_tokens = {t.lower() for n in names for t in _name_tokens(n)}

    # 1. contact data
    text, n = EMAIL.subn("[EMAIL]", text)
    add("email", n)
    text, n = _LINKEDIN.subn("[LINKEDIN REMOVED]", text)
    add("linkedin", n)
    text, n = _GITHUB_REPO.subn(r"github.com/[GITHUB USER]/\4", text)
    add("github", n)
    text, n = _GITHUB_PROFILE.subn("[GITHUB REMOVED]", text)
    add("github", n)
    text, n = _PORTFOLIO.subn("[PERSONAL WEBSITE REMOVED]", text)
    add("website", n)
    text, n = replace_spans(text, find_phones(text), "[PHONE]")
    add("phone", n)

    # 2. date and place of birth
    def birth(m: re.Match) -> str:
        label, sep = m.group(1), m.group(2) or " "
        token = BIRTHPLACE_TOKEN if _BIRTHPLACE.match(label) else "[DOB REMOVED]"
        trailing = m.group(3)[len(m.group(3).rstrip()):]
        return f"{label}{sep}{token}{trailing}"

    text, n = _DOB.subn(birth, text)
    add("birth", n)
    text, n = _DOB_STAR.subn("[DOB REMOVED]", text)
    add("birth", n)

    # 3. personal details and 4. labelled addresses: keep the label, drop the value
    def keep_label(token: str):
        def sub(m: re.Match) -> str:
            trailing = m.group(3)[len(m.group(3).rstrip()):]
            return f"{m.group(1)}{m.group(2)}: {token}{trailing}"
        return sub

    text, n = _PERSONAL.subn(keep_label("[PERSONAL DETAIL REMOVED]"), text)
    add("personal_detail", n)
    text, n = _ADDRESS_LABELLED.subn(keep_label("[ADDRESS REMOVED]"), text)
    add("address", n)
    lines = text.split("\n")
    for i in range(len(lines) - 1):
        for kind in ("dob", "personal", "address"):
            value = lines[i + 1].strip()
            if (_LABEL_ONLY[kind].match(lines[i]) and 0 < len(value) <= 60
                    and not value.startswith("[") and not is_title_line(value)):
                label = lines[i].strip().rstrip(":").strip()
                lines[i + 1] = BIRTHPLACE_TOKEN if kind == "dob" and _BIRTHPLACE.match(label) else _TOKEN[kind]
                add({"dob": "birth", "personal": "personal_detail"}.get(kind, kind), 1)
    text = "\n".join(lines)

    # 4. street addresses anywhere, the looser pattern only in header and personal sections
    for rx in _STREETS:
        text, n = rx.subn("[ADDRESS REMOVED]", text)
        add("address", n)
    text, n = replace_spans(text, _in_zones(text, _STREET_PREPOSITION), "[ADDRESS REMOVED]")
    add("address", n)
    text, n = replace_spans(text, _in_zones(text, _PLZ), "[PLZ]")
    add("postal_code", n)
    text, n = _PLZ_IN_ADDRESS.subn(lambda m: f"{m.group(1)}[PLZ]", text)
    add("postal_code", n)

    # 5. name
    text, n = _mask_names(text, names)
    add("name", n)

    # 6. websites that carry the name
    if name_tokens:
        def web(m: re.Match) -> str:
            if any(t in m.group(0).lower() for t in name_tokens):
                add("website", 1)
                return "[PERSONAL WEBSITE REMOVED]"
            return m.group(0)
        text = _URL.sub(web, text)

    return AnonymisationResult(text=text, counts=counts, names=names)


def anonymise_text(text: str, *, filename: str = "", data: bytes | None = None) -> str:
    return anonymise(text, filename=filename, data=data).text
