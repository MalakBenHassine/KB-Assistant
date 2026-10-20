"""Document management through the HTTP API, with the worker run in-process.

Phase 2 acceptance: a sample corpus is ingested; re-uploading the same file is
a no-op; versioning archives the previous version.
"""

from typing import Any
from uuid import UUID

import httpx

from kb_assistant.bootstrap import WorkerContainer
from tests.e2e.helpers import assert_problem, auth_headers
from tests.fakes import RecordingJobQueue
from tests.sample_documents import LEAVE_POLICY_MD, ONCALL_RUNBOOK_HTML, handbook_pdf, make_docx
from tests.support import Member, Seeder

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def run_jobs(jobs: RecordingJobQueue, worker: WorkerContainer) -> int:
    """What the Celery worker would do with every job enqueued so far."""
    taken = jobs.take()
    for tenant_id, version_id in taken:
        await worker.ingest().execute(tenant_id, version_id)
    return len(taken)


async def upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    department: UUID,
    filename: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    title: str | None = None,
) -> httpx.Response:
    form: dict[str, Any] = {"department_id": str(department)}
    if title:
        form["title"] = title
    return await client.post(
        "/v1/documents", headers=headers, data=form, files={"file": (filename, data, content_type)}
    )


async def admin_of(seeder: Seeder, role: str = "tenant_admin") -> tuple[Member, UUID, UUID]:
    tenant = await seeder.tenant()
    hr, eng = await seeder.department(tenant, "hr"), await seeder.department(tenant, "engineering")
    member = await seeder.member(tenant, role, (hr,) if role == "dept_admin" else ())
    return member, hr, eng


