"""Cross-tenant negative tests (Phase 1 acceptance criterion).

The hardest case is a person who legitimately belongs to two tenants: a valid
token for tenant A must still reveal nothing of tenant B (ADR-0012).
"""

import httpx

from kb_assistant.bootstrap import Container
from kb_assistant.domain.ids import TenantId, UserId
from tests.e2e.helpers import assert_problem, auth_headers, login
from tests.support import Member, Seeder


async def test_foreign_membership_is_a_404_not_a_403(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    acme, globex = await seeder.tenant(), await seeder.tenant()
    acme_admin = await seeder.member(acme, "tenant_admin")
    globex_member = await seeder.member(globex)

    headers = await auth_headers(client, acme_admin)
    response = await client.get(f"/v1/memberships/{globex_member.membership_id}", headers=headers)
    assert_problem(response, 404, "not_found")


async def test_lists_never_include_another_tenants_rows(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    acme, globex = await seeder.tenant(), await seeder.tenant()
    await seeder.department(acme, "engineering")
    await seeder.department(globex, "secret-projects")
    acme_admin = await seeder.member(acme, "tenant_admin")
    await seeder.member(globex)

    headers = await auth_headers(client, acme_admin)
    departments = (await client.get("/v1/departments", headers=headers)).json()["items"]
    memberships = (await client.get("/v1/memberships", headers=headers)).json()["items"]
    assert [d["slug"] for d in departments] == ["engineering"]
    assert [m["id"] for m in memberships] == [str(acme_admin.membership_id)]


async def test_member_of_both_tenants_is_scoped_to_the_token_tenant(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    acme, globex = await seeder.tenant("Acme"), await seeder.tenant("Globex")
    consultant, email = await seeder.user()
    acme_membership = await seeder.membership(consultant, acme, "tenant_admin")
    globex_membership = await seeder.membership(consultant, globex, "employee")
    await seeder.department(globex, "globex-only")

    tokens = await login(client, email, tenant_id=acme)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Admin in Acme, yet nothing of Globex is reachable with an Acme token...
    response = await client.get(f"/v1/memberships/{globex_membership}", headers=headers)
    assert_problem(response, 404)
    slugs = [
        d["slug"] for d in (await client.get("/v1/departments", headers=headers)).json()["items"]
    ]
    assert "globex-only" not in slugs

    # ...but the profile lists the other organization, so the UI can offer a switch.
    me = (await client.get("/v1/me", headers=headers)).json()
    assert me["membership_id"] == str(acme_membership)
    assert [t["name"] for t in me["other_tenants"]] == ["Globex"]


async def test_switch_tenant_issues_a_token_with_the_other_role(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    acme, globex = await seeder.tenant(), await seeder.tenant()
    consultant, email = await seeder.user()
    await seeder.membership(consultant, acme, "tenant_admin")
    await seeder.membership(consultant, globex, "employee")

    acme_tokens = await login(client, email, tenant_id=acme)
    response = await client.post(
        "/v1/auth/switch-tenant",
        json={"tenant_id": str(globex)},
        headers={"Authorization": f"Bearer {acme_tokens['access_token']}"},
    )
    assert response.status_code == 200
    globex_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = (await client.get("/v1/me", headers=globex_headers)).json()
    assert me["tenant"]["id"] == str(globex)
    assert me["role"] == "employee"
    # Tenant-admin rights from Acme do not travel.
    assert_problem(await client.get("/v1/memberships", headers=globex_headers), 403)


async def test_switching_to_a_tenant_without_membership_is_a_404(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    acme, globex = await seeder.tenant(), await seeder.tenant()
    member = await seeder.member(acme)
    headers = await auth_headers(client, member)
    response = await client.post(
        "/v1/auth/switch-tenant", json={"tenant_id": str(globex)}, headers=headers
    )
    assert_problem(response, 404)


async def test_login_into_a_tenant_without_membership_fails_like_a_bad_password(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    acme, globex = await seeder.tenant(), await seeder.tenant()
    member = await seeder.member(acme)
    response = await client.post(
        "/v1/auth/login",
        json={
            "email": member.email,
            "password": "correct horse battery staple",
            "tenant_id": str(globex),
        },
    )
    assert_problem(response, 401, "invalid_credentials")


async def test_validly_signed_token_for_a_foreign_tenant_is_rejected(
    client: httpx.AsyncClient, seeder: Seeder, container: Container
) -> None:
    """Even a token signed with our own key fails if the membership does not exist.

    This is what happens if a bug ever mints a token with the wrong `tid`: the
    per-request membership lookup (ADR-0005) stops it.
    """
    acme, globex = await seeder.tenant(), await seeder.tenant()
    member: Member = await seeder.member(acme)
    forged = container.tokens.issue(
        user_id=UserId(member.user_id), tenant_id=TenantId(globex), token_version=0
    )
    response = await client.get("/v1/me", headers={"Authorization": f"Bearer {forged}"})
    assert_problem(response, 401)
