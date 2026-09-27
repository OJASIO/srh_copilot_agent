"""CV Check task. Logic ported from the CV Optimizer Agent (teammate's project),
wired to the platform's injected services instead of its own FastAPI app,
Gemini SDK and React frontend.

Pipeline for one upload:
    attachment bytes -> extract_text -> detect_language (or the UI's choice)
                     -> document facts (pages, photo, tables, email style), measured from the file
                     -> neutralise text addressed to AI systems (CV and job description)
                     -> anonymise -> LLM tiered review (EN or DE prompt, JSON schema)
                     -> validate the review, one retry when it is cut off or not JSON
                     -> check_ats (on raw text, local, nothing leaves the server)
                     -> markdown report + structured findings

Without an attachment, a question after a review in the same session is a follow-up:
answered from the earlier review (never from the CV, which is not stored).

What the core guarantees this task receives:
    request.attachments  list of Attachment(filename, content_type, data=bytes)
    request.inputs       "job_description" and "review_language" (auto | en | de) from the task's fields
    request.message      free text; a long pasted text (200+ characters) is taken as the job description
                         when the field is empty
    request.language     "auto" | "en" | "de" (API parameter, used when review_language is not set)

What it returns: AgentResponse with `content` (markdown) and `structured`:
    {"findings": {"filename", "language", "language_label", "document_facts",
                  "review": {overall_score, tier_1, tier_2, tier_3, summary, ready},
                  "ats": {score, issues, recommendation},
                  "privacy": {"masked": {category: count}, "total": n},
                  "integrity": {"ai_instructions_removed": [..]},
                  "review_attempts": n, "review_valid": bool}}
"""

from __future__ import annotations

import logging

from agents.student_service.cv_check.anonymiser import anonymise
from agents.student_service.cv_check.ats_checker import check_ats
from agents.student_service.cv_check.document_facts import document_facts, render_facts
from agents.student_service.cv_check.extractor import ExtractionError, extract_text
from agents.student_service.cv_check.language import detect_language, language_label
from agents.student_service.cv_check.report import integrity_item, render_markdown
from agents.student_service.cv_check.schema import CV_REVIEW_SCHEMA
from core.agent_base import BaseTask
from core.guardrails import neutralise_document, strip_tags
from core.language import detect_language as detect_question_language
from core.language import today_text
from core.pii import redact_pii
from core.providers import finish_reason, parse_json
from core.schemas import AgentRequest, AgentResponse

log = logging.getLogger(__name__)

MAX_CV_CHARS = 9000  # characters sent to the LLM, as in the original
MAX_JOB_CHARS = 3000
REVIEW_MAX_TOKENS = 2500  # output budget for the JSON review
RETRY_MAX_TOKENS = 8000  # second attempt when the first answer was cut off
MIN_TEXT_CHARS = 100  # below this the file is almost certainly a scanned image
JOB_IN_MESSAGE_MIN_CHARS = 200  # a pasted job advert is long; "please check my CV" is not
_TAGS = ("cv_document", "job_description", "cv_review")
# Default message the /chat/upload route fills in when the user typed nothing.
_DEFAULT_MESSAGES = {"", "please review the attached document."}
_LIMITS = {"tier_1": 12, "tier_2": 12, "tier_3": 8}

_EMPTY_REVIEW = {
    "overall_score": 0, "tier_1": [], "tier_2": [], "tier_3": [],
    "summary": "The automated review could not be completed. Please try again.", "ready": False,
}


