"""Masks secrets and personal data before text is stored, notified or sent to an LLM.

Logs, error messages and data samples are untrusted and may contain tokens or
personal data. Everything leaving the platform adapters goes through here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (label, pattern). Order matters: specific token formats before generic ones.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("DATABRICKS_TOKEN", re.compile(r"\bdapi[A-Za-z0-9_\-]{8,}")),
    ("GITHUB_TOKEN", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("OPENROUTER_KEY", re.compile(r"\bsk-or-[A-Za-z0-9\-]{10,}")),
    ("API_KEY", re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}")),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{10,}")),
    ("SECRET_ASSIGNMENT", re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*[^\s,;'\"]+")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # Long digit runs (card-like or national-ID-like numbers), with optional separators.
    ("NUMBER", re.compile(r"\b(?:\d[ \-]?){12,18}\d\b")),
]


@dataclass(frozen=True)
class RedactionResult:
    text: str
    count: int


def redact(text: str | None) -> RedactionResult:
    if not text:
        return RedactionResult(text or "", 0)
    total = 0
    for label, pattern in _PATTERNS:
        if label == "SECRET_ASSIGNMENT":
            text, n = pattern.subn(lambda m: f"{m.group(1)}=[REDACTED:{label}]", text)
        else:
            text, n = pattern.subn(f"[REDACTED:{label}]", text)
        total += n
    return RedactionResult(text, total)


def redact_text(text: str | None) -> str:
    return redact(text).text
