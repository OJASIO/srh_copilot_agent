"""Text extraction for CVs. Works on bytes, since attachments never touch disk.

PDF: PyMuPDF (pymupdf) when installed, because it keeps font metadata that the
name detection needs and handles ligatures better; falls back to pypdf.
DOCX: python-docx, walking the document in reading order: paragraphs, tables
(one line per row, cells joined by " | "), text boxes, content controls, and the
page header and footer, where many templates put the name and contact details.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata

log = logging.getLogger(__name__)

# Only truly non-standard characters need manual mapping.
# Standard ligatures (fi, fl, ff, ...) are handled by NFKC automatically.
CUSTOM_LIGATURES = {
    "Ɵ": "ti",  # misused as ti ligature
    "Ʃ": "tt",  # misused as tt ligature
    "Ņ": "fk",  # misused as fk ligature
    "ƞ": "tf",  # misused as tf ligature
    "Ō": "ft",  # misused as ft ligature
    "\uf0b7": "\u2022",  # private-use bullet (Symbol font) -> standard bullet
}

_NOT_A_NAME = ("@", "/", "|", ":", "+", "(", ")", ".com", "http", ".de", ".pdf", "\u00b7", "\u2013")  # middle dot, en dash

# Large text on page one that is not a person's name: document titles and section headings.
TITLE_WORDS = {
    "lebenslauf", "tabellarischer lebenslauf", "curriculum vitae", "curriculum", "vitae", "cv", "resume",
    "résumé", "bewerbung", "bewerbungsunterlagen", "anschreiben", "cover letter", "motivationsschreiben",
    "profil", "profile", "kurzprofil", "kontakt", "contact", "persönliche daten", "personal details",
    "personal information", "persönliches", "über mich", "about me", "berufserfahrung", "work experience",
    "experience", "professional experience", "praktische erfahrung", "ausbildung", "education", "studium",
    "skills", "technical skills", "kenntnisse", "kompetenzen", "projects", "projekte", "summary",
    "professional summary", "zusammenfassung", "sprachen", "languages", "referenzen", "references",
    "zertifikate", "certificates", "certifications", "hobbys", "hobbies", "interessen", "interests",
}


# Large text that is a role, not a name ("Data Scientist" under the name in many templates).
ROLE_WORDS = {
    "data", "scientist", "science", "engineer", "engineering", "software", "developer", "manager", "analyst",
    "analytics", "student", "studentin", "consultant", "designer", "marketing", "sales", "business", "senior",
    "junior", "lead", "head", "project", "product", "research", "researcher", "assistant", "intern", "trainee",
    "werkstudent", "werkstudentin", "entwickler", "entwicklerin", "ingenieur", "ingenieurin", "berater",
    "beraterin", "informatiker", "informatikerin", "kauffrau", "kaufmann", "master", "bachelor", "computer",
    "machine", "learning", "full", "stack", "frontend", "backend", "cloud", "devops", "specialist", "expert",
    "officer", "executive", "director", "coordinator", "teacher", "nurse", "psychologist", "candidate",
    "applicant", "graduate", "absolvent", "absolventin", "ux", "ui", "ai", "ml", "it", "hr", "and", "und", "of",
}


class ExtractionError(Exception):
    pass


def fix_encoding(text: str) -> str:
    """NFKC normalisation for standard ligatures, then the custom map."""
    text = unicodedata.normalize("NFKC", text)
    for char, replacement in CUSTOM_LIGATURES.items():
        text = text.replace(char, replacement)
    return text


def _pymupdf():
    try:
        import pymupdf  # noqa: F401

        return pymupdf
    except ImportError:
        return None


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return _extract_pdf(data)
    if name.endswith(".docx"):
        return _extract_docx(data)
    raise ExtractionError(f"unsupported file type: {filename}")


def _extract_pdf(data: bytes) -> str:
    mupdf = _pymupdf()
    if mupdf is not None:
        with mupdf.open(stream=data, filetype="pdf") as doc:
            pages = [p.get_text() for p in doc]
    else:
        from pypdf import PdfReader

        pages = [(p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages]
    raw = "\n".join(p for p in pages if p.strip()).strip()
    return fix_encoding(raw)


# DOCX

_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


def _walk_docx(container, parent, out: list[str]) -> None:
    """Append the text of every block in `container` in reading order."""
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    for child in container.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, parent).text
            if text.strip():
                out.append(text)
            # text boxes anchored in this paragraph; the VML fallback copy is skipped
            for box in child.iter(qn("w:txbxContent")):
                if not any(a.tag == _MC_FALLBACK for a in box.iterancestors()):
                    _walk_docx(box, parent, out)
        elif child.tag == qn("w:tbl"):
            for tr in child.iterchildren(qn("w:tr")):
                cells = []
                for tc in tr.iterchildren(qn("w:tc")):
                    sub: list[str] = []
                    _walk_docx(tc, parent, sub)
                    cell = "\n".join(sub).strip()
                    if cell:
                        cells.append(cell)
                if cells:
                    out.append(" | ".join(cells))
        elif child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                _walk_docx(content, parent, out)


def _extract_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    header: list[str] = []
    footer: list[str] = []
    seen: set[int] = set()
    for section in doc.sections:
        for part, out in ((section.first_page_header, header), (section.header, header),
                          (section.even_page_header, header), (section.first_page_footer, footer),
                          (section.footer, footer), (section.even_page_footer, footer)):
            try:
                if part.is_linked_to_previous or id(part._element) in seen:
                    continue
                seen.add(id(part._element))
                before = len(out)
                _walk_docx(part._element, doc, out)
                # the same header often exists for first page and default pages
                out[before:] = [t for t in out[before:] if t not in out[:before]]
            except Exception as exc:  # a malformed header must not lose the whole CV
                log.debug("docx header/footer skipped: %s", exc)
    body: list[str] = []
    _walk_docx(doc.element.body, doc, body)
    return fix_encoding("\n".join(header + body + footer))


# Name detection

def _normalise_title(text: str) -> str:
    words = re.sub(r"[^a-zäöüßé ]", " ", text.lower()).split()
    if words and all(len(w) == 1 for w in words):  # letter-spaced headings: "L E B E N S L A U F"
        return "".join(words)
    return " ".join(words)


def is_title_line(text: str) -> bool:
    norm = _normalise_title(text)
    return norm in TITLE_WORDS or norm.replace(" ", "") in {t.replace(" ", "") for t in TITLE_WORDS}


def plausible_name(text: str) -> bool:
    text = text.strip()
    words = text.split()
    if not 1 <= len(words) <= 4 or len(text) <= 3 or any(c.isdigit() for c in text):
        return False
    if any(c in text for c in _NOT_A_NAME) or is_title_line(text):
        return False
    if all(w.lower().strip(".,&") in ROLE_WORDS for w in words):
        return False
    compact = text.replace(" ", "")
    return sum(c.isalpha() for c in compact) >= 0.8 * len(compact)


def pdf_name_candidates(data: bytes) -> list[str]:
    """Plausible name lines on page one, largest font first."""
    mupdf = _pymupdf()
    if mupdf is None:
        return []
    candidates: list[tuple[float, str]] = []
    with mupdf.open(stream=data, filetype="pdf") as doc:
        if doc.page_count == 0:
            return []
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = fix_encoding(" ".join(s["text"] for s in line["spans"]).strip())
                size = max((s["size"] for s in line["spans"]), default=0)
                if plausible_name(text):
                    candidates.append((size, re.sub(r"\s+", " ", text)))
    candidates.sort(key=lambda x: x[0], reverse=True)
    out: list[str] = []
    for _, text in candidates:
        if text not in out:
            out.append(text)
    return out


def extract_name_from_pdf(data: bytes) -> str:
    """The candidate name is almost always the largest text on page one that is
    not a title such as LEBENSLAUF. Returns "" when nothing qualifies."""
    cands = pdf_name_candidates(data)
    return cands[0] if cands else ""


def guess_name_from_lines(text: str) -> str:
    """DOCX fallback: first short line in the header that does not look like contact data or a title."""
    for cand in line_name_candidates(text):
        return cand
    return ""


def line_name_candidates(text: str, max_lines: int = 8) -> list[str]:
    out = []
    for line in text.split("\n")[:max_lines]:
        for part in line.split("|"):
            part = part.strip()
            if plausible_name(part) and part not in out:
                out.append(part)
    return out