async def test_the_sample_corpus_is_ingested(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    admin, hr, eng = await admin_of(seeder)
    headers = await auth_headers(client, admin)
    corpus = [
        (hr, "leave.md", LEAVE_POLICY_MD.encode(), "text/markdown"),
        (eng, "oncall.html", ONCALL_RUNBOOK_HTML.encode(), "text/html"),
        (hr, "expenses.docx", make_docx(), DOCX),
        (hr, "handbook.pdf", handbook_pdf(), "application/pdf"),
    ]
    jobs.take()
    for department, name, data, content_type in corpus:
        response = await upload(client, headers, department, name, data, content_type)
        assert response.status_code == 202, response.text
        assert response.json()["latest_version"]["status"] == "pending"
        assert response.headers["location"] == f"/v1/documents/{response.json()['id']}"
    assert await run_jobs(jobs, worker) == 4

    listing = (await client.get("/v1/documents", headers=headers)).json()["items"]
    assert sorted(d["latest_version"]["status"] for d in listing) == ["indexed"] * 4
    assert all(d["latest_version"]["chunk_count"] > 0 for d in listing)
    assert all(d["current_version_id"] == d["latest_version"]["id"] for d in listing)


async def test_uploading_the_same_bytes_again_is_a_no_op(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    admin, hr, _ = await admin_of(seeder)
    headers = await auth_headers(client, admin)
    jobs.take()
    first = await upload(client, headers, hr, "leave.md", LEAVE_POLICY_MD.encode())
    await run_jobs(jobs, worker)

    again = await upload(client, headers, hr, "renamed.md", LEAVE_POLICY_MD.encode())
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert jobs.jobs == []  # nothing new to process

    document_id = first.json()["id"]
    same_version = await client.post(
        f"/v1/documents/{document_id}/versions",
        headers=headers,
        files={"file": ("leave.md", LEAVE_POLICY_MD.encode(), "text/markdown")},
    )
    assert same_version.status_code == 200
    assert same_version.json()["latest_version"]["version"] == 1


async def test_a_new_version_replaces_the_current_one(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    admin, hr, _ = await admin_of(seeder)
    headers = await auth_headers(client, admin)
    jobs.take()
    created = await upload(client, headers, hr, "remote.md", b"# Remote work\n\nTwo days a week.")
    await run_jobs(jobs, worker)
    document_id = created.json()["id"]

    updated = await client.post(
        f"/v1/documents/{document_id}/versions",
        headers=headers,
        files={"file": ("remote.md", b"# Remote work\n\nThree days a week.", "text/markdown")},
    )
    assert updated.status_code == 202
    await run_jobs(jobs, worker)

    detail = (await client.get(f"/v1/documents/{document_id}", headers=headers)).json()
    v2, v1 = detail["versions"]
    assert (v2["version"], v2["status"], v2["archived_at"]) == (2, "indexed", None)
    assert (v1["version"], v1["status"]) == (1, "indexed")
    assert v1["archived_at"] is not None
    assert detail["current_version_id"] == v2["id"]


async def test_a_failed_version_reports_why_and_can_be_retried(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue, worker: WorkerContainer
) -> None:
    admin, hr, _ = await admin_of(seeder)
    headers = await auth_headers(client, admin)
    jobs.take()
    created = await upload(client, headers, hr, "scan.pdf", b"%PDF-1.7\nbroken")
    await run_jobs(jobs, worker)
    document_id = created.json()["id"]
    version = (await client.get(f"/v1/documents/{document_id}", headers=headers)).json()[
        "versions"
    ][0]
    assert version["status"] == "failed"
    assert version["error"] == {
        "code": "unreadable_document",
        "message": "The PDF is damaged or not a valid PDF.",
        "retryable": False,
    }

    retried = await client.post(
        f"/v1/documents/{document_id}/versions/{version['id']}/retry", headers=headers
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "pending"
    assert len(jobs.jobs) == 1


async def test_uploads_are_validated(client: httpx.AsyncClient, seeder: Seeder) -> None:
    admin, hr, _ = await admin_of(seeder)
    headers = await auth_headers(client, admin)
    spoofed = await upload(client, headers, hr, "invoice.pdf", ONCALL_RUNBOOK_HTML.encode())
    assert_problem(spoofed, 415, "unsupported_document")
    executable = await upload(client, headers, hr, "tool.exe", b"MZ\x90\x00")
    assert_problem(executable, 415)
    empty = await upload(client, headers, hr, "empty.md", b"")
    assert_problem(empty, 422)
    unknown_department = await upload(
        client, headers, UUID(int=1), "leave.md", LEAVE_POLICY_MD.encode()
    )
    assert_problem(unknown_department, 422)


async def test_the_size_limit_is_enforced_while_reading(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    admin, hr, _ = await admin_of(seeder)
    headers = await auth_headers(client, admin)
    too_big = b"# big\n" + b"x" * (20 * 1024 * 1024)
    assert_problem(await upload(client, headers, hr, "big.md", too_big), 413, "document_too_large")


async def test_path_tricks_in_the_filename_are_neutralised(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    admin, hr, _ = await admin_of(seeder)
    headers = await auth_headers(client, admin)
    response = await upload(client, headers, hr, "../../etc/passwd.md", b"# Notes\n\nHello.")
    assert response.json()["latest_version"]["filename"] == "passwd.md"


async def test_employees_cannot_manage_documents(client: httpx.AsyncClient, seeder: Seeder) -> None:
    tenant = await seeder.tenant()
    hr = await seeder.department(tenant)
    employee = await seeder.member(tenant, "employee", (hr,))
    headers = await auth_headers(client, employee)
    assert_problem(await upload(client, headers, hr, "a.md", b"# A\n\ntext"), 403)
    assert_problem(await client.get("/v1/documents", headers=headers), 403)


async def test_dept_admins_are_confined_to_their_departments(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue
) -> None:
    lead, hr, eng = await admin_of(seeder, "dept_admin")  # manages hr only
    lead_headers = await auth_headers(client, lead)
    assert_problem(await upload(client, lead_headers, eng, "a.md", b"# A\n\ntext"), 403)

    boss = await seeder.member(lead.tenant_id, "tenant_admin")
    boss_headers = await auth_headers(client, boss)
    eng_doc = (await upload(client, boss_headers, eng, "eng.md", b"# Eng\n\ntext")).json()
    hr_doc = (await upload(client, boss_headers, hr, "hr.md", b"# HR\n\ntext")).json()

    listing = (await client.get("/v1/documents", headers=lead_headers)).json()["items"]
    assert [d["id"] for d in listing] == [hr_doc["id"]]
    assert_problem(await client.get(f"/v1/documents/{eng_doc['id']}", headers=lead_headers), 404)
    jobs.take()


async def test_another_tenants_document_is_invisible(
    client: httpx.AsyncClient, seeder: Seeder, jobs: RecordingJobQueue
) -> None:
    acme_admin, acme_hr, _ = await admin_of(seeder)
    globex_admin, _, _ = await admin_of(seeder)
    acme_headers = await auth_headers(client, acme_admin)
    globex_headers = await auth_headers(client, globex_admin)
    document = (await upload(client, acme_headers, acme_hr, "acme.md", b"# Acme\n\nsecret")).json()
    jobs.take()

    assert_problem(await client.get(f"/v1/documents/{document['id']}", headers=globex_headers), 404)
    new_version = await client.post(
        f"/v1/documents/{document['id']}/versions",
        headers=globex_headers,
        files={"file": ("x.md", b"# overwrite\n\nattempt", "text/markdown")},
    )
    assert_problem(new_version, 404)
    # Uploading into Acme's department from Globex: the department does not exist there.
    assert_problem(await upload(client, globex_headers, acme_hr, "x.md", b"# X\n\ny"), 422)
