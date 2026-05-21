"""Redaction of secret-shaped substrings before anything is logged.

Rule 7 forbids secrets reaching logs/traces. ``redact`` masks Anthropic keys,
GitHub PATs, Vault tokens, ``password`` assignments, bearer tokens, and any
long opaque token-shaped run. ``RedactingFilter`` applies it to every log
record so a careless ``logger.info(secret)`` is still safe.
"""

import logging
import re

PLACEHOLDER = "[REDACTED]"

# (pattern, replacement) — replacement keeps the key/prefix where one exists
# so redacted logs stay readable.
_RULES: list[tuple[re.Pattern[str], str]] = [
    # Anthropic API keys.
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]+"), PLACEHOLDER),
    # GitHub PATs (classic ghp_/gho_/... and fine-grained github_pat_).
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), PLACEHOLDER),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), PLACEHOLDER),
    # Vault service tokens.
    (re.compile(r"\bhvs\.[A-Za-z0-9_\-]{20,}\b"), PLACEHOLDER),
    # Authorization: Bearer <token>.
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1" + PLACEHOLDER),
    # password / passwd / pwd assignment in key=value or "key": "value" form.
    (
        re.compile(r'(?i)(pass(?:word|wd)?"?\s*[:=]\s*"?)([^"\s,}]+)'),
        r"\1" + PLACEHOLDER,
    ),
    # Generic long opaque token-shaped run (kept last; 40+ chars so 32-char
    # hex trace/request ids are not clobbered).
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), PLACEHOLDER),
]


def redact(text: str) -> str:
    """Return ``text`` with every secret-shaped substring masked."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that redacts the formatted message of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True
