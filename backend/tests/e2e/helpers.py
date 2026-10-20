from typing import Any
from uuid import UUID

import httpx

from tests.support import DEFAULT_PASSWORD, Member


async def login(
    client: httpx.AsyncClient,
    email: str,
    password: str = DEFAULT_PASSWORD,
    tenant_id: UUID | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"email": email, "password": password}
    if tenant_id is not None:
        body["tenant_id"] = str(tenant_id)
    response = await client.post("/v1/auth/login", json=body)
    assert response.status_code == 200, response.text
    tokens: dict[str, Any] = response.json()
    return tokens


async def auth_headers(
    client: httpx.AsyncClient, member: Member, tenant_id: UUID | None = None
) -> dict[str, str]:
    tokens = await login(client, member.email, tenant_id=tenant_id or member.tenant_id)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def assert_problem(response: httpx.Response, status: int, code: str | None = None) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == status
    assert body["request_id"] == response.headers["x-request-id"]
    if code is not None:
        assert body["type"] == f"urn:kb-assistant:problem:{code}"
