"""Asking questions through the HTTP API: SSE and JSON, persistence, authorization."""

import json
from typing import Any
from uuid import UUID

import httpx

from kb_assistant.bootstrap import WorkerContainer
from tests.e2e.helpers import assert_problem, auth_headers
from tests.e2e.test_documents import run_jobs, upload
from tests.fakes import RecordingJobQueue
from tests.sample_documents import LEAVE_POLICY_MD
from tests.support import Member, Seeder

SSE = {"Accept": "text/event-stream"}


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def setup(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> tuple[Member, Member, UUID, UUID]:
    """An HR employee, an engineer, and the leave policy indexed in HR."""
    tenant = await seeder.tenant()
    hr, eng = await seeder.department(tenant, "hr"), await seeder.department(tenant, "engineering")
    admin = await seeder.member(tenant, "tenant_admin")
    jobs.take()
    response = await upload(
        client, await auth_headers(client, admin), hr, "leave.md", LEAVE_POLICY_MD.encode()
    )
    assert response.status_code == 202
    await run_jobs(jobs, worker)
    return (
        await seeder.member(tenant, "employee", (hr,)),
        await seeder.member(tenant, "employee", (eng,)),
        hr,
        eng,
    )


async def new_conversation(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post("/v1/conversations", json={"title": "Leave"}, headers=headers)
    assert response.status_code == 201
    return str(response.json()["id"])


async def test_a_grounded_answer_streams_sentence_by_sentence(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    hr_employee, _, _, _ = await setup(client, seeder, jobs, worker)
    headers = await auth_headers(client, hr_employee)
    conversation = await new_conversation(client, headers)

    response = await client.post(
        f"/v1/conversations/{conversation}/messages",
        json={"question": "Who is eligible for parental leave?"},
        headers={**headers, **SSE},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "meta"
    assert kinds[-1] == "done"
    assert "sentence" in kinds

    done = events[-1][1]
    assert done["abstained"] is False
    assert done["grade"] == "good"
    citations = done["citations"]
    assert {c["document_title"] for c in citations} == {"leave.md"}
    assert ["Leave policy", "Parental leave", "Eligibility"] in [
        c["heading_path"] for c in citations
    ]
    assert "six months" in done["answer"]

    history = (
        await client.get(f"/v1/conversations/{conversation}/messages", headers=headers)
    ).json()["items"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["citations"][0]["document_title"] == "leave.md"


async def test_without_streaming_the_answer_comes_back_as_json(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    hr_employee, _, _, _ = await setup(client, seeder, jobs, worker)
    headers = await auth_headers(client, hr_employee)
    conversation = await new_conversation(client, headers)
    response = await client.post(
        f"/v1/conversations/{conversation}/messages",
        json={"question": "How many days of annual leave do employees get?"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "25 days" in body["answer"]
    assert body["citations"]


async def test_another_departments_document_is_never_used(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    _, engineer, _, _ = await setup(client, seeder, jobs, worker)
    headers = await auth_headers(client, engineer)
    conversation = await new_conversation(client, headers)
    body = (
        await client.post(
            f"/v1/conversations/{conversation}/messages",
            json={"question": "Who is eligible for parental leave?"},
            headers=headers,
        )
    ).json()
    assert body["abstained"] is True
    assert body["citations"] == []
    assert "don't know" in body["answer"] or "rephrase" in body["answer"]


async def test_searching_a_department_you_do_not_belong_to_is_a_403(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    _, engineer, hr, _ = await setup(client, seeder, jobs, worker)
    headers = await auth_headers(client, engineer)
    conversation = await new_conversation(client, headers)
    response = await client.post(
        f"/v1/conversations/{conversation}/messages",
        json={"question": "parental leave", "department_ids": [str(hr)]},
        headers={**headers, **SSE},
    )
    # A normal problem response, not a stream that dies after its first byte.
    assert_problem(response, 403, "forbidden")


async def test_conversations_are_private(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    hr_employee, engineer, _, _ = await setup(client, seeder, jobs, worker)
    owner_headers = await auth_headers(client, hr_employee)
    conversation = await new_conversation(client, owner_headers)

    intruder = await auth_headers(client, engineer)
    assert_problem(
        await client.get(f"/v1/conversations/{conversation}/messages", headers=intruder), 404
    )
    assert_problem(
        await client.post(
            f"/v1/conversations/{conversation}/messages",
            json={"question": "anything"},
            headers=intruder,
        ),
        404,
    )
    listing = (await client.get("/v1/conversations", headers=intruder)).json()["items"]
    assert conversation not in [c["id"] for c in listing]


async def test_greetings_get_a_reply_without_sources(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    hr_employee, _, _, _ = await setup(client, seeder, jobs, worker)
    headers = await auth_headers(client, hr_employee)
    conversation = await new_conversation(client, headers)
    body = (
        await client.post(
            f"/v1/conversations/{conversation}/messages",
            json={"question": "Hello!"},
            headers=headers,
        )
    ).json()
    assert body["abstained"] is False
    assert body["citations"] == []
    assert "documents" in body["answer"]


async def test_questions_are_validated(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    hr_employee, _, _, _ = await setup(client, seeder, jobs, worker)
    headers = await auth_headers(client, hr_employee)
    conversation = await new_conversation(client, headers)
    url = f"/v1/conversations/{conversation}/messages"
    assert_problem(await client.post(url, json={"question": ""}, headers=headers), 422)
    assert_problem(await client.post(url, json={"question": "x" * 2001}, headers=headers), 422)
    assert_problem(
        await client.post(url, json={"question": "hi", "tenant_id": "x"}, headers=headers), 422
    )