def _text(value, limit: int) -> str:
    """Strip and cap a model string; a cut is made at a word boundary and marked with "..."."""
    text = str(value).strip() if value is not None else ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut[limit // 2:]:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:") + " ..."


def validate_review(data: dict) -> dict | None:
    """Coerce the model's JSON into the expected shape, or None if it is not a review.
    Scores are clamped to 1-10, lists and strings are capped, malformed items dropped."""
    if not isinstance(data, dict) or not isinstance(data.get("tier_1"), list):
        return None
    try:
        score = int(round(float(data.get("overall_score", 0))))
    except (TypeError, ValueError):
        score = 0
    review = {"overall_score": min(10, max(1, score)) if score else 0}
    for tier, keys in (("tier_1", ("title", "detail", "fix")), ("tier_2", ("title", "detail"))):
        items = []
        for item in data.get(tier) or []:
            if isinstance(item, str):
                item = {"title": "", "detail": item}
            if isinstance(item, dict) and (item.get("title") or item.get("detail")):
                items.append({k: _text(item.get(k), 200 if k == "title" else 1500) for k in keys})
        review[tier] = items[:_LIMITS[tier]]
    review["tier_3"] = [_text(t, 500) for t in (data.get("tier_3") or []) if isinstance(t, str) and t.strip()]
    review["tier_3"] = review["tier_3"][:_LIMITS["tier_3"]]
    review["summary"] = _text(data.get("summary"), 1500)
    review["ready"] = not review["tier_1"]  # derived, not trusted from the model
    return review


class CvCheckTask(BaseTask):
    id = "cv_check"

    async def run(self, request: AgentRequest) -> AgentResponse:
        def reply(text: str, **structured) -> AgentResponse:
            return AgentResponse(request_id=request.request_id, agent_id=self.agent.id, task_id=self.id,
                                 content=text, model_text=text, structured={"append_disclaimer": False, **structured})

        if not request.attachments:
            previous = [m for m in request.history if m.role == "assistant" and m.task_id == self.id]
            if previous and request.message.strip().lower() not in _DEFAULT_MESSAGES:
                return await self._follow_up(request, previous[-1].content)
            return reply("Please upload your CV as PDF or DOCX. You can also paste the job description "
                         "you are applying for in the job description field so the review is targeted.")

        att = request.attachments[0]
        kind = att.filename.lower().rsplit(".", 1)[-1] if "." in att.filename else "unknown"
        try:
            cv_text = extract_text(att.filename, att.data)
        except ExtractionError:
            return reply("This file type is not supported. Please upload a PDF or DOCX file.")
        except Exception as exc:  # corrupt file, password protected, ...
            # never log the file name: it usually contains the student's name
            log.warning("cv extraction failed (%s, %d bytes): %s", kind, len(att.data), type(exc).__name__)
            return reply("I could not read this file. Please upload a text-based PDF or DOCX.")

        if len(cv_text.strip()) < MIN_TEXT_CHARS:
            return reply("No usable text could be extracted from this document. This usually happens when the "
                         "PDF is a scanned image or contains redaction boxes covering the text. "
                         "Please upload a digitally created PDF or a DOCX file instead.")

        chosen = request.inputs.get("review_language", "").strip().lower()
        if chosen not in ("en", "de"):
            chosen = request.language if request.language in ("en", "de") else ""
        lang = chosen or detect_language(cv_text)
        if lang == "unknown":
            lang = "en"

        job = request.inputs.get("job_description", "").strip()
        message = request.message.strip()
        if not job and message.lower() not in _DEFAULT_MESSAGES and len(message) >= JOB_IN_MESSAGE_MIN_CHARS:
            job = message

        facts = document_facts(att.filename, att.data, cv_text)
        review, meta = await self._review(cv_text, lang, job, att.filename, att.data, facts)
        ats = check_ats(cv_text)
        if meta["removed"]:
            review["tier_1"] = [integrity_item(lang, meta["removed"])] + review["tier_1"]
            review["ready"] = False

        findings = {"filename": att.filename, "language": lang, "language_label": language_label(lang),
                    "document_facts": facts, "review": review, "ats": ats,
                    "privacy": {"masked": meta["masked"], "total": sum(meta["masked"].values())},
                    "integrity": {"ai_instructions_removed": meta["removed"]},
                    "review_attempts": meta["attempts"], "review_valid": meta["valid"]}
        return reply(render_markdown(findings), findings=findings, language=lang)

    async def _review(self, cv_text: str, lang: str, job: str, filename: str, data: bytes,
                      facts: dict) -> tuple[dict, dict]:
        """Neutralise, anonymise, then ask the injected LLM for the tiered review."""
        s = self.services
        cv_clean, removed_cv = neutralise_document(cv_text)
        anon = anonymise(cv_clean, filename=filename, data=data)
        cv_for_model = strip_tags(anon.text, _TAGS)[:MAX_CV_CHARS]

        job_context, removed_job = "", []
        if job:
            job_clean, removed_job = neutralise_document(job)
            job_clean = strip_tags(redact_pii(job_clean), _TAGS)[:MAX_JOB_CHARS]  # recruiter contact data
            label = "ZIELSTELLE / STELLENBESCHREIBUNG" if lang == "de" else "TARGET ROLE / JOB DESCRIPTION"
            job_context = f"{label}:\n<job_description>\n{job_clean}\n</job_description>"

        prompt = s.prompts.get(f"cv_check_{lang}", agent_id=self.agent.id).render(
            today=today_text(lang), job_context=job_context, document_facts=render_facts(facts, lang),
            cv_text=cv_for_model)
        structured = s.settings.llm_provider != "mock"
        review, attempts = None, 0
        for max_tokens in (REVIEW_MAX_TOKENS, RETRY_MAX_TOKENS):
            attempts += 1
            # Schema-enforced where the provider supports it; a cut-off answer is invalid JSON,
            # so the second attempt gets a larger output budget.
            raw = await s.llm.chat([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=max_tokens,
                                   json_mode=structured, json_schema=CV_REVIEW_SCHEMA if structured else None)
            review = validate_review(parse_json(raw))
            if review is not None and finish_reason(raw) != "length":
                break
            log.warning("cv review attempt %d unusable (finish=%s)", attempts, finish_reason(raw))
        valid = review is not None
        if review is None:
            review = dict(_EMPTY_REVIEW)
        meta = {"masked": anon.counts, "removed": removed_cv + removed_job, "attempts": attempts, "valid": valid}
        return review, meta

    async def _follow_up(self, request: AgentRequest, review: str) -> AgentResponse:
        s = self.services
        system = s.prompts.get("cv_followup", agent_id=self.agent.id).render(review=strip_tags(review, _TAGS))
        turns = [m for m in request.history if m.task_id == self.id and m.content != review][-6:]
        messages = [{"role": "system", "content": system}]
        messages += [{"role": m.role, "content": m.content} for m in turns]
        messages.append({"role": "user", "content": request.message})
        answer = await s.llm.chat(messages)
        return AgentResponse(request_id=request.request_id, agent_id=self.agent.id, task_id=self.id,
                             content=answer, model_text=answer,
                             structured={"append_disclaimer": False, "follow_up": True,
                                         "language": detect_question_language(request.message)})
