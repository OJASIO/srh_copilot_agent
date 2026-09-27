# Student Service agent

One plug, two tasks, two owners.

| Task id           | UI label                | Owner    | Code                                        |
|-------------------|-------------------------|----------|---------------------------------------------|
| cv_check          | CV Check                | teammate | tasks/cv_check.py + cv_check/ package       |
| scholarship_info  | Scholarship Information | Subodh   | tasks/scholarship_info.py                   |

Knowledge collections (see data/raw/student_service/):
- student_service/general      FAQ hotline, processes (Beurlaubung, Studiengangwechsel), summary
- student_service/scholarship  Stipendium facts (FAQ section 8) and public SRH financing pages

Ingest: `python scripts/ingest.py --agent student_service`

## CV Check

Logic ported from the teammate's standalone CV Optimizer Agent. Only the logic was
taken; the FastAPI app, Gemini SDK client and React/HTML frontend were dropped
because the platform already provides all three (API in main.py, LLM through
ServiceContainer, UI in frontend/streamlit_app.py).

Pipeline per upload (tasks/cv_check.py):

1. `cv_check/extractor.py`       PDF (PyMuPDF, pypdf fallback) or DOCX bytes to text. DOCX in reading order:
                                 paragraphs, tables (one line per row), text boxes, page header and footer
2. `cv_check/language.py`        EN/DE detection (stopword count); the task field `review_language` or
                                 `request.language` overrides it
3. `cv_check/document_facts.py`  measured from the file: pages, photo, layout tables, unprofessional email.
                                 The prompt answers photo, length and layout questions only from these facts
4. `core/guardrails.py`          sentences addressed to AI systems are removed from the CV and the job
                                 description and reported as a critical finding
5. `cv_check/anonymiser.py`      contact data, birth data, personal details (nationality, marital status,
                                 religion, ...), addresses anywhere, the name (skipping titles such as
                                 LEBENSLAUF, every part of it) masked BEFORE the text goes to the model
6. LLM tiered review             prompts/cv_check_en.md or cv_check_de.md (v3, CV and job description in
                                 delimited blocks), JSON schema, validated and clamped, one retry if cut off
7. `cv_check/ats_checker.py`     rule-based ATS score 0-100 on the raw text (local, no LLM)
8. `cv_check/report.py`          markdown in the review language; the dict lands in `AgentResponse.structured["findings"]`

Without a file, a question after a review in the same session is answered from that review
(`prompts/cv_followup.md`); the CV itself is never stored.

Findings shape:

    {"filename", "language", "language_label", "document_facts",
     "review": {"overall_score", "tier_1": [{title, detail, fix}], "tier_2": [{title, detail}],
                "tier_3": [str], "summary", "ready"},
     "ats": {"score", "issues": [{code, issue, severity, params}], "recommendation"},
     "privacy": {"masked": {category: count}, "total"},
     "integrity": {"ai_instructions_removed": [str]},
     "review_attempts", "review_valid"}

`ready` is derived (tier_1 empty), never taken from the model. The job description comes from the task
field; a long pasted message (200+ characters) is used when the field is empty.

## Scholarship Information

RAG over `student_service/scholarship` only. A follow-up question is searched together with the
previous one, and only this task's turns are used as history. The "Responsible contact" block is rule
based: each office has the programmes and topics it handles, matched on the question and the answer with
email addresses and links removed. Heading, fallback and disclaimer follow the language of the question.
