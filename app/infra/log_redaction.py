"""Redaction of secret-shaped and PII-shaped substrings.

Rule 7 forbids secrets reaching logs/traces. ``redact`` masks Anthropic keys,
GitHub PATs, Vault tokens, ``password`` assignments, bearer tokens, JWT-shaped
tokens, email addresses, and any long opaque token-shaped run.

Two surfaces:

  * :class:`RedactingFilter` applies ``redact`` at the log handler, so a
    careless ``logger.info(secret)`` is still safe.
  * :func:`redact_for_persistence` is the *service-boundary* helper called
    inside ``write_memory`` and ``short_term_memory_service.append`` before
    the content reaches Postgres or Redis (research R6 — handler-only
    redaction leaves a hole because the secret hits the DB unredacted).

Rules are conservative: a benign technical phrase like ``ConnectionError`` or
``the requests package`` is left untouched. Only strings that match a known
secret/PII shape are replaced.
"""

import logging
import re

PLACEHOLDER = "[REDACTED]"
PLACEHOLDER_JWT = "[REDACTED_JWT]"
PLACEHOLDER_EMAIL = "[REDACTED_EMAIL]"

# (pattern, replacement). Order matters — more specific rules first so the
# generic "long opaque token" rule does not clobber the explicit placeholders.
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
    # JWT-shaped token (header.payload.signature, base64url segments).
    # Matched before the generic long-opaque-token rule so the placeholder
    # stays readable ([REDACTED_JWT] not [REDACTED]).
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
        PLACEHOLDER_JWT,
    ),
    # RFC-5322-shaped email addresses. Conservative — the local part allows
    # letters/digits/dots/underscores/percents/plus/hyphen; the domain
    # requires at least one dot and a 2+ letter TLD.
    (
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        PLACEHOLDER_EMAIL,
    ),
    # Generic long opaque token-shaped run (kept last; 40+ chars so 32-char
    # hex trace/request ids are not clobbered).
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), PLACEHOLDER),
]


def redact(text: str) -> str:
    """Return ``text`` with every secret/PII-shaped substring masked."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_for_persistence(text: str) -> str:
    """Redact ``text`` before it is persisted to Postgres or Redis.

    Same ruleset as :func:`redact` today. Kept as a separate symbol so the
    call sites in ``write_memory`` and ``short_term_memory_service`` are
    self-documenting (Rule 9 — the name tells the reader why the call
    exists) and so the persistence path can diverge from log redaction
    later if the threat model changes (research R6).
    """
    return redact(text)


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
