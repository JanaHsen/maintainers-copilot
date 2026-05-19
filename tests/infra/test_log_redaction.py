"""Rule 7: prove the redaction layer actually hides secrets."""

import logging

from app.infra.log_redaction import PLACEHOLDER, RedactingFilter, redact

ANTHROPIC_KEY = "sk-ant-api03-AbCdEf123456789_-XyZ"
GITHUB_PAT = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" * 2
FINE_GRAINED_PAT = "github_pat_" + "1A2b3C4d5E6f7G8h9I0j" * 2
VAULT_TOKEN = "hvs.CAESIJ1234567890abcdefghijklmnop"
GENERIC_TOKEN = "Zm9vYmFyYmF6cXV4MDEyMzQ1Njc4OWFiY2RlZmdoaWprbG1ub3A"


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
