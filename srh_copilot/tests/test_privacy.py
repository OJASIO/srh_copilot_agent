"""Personal data: shared detectors, CV anonymiser, chat masking, logs, sessions.
Offline; CVs are generated on the fly."""

from __future__ import annotations

import asyncio
import io
import logging

import pytest

from agents.student_service.cv_check.anonymiser import anonymise
from agents.student_service.cv_check.document_facts import document_facts
from agents.student_service.cv_check.extractor import extract_name_from_pdf, extract_text
from core.guardrails import Guardrails
from core.logging_config import RedactingFormatter
from core.pii import contains_phone, redact_pii
from core.schemas import AgentRequest, Message
from core.sessions import InMemorySessionStore

DE_CV_HARD = """Erika Musterfrau
Tel.: 0151/1234567 | Festnetz: 06221 / 123456 | erika.musterfrau@example.de
linkedin.com/in/erika-musterfrau | https://erika-musterfrau.de

BERUFSERFAHRUNG
10/2019 - 03/2021 Werkstudentin, Beispiel GmbH, Heidelberg
Software Engineering 2024, Monitoring 24/7, 50000 Datensätze verarbeitet, ISO 27001

KENNTNISSE
Adobe Photoshop, Illustrator, InDesign, Figma

PERSÖNLICHE DATEN
Anschrift: Hauptstraße 12, 69117 Heidelberg
Geburtsdatum: 01.01.1999 in Mumbai
Staatsangehörigkeit: indisch | Familienstand: verheiratet
Konfession: katholisch
Theodor-Heuss-Ring 5

Heidelberg, 27.09.2026
E. Musterfrau"""

PERSONAL = ["Musterfrau", "Erika", "0151/1234567", "123456", "erika.musterfrau@example.de", "Hauptstraße 12",
            "69117", "01.01.1999", "Mumbai", "indisch", "verheiratet", "katholisch", "Theodor-Heuss-Ring 5"]
MUST_KEEP = ["Adobe Photoshop", "Illustrator", "Beispiel GmbH", "10/2019 - 03/2021", "Software Engineering 2024",
             "Monitoring 24/7", "50000", "ISO 27001", "Staatsangehörigkeit", "Familienstand"]


def _pdf(lines: list[tuple[str, int]], image: tuple[int, int] | None = None) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    y = 50
    for text, size in lines:
        page.insert_text((50, y), text, fontsize=size)
        y += size + 8
    if image:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
        pix.set_rect(pix.irect, (180, 180, 180))
        page.insert_image(pymupdf.Rect(450, 40, 450 + image[0], 40 + image[1]), stream=pix.tobytes("png"))
    out = doc.tobytes()
    doc.close()
    return out


# shared detectors

@pytest.mark.parametrize("text", ["0151/1234567", "06221 / 123456", "(06221) 123456", "+49 (0) 6221 12345-67",
                                  "0049 151 12345678", "+49-151-1234567", "Mobil 176 12345678"])
def test_phone_formats_are_found(text):
    assert contains_phone(text)


@pytest.mark.parametrize("text", ["2017 - 2021", "09/2017 - 06/2021", "04/2025 - present", "01.01.1999",
                                  "2026-09-27 14:03:12,345", "ISO 27001", "50000 records"])
def test_dates_and_numbers_are_not_phones(text):
    assert not contains_phone(text)


def test_redact_pii_keeps_institutional_addresses():
    out = redact_pii("write to scholarship.hsg@srh.de, I am erika@example.de, 0176 1234567", ["srh.de"])
    assert "scholarship.hsg@srh.de" in out and "erika@example.de" not in out and "1234567" not in out


# CV anonymiser

def test_anonymiser_masks_hard_german_cv_and_keeps_content():
    result = anonymise(DE_CV_HARD, filename="cv.docx")
    for value in PERSONAL:
        assert value not in result.text, value
    for value in MUST_KEEP:
        assert value in result.text, value
    assert "E. [PER]" in result.text  # surname alone in the signature line
    assert result.counts["personal_detail"] == 3 and result.total >= 12


def test_title_is_not_taken_for_the_name_in_a_pdf():
    data = _pdf([("LEBENSLAUF", 28), ("Erika Musterfrau", 16), ("erika.musterfrau@example.de", 10),
                 ("BERUFSERFAHRUNG", 12), ("Werkstudentin bei Beispiel GmbH", 10)])
    assert extract_name_from_pdf(data) == "Erika Musterfrau"
    text = anonymise(extract_text("cv.pdf", data), filename="cv.pdf", data=data).text
    assert "Musterfrau" not in text and "LEBENSLAUF" in text


def test_role_line_is_not_taken_for_the_name():
    data = _pdf([("Data Scientist", 26), ("Max Mustermann", 18), ("max@example.com", 10)])
    assert extract_name_from_pdf(data) == "Max Mustermann"


