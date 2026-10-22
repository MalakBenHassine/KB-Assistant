"""Read-only demo mode (deploy/oracle/README.md).

A public demo shares its accounts with every visitor, so nobody may change
what the next visitor sees: when `KB_DEMO__READ_ONLY` is on, a state-changing
request is refused unless it is one of the few a visitor needs to try the
product. Reads are never affected.

The rule is an allowlist of (method, route template): a route added later is
read-only in the demo until someone decides otherwise, never writable by
omission.
"""

from fastapi import Request

from kb_assistant.domain.errors import DemoReadOnlyError

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# What a visitor can do: sign in and out, switch organization, and ask.
DEMO_WRITES = frozenset(
    {
        ("POST", "/v1/auth/login"),
        ("POST", "/v1/auth/refresh"),
        ("POST", "/v1/auth/logout"),
        ("POST", "/v1/auth/switch-tenant"),
        ("POST", "/v1/conversations"),
        ("POST", "/v1/conversations/{conversation_id}/messages"),
        ("PUT", "/v1/messages/{message_id}/feedback"),
    }
)


def allowed_in_demo(method: str, route_template: str | None) -> bool:
    return method in SAFE_METHODS or (method, route_template or "") in DEMO_WRITES


async def demo_guard(request: Request) -> None:
    """An application-wide dependency; a no-op unless the demo is read-only."""
    if not request.app.state.container.settings.demo.read_only:
        return
    template = getattr(request.scope.get("route"), "path", None)
    if not allowed_in_demo(request.method, template):
        raise DemoReadOnlyError()
