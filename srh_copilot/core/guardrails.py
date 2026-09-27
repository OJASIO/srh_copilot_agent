"""Guardrails for Ethical AI (orchestration layer).

Hooks the orchestrator always calls:
    check_input(request)    may raise GuardrailViolation; masks personal data in the
                            message and the task inputs before any model sees them
    check_output(response)  masks secrets, blocks leaked system prompts, adds the disclaimer

Helpers for agents that pass documents to a model (a CV, a job description):
    find_injection(text)          instruction-like phrases addressed to an AI system
    neutralise_document(text)     same, but replaces those sentences and returns them

Chat messages are blocked when they contain an injection attempt. Documents are
not blocked (a student cannot always control what a template or a pasted job
advert contains); the offending sentences are removed and reported instead.

Plus `redact_pii()` (core/pii.py) used before anything is written to logs or the audit table.
Rules are deliberately simple and transparent; extend the lists, or replace a
rule with an LLM classifier, without touching the orchestrator.
"""

from __future__ import annotations

import re
import unicodedata

from core.pii import redact_pii  # noqa: F401  (re-exported: orchestrator, logging, tests)
from core.schemas import AgentRequest, AgentResponse


class GuardrailViolation(Exception):
    def __init__(self, rule: str, message: str, message_de: str = ""):
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.message_de = message_de or message


