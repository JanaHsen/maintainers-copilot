"""Rule 7: prove the redaction layer actually hides secrets."""

import logging

from app.infra.log_redaction import (
    PLACEHOLDER,
    PLACEHOLDER_EMAIL,
    PLACEHOLDER_JWT,
    RedactingFilter,
    redact,
    redact_for_persistence,
)

ANTHROPIC_KEY = "sk-ant-api03-AbCdEf123456789_-XyZ"
GITHUB_PAT = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" * 2
FINE_GRAINED_PAT = "github_pat_" + "1A2b3C4d5E6f7G8h9I0j" * 2
VAULT_TOKEN = "hvs.CAESIJ1234567890abcdefghijklmnop"
GENERIC_TOKEN = "Zm9vYmFyYmF6cXV4MDEyMzQ1Njc4OWFiY2RlZmdoaWprbG1ub3A"
# Fake JWT — header.payload.signature; the header literal "eyJ" is the
# base64url of "{\"" which begins virtually every real JWT.
FAKE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxIiwiaWF0IjoxNzE2MDAwMDAwfQ"
    ".dQw4w9WgXcQ-redactedSignaturePart"
)
EMAIL_ADDRESS = "alice@example.com"


def test_anthropic_key_redacted() -> None:
    assert ANTHROPIC_KEY not in redact(f"key={ANTHROPIC_KEY}")
    assert PLACEHOLDER in redact(f"key={ANTHROPIC_KEY}")


def test_github_pats_redacted() -> None:
    assert GITHUB_PAT not in redact(f"using {GITHUB_PAT} now")
    assert FINE_GRAINED_PAT not in redact(f"using {FINE_GRAINED_PAT} now")


def test_vault_token_redacted() -> None:
    assert VAULT_TOKEN not in redact(f"X-Vault-Token: {VAULT_TOKEN}")


def test_password_assignment_redacted() -> None:
    out = redact('{"password": "hunter2supersecret"}')
    assert "hunter2supersecret" not in out
    assert "password" in out  # key preserved, value masked


def test_bearer_token_redacted() -> None:
    out = redact("Authorization: Bearer abc.def.ghijklmnopqrstuvwx")
    assert "abc.def.ghijklmnopqrstuvwx" not in out


def test_generic_long_token_redacted() -> None:
    assert GENERIC_TOKEN not in redact(f"token {GENERIC_TOKEN}")


def test_benign_text_and_short_ids_pass_through() -> None:
    benign = "health check ok for postgres in 4ms"
    assert redact(benign) == benign
    # 32-char hex trace id must NOT be redacted (under the 40-char threshold).
    trace_id = "7f3e1a2b3c4d5e6f7a8b9c0d1e2f3a4b"
    assert redact(f"trace_id={trace_id}") == f"trace_id={trace_id}"


def test_benign_technical_phrase_pass_through() -> None:
    """A maintainer's message mentioning packages/errors must NOT be mangled."""
    phrase = "ConnectionError raised in the requests package on src/foo.py"
    assert redact(phrase) == phrase


def test_jwt_redacted_with_specific_placeholder() -> None:
    out = redact(f"set-cookie mc_session={FAKE_JWT}")
    assert FAKE_JWT not in out
    # The dedicated JWT placeholder makes redacted logs readable.
    assert PLACEHOLDER_JWT in out


def test_email_redacted_with_specific_placeholder() -> None:
    out = redact(f"contact {EMAIL_ADDRESS} for details")
    assert EMAIL_ADDRESS not in out
    assert PLACEHOLDER_EMAIL in out


def test_redact_for_persistence_strips_secret_and_email() -> None:
    """Service-boundary helper (research R6) for write_memory + STM append."""
    payload = (
        f"Use {ANTHROPIC_KEY} to log in. Reach me at {EMAIL_ADDRESS}."
    )
    out = redact_for_persistence(payload)
    assert ANTHROPIC_KEY not in out
    assert EMAIL_ADDRESS not in out
    assert PLACEHOLDER in out
    assert PLACEHOLDER_EMAIL in out


def test_redacting_filter_masks_log_record(caplog: object) -> None:
    logger = logging.getLogger("redaction-test")
    logger.addFilter(RedactingFilter())
    handler = logging.StreamHandler()
    records: list[str] = []
    handler.emit = lambda record: records.append(record.getMessage())  # type: ignore[method-assign]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("connecting with %s", ANTHROPIC_KEY)

    assert records
    assert ANTHROPIC_KEY not in records[0]
    assert PLACEHOLDER in records[0]
