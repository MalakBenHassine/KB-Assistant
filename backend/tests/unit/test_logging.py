"""Redaction is a safety net: it must catch secrets without mangling identifiers."""

import json
import logging
from uuid import uuid4

import pytest
import structlog

from kb_assistant.infrastructure.observability.logging import (
    REDACTED,
    configure_logging,
    redact_processor,
    redact_text,
)


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("contact jane.doe@acme.example now", "jane.doe@acme.example"),
        ("Authorization: Bearer abc.def-ghi", "abc.def-ghi"),
        ("token eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl", "eyJhbGciOi.eyJzdWIiOi"),
        ("IBAN FR76 3000 6000 0112 3456 7890 189", "3000 6000 0112"),
        ("call +33 6 12 34 56 78", "12 34 56 78"),
    ],
)
def test_patterns_are_redacted(raw: str, leaked: str) -> None:
    assert leaked not in redact_text(raw)


@pytest.mark.parametrize(
    "safe",
    [str(uuid4()), "2026-10-20T12:00:00.123456Z", "GET /v1/memberships/{membership_id}", "42"],
)
def test_identifiers_and_timestamps_survive(safe: str) -> None:
    assert redact_text(safe) == safe


def test_sensitive_keys_are_redacted_at_any_depth() -> None:
    event = redact_processor(
        None,
        "info",
        {
            "event": "x",
            "password": "hunter2hunter2",
            "nested": {"refresh_token": "abc", "count": 3},
        },
    )
    assert event["password"] == REDACTED
    assert event["nested"] == {"refresh_token": REDACTED, "count": 3}


def test_configured_logger_emits_json_with_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=True)
    structlog.contextvars.bind_contextvars(request_id="req-12345678")
    try:
        structlog.get_logger("test").info("user.invited", email="bob@acme.example")
        logging.getLogger("stdlib").warning("plain %s", "message")
    finally:
        structlog.contextvars.clear_contextvars()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert lines[0]["event"] == "user.invited"
    assert lines[0]["request_id"] == "req-12345678"
    assert lines[0]["email"] == "[EMAIL]"
    assert lines[1]["event"] == "plain message"
