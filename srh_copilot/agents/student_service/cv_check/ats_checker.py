"""Rule-based ATS (Applicant Tracking System) compatibility check.

Runs on the raw, non-anonymised text so the contact checks see real data.
Score starts at 100 and drops per issue by severity. No LLM involved, so it
is deterministic and free.
"""

from __future__ import annotations

import re

from core.pii import EMAIL, contains_phone

_DEDUCTION = {"high": 25, "medium": 15, "low": 5}
_PROBLEMATIC_CHARS = ["★", "●", "■", "►", "✓", "✗", "→", "©", "®"]
_REQUIRED_SECTIONS = {
    "education": ["EDUCATION", "AUSBILDUNG", "STUDIUM", "SCHULISCHE"],
    "experience": ["EXPERIENCE", "BERUFSERFAHRUNG", "ERFAHRUNG"],
    "skills": ["SKILLS", "KENNTNISSE", "KOMPETENZEN"],
}
# One entry per format class. English and German month names are one class: April,
# August, September and November are spelled the same in both languages, and a CV
# that writes "April 2025" everywhere is consistent. DD.MM.YYYY (birth date, the
# place-and-date line) is not an employment date format and is ignored.
_MONTHS = (r"Jan(?:uary|uar)?|Feb(?:ruary|ruar)?|M(?:ar(?:ch)?|ärz|aerz)|Apr(?:il)?|Ma[iy]|"
           r"Jun[ei]?|Jul[iy]?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|O[ck]t(?:ober)?|Nov(?:ember)?|De[cz](?:ember)?")
_DATE_FORMATS = [
    ("MM/YYYY", re.compile(r"(?<![\d/.])(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}(?!\d)")),
    ("MM.YYYY", re.compile(r"(?<![\d/.])(?:0?[1-9]|1[0-2])\.(?:19|20)\d{2}(?!\d)")),
    ("Month YYYY", re.compile(rf"\b(?:{_MONTHS})\.?\s+(?:19|20)\d{{2}}\b")),
]


def check_ats(cv_text: str) -> dict:
    issues: list[dict] = []
    issues += _check_date_consistency(cv_text)
    issues += _check_email_present(cv_text)
    issues += _check_phone_present(cv_text)
    issues += _check_section_headings(cv_text)
    issues += _check_special_characters(cv_text)
    score = _calculate_score(issues)
    return {"score": score, "issues": issues, "recommendation": _recommendation(score)}


def _issue(code: str, issue: str, severity: str, **params) -> dict:
    """`issue` is the English text (API and logs); `code` and `params` let the report translate it."""
    return {"code": code, "issue": issue, "severity": severity, "params": params}


def _check_date_consistency(cv_text: str) -> list[dict]:
    found = [label for label, rx in _DATE_FORMATS if rx.search(cv_text)]
    if len(found) > 1:
        formats = ", ".join(found)
        return [_issue("date_formats", f"Inconsistent date formats found: {formats}", "high", formats=formats)]
    return []


def _check_email_present(cv_text: str) -> list[dict]:
    return [] if EMAIL.search(cv_text) else [_issue("no_email", "No email address found", "high")]


def _check_phone_present(cv_text: str) -> list[dict]:
    return [] if contains_phone(cv_text) else [_issue("no_phone", "No phone number found", "high")]


def _check_section_headings(cv_text: str) -> list[dict]:
    upper = cv_text.upper()
    return [_issue("missing_section", f"Standard section missing: {section}", "medium", section=section)
            for section, keywords in _REQUIRED_SECTIONS.items() if not any(k in upper for k in keywords)]


def _check_special_characters(cv_text: str) -> list[dict]:
    found = [c for c in _PROBLEMATIC_CHARS if c in cv_text]
    if found:
        chars = " ".join(found)
        return [_issue("special_chars", f"Special characters found that may break ATS parsers: {chars}", "low",
                       chars=chars)]
    return []


def _calculate_score(issues: list[dict]) -> int:
    score = 100
    for issue in issues:
        score -= _DEDUCTION.get(issue.get("severity", "low"), 5)
    return max(0, score)


def _recommendation(score: int) -> str:
    if score >= 80:
        return "CV is ATS-friendly."
    if score >= 60:
        return "CV has minor ATS issues; review before submitting."
    return "CV has significant ATS issues; fix before submitting."
