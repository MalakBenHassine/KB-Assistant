"""Alembic environment (async).

Migrations run as the schema owner, from `KB_DATABASE__MIGRATION_URL`.
The running API never has these privileges.
"""

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from kb_assistant.infrastructure.db.tables import Base

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("KB_DATABASE__MIGRATION_URL")
    if not url:
        raise RuntimeError("KB_DATABASE__MIGRATION_URL must be set to run migrations")
    return url


def _run(connection: object) -> None:
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
