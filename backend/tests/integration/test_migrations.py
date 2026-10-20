from collections.abc import AsyncIterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine

from kb_assistant.infrastructure.db.tables import Base
from tests.conftest import Database, run_migrations

SCRATCH_DATABASE = "migration_roundtrip"


async def test_models_match_the_migrated_schema(owner_engine: AsyncEngine) -> None:
    """Hand-written migrations and `tables.py` must describe the same schema."""

    def diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return list(compare_metadata(context, Base.metadata))

    async with owner_engine.connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_every_tenant_table_has_row_level_security(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity FROM pg_class c"
                " JOIN information_schema.columns col"
                "   ON col.table_name = c.relname AND col.column_name = 'tenant_id'"
                " WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace"
            )
        )
        unprotected = [name for name, protected in rows if not protected]
    assert unprotected == []


@pytest.fixture
async def scratch_database(database: Database, owner_engine: AsyncEngine) -> AsyncIterator[str]:
    """A separate database, so downgrading does not disturb the shared one."""
    autocommit = owner_engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit.connect() as conn:
        await conn.execute(text(f"CREATE DATABASE {SCRATCH_DATABASE}"))
    yield database.owner_url.rsplit("/", 1)[0] + f"/{SCRATCH_DATABASE}"
    async with autocommit.connect() as conn:
        await conn.execute(text(f"DROP DATABASE {SCRATCH_DATABASE} WITH (FORCE)"))


async def test_downgrade_then_upgrade_round_trips(scratch_database: str) -> None:
    run_migrations(scratch_database, "upgrade", "head")
    run_migrations(scratch_database, "downgrade", "base")
    run_migrations(scratch_database, "upgrade", "head")
