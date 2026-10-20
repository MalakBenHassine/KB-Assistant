"""Health probes, error format, request ids, security headers, route policies."""

from typing import Any

import httpx
from pydantic import SecretStr

from kb_assistant.api.app import create_app
from kb_assistant.api.deps import public_endpoint
from kb_assistant.bootstrap import Container, build_container
from kb_assistant.infrastructure.security.jwt import SigningKeys
from kb_assistant.settings import RedisSettings, StorageSettings
from tests.conftest import Database, make_settings
from tests.support import fast_hasher


async def test_liveness(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_checks_dependencies(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": True, "redis": True}


async def test_readiness_reports_unavailable_without_leaking_details(
    database: Database, signing_keys: SigningKeys, storage_settings: StorageSettings
) -> None:
    settings = make_settings(database, "redis://127.0.0.1:1/0", storage_settings)
    settings = settings.model_copy(
        update={
            "redis": RedisSettings(url=SecretStr("redis://127.0.0.1:1/0"), socket_timeout_s=0.2)
        }
    )
    container = build_container(settings, hasher=fast_hasher(), signing_keys=signing_keys)
    try:
        app = create_app(container=container)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            response = await c.get("/readyz")
    finally:
        await container.aclose()
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": True, "redis": False},
    }


async def test_unknown_route_is_a_problem_document(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_request_id_is_generated_or_propagated(client: httpx.AsyncClient) -> None:
    generated = await client.get("/healthz")
    assert len(generated.headers["x-request-id"]) == 32
    propagated = await client.get("/healthz", headers={"X-Request-ID": "upstream-123456"})
    assert propagated.headers["x-request-id"] == "upstream-123456"
    hostile = await client.get("/healthz", headers={"X-Request-ID": 'x" onload=alert(1)'})
    assert hostile.headers["x-request-id"] != 'x" onload=alert(1)'


async def test_security_headers(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


PUBLIC_OPERATIONS = {
    ("GET", "/.well-known/jwks.json"),
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("POST", "/v1/auth/login"),
    ("POST", "/v1/auth/logout"),
    ("POST", "/v1/auth/refresh"),
    ("POST", "/v1/auth/register"),
}


def _operations(container: Container) -> dict[tuple[str, str], dict[str, Any]]:
    """Every operation, read from the OpenAPI schema: a public, stable contract.

    Walking `app.routes` would depend on FastAPI internals (since 0.151 an
    included router is a lazy `_IncludedRouter`, and a naive walk finds nothing).
    """
    schema = create_app(container=container).openapi()
    return {
        (method.upper(), path): operation
        for path, item in schema["paths"].items()
        for method, operation in item.items()
    }


def test_every_operation_is_authenticated_or_explicitly_public(container: Container) -> None:
    """No endpoint is public by omission (SECURITY.md, T3).

    `current_principal` depends on the HTTP bearer scheme, so every protected
    operation carries a `security` requirement in the schema. Anything without
    one must be on the reviewed list above.
    """
    operations = _operations(container)
    assert len(operations) >= 14, "the walk found too few operations to be meaningful"
    unauthenticated = {key for key, op in operations.items() if not op.get("security")}
    assert unauthenticated == PUBLIC_OPERATIONS


def test_public_marker_matches_the_reviewed_list() -> None:
    """Every router that is public says so with the `public_endpoint` dependency."""
    from kb_assistant.api.routers import auth, health, jwks

    for router in (health.router, jwks.router, auth.public):
        assert any(d.dependency is public_endpoint for d in router.dependencies)
    assert not any(d.dependency is public_endpoint for d in auth.router.dependencies)
