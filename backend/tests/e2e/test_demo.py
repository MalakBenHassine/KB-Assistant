"""Read-only demo mode: visitors can sign in and ask, and change nothing else."""

import httpx
import pytest

from kb_assistant.api.demo import DEMO_WRITES, allowed_in_demo
from kb_assistant.bootstrap import Container
from kb_assistant.settings import DemoSettings
from tests.e2e.helpers import assert_problem, auth_headers
from tests.support import Seeder


def test_reads_are_always_allowed_and_writes_only_from_the_allowlist() -> None:
    assert allowed_in_demo("GET", "/v1/documents")
    assert allowed_in_demo("POST", "/v1/conversations/{conversation_id}/messages")
    assert not allowed_in_demo("POST", "/v1/documents")
    assert not allowed_in_demo("PUT", "/v1/admin/settings")
    # A route the guard cannot name is refused, never allowed by default.
    assert not allowed_in_demo("POST", None)


def test_the_allowlist_names_only_routes_that_exist(container: Container) -> None:
    from kb_assistant.api.app import create_app

    paths = create_app(container=container).openapi()["paths"]
    for method, path in DEMO_WRITES:
        assert method.lower() in paths[path], f"{method} {path} is not a route"


@pytest.fixture
def demo(container: Container, monkeypatch: pytest.MonkeyPatch) -> None:
    read_only = container.settings.model_copy(update={"demo": DemoSettings(read_only=True)})
    monkeypatch.setattr(container, "settings", read_only)


@pytest.mark.usefixtures("demo")
async def test_a_visitor_can_ask_but_not_change_anything(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    hr = await seeder.department(tenant, "hr")
    admin = await auth_headers(client, await seeder.member(tenant, "tenant_admin"))

    conversation = await client.post("/v1/conversations", json={}, headers=admin)
    assert conversation.status_code == 201
    asked = await client.post(
        f"/v1/conversations/{conversation.json()['id']}/messages",
        json={"question": "Hello"},
        headers=admin,
    )
    assert asked.status_code == 200
    assert (await client.get("/v1/documents", headers=admin)).status_code == 200

    upload = await client.post(
        "/v1/documents",
        data={"department_id": str(hr)},
        files={"file": ("x.md", b"# X\n\nText.", "text/markdown")},
        headers=admin,
    )
    assert_problem(upload, 403, "demo_read_only")
    budget = await client.put("/v1/admin/settings", json={"monthly_token_budget": 0}, headers=admin)
    assert_problem(budget, 403, "demo_read_only")
    invite = await client.post(
        "/v1/invitations", json={"email": "x@example.com", "role": "employee"}, headers=admin
    )
    assert_problem(invite, 403, "demo_read_only")


async def test_writes_are_untouched_when_the_demo_is_off(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    admin = await auth_headers(client, await seeder.member(tenant, "tenant_admin"))
    response = await client.put(
        "/v1/admin/settings", json={"monthly_token_budget": 10}, headers=admin
    )
    assert response.status_code == 200
