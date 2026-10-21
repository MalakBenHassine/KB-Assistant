"""The Prometheus endpoint: reachable inside the cluster, bounded labels, no identifiers."""

import httpx

from tests.e2e.helpers import auth_headers
from tests.support import Seeder


async def test_metrics_count_requests_by_template_and_answers_by_outcome(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    member = await seeder.member(tenant, "employee", (await seeder.department(tenant),))
    headers = await auth_headers(client, member)
    created = await client.post("/v1/conversations", json={}, headers=headers)
    conversation = created.json()["id"]
    await client.post(
        f"/v1/conversations/{conversation}/messages", json={"question": "Hello"}, headers=headers
    )

    response = await client.get("/metrics")
    assert response.status_code == 200
    text = response.text
    assert 'route="/v1/conversations/{conversation_id}/messages"' in text
    assert "kb_answers_total{" in text
    assert "kb_http_request_duration_seconds_bucket{" in text
    # No raw identifiers in any label: only templates.
    assert conversation not in text
    assert str(tenant) not in text


async def test_the_metrics_route_is_not_in_the_public_api_description(
    client: httpx.AsyncClient,
) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert "/metrics" not in schema["paths"]
