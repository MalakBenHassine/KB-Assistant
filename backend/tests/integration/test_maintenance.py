"""Retention and audit-head jobs, as the operator role, on real Postgres."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from kb_assistant.domain.ids import TenantId, new_id
from kb_assistant.maintenance import audit_heads, purge_expired_messages
from tests.support import Seeder

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


async def _conversation_with_messages(
    owner_engine: AsyncEngine, seeder: Seeder, ages_days: list[int]
) -> TenantId:
    # As in the application, the conversation was last updated by its newest message.
    last = NOW - timedelta(days=min(ages_days))
    tenant = await seeder.tenant()
    member = await seeder.member(tenant)
    conversation = new_id()
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, membership_id, title, created_at,"
                " updated_at) VALUES (:id, :t, :m, 'c', :now, :now)"
            ),
            {"id": conversation, "t": tenant, "m": member.membership_id, "now": last},
        )
        for age in ages_days:
            await conn.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, conversation_id, role, content,"
                    " retrieved_chunk_ids, cited_chunk_ids, citations, scores, thresholds,"
                    " token_usage, created_at) VALUES (:id, :t, :c, 'user', 'q', '{}', '{}',"
                    " '[]', '{}', '{}', '{}', :at)"
                ),
                {"id": new_id(), "t": tenant, "c": conversation, "at": NOW - timedelta(days=age)},
            )
    return tenant


async def test_old_messages_are_purged_and_the_purge_is_audited(
    owner_engine: AsyncEngine, seeder: Seeder
) -> None:
    tenant = await _conversation_with_messages(owner_engine, seeder, [400, 380, 10])
    purged = await purge_expired_messages(owner_engine, older_than_days=365, now=NOW)
    assert {p.tenant_id: p.messages for p in purged}[tenant] == 2
    async with owner_engine.connect() as conn:
        left = await conn.scalar(
            text("SELECT count(*) FROM messages WHERE tenant_id = :t"), {"t": tenant}
        )
        details = await conn.scalar(
            text(
                "SELECT details FROM audit_log WHERE tenant_id = :t"
                " AND action = 'retention.messages_purged'"
            ),
            {"t": tenant},
        )
    assert left == 1
    # The conversation keeps its recent message, so it is not deleted.
    assert details == {"messages": 2, "conversations": 0, "older_than_days": 365}


async def test_a_retention_below_one_day_is_refused(owner_engine: AsyncEngine) -> None:
    with pytest.raises(ValueError, match="at least"):
        await purge_expired_messages(owner_engine, older_than_days=0, now=NOW)


async def test_audit_heads_are_the_newest_entry_of_each_tenant(
    owner_engine: AsyncEngine, seeder: Seeder
) -> None:
    tenant = await _conversation_with_messages(owner_engine, seeder, [400, 401])
    await purge_expired_messages(owner_engine, older_than_days=365, now=NOW)
    heads = {h["tenant_id"]: h for h in map(json.loads, await audit_heads(owner_engine, now=NOW))}
    head = heads[str(tenant)]
    assert head["event"] == "audit.head"
    assert head["seq"] == 1
    assert len(head["hash"]) == 64


async def test_a_conversation_left_empty_is_purged_with_its_messages(
    owner_engine: AsyncEngine, seeder: Seeder
) -> None:
    """The nightly reset of the public demo: old conversations disappear entirely."""
    tenant = await _conversation_with_messages(owner_engine, seeder, [3, 2])
    purged = await purge_expired_messages(owner_engine, older_than_days=1, now=NOW)
    assert {p.tenant_id: p.messages for p in purged}[tenant] == 2
    async with owner_engine.connect() as conn:
        left = await conn.scalar(
            text("SELECT count(*) FROM conversations WHERE tenant_id = :t"), {"t": tenant}
        )
    assert left == 0
