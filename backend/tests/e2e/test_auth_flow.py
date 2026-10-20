"""Login, refresh rotation with reuse detection, logout, immediate revocation."""

import httpx

from tests.e2e.helpers import assert_problem, auth_headers, login
from tests.support import DEFAULT_PASSWORD, Seeder


async def test_login_then_profile(client: httpx.AsyncClient, seeder: Seeder) -> None:
    tenant = await seeder.tenant("Acme Corp")
    engineering = await seeder.department(tenant, "engineering")
    member = await seeder.member(tenant, "employee", (engineering,))

    tokens = await login(client, member.email)
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 600
    assert tokens["tenant_id"] == str(tenant)

    me = await client.get("/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == member.email
    assert body["role"] == "employee"
    assert body["tenant"]["name"] == "Acme Corp"
    assert [d["slug"] for d in body["departments"]] == ["engineering"]
    assert body["other_tenants"] == []


async def test_email_is_case_insensitive(client: httpx.AsyncClient, seeder: Seeder) -> None:
    member = await seeder.member(await seeder.tenant())
    await login(client, member.email.upper())


async def test_wrong_password_and_unknown_email_look_the_same(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    member = await seeder.member(await seeder.tenant())
    wrong = await client.post(
        "/v1/auth/login", json={"email": member.email, "password": "not the password"}
    )
    unknown = await client.post(
        "/v1/auth/login", json={"email": "nobody@example.com", "password": DEFAULT_PASSWORD}
    )
    assert_problem(wrong, 401, "invalid_credentials")
    assert_problem(unknown, 401, "invalid_credentials")
    assert wrong.json()["detail"] == unknown.json()["detail"]
    assert wrong.headers["www-authenticate"] == "Bearer"


async def test_inactive_user_and_inactive_membership_cannot_log_in(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    inactive_user, inactive_email = await seeder.user(active=False)
    await seeder.membership(inactive_user, tenant)
    user, email = await seeder.user()
    await seeder.membership(user, tenant, active=False)
    for address in (inactive_email, email):
        response = await client.post(
            "/v1/auth/login", json={"email": address, "password": DEFAULT_PASSWORD}
        )
        assert_problem(response, 401, "invalid_credentials")


async def test_refresh_rotates_and_reuse_revokes_the_family(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    member = await seeder.member(await seeder.tenant())
    first = await login(client, member.email)

    rotated = await client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]

    # The old token comes back: someone else holds this family.
    replay = await client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert_problem(replay, 401, "not_authenticated")

    # The legitimate client's newer token is revoked with the family.
    after = await client.post("/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert_problem(after, 401, "not_authenticated")


async def test_logout_revokes_the_refresh_token(client: httpx.AsyncClient, seeder: Seeder) -> None:
    member = await seeder.member(await seeder.tenant())
    tokens = await login(client, member.email)
    response = await client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 204
    again = await client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert_problem(again, 401)
    # Idempotent: logging out twice is not an error.
    response = await client.post("/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 204


async def test_unknown_refresh_token_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/auth/refresh", json={"refresh_token": "x" * 43})
    assert_problem(response, 401)


async def test_missing_or_invalid_bearer_is_rejected(client: httpx.AsyncClient) -> None:
    assert_problem(await client.get("/v1/me"), 401)
    response = await client.get("/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert_problem(response, 401)


async def test_bumping_token_version_revokes_access_immediately(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    member = await seeder.member(await seeder.tenant())
    headers = await auth_headers(client, member)
    assert (await client.get("/v1/me", headers=headers)).status_code == 200

    await seeder.execute(
        "UPDATE users SET token_version = token_version + 1 WHERE id = :u", u=member.user_id
    )
    assert_problem(await client.get("/v1/me", headers=headers), 401)


async def test_deactivated_membership_loses_access_immediately(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    member = await seeder.member(await seeder.tenant())
    headers = await auth_headers(client, member)
    await seeder.execute(
        "UPDATE memberships SET is_active = false WHERE id = :m", m=member.membership_id
    )
    assert_problem(await client.get("/v1/me", headers=headers), 401)


async def test_demotion_takes_effect_without_a_new_token(
    client: httpx.AsyncClient, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    admin = await seeder.member(tenant, "tenant_admin")
    headers = await auth_headers(client, admin)
    assert (await client.get("/v1/memberships", headers=headers)).status_code == 200

    await seeder.execute(
        "UPDATE memberships SET role = 'employee' WHERE id = :m", m=admin.membership_id
    )
    assert_problem(await client.get("/v1/memberships", headers=headers), 403, "forbidden")


async def test_login_rejects_unexpected_fields(client: httpx.AsyncClient, seeder: Seeder) -> None:
    member = await seeder.member(await seeder.tenant())
    response = await client.post(
        "/v1/auth/login",
        json={"email": member.email, "password": DEFAULT_PASSWORD, "role": "tenant_admin"},
    )
    assert_problem(response, 422, "validation_error")
    # Validation errors never echo the submitted values, passwords included.
    assert DEFAULT_PASSWORD not in response.text


async def test_jwks_is_published(client: httpx.AsyncClient) -> None:
    response = await client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    assert response.json()["keys"][0]["kty"] == "RSA"
