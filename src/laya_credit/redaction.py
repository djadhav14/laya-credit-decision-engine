"""PII masking (DPDP Act data-minimisation): applied BEFORE text reaches the model and
BEFORE anything is written to the audit trail. Patterns cover common Indian identifiers."""
from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")),
    ("AADHAAR", re.compile(r"\b[2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4}\b")),
    ("MOBILE", re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9][0-9]{9}(?!\d)")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("ACCOUNT", re.compile(r"\b(?:a/c|acct|account)\s*(?:no\.?|number)?\s*[:#]?\s*\d{9,18}\b", re.I)),
]

# Structured keys whose values are always masked, wherever they appear.
SENSITIVE_KEYS = {"pan", "aadhaar", "mobile", "email", "bank_account", "full_name", "address"}


def redact_text(text: str) -> str:
    for label, pattern in _PATTERNS:
        text = pattern.sub(f"[{label}]", text)
    return text


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
