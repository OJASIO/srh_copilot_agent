"""Personal data detection shared by the whole platform.

One place for the patterns, so the chat guardrail, the log formatter, the audit
trail, the CV anonymiser and the ATS checker all agree on what an email address
or a phone number is.

Phone numbers are the hard part: a loose pattern also swallows year ranges
("2017 - 2021"), MM/YYYY ranges and log timestamps. A candidate therefore has to
start like a phone number (+, 00, 0 or a label such as "Tel.") and is rejected
when it looks like a date.
"""

from __future__ import annotations

import re

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b")
MATRIKEL = re.compile(r"(?<![\d.,])\b\d{7,8}\b(?![.,]\d)")

# Separators that occur inside phone numbers. No newline (a number never spans
# lines) and no dot for national numbers (01.01.1999 is a date, not a phone).
_INTL = r"(?:\+|\b00)\d[\d \t/()\-.]{5,}\d"
_NATIONAL = r"\(?\b0\d[\d \t/()\-]{5,}\d"
_PHONE_CANDIDATE = re.compile(rf"(?<![\w+]){_INTL}|(?<![\w+(]){_NATIONAL}")
_PHONE_LABELLED = re.compile(
    r"(?i)\b(?:tel(?:efon)?|phone|mobil(?:e|funk)?|handy|fon|cell(?:phone)?|fax|whatsapp|telephone)\b"
    r"\.?\s*(?:\([^)\n]{0,20}\))?\s*[:.]?\s*(\+?\(?\d[\d \t/()\-.]{5,}\d)"
)
_MONTH_YEAR = re.compile(r"(?<!\d)(?:0?[1-9]|1[0-2])\s?[/.]\s?(?:19|20)\d{2}(?!\d)")
_YEAR_RANGE = re.compile(r"(?<!\d)(?:19|20)\d{2}\s*[-\u2013/]\s*(?:19|20)\d{2}(?!\d)")
_FULL_DATE = re.compile(r"(?<!\d)\d{1,2}[./-]\d{1,2}[./-](?:19|20)?\d{2}(?!\d)")


def _looks_like_phone(candidate: str) -> bool:
    digits = re.sub(r"\D", "", candidate)
    if not 6 <= len(digits) <= 17:
        return False
    return not (_MONTH_YEAR.search(candidate) or _YEAR_RANGE.search(candidate) or _FULL_DATE.search(candidate))


def find_phones(text: str) -> list[tuple[int, int]]:
    """Character spans of phone numbers, sorted and merged."""
    spans: list[tuple[int, int]] = []
    for m in _PHONE_LABELLED.finditer(text):
        if _looks_like_phone(m.group(1)):
            spans.append(m.span(1))
    for m in _PHONE_CANDIDATE.finditer(text):
        if len(re.sub(r"\D", "", m.group(0))) >= 7 and _looks_like_phone(m.group(0)):
            spans.append(m.span())
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def contains_phone(text: str) -> bool:
    return bool(find_phones(text))


def replace_spans(text: str, spans: list[tuple[int, int]], token: str) -> tuple[str, int]:
    """Replace non-overlapping sorted spans by `token`; returns (text, count)."""
    out, last = [], 0
    for start, end in spans:
        out.append(text[last:start])
        out.append(token)
        last = end
    out.append(text[last:])
    return "".join(out), len(spans)


def mask_phones(text: str, token: str = "<phone>") -> tuple[str, int]:
    return replace_spans(text, find_phones(text), token)


def mask_emails(text: str, token: str = "<email>", allowed_domains: tuple[str, ...] | list[str] = ()) -> tuple[str, int]:
    """Mask email addresses except those whose domain is exactly one of `allowed_domains`
    (institutional mailboxes such as scholarship.hsg@srh.de are not personal data)."""
    allowed = {d.lower().strip() for d in allowed_domains if d.strip()}
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        if m.group(0).rsplit("@", 1)[1].lower() in allowed:
            return m.group(0)
        count += 1
        return token

    return EMAIL.sub(sub, text), count


def redact_pii(text: str, allowed_email_domains: tuple[str, ...] | list[str] = ()) -> str:
    """Mask IBANs, emails, phone numbers and matriculation numbers. Used for chat
    messages before they reach a model, for the audit trail and for log lines."""
    if not text:
        return text
    text = IBAN.sub("<iban>", text)
    text, _ = mask_emails(text, "<email>", allowed_email_domains)
    text, _ = mask_phones(text, "<phone>")
    return MATRIKEL.sub("<matrikel>", text)
