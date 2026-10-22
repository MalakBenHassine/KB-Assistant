"""Scheduled maintenance, run by the operator role, never by the API (Helm CronJobs).

- `purge_expired_messages`: deletes questions and answers older than the
  retention period (feedback goes with them), and the conversations left
  empty, and records one audit entry per tenant with the counts.
- `audit_heads`: the newest entry of every tenant's audit chain, as JSON lines
  for the log pipeline to keep in write-once storage. Comparing a stored head
  with the table later detects the one tampering the chain cannot: removing
  the newest entries (ADR-0019).

Both connect as the schema owner (`KB_DATABASE__MIGRATION_URL`): the API role
has no DELETE on messages, by design.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from kb_assistant.domain.ids import TenantId
from kb_assistant.domain.models import AuditEvent
from kb_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWork

MIN_RETENTION_DAYS = 1


@dataclass(frozen=True, slots=True)
class Purged:
    tenant_id: TenantId
    messages: int


async def purge_expired_messages(
    engine: AsyncEngine, *, older_than_days: int, now: datetime
) -> list[Purged]:
    if older_than_days < MIN_RETENTION_DAYS:
        raise ValueError(f"retention must be at least {MIN_RETENTION_DAYS} day")
    cutoff = now - timedelta(days=older_than_days)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        tenants = (
            (await session.execute(text("SELECT id FROM tenants ORDER BY id"))).scalars().all()
        )
    purged: list[Purged] = []
    for tenant in tenants:
        # One transaction per tenant: the deletion and its audit entry commit together.
        async with sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.bind(tenant_id=TenantId(tenant), user_id=None)
            result = await session.execute(
                text("DELETE FROM messages WHERE tenant_id = :t AND created_at < :cutoff"),
                {"t": tenant, "cutoff": cutoff},
            )
            count = int(getattr(result, "rowcount", 0) or 0)
            # A conversation left with no message is noise in the sidebar.
            emptied = await session.execute(
                text(
                    "DELETE FROM conversations c WHERE c.tenant_id = :t AND c.updated_at < :cutoff"
                    " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id)"
                ),
                {"t": tenant, "cutoff": cutoff},
            )
            conversations = int(getattr(emptied, "rowcount", 0) or 0)
            if count or conversations:
                await uow.audit.record(
                    AuditEvent(
                        tenant_id=TenantId(tenant),
                        action="retention.messages_purged",
                        actor_user_id=None,
                        details={
                            "messages": count,
                            "conversations": conversations,
                            "older_than_days": older_than_days,
                        },
                    )
                )
            await uow.commit()
        purged.append(Purged(TenantId(tenant), count))
    return purged


async def audit_heads(engine: AsyncEngine, *, now: datetime) -> list[str]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT DISTINCT ON (tenant_id) tenant_id, seq, hash FROM audit_log"
                    " WHERE seq IS NOT NULL ORDER BY tenant_id, seq DESC"
                )
            )
        ).all()
    return [
        json.dumps(
            {
                "event": "audit.head",
                "tenant_id": str(r.tenant_id),
                "seq": r.seq,
                "hash": r.hash,
                "observed_at": now.isoformat(),
            },
            sort_keys=True,
        )
        for r in rows
    ]
