"""Request-scoped dependencies.

Every route depends on exactly one of `current_principal` or `public_endpoint`.
`tests/e2e/test_route_policies.py` walks the application and fails if a route
declares neither, so no endpoint is ever public by omission.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kb_assistant.application.auth import ResolvePrincipal
from kb_assistant.bootstrap import Container
from kb_assistant.domain.errors import NotAuthenticatedError
from kb_assistant.domain.ports import UnitOfWork
from kb_assistant.domain.principal import Principal

_bearer = HTTPBearer(auto_error=False, description="Access token issued by /v1/auth/login")


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_uow(container: ContainerDep) -> AsyncIterator[UnitOfWork]:
    async with container.unit_of_work() as uow:
        yield uow


UowDep = Annotated[UnitOfWork, Depends(get_uow)]


async def public_endpoint() -> None:
    """Marker: this route is deliberately reachable without authentication."""


async def current_principal(
    uow: UowDep,
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise NotAuthenticatedError("A bearer access token is required.")
    claims = container.tokens.verify(credentials.credentials)
    principal = await ResolvePrincipal(uow).execute(claims)
    structlog.contextvars.bind_contextvars(
        tenant_id=str(principal.tenant_id), user_id=str(principal.user_id)
    )
    return principal


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def client_ip(request: Request) -> str | None:
    """The caller's IP, trusting X-Forwarded-For only as far as configured proxies."""
    hops: int = request.app.state.container.settings.trusted_proxy_hops
    if hops > 0:
        forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        forwarded = [p for p in forwarded if p]
        if len(forwarded) >= hops:
            return forwarded[-hops]
    return request.client.host if request.client else None
