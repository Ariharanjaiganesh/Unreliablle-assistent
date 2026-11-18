"""
Injection Shield plugin — PRE_EXECUTION, priority=20.
Detects direct jailbreaks, indirect prompt injections, and unicode homoglyphs.
Raises ValueError with INJECT_001 on detection.
"""
from __future__ import annotations

import re
import unicodedata

_DIRECT_JAILBREAK = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(dan|an?\s+ai\s+without)", re.IGNORECASE),
    re.compile(r"(system\s+prompt|initial\s+instructions?)\s*[:=]", re.IGNORECASE),
    re.compile(r"pretend\s+(you\s+are|to\s+be)\s+(evil|uncensored|jailbreak)", re.IGNORECASE),
    re.compile(r"<\s*(system|INST|SYS)\s*>", re.IGNORECASE),
    re.compile(r"###\s*(System|Human|Assistant)\s*:", re.IGNORECASE),
]

_INDIRECT_INJECTION = [
    re.compile(r"\[\[.*?(inject|execute|run|eval).*?\]\]", re.IGNORECASE | re.DOTALL),
    re.compile(r"<\s*script\s*>", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
]

# Homoglyph confusables — common unicode substitutions
_HOMOGLYPH_PAIRS = [
    ("а", "a"), ("е", "e"), ("о", "o"), ("р", "r"),
    ("с", "c"), ("ѕ", "s"), ("і", "i"), ("р", "p"),
]


def _normalize_homoglyphs(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    for src, dst in _HOMOGLYPH_PAIRS:
        nfkd = nfkd.replace(src, dst)
    return nfkd


def detect_injection(text: str) -> list[str]:
    findings: list[str] = []
    normalized = _normalize_homoglyphs(text)

    for pattern in _DIRECT_JAILBREAK:
        if pattern.search(normalized):
            findings.append("direct_jailbreak")
            break

    for pattern in _INDIRECT_INJECTION:
        if pattern.search(normalized):
            findings.append("indirect_injection")
            break

    if normalized != text:
        findings.append("homoglyph_detected")

    return findings


def run_pre_execution(query: str) -> str:
    """Raises ValueError on injection detection."""
    findings = detect_injection(query)
    if findings:
        raise ValueError(f"INJECT_001: Injection detected {findings} — request rejected")
    return query
