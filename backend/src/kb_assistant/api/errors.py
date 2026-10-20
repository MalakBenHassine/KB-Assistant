"""RFC 7807 problem responses.

Every error leaves the API as `application/problem+json` with a stable `type`,
the HTTP status, a human-readable `detail` and the `request_id` to quote to
support. Stack traces, SQL and other tenants' data never do.
"""

from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from kb_assistant.domain.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    InvalidInvitationError,
    NotAuthenticatedError,
    NotFoundError,
    PolicyViolationError,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"

_STATUS_BY_ERROR: tuple[tuple[type[DomainError], int], ...] = (
    (NotAuthenticatedError, 401),
    (ForbiddenError, 403),
    (NotFoundError, 404),
    (ConflictError, 409),
    (InvalidInvitationError, 400),
    (PolicyViolationError, 422),
)

log = structlog.get_logger(__name__)


def problem(
    request: Request,
    *,
    status: int,
    code: str,
    detail: str,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"urn:kb-assistant:problem:{code}",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "request_id": getattr(request.state, "request_id", None),
    }
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


def _status_for(error: DomainError) -> int:
    for cls, status in _STATUS_BY_ERROR:
        if isinstance(error, cls):
            return status
    return 400


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, DomainError):  # registered for this type only
        raise exc
    status = _status_for(exc)
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return problem(request, status=status, code=exc.code, detail=exc.detail, headers=headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # registered for this type only
        raise exc
    # Location, message and type only: echoing the input would echo passwords.
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return problem(
        request,
        status=422,
        code="validation_error",
        detail="The request is invalid.",
        extra={"errors": errors},
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # registered for this type only
        raise exc
    code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    detail = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return problem(request, status=exc.status_code, code=code, detail=detail)


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("http.unhandled_error", error_type=type(exc).__name__)
    return problem(
        request,
        status=500,
        code="internal_error",
        detail="An unexpected error occurred. Quote the request id when reporting it.",
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
