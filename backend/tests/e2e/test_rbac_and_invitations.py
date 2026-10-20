"""Roles, departments and the invitation flow (register)."""

from typing import Any

import httpx

from tests.e2e.helpers import assert_problem, auth_headers, login
from tests.support import DEFAULT_PASSWORD, Seeder

NEW_PASSWORD = "a brand new passphrase"


async def _invite(
    client: httpx.AsyncClient, headers: dict[str, str], **body: Any
) -> httpx.Response:
    return await client.post("/v1/invitations", json=body, headers=headers)


async def test_full_invitation_flow(client: httpx.AsyncClient, seeder: Seeder) -> None:
    tenant = await seeder.tenant()
    hr = await seeder.department(tenant, "hr")
    admin = await seeder.member(tenant, "tenant_admin")
    headers = await auth_headers(client, admin)

    invited = await _invite(
        client, headers, email="New.Hire@Example.com", role="employee", department_ids=[str(hr)]
    )
    assert invited.status_code == 201, invited.text
    invitation = invited.json()
    assert invitation["email"] == "new.hire@example.com"

    registered = await client.post(
        "/v1/auth/register",
        json={"invitation_token": invitation["token"], "password": NEW_PASSWORD},
    )
    assert registered.status_code == 201, registered.text
    me = await client.get(
        "/v1/me", headers={"Authorization": f"Bearer {registered.json()['access_token']}"}
    )
    assert me.json()["role"] == "employee"
    assert [d["slug"] for d in me.json()["departments"]] == ["hr"]

    # The new account can log in, and the invitation cannot be used twice.
    await login(client, "new.hire@example.com", NEW_PASSWORD)
    reused = await client.post(
        "/v1/auth/register",
        json={"invitation_token": invitation["token"], "password": NEW_PASSWORD},
    )
    assert_problem(reused, 400, "invalid_invitation")


async def test_existing_account_must_prove_ownership_to_accept(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    acme, globex = await seeder.tenant(), await seeder.tenant()
    globex_admin = await seeder.member(globex, "tenant_admin")
    acme_member = await seeder.member(acme)
    headers = await auth_headers(client, globex_admin)
    token = (await _invite(client, headers, email=acme_member.email, role="employee")).json()[
        "token"
    ]

    hijack = await client.post(
        "/v1/auth/register", json={"invitation_token": token, "password": "attacker passphrase"}
    )
    assert_problem(hijack, 401, "invalid_credentials")

    accepted = await client.post(
        "/v1/auth/register", json={"invitation_token": token, "password": DEFAULT_PASSWORD}
    )
    assert accepted.status_code == 201
    assert accepted.json()["tenant_id"] == str(globex)


async def test_weak_password_is_refused_at_registration(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    admin = await seeder.member(tenant, "tenant_admin")
    headers = await auth_headers(client, admin)
    token = (await _invite(client, headers, email="weak@example.com", role="employee")).json()[
        "token"
    ]
    response = await client.post(
        "/v1/auth/register", json={"invitation_token": token, "password": "short"}
    )
    assert_problem(response, 422, "policy_violation")


async def test_employee_cannot_invite_or_create_departments(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    hr = await seeder.department(tenant)
    employee = await seeder.member(tenant, "employee", (hr,))
    headers = await auth_headers(client, employee)

    assert_problem(
        await _invite(
            client, headers, email="x@example.com", role="employee", department_ids=[str(hr)]
        ),
        403,
    )
    response = await client.post(
        "/v1/departments", json={"slug": "legal", "name": "Legal"}, headers=headers
    )
    assert_problem(response, 403)


async def test_dept_admin_cannot_escalate_or_reach_other_departments(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    engineering, hr = await seeder.department(tenant), await seeder.department(tenant)
    lead = await seeder.member(tenant, "dept_admin", (engineering,))
    headers = await auth_headers(client, lead)

    ok = await _invite(
        client, headers, email="dev@example.com", role="employee", department_ids=[str(engineering)]
    )
    assert ok.status_code == 201
    for body in (
        {"email": "boss@example.com", "role": "tenant_admin", "department_ids": [str(engineering)]},
        {"email": "peer@example.com", "role": "dept_admin", "department_ids": [str(engineering)]},
        {"email": "hr@example.com", "role": "employee", "department_ids": [str(hr)]},
    ):
        assert_problem(await _invite(client, headers, **body), 403)


async def test_cannot_invite_into_another_tenants_department(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    acme, globex = await seeder.tenant(), await seeder.tenant()
    foreign = await seeder.department(globex)
    admin = await seeder.member(acme, "tenant_admin")
    headers = await auth_headers(client, admin)
    response = await _invite(
        client, headers, email="x@example.com", role="employee", department_ids=[str(foreign)]
    )
    assert_problem(response, 422, "policy_violation")


async def test_inviting_an_existing_member_is_a_conflict(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    admin = await seeder.member(tenant, "tenant_admin")
    other = await seeder.member(tenant)
    headers = await auth_headers(client, admin)
    assert_problem(await _invite(client, headers, email=other.email, role="employee"), 409)


async def test_tenant_admin_manages_departments(client: httpx.AsyncClient, seeder: Seeder) -> None:
    tenant = await seeder.tenant()
    admin = await seeder.member(tenant, "tenant_admin")
    headers = await auth_headers(client, admin)
    created = await client.post(
        "/v1/departments", json={"slug": "legal", "name": "Legal"}, headers=headers
    )
    assert created.status_code == 201
    duplicate = await client.post(
        "/v1/departments", json={"slug": "legal", "name": "Legal again"}, headers=headers
    )
    assert_problem(duplicate, 409)
    invalid = await client.post(
        "/v1/departments", json={"slug": "Not A Slug", "name": "x"}, headers=headers
    )
    assert_problem(invalid, 422)


async def test_employee_sees_only_own_departments_and_own_membership(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    mine, other = await seeder.department(tenant, "mine"), await seeder.department(tenant, "other")
    employee = await seeder.member(tenant, "employee", (mine,))
    colleague = await seeder.member(tenant, "employee", (other,))
    headers = await auth_headers(client, employee)

    slugs = [
        d["slug"] for d in (await client.get("/v1/departments", headers=headers)).json()["items"]
    ]
    assert slugs == ["mine"]
    own = await client.get(f"/v1/memberships/{employee.membership_id}", headers=headers)
    assert own.status_code == 200
    assert_problem(
        await client.get(f"/v1/memberships/{colleague.membership_id}", headers=headers), 404
    )