# Phrases that address the model rather than ask a question. English and German.
# They are matched on normalised text (NFKC, invisible characters removed, lower case,
# single spaces), so zero-width characters or odd spacing do not slip through.
_INJECTION_PATTERNS = [
    # ignore / forget previous instructions
    r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(previous|prior|above|earlier|preceding|all|your|"
    r"system|original|initial)\b[^.\n]{0,25}\b(instructions?|prompts?|guidelines|directions|directives|rules)\b",
    r"\b(ignorier\w*|vergiss|vergessen|missacht\w*|übergeh\w*|umgeh\w*)\b[^.\n]{0,40}\b(vorherigen?|bisherigen?|"
    r"obigen?|vorigen?|alle[nrs]?|deine[nrs]?|ursprünglichen?|system\w*)\b[^.\n]{0,25}\b(anweisung\w*|"
    r"instruktion\w*|regeln|vorgaben|befehle?|richtlinien|prompts?)\b",
    # role change
    r"\byou are now (an?|the|my|no longer|in)\b",
    r"\bdu bist (jetzt|ab jetzt|nun|ab sofort)\b",
    r"\bpretend (to be|you are|that you)\b",
    r"\btu so,? als (ob|wärst)\b",
    r"\b(act|behave|respond) as an? (unrestricted|unfiltered|uncensored|jailbroken)\b",
    r"\b(developer|dan|god|jailbreak)[ -]?mode\b",
    r"\bjailbreak\w*\b",
    r"\b(new|neue) (instructions?|anweisung(en)?)\s*:",
    # prompt disclosure
    r"\b(reveal|show|print|repeat|output|display|tell|give|leak|share|write|list|what)\b[^.\n]{0,30}"
    r"(\b(system|hidden|developer)[ _-]?(prompt|instructions?)\b|\byour (initial |original |hidden |secret )?"
    r"(prompt|instructions)\b)",
    r"\b(zeig\w*|verrat\w*|gib|nenn\w*|wiederhol\w*|ausgeben|schreib\w*|was)\b[^.\n]{0,30}"
    r"\b(system[ -]?prompt|systemanweisung\w*|versteckten? (prompt|anweisung\w*)|interne[nr]? anweisung\w*)",
]

# Extra patterns for documents: text aimed at AI screening of a CV. Imperative
# sentences only, so "hired the candidates" in an HR CV does not trigger.
_DOCUMENT_PATTERNS = [
    r"(?:^|[.:;!?]\s*|\b(?:please|bitte)\s+)(rate|score|rank|recommend|hire|select|shortlist|approve|accept)\s+"
    r"(this|the|me|my)\s+(cv|resume|candidate|applicant|application|profile)\b",
    r"\b(give|assign)\s+(this|the|my)\s+(cv|resume|candidate|applicant|application)\s+(a\s+)?"
    r"(score|rating|grade|mark)\b",
    r"\b(this|the)\s+(candidate|applicant)\s+(is|should be)\s+(the\s+)?(best|perfect|ideal|top|hired|selected|"
    r"shortlisted)\b",
    r"\bnote\s+(to|for)\s+(the\s+)?(ai|a\.i\.|llm|chatgpt|gpt|language model|screening (tool|system|software))\b",
    r"(?:^|[.:;!?]\s*|\bbitte\s+)(bewerte|empfiehl|empfehle|stelle ein)\s+(diesen|den|die|mich)\s+"
    r"(lebenslauf|bewerber\w*|kandidat\w*|bewerbung)\b",
    r"\b(ai|ki|llm|chatgpt|gpt|sprachmodell|language model)\b[^\n]{0,20}\b(reading|reviewing|screening|parsing|"
    r"der|die|das) (this|these|diesen|dieses|diese)\b",
]

_INVISIBLE = {"Cf", "Cc", "Co", "Cs"}


def normalise(text: str) -> str:
    """NFKC, drop invisible/control characters (zero-width joiners and friends),
    lower case, single spaces. Newlines are kept for sentence splitting."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch) not in _INVISIBLE)
    text = re.sub(r"[^\S\n]+", " ", text)
    return text.lower()


def strip_invisible(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch in "\n\t" or unicodedata.category(ch) not in _INVISIBLE)


_CHAT_RX = [re.compile(p, re.I | re.M) for p in _INJECTION_PATTERNS]
_DOC_RX = _CHAT_RX + [re.compile(p, re.I | re.M) for p in _DOCUMENT_PATTERNS]


def find_injection(text: str, *, document: bool = False) -> list[str]:
    """Matched phrases (normalised), empty when the text looks clean."""
    norm = normalise(text)
    return [m.group(0).strip() for rx in (_DOC_RX if document else _CHAT_RX) for m in rx.finditer(norm)]


_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n")
REMOVED_MARKER = "[REMOVED: text addressed to an AI system]"


def neutralise_document(text: str) -> tuple[str, list[str]]:
    """Replace every sentence that addresses an AI system by a marker.
    Returns (clean text, removed sentences shortened for display)."""
    removed: list[str] = []
    out_lines = []
    for line in strip_invisible(text).split("\n"):
        parts = _SENTENCE.split(line)
        if not any(find_injection(p, document=True) for p in parts):
            out_lines.append(line)
            continue
        kept = []
        for p in parts:
            if find_injection(p, document=True):
                removed.append(re.sub(r"\s+", " ", p).strip()[:120])
                if not kept or kept[-1] != REMOVED_MARKER:
                    kept.append(REMOVED_MARKER)
            else:
                kept.append(p)
        out_lines.append(" ".join(kept))
    return "\n".join(out_lines), removed


def strip_tags(text: str, tags: tuple[str, ...]) -> str:
    """Remove delimiter tags a document could use to break out of its block."""
    for tag in tags:
        text = re.sub(rf"</?\s*{re.escape(tag)}\s*>", "", text, flags=re.I)
    return text


_FOOTER = {
    "en": ("\n\nNote: this assistant gives general information about SRH University services. "
           "It is not a legal, medical or financial adviser and does not make binding decisions."),
    "de": ("\n\nHinweis: Dieser Assistent gibt allgemeine Informationen zu den Services der SRH University. "
           "Er ersetzt keine Rechts-, Medizin- oder Finanzberatung und trifft keine verbindlichen Entscheidungen."),
}
_EMPTY_ANSWER = {
    "en": "I could not produce an answer. Please rephrase or contact Student Service.",
    "de": "Ich konnte keine Antwort erstellen. Bitte formuliere die Frage neu oder wende dich an den Student Service.",
}
_PROMPT_LEAK = {
    "en": "I cannot share my internal instructions. Please ask your question about SRH University services.",
    "de": "Meine internen Anweisungen kann ich nicht teilen. Bitte stelle deine Frage zu den Services der SRH University.",
}


def _prompt_lines(texts: list[str]) -> set[str]:
    """Distinctive lines of the prompt templates (no placeholders, 50+ characters)."""
    lines = set()
    for t in texts:
        for line in t.splitlines():
            line = re.sub(r"\s+", " ", line).strip().lower()
            if len(line) >= 50 and "{" not in line:
                lines.add(line)
    return lines


class Guardrails:
    def __init__(self, max_message_chars: int = 4000, *, max_input_chars: int = 8000, mask_pii: bool = True,
                 allowed_email_domains: tuple[str, ...] | list[str] = (), prompt_texts: list[str] | None = None):
        self.max_message_chars = max_message_chars
        self.max_input_chars = max_input_chars
        self.mask_pii = mask_pii
        self.allowed_email_domains = tuple(allowed_email_domains)
        self._prompt_lines = _prompt_lines(prompt_texts or [])

    def check_input(self, request: AgentRequest) -> AgentRequest:
        text = strip_invisible(request.message).strip()
        if not text:
            raise GuardrailViolation("empty", "The message is empty.", "Die Nachricht ist leer.")
        if len(text) > self.max_message_chars:
            raise GuardrailViolation("too_long", f"Message exceeds {self.max_message_chars} characters.",
                                     f"Die Nachricht ist länger als {self.max_message_chars} Zeichen.")
        for key, value in request.inputs.items():
            if len(value) > self.max_input_chars:
                raise GuardrailViolation("too_long", f"Field {key!r} exceeds {self.max_input_chars} characters.",
                                         f"Das Feld {key!r} ist länger als {self.max_input_chars} Zeichen.")
        if find_injection(text):
            raise GuardrailViolation("prompt_injection", "The message contains disallowed instructions.",
                                     "Die Nachricht enthält unzulässige Anweisungen.")
        if self.mask_pii:
            # Data minimisation: no model needs a student's email, phone, IBAN or
            # matriculation number to answer a question. Stored history is masked too.
            text = redact_pii(text, self.allowed_email_domains)
            request.inputs = {k: redact_pii(strip_invisible(v), self.allowed_email_domains)
                              for k, v in request.inputs.items()}
        request.message = text
        return request

    def check_output(self, response: AgentResponse) -> AgentResponse:
        lang = response.structured.get("language") if response.structured.get("language") in _FOOTER else "en"
        content = response.content or ""
        if not content.strip():
            content = _EMPTY_ANSWER[lang]
        # never leak raw secrets that may have ended up in retrieved text
        content = re.sub(r"(?i)(api[_-]?key|password|passwort)\s*[:=]\s*\S+", r"\1: <redacted>", content)
        if self.leaks_prompt(content):
            content = _PROMPT_LEAK[lang]
            response.trace["blocked_output"] = "prompt_leak"
        response.content = content
        if response.structured.get("append_disclaimer", True):
            response.content += _FOOTER[lang]
        return response

    def leaks_prompt(self, content: str) -> bool:
        """True when the answer repeats two or more distinctive lines of a prompt template."""
        if not self._prompt_lines:
            return False
        norm = re.sub(r"\s+", " ", content).lower()
        return sum(1 for line in self._prompt_lines if line in norm) >= 2
