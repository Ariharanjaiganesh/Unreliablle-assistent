"""
PII Redactor plugin — PRE_EXECUTION, priority=10, fail_closed=True.
Catches: email, phone, SSN, obfuscated (name[at]domain), credit card patterns.
Raises ValueError with PII_001 if patterns detected (fail-closed).
"""
from __future__ import annotations

import re

_PATTERNS = {
    "email":      re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    "obfuscated": re.compile(r"\b[A-Za-z0-9._%+\-]+\[at\][A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", re.IGNORECASE),
    "phone":      re.compile(r"\b(\+\d{1,3}\s?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}\b"),
    "ssn":        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card":re.compile(r"\b(?:\d[ \-]?){13,16}\b"),
}

_REDACT_MAP = {
    "email":       "[REDACTED_EMAIL]",
    "obfuscated":  "[REDACTED_EMAIL]",
    "phone":       "[REDACTED_PHONE]",
    "ssn":         "[REDACTED_SSN]",
    "credit_card": "[REDACTED_CC]",
}


def redact(text: str) -> str:
    """Replace PII patterns with placeholders."""
    for label, pattern in _PATTERNS.items():
        text = pattern.sub(_REDACT_MAP[label], text)
    return text


def detect_pii(text: str) -> list[str]:
    """Return list of PII type names found in text."""
    return [label for label, pattern in _PATTERNS.items() if pattern.search(text)]


def run_pre_execution(query: str, fail_closed: bool = True) -> str:
    """
    Run PII check/redaction.
    If fail_closed=True, raises ValueError on detection (blocks request).
    If fail_closed=False, returns redacted string.
    """
    detected = detect_pii(query)
    if detected:
        if fail_closed:
            raise ValueError(f"PII_001: Detected PII types {detected} — request rejected")
        return redact(query)
    return query
