"""Facts about the CV file that the model cannot see in extracted text.

The review prompt asks about a photo, the length in pages, tables and the email
address. Text extraction loses the first three, and the anonymiser masks the
fourth, so without these facts the model can only guess. Everything here is
measured from the file, deterministic, and phrased for the prompt.

    pages               PDF: page count. DOCX: the page count Word saved in the file, if any
    photo               True / False: a portrait-shaped picture of at least ~2 cm
    tables              DOCX only: layout tables (some ATS parsers read them badly)
    email_unprofessional  True / False / None (no email): nicknames such as "partyboy99"
"""

from __future__ import annotations

import io
import logging
import re
import zipfile

from core.pii import EMAIL

log = logging.getLogger(__name__)

_EMU_PER_PT = 12700
# Nickname words. Whole tokens only, so real names (Angela, Engel, King) never match.
_SLANG = ("sexy", "baby", "babe", "party", "cool", "cute", "princess", "prinzessin", "killer", "gamer", "honey",
          "bunny", "maus", "mausi", "schatz", "devil", "teufel", "lol", "crazy", "dragon", "ninja", "sunshine",
          "hasi", "zocker", "chiller", "xxx", "süß", "suess", "sweet", "sweety", "hottie", "bad", "wild")
_FILLER = ("boy", "girl", "kid", "man", "lady", "chen", "xx", "x", "the", "lil", "mr", "miss")
_NICKNAME = re.compile(rf"^(?:{'|'.join(_SLANG + _FILLER)})*(?:{'|'.join(_SLANG)})(?:{'|'.join(_SLANG + _FILLER)})*$")


def _is_photo(width_pt: float, height_pt: float, page_width_pt: float | None = None) -> bool:
    """Portrait or square picture, at least ~50 pt (1.8 cm), not a full-width banner or a small icon."""
    if width_pt < 50 or height_pt < 50:
        return False
    if page_width_pt and width_pt > 0.6 * page_width_pt:
        return False
    return 0.55 <= width_pt / height_pt <= 1.25


def _pdf_facts(data: bytes) -> dict:
    try:
        import pymupdf
    except ImportError:
        return {"pages": None, "photo": None, "tables": None}
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages = doc.page_count
        photo = False
        if pages:
            page = doc[0]
            for info in page.get_image_info():
                x0, y0, x1, y1 = info["bbox"]
                if _is_photo(x1 - x0, y1 - y0, page.rect.width):
                    photo = True
                    break
    return {"pages": pages, "photo": photo, "tables": None}


def _docx_facts(data: bytes) -> dict:
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(io.BytesIO(data))
    photo = False
    page_width = None
    if doc.sections and doc.sections[0].page_width:
        page_width = doc.sections[0].page_width / _EMU_PER_PT
    parts = [doc.element.body] + [s.header._element for s in doc.sections if not s.header.is_linked_to_previous]
    for part in parts:
        for tag in ("wp:inline", "wp:anchor"):
            for el in part.iter(qn(tag)):
                if el.find(".//" + qn("pic:pic")) is None:
                    continue
                extent = el.find(qn("wp:extent"))
                if extent is None:
                    continue
                w, h = int(extent.get("cx", 0)) / _EMU_PER_PT, int(extent.get("cy", 0)) / _EMU_PER_PT
                if _is_photo(w, h, page_width):
                    photo = True
    pages = None
    try:
        app = zipfile.ZipFile(io.BytesIO(data)).read("docProps/app.xml").decode("utf-8", "ignore")
        m = re.search(r"<Pages>(\d+)</Pages>", app)
        pages = int(m.group(1)) if m else None
    except (KeyError, zipfile.BadZipFile):
        pass
    return {"pages": pages, "photo": photo, "tables": len(doc.tables) > 0}


def email_unprofessional(text: str) -> bool | None:
    m = EMAIL.search(text)
    if not m:
        return None
    local = m.group(0).split("@", 1)[0].lower()
    return any(_NICKNAME.match(tok) for tok in re.split(r"[._\-+\d]+", local) if tok)


def document_facts(filename: str, data: bytes, raw_text: str) -> dict:
    name = filename.lower()
    try:
        facts = _pdf_facts(data) if name.endswith(".pdf") else _docx_facts(data) if name.endswith(".docx") else {}
    except Exception as exc:  # facts are a bonus; never fail the review because of them
        log.warning("document facts unavailable (%s): %s", name.rsplit(".", 1)[-1], type(exc).__name__)
        facts = {}
    facts.setdefault("pages", None)
    facts.setdefault("photo", None)
    facts.setdefault("tables", None)
    facts["file_type"] = name.rsplit(".", 1)[-1] if "." in name else "unknown"
    facts["email_unprofessional"] = email_unprofessional(raw_text)
    return facts


_LINES = {
    "en": {
        "pages": ("Length: {v} page(s).", "Length in pages: not measured, do not report on length."),
        "photo": ("Photo: a portrait photo is included.", "Photo: no photo is included.",
                  "Photo: not measured, do not report on a photo."),
        "tables": ("Tables: the file uses layout tables.", "Tables: the file uses no tables.",
                   "Tables and columns: not measured, do not report on layout."),
        "email": ("Email address: present, and it looks unprofessional (nickname or joke words).",
                  "Email address: present and looks professional.", "Email address: none found in the file."),
    },
    "de": {
        "pages": ("Länge: {v} Seite(n).", "Seitenzahl: nicht gemessen, nicht zur Länge äußern."),
        "photo": ("Lichtbild: ein Porträtfoto ist enthalten.", "Lichtbild: es ist kein Foto enthalten.",
                  "Lichtbild: nicht gemessen, nicht zum Foto äußern."),
        "tables": ("Tabellen: die Datei nutzt Layout-Tabellen.", "Tabellen: die Datei nutzt keine Tabellen.",
                   "Tabellen und Spalten: nicht gemessen, nicht zum Layout äußern."),
        "email": ("E-Mail-Adresse: vorhanden, wirkt unprofessionell (Spitzname oder Scherzwörter).",
                  "E-Mail-Adresse: vorhanden und wirkt professionell.", "E-Mail-Adresse: keine in der Datei gefunden."),
    },
}


def render_facts(facts: dict, lang: str) -> str:
    t = _LINES.get(lang, _LINES["en"])
    lines = [t["pages"][0].format(v=facts["pages"]) if facts.get("pages") else t["pages"][1]]
    if facts.get("pages") and facts.get("file_type") == "docx":  # Word stores the count; other tools may not update it
        lines[0] += " (as saved in the file)" if lang != "de" else " (laut Datei)"
    photo = facts.get("photo")
    lines.append(t["photo"][0] if photo is True else t["photo"][1] if photo is False else t["photo"][2])
    tables = facts.get("tables")
    lines.append(t["tables"][0] if tables is True else t["tables"][1] if tables is False else t["tables"][2])
    email = facts.get("email_unprofessional")
    lines.append(t["email"][0] if email is True else t["email"][1] if email is False else t["email"][2])
    return "\n".join(f"- {line}" for line in lines)
