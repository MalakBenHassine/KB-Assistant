"""Row-level security, tested as the real application role.

These tests talk SQL directly, below the repositories, to prove that the
database refuses cross-tenant access even when application code forgets to
filter (ARCHITECTURE §6.3, check 3).
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from kb_assistant.domain.ids import TenantId
from kb_assistant.infrastructure.db.engine import build_sessionmaker
from kb_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWork, UnboundTenantError
from tests.support import Seeder

SET = (
    "SELECT set_config('app.tenant_id', :tenant, true),"
    " set_config('app.cross_tenant_user_id', :cross, true),"
    " set_config('app.token_hash', :hash, true)"
)


@pytest.fixture
async def conn(app_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with app_engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()


async def _context(
    conn: AsyncConnection, tenant: Any = "", cross: Any = "", hash: str = ""
) -> None:
    await conn.execute(text(SET), {"tenant": str(tenant), "cross": str(cross), "hash": hash})


async def _count(conn: AsyncConnection, sql: str, **params: Any) -> int:
    return int((await conn.execute(text(sql), params)).scalar_one())


async def test_application_role_cannot_bypass_rls(conn: AsyncConnection) -> None:
    row = (
        await conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    ).one()
    assert tuple(row) == (False, False)


async def test_no_tenant_bound_means_no_rows(conn: AsyncConnection, seeder: Seeder) -> None:
    tenant = await seeder.tenant()
    await seeder.department(tenant)
    await seeder.member(tenant)
    assert await _count(conn, "SELECT count(*) FROM departments") == 0
    assert await _count(conn, "SELECT count(*) FROM memberships") == 0


async def test_bound_tenant_sees_only_its_rows(conn: AsyncConnection, seeder: Seeder) -> None:
    mine, theirs = await seeder.tenant(), await seeder.tenant()
    await seeder.department(mine)
    their_department = await seeder.department(theirs)
    await _context(conn, tenant=mine)

    assert await _count(conn, "SELECT count(*) FROM departments WHERE tenant_id = :t", t=mine) == 1
    # Even asking for the other tenant's row by primary key returns nothing.
    assert (
        await _count(conn, "SELECT count(*) FROM departments WHERE id = :d", d=their_department)
        == 0
    )


async def test_cannot_write_into_another_tenant(conn: AsyncConnection, seeder: Seeder) -> None:
    mine, theirs = await seeder.tenant(), await seeder.tenant()
    await _context(conn, tenant=mine)
    with pytest.raises(ProgrammingError, match="row-level security"):
        await conn.execute(
            text(
                "INSERT INTO departments (id, tenant_id, slug, name)"
                " VALUES (gen_random_uuid(), :t, 'x', 'X')"
            ),
            {"t": theirs},
        )


async def test_cross_tenant_identity_reveals_only_that_users_memberships(
    conn: AsyncConnection, seeder: Seeder
) -> None:
    acme, globex = await seeder.tenant(), await seeder.tenant()
    consultant, _ = await seeder.user()
    await seeder.membership(consultant, acme)
    await seeder.membership(consultant, globex)
    await seeder.member(globex)  # someone else, who must stay invisible

    await _context(conn, cross=consultant)
    assert await _count(conn, "SELECT count(*) FROM memberships") == 2
    assert (
        await _count(conn, "SELECT count(*) FROM memberships WHERE user_id <> :u", u=consultant)
        == 0
    )


async def test_token_hash_reveals_exactly_one_refresh_token(
    conn: AsyncConnection, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    member = await seeder.member(tenant)
    for token_hash in ("a" * 64, "b" * 64):
        await seeder.execute(
            "INSERT INTO refresh_tokens (id, family_id, user_id, tenant_id, membership_id,"
            " token_hash, issued_at, expires_at, family_expires_at)"
            " VALUES (gen_random_uuid(), gen_random_uuid(), :u, :t, :m, :h, now(),"
            " now() + interval '1 day', now() + interval '1 day')",
            u=member.user_id,
            t=tenant,
            m=member.membership_id,
            h=token_hash,
        )
    await _context(conn, hash="a" * 64)
    assert await _count(conn, "SELECT count(*) FROM refresh_tokens") == 1


async def test_audit_log_is_append_only(conn: AsyncConnection, seeder: Seeder) -> None:
    tenant = await seeder.tenant()
    await _context(conn, tenant=tenant)
    await conn.execute(
        text(
            "INSERT INTO audit_log (id, tenant_id, action) VALUES (gen_random_uuid(), :t, 'test')"
        ),
        {"t": tenant},
    )
    for statement in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
        nested = await conn.begin_nested()
        with pytest.raises(DBAPIError, match="permission denied"):
            await conn.execute(text(statement))
        await nested.rollback()


async def test_application_role_cannot_create_tenants(conn: AsyncConnection) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await conn.execute(
            text("INSERT INTO tenants (id, name, slug) VALUES (gen_random_uuid(), 'x', 'x')")
        )


async def test_context_does_not_leak_to_the_next_transaction(
    app_engine: AsyncEngine, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    await seeder.department(tenant)
    async with app_engine.connect() as connection:
        async with connection.begin():
            await _context(connection, tenant=tenant)
            assert await _count(connection, "SELECT count(*) FROM departments") == 1
        async with connection.begin():
            assert await _count(connection, "SELECT count(*) FROM departments") == 0


async def test_repositories_refuse_to_run_without_a_bound_tenant(app_engine: AsyncEngine) -> None:
    async with build_sessionmaker(app_engine)() as session:
        uow = SqlAlchemyUnitOfWork(session)
        with pytest.raises(UnboundTenantError):
            await uow.departments.list_all()


async def test_unit_of_work_reapplies_context_after_commit(
    app_engine: AsyncEngine, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    await seeder.department(tenant)
    async with build_sessionmaker(app_engine)() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await uow.bind(tenant_id=TenantId(tenant), user_id=None)
        await uow.commit()
        # New transaction: the after_begin hook must have restored the context.
        assert len(await uow.departments.list_all()) == 1