def test_email_confirms_the_name_among_large_lines():
    data = _pdf([("Portfolio Showcase", 30), ("Max Mustermann", 18), ("max.mustermann@example.com", 10)])
    text = anonymise(extract_text("cv.pdf", data), filename="cv.pdf", data=data).text
    assert "Mustermann" not in text and "Portfolio Showcase" in text


def test_name_label_is_masked():
    text = anonymise("Lebenslauf\nName: Jonas Beispielmann\nSKILLS\nPython", filename="cv.docx").text
    assert "Beispielmann" not in text and "Jonas" not in text


def test_label_and_value_in_separate_lines_or_cells():
    text = anonymise("Kontakt\nStaatsangehörigkeit\ndeutsch\nFamilienstand | ledig\nSKILLS", filename="cv.docx").text
    assert "deutsch" not in text and "ledig" not in text


def test_docx_header_and_text_box_are_extracted_and_masked():
    from docx import Document

    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "Jonas Beispielmann | jonas@example.org | 0176 99887766"
    doc.add_paragraph("BERUFSERFAHRUNG")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "10/2020 - heute"
    table.cell(0, 1).text = "Werkstudent, Beispiel AG"
    buf = io.BytesIO()
    doc.save(buf)
    raw = extract_text("cv.docx", buf.getvalue())
    assert "Jonas Beispielmann" in raw and "10/2020 - heute | Werkstudent, Beispiel AG" in raw
    clean = anonymise(raw, filename="cv.docx").text
    assert "Beispielmann" not in clean and "99887766" not in clean and "Beispiel AG" in clean


def test_document_facts_detect_photo_and_pages():
    with_photo = document_facts("cv.pdf", _pdf([("Max Mustermann", 20)], image=(90, 120)), "")
    assert with_photo["photo"] is True and with_photo["pages"] == 1
    icon = document_facts("cv.pdf", _pdf([("Max Mustermann", 20)], image=(12, 12)), "partyboy99@web.de")
    assert icon["photo"] is False and icon["email_unprofessional"] is True


# chat, logs, sessions

def test_chat_message_and_inputs_are_masked_before_the_model():
    g = Guardrails(mask_pii=True, allowed_email_domains=["srh.de"])
    req = g.check_input(AgentRequest(session_id="s", user_id="u", message="I am erika@example.de, call 0176 1234567",
                                     inputs={"job_description": "Contact: hr@firma.de, 06221 123456"}))
    assert "erika@example.de" not in req.message and "1234567" not in req.message
    assert "hr@firma.de" not in req.inputs["job_description"]


def test_log_lines_keep_their_timestamp():
    fmt = RedactingFormatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "mail %s", ("erika@example.de",), None)
    line = fmt.format(record)
    assert "<phone>" not in line and "<email>" in line and line[:4].isdigit()


def test_sessions_are_private_to_their_user():
    store = InMemorySessionStore()
    asyncio.run(store.append("s1", "alice", Message(role="user", content="my question")))
    assert asyncio.run(store.history("s1", "mallory")) == []
    assert len(asyncio.run(store.history("s1", "alice"))) == 1


def test_cv_eval_cases_leak_nothing_and_keep_content(tmp_path):
    """The six evaluation CVs, end to end through the CV task: no planted personal
    value may reach the model, and normal content must survive (v5 original: 13 leaks)."""
    import json

    from config.settings import Settings
    from core.orchestrator import Orchestrator
    from core.registry import AgentRegistry
    from core.services import ServiceContainer
    from evaluation.cv_cases import CASES
    from evaluation.run_cv_eval import RecordingLLM, run_case
    from tests.test_cv_check import REVIEW, StubLLM

    s = Settings(llm_provider="mock", vector_backend="memory", app_env="test", api_key="k",
                 vector_index_path=tmp_path / "i.json", enabled_agents=[])
    services = ServiceContainer.build(s)
    recorder = RecordingLLM(StubLLM())
    services.llm = recorder
    reg = AgentRegistry(services)
    reg.load()
    orch = Orchestrator(reg, services)
    for case in CASES:
        r = asyncio.run(run_case(orch, recorder, case))
        assert r["pii_leaked"] == [], (case.id, r["pii_leaked"])
        assert r["over_masked"] == [], (case.id, r["over_masked"])
        assert r["language_ok"], case.id
        assert r["review_valid"] and json.dumps(REVIEW)  # stub answer parsed
        if case.injection:
            assert r["injection_removed"]


def test_birth_labels_stay_so_date_and_place_can_be_told_apart():
    text = anonymise("Tim Becker\nGeburtsdatum: 02.07.1998\nGeburtsort: Freiburg\nSKILLS\nBorn to code",
                     filename="cv.docx").text
    assert "Geburtsdatum: [DOB REMOVED]" in text and "Geburtsort: [BIRTHPLACE REMOVED]" in text
    assert "Freiburg" not in text and "Born to code" in text
