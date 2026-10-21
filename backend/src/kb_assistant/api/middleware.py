"""Request context: request id, access log, security headers.

A pure ASGI middleware rather than `BaseHTTPMiddleware`, which buffers
responses and would break server-sent events in Phase 3.
"""

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
# Accept an upstream id (ingress, BFF) only if it is short and boring.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
}

log = structlog.get_logger("kb_assistant.http")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            route = scope.get("route")
            duration = time.perf_counter() - started
            # The route template keeps the label set bounded; unmatched paths share one value.
            template = getattr(route, "path", None)
            app = scope.get("app")
            container = getattr(getattr(app, "state", None), "container", None)
            if container is not None:
                container.telemetry.request_finished(
                    method=scope["method"], route=template, status=status, seconds=duration
                )
            log.info(
                "http.request",
                method=scope["method"],
                # The route template ("/v1/memberships/{membership_id}"), not the raw path.
                path=getattr(route, "path", scope["path"]),
                status=status,
                duration_ms=round(duration * 1000, 2),
            )
            structlog.contextvars.clear_contextvars()
