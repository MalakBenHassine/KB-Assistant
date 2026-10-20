"""Structured JSON logs with redaction.

Application code logs events and identifiers (`request_id`, `tenant_id`,
lengths, durations), never question text, document text or credentials.
The redaction processor is a safety net for the day someone forgets: it masks
values under sensitive keys and anything that looks like an email, a bearer
token, a JWT, an IBAN or a phone number (SECURITY.md, T5).
"""

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "token",
        "access_token",
        "refresh_token",
        "invitation_token",
        "authorization",
        "cookie",
        "set-cookie",
        "secret",
        "api_key",
    }
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*"), "[JWT]"),
    (re.compile(r"(?i)\bbearer\s+[\w.~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b"), "[IBAN]"),
    (re.compile(r"(?<![\w-])\+?\d[\d\s().-]{8,}\d(?![\w-])"), "[PHONE]"),
)


def redact_text(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            k: REDACTED if str(k).lower() in _SENSITIVE_KEYS else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def redact_processor(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    redacted: MutableMapping[str, Any] = {}
    for key, value in event_dict.items():
        redacted[key] = REDACTED if key.lower() in _SENSITIVE_KEYS else _redact(value)
    return dict(redacted)


def configure_logging(level: str, *, json: bool = True) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        # Fields passed as `logging` extras (application code uses the stdlib logger).
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        redact_processor,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    # Route the standard library (uvicorn, SQLAlchemy, Celery) through the same pipeline.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn's access log duplicates ours, which carries the request id.
    logging.getLogger("uvicorn.access").disabled = True
