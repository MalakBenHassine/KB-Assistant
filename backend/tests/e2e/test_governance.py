"""Feedback, limits, budgets, the audit log and analytics through the HTTP API."""

from typing import Any

import httpx

from kb_assistant.bootstrap import WorkerContainer
from kb_assistant.domain.ids import DepartmentId
from tests.e2e.helpers import assert_problem, auth_headers
from tests.e2e.test_chat import new_conversation, setup
from tests.fakes import RecordingJobQueue
from tests.support import Seeder

UNANSWERED = "What is the parking policy at the office?"


async def ask(
    client: httpx.AsyncClient, headers: dict[str, str], conversation: str, question: str
) -> httpx.Response:
    return await client.post(
        f"/v1/conversations/{conversation}/messages",
        json={"question": question},
        headers=headers,
    )


async def answer_id(client: httpx.AsyncClient, headers: dict[str, str], conversation: str) -> str:
    response = await client.get(f"/v1/conversations/{conversation}/messages", headers=headers)
    answers = [m for m in response.json()["items"] if m["role"] == "assistant"]
    return str(answers[-1]["id"])


# ----------------------------------------------------------------------------- feedback


async def test_feedback_is_one_rating_per_person_and_answer(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    hr_employee, engineer, _, _ = await setup(client, seeder, jobs, worker)
    headers = await auth_headers(client, hr_employee)
    conversation = await new_conversation(client, headers)
    assert (
        await ask(client, headers, conversation, "Who is eligible for parental leave?")
    ).is_success
    message = await answer_id(client, headers, conversation)

    first = await client.put(
        f"/v1/messages/{message}/feedback",
        json={"rating": -1, "reason": "incomplete", "comment": "Missing the duration."},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    second = await client.put(
        f"/v1/messages/{message}/feedback", json={"rating": 1}, headers=headers
    )
    assert second.json()["id"] == first.json()["id"]
    assert (second.json()["rating"], second.json()["reason"]) == (1, None)

    # Someone else's answer is a 404, never a 403 (no existence oracle).
    other = await auth_headers(client, engineer)
    assert_problem(
        await client.put(f"/v1/messages/{message}/feedback", json={"rating": 1}, headers=other),
        404,
    )
    assert_problem(
        await client.put(f"/v1/messages/{message}/feedback", json={"rating": 5}, headers=headers),
        422,
    )


# ----------------------------------------------------------------------------- limits


async def test_a_burst_of_questions_gets_429_with_retry_after(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    member = await seeder.member(tenant, "employee", (await seeder.department(tenant),))
    headers = await auth_headers(client, member)
    conversation = await new_conversation(client, headers)
    statuses = [(await ask(client, headers, conversation, "Hello")).status_code for _ in range(6)]
    assert statuses[:5] == [200] * 5
    limited = await ask(client, headers, conversation, "Hello")
    assert_problem(limited, 429, "rate_limited")
    assert int(limited.headers["retry-after"]) >= 1


async def test_a_spent_budget_stops_questions_until_next_month(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    admin = await auth_headers(client, await seeder.member(tenant, "tenant_admin"))
    employee = await auth_headers(
        client, await seeder.member(tenant, "employee", (await seeder.department(tenant),))
    )
    response = await client.put(
        "/v1/admin/settings", json={"monthly_token_budget": 0}, headers=admin
    )
    assert response.status_code == 200, response.text
    assert response.json()["monthly_token_budget"] == 0
    assert response.json()["budget_is_default"] is False

    conversation = await new_conversation(client, employee)
    blocked = await ask(client, employee, conversation, "How many days of leave?")
    assert_problem(blocked, 429, "budget_exhausted")
    assert int(blocked.headers["retry-after"]) > 0

    lifted = await client.put(
        "/v1/admin/settings", json={"monthly_token_budget": None}, headers=admin
    )
    assert lifted.json()["monthly_token_budget"] is None
    assert (await ask(client, employee, conversation, "How many days of leave?")).is_success
    usage = (await client.get("/v1/admin/settings", headers=admin)).json()["usage"]
    assert usage["questions"] == 1
    assert_problem(await client.get("/v1/admin/settings", headers=employee), 403)


# ----------------------------------------------------------------------------- audit log


async def test_the_audit_log_pages_verifies_and_exports(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    admin = await auth_headers(client, await seeder.member(tenant, "tenant_admin"))
    for budget in (100, 200, 300):
        await client.put("/v1/admin/settings", json={"monthly_token_budget": budget}, headers=admin)

    first = (await client.get("/v1/admin/audit-log?limit=2", headers=admin)).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    rest = (
        await client.get(
            "/v1/admin/audit-log", params={"cursor": first["next_cursor"]}, headers=admin
        )
    ).json()
    seen = [e["id"] for e in first["items"] + rest["items"]]
    assert len(seen) == len(set(seen))
    settings_events = (
        await client.get(
            "/v1/admin/audit-log", params={"action": "tenant.settings_updated"}, headers=admin
        )
    ).json()["items"]
    assert [e["details"]["monthly_token_budget"] for e in settings_events] == [300, 200, 100]

    verdict = (await client.get("/v1/admin/audit-log/verify", headers=admin)).json()
    assert verdict["valid"] is True
    assert verdict["checked"] >= 3  # at least the three settings changes
    assert len(verdict["head_hash"]) == 64

    export = await client.get("/v1/admin/audit-log.csv", headers=admin)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    lines = export.text.strip().splitlines()
    assert lines[0].startswith("seq,created_at,action")
    assert len(lines) - 1 == len(seen)


async def test_only_tenant_admins_read_the_audit_log(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    department = await seeder.department(tenant)
    for role, departments in (("employee", (department,)), ("dept_admin", (department,))):
        headers = await auth_headers(client, await seeder.member(tenant, role, departments))
        for path in (
            "/v1/admin/audit-log",
            "/v1/admin/audit-log/verify",
            "/v1/admin/audit-log.csv",
        ):
            assert_problem(await client.get(path, headers=headers), 403)


# ----------------------------------------------------------------------------- analytics


async def test_analytics_show_usage_and_knowledge_gaps_within_scope(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    hr_employee, engineer, hr, eng = await setup(client, seeder, jobs, worker)
    hr_headers = await auth_headers(client, hr_employee)
    conversation = await new_conversation(client, hr_headers)
    await ask(client, hr_headers, conversation, "Who is eligible for parental leave?")
    message = await answer_id(client, hr_headers, conversation)
    await client.put(f"/v1/messages/{message}/feedback", json={"rating": 1}, headers=hr_headers)
    for _ in range(2):
        await ask(client, hr_headers, conversation, UNANSWERED)
    eng_headers = await auth_headers(client, engineer)
    await ask(client, eng_headers, await new_conversation(client, eng_headers), "Hello")

    tenant = hr_employee.tenant_id
    admin = await auth_headers(client, await seeder.member(tenant, "tenant_admin"))
    report: dict[str, Any] = (await client.get("/v1/admin/analytics?days=7", headers=admin)).json()
    assert report["questions"] == 4
    assert report["answered"] == 1
    assert report["feedback_up"] == 1
    assert report["satisfaction"] == 1.0
    assert report["knowledge_gaps"][0]["question"] == UNANSWERED.lower()
    assert report["knowledge_gaps"][0]["times_asked"] == 2
    assert sum(d["questions"] for d in report["daily"]) == 4

    # A department admin sees the questions that searched their department only.
    eng_admin = await auth_headers(
        client, await seeder.member(tenant, "dept_admin", (DepartmentId(eng),))
    )
    scoped = (await client.get("/v1/admin/analytics", headers=eng_admin)).json()
    assert scoped["questions"] == 1
    assert scoped["knowledge_gaps"] == []
    assert hr  # the HR department exists; only its questions are hidden from eng_admin

    assert_problem(await client.get("/v1/admin/analytics", headers=hr_headers), 403)
    assert_problem(await client.get("/v1/admin/analytics?days=0", headers=admin), 422)


# ----------------------------------------------------------------------------- memory


async def test_a_conversation_is_titled_by_its_first_question(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    member = await seeder.member(tenant, "employee", (await seeder.department(tenant),))
    headers = await auth_headers(client, member)
    created = await client.post("/v1/conversations", json={}, headers=headers)
    conversation = str(created.json()["id"])
    await ask(client, headers, conversation, "How long is parental leave?")
    await ask(client, headers, conversation, "And for the second parent?")
    (listed,) = [
        c
        for c in (await client.get("/v1/conversations", headers=headers)).json()["items"]
        if c["id"] == conversation
    ]
    assert listed["title"] == "How long is parental leave?"
