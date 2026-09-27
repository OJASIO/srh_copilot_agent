"""Turns the review dict into the markdown the chat UI shows.

Structured data stays in AgentResponse.structured for the UI panels and for
evaluation; this module only produces the human-readable version, in the
language of the review (ATS findings included).
"""

from __future__ import annotations

_LABELS = {
    "en": {
        "score": "Overall score", "lang": "Detected language", "ats": "ATS compatibility",
        "tier_1": "Critical issues (fix before counselling)", "tier_2": "Recommended improvements",
        "tier_3": "Topics for your Career Service appointment", "ats_issues": "ATS findings",
        "ready": "No critical issues found. Your CV is ready for a counselling appointment.",
        "not_ready": "Please fix the critical issues above before booking a counselling appointment.",
        "fix": "Fix",
        "privacy": "Privacy: {n} personal detail(s) were masked before the review; the AI reviewer never saw them.",
        "failed": "The automated review could not be completed. The ATS check below is still valid; please try again.",
        "severity": {"high": "high", "medium": "medium", "low": "low"},
        "ats_text": {
            "date_formats": "Inconsistent date formats found: {formats}",
            "no_email": "No email address found",
            "no_phone": "No phone number found",
            "missing_section": "Standard section missing: {section}",
            "special_chars": "Special characters found that may break ATS parsers: {chars}",
        },
        "recommendation": {"ok": "CV is ATS-friendly.", "minor": "CV has minor ATS issues; review before submitting.",
                           "major": "CV has significant ATS issues; fix before submitting."},
        "integrity_title": "Text addressed to AI screening tools",
        "integrity_detail": "The CV contains instructions aimed at AI systems (for example: \"{example}\"). "
                            "They were ignored for this review. Recruiters treat such text as manipulation.",
        "integrity_fix": "Remove this text, including any hidden (white or tiny) text, from the CV.",
    },
    "de": {
        "score": "Gesamtbewertung", "lang": "Erkannte Sprache", "ats": "ATS-Kompatibilität",
        "tier_1": "Kritische Fehler (vor der Beratung beheben)", "tier_2": "Empfohlene Verbesserungen",
        "tier_3": "Themen für den Beratungstermin im Career Service", "ats_issues": "ATS-Befunde",
        "ready": "Keine kritischen Fehler gefunden. Der Lebenslauf ist bereit für einen Beratungstermin.",
        "not_ready": "Bitte zuerst die kritischen Fehler oben beheben, dann einen Beratungstermin buchen.",
        "fix": "Korrektur",
        "privacy": "Datenschutz: {n} persönliche Angabe(n) wurden vor der Prüfung maskiert; die KI hat sie nie gesehen.",
        "failed": "Die automatische Prüfung konnte nicht abgeschlossen werden. Der ATS-Check unten gilt trotzdem; "
                  "bitte versuche es erneut.",
        "severity": {"high": "hoch", "medium": "mittel", "low": "niedrig"},
        "ats_text": {
            "date_formats": "Uneinheitliche Datumsformate: {formats}",
            "no_email": "Keine E-Mail-Adresse gefunden",
            "no_phone": "Keine Telefonnummer gefunden",
            "missing_section": "Standardabschnitt fehlt: {section}",
            "special_chars": "Sonderzeichen, die ATS-Parser stören können: {chars}",
        },
        "recommendation": {"ok": "Der Lebenslauf ist ATS-freundlich.",
                           "minor": "Kleinere ATS-Probleme; vor dem Absenden prüfen.",
                           "major": "Deutliche ATS-Probleme; vor dem Absenden beheben."},
        "integrity_title": "Text für KI-Auswahlsysteme",
        "integrity_detail": "Der Lebenslauf enthält Anweisungen an KI-Systeme (zum Beispiel: \"{example}\"). "
                            "Sie wurden bei dieser Prüfung ignoriert. Personalabteilungen werten solchen Text als "
                            "Manipulation.",
        "integrity_fix": "Diesen Text entfernen, auch versteckten (weißen oder winzigen) Text.",
    },
}
_SECTION_DE = {"education": "Ausbildung", "experience": "Berufserfahrung", "skills": "Kenntnisse"}


def integrity_item(lang: str, removed: list[str]) -> dict:
    """Tier 1 finding added by code (not by the model) when AI-directed text was removed."""
    t = _LABELS.get(lang, _LABELS["en"])
    example = removed[0][:80] if removed else ""
    return {"title": t["integrity_title"], "detail": t["integrity_detail"].format(example=example),
            "fix": t["integrity_fix"]}


def _ats_line(issue: dict, t: dict, lang: str) -> str:
    params = dict(issue.get("params") or {})
    if lang == "de" and "section" in params:
        params["section"] = _SECTION_DE.get(params["section"], params["section"])
    template = t["ats_text"].get(issue.get("code", ""))
    text = template.format(**params) if template else issue.get("issue", "")
    severity = t["severity"].get(issue.get("severity", "low"), issue.get("severity", "low"))
    return f"- ({severity}) {text}"


def _recommendation(score, t: dict) -> str:
    if not isinstance(score, int):
        return ""
    return t["recommendation"]["ok" if score >= 80 else "minor" if score >= 60 else "major"]


def render_markdown(findings: dict) -> str:
    lang = findings.get("language") if findings.get("language") in _LABELS else "en"
    t = _LABELS[lang]
    review = findings.get("review", {})
    ats = findings.get("ats", {})
    out: list[str] = []

    out.append(f"**{t['score']}:** {review.get('overall_score', 'n/a')}/10  |  "
               f"**{t['ats']}:** {ats.get('score', 'n/a')}/100  |  "
               f"**{t['lang']}:** {findings.get('language_label', lang)}")
    if findings.get("review_valid") is False:
        out += ["", t["failed"]]
    elif review.get("summary"):
        out += ["", review["summary"]]

    tier_1 = review.get("tier_1") or []
    if tier_1:
        out += ["", f"### {t['tier_1']}"]
        for item in tier_1:
            title, detail, fix = item.get("title", ""), item.get("detail", ""), item.get("fix", "")
            line = f"- **{title}**: {detail}" if title else f"- {detail}"
            if fix:
                line += f" *{t['fix']}: {fix}*"
            out.append(line)

    tier_2 = review.get("tier_2") or []
    if tier_2:
        out += ["", f"### {t['tier_2']}"]
        for item in tier_2:
            title, detail = item.get("title", ""), item.get("detail", "")
            out.append(f"- **{title}**: {detail}" if title else f"- {detail}")

    tier_3 = review.get("tier_3") or []
    if tier_3:
        out += ["", f"### {t['tier_3']}"]
        out += [f"- {topic}" for topic in tier_3]

    if ats.get("issues"):
        out += ["", f"### {t['ats_issues']}"]
        out += [_ats_line(i, t, lang) for i in ats["issues"]]
    recommendation = _recommendation(ats.get("score"), t) or ats.get("recommendation", "")
    if recommendation:
        out += ["", recommendation]

    out += ["", t["ready"] if review.get("ready") else t["not_ready"]]
    masked = (findings.get("privacy") or {}).get("total", 0)
    if masked:
        out += ["", f"*{t['privacy'].format(n=masked)}*"]
    return "\n".join(out).strip()
