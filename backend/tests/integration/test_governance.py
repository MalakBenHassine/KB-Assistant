"""Rate limiting on real Redis; the audit chain and usage counters on real Postgres."""

import asyncio
from datetime import date
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from kb_assistant.application.governance import VerifyAuditLog
from kb_assistant.bootstrap import Container
from kb_assistant.domain.chat import TokenUsage
from kb_assistant.domain.governance import ChainVerdict, Limit
from kb_assistant.domain.ids import TenantId
from kb_assistant.domain.models import AuditEvent, Role
from kb_assistant.domain.principal import Principal
from kb_assistant.infrastructure.rate_limit import RedisRateLimiter
from tests.support import Seeder

# ----------------------------------------------------------------------------- rate limits


async def test_a_bucket_allows_its_burst_then_says_how_long_to_wait(redis_url: str) -> None:
    redis = Redis.from_url(redis_url)
    limiter = RedisRateLimiter(redis)
    limit = Limit(per_minute=6, burst=3)  # one token every 10 s
    key = f"test:{uuid4()}"
    decisions = [await limiter.hit(key, limit) for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert 9 < decisions[-1].retry_after_s <= 10
    assert (await limiter.hit(f"test:{uuid4()}", limit)).allowed  # keys are independent
    assert 0 < await redis.ttl("kb:rl:" + key) <= 31
    await redis.aclose()


async def test_concurrent_requests_never_overspend_the_bucket(redis_url: str) -> None:
    redis = Redis.from_url(redis_url)
    limiter = RedisRateLimiter(redis)
    key = f"test:{uuid4()}"
    decisions = await asyncio.gather(
        *(limiter.hit(key, Limit(per_minute=1, burst=5)) for _ in range(20))
    )
    assert sum(d.allowed for d in decisions) == 5
    await redis.aclose()


async def test_an_unreachable_redis_fails_open() -> None:
    redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    decision = await RedisRateLimiter(redis).hit("test:down", Limit(per_minute=1, burst=1))
    assert decision.allowed
    await redis.aclose()


# ----------------------------------------------------------------------------- audit chain


async def _admin(seeder: Seeder, tenant: TenantId) -> Principal:
    member = await seeder.member(tenant, "tenant_admin")
    return Principal(member.user_id, tenant, member.membership_id, Role.TENANT_ADMIN, frozenset())


async def _record(container: Container, tenant: TenantId, action: str) -> None:
    async with container.unit_of_work() as uow:
        await uow.bind(tenant_id=tenant, user_id=None)
        await uow.audit.record(
            AuditEvent(tenant_id=tenant, action=action, actor_user_id=None, details={"k": action})
        )
        await uow.commit()


async def _verify(container: Container, admin: Principal) -> ChainVerdict:
    async with container.unit_of_work() as uow:
        await uow.bind(tenant_id=admin.tenant_id, user_id=admin.user_id)
        return await VerifyAuditLog(uow).execute(admin)


async def test_concurrent_writers_extend_one_unbroken_chain(
    seeder: Seeder, container: Container, owner_engine: AsyncEngine
) -> None:
    tenant = await seeder.tenant()
    admin = await _admin(seeder, tenant)
    await asyncio.gather(*(_record(container, tenant, f"test.{n}") for n in range(12)))
    async with owner_engine.connect() as conn:
        seqs = (
            (
                await conn.execute(
                    text("SELECT seq FROM audit_log WHERE tenant_id = :t ORDER BY seq"),
                    {"t": tenant},
                )
            )
            .scalars()
            .all()
        )
    assert list(seqs) == list(range(1, 13))
    verdict = await _verify(container, admin)
    assert verdict.valid
    assert verdict.checked == 12


async def test_a_row_deleted_by_the_database_owner_is_detected(
    seeder: Seeder, container: Container, owner_engine: AsyncEngine
) -> None:
    """SECURITY.md T11: the application role cannot delete; the owner can, visibly."""
    tenant = await seeder.tenant()
    admin = await _admin(seeder, tenant)
    for n in range(5):
        await _record(container, tenant, f"test.{n}")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM audit_log WHERE tenant_id = :t AND seq = 3"), {"t": tenant}
        )
    verdict = await _verify(container, admin)
    assert not verdict.valid
    assert verdict.first_broken_seq == 4


async def test_a_row_edited_by_the_database_owner_is_detected(
    seeder: Seeder, container: Container, owner_engine: AsyncEngine
) -> None:
    tenant = await seeder.tenant()
    admin = await _admin(seeder, tenant)
    for n in range(3):
        await _record(container, tenant, f"test.{n}")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                'UPDATE audit_log SET details = \'{"k": "rewritten"}\''
                " WHERE tenant_id = :t AND seq = 2"
            ),
            {"t": tenant},
        )
    verdict = await _verify(container, admin)
    assert not verdict.valid
    assert verdict.first_broken_seq == 2
    assert verdict.reason == "the entry was modified"


# ----------------------------------------------------------------------------- usage


async def test_usage_increments_are_atomic(seeder: Seeder, container: Container) -> None:
    tenant = await seeder.tenant()
    month = date(2026, 10, 1)

    async def add() -> None:
        async with container.unit_of_work() as uow:
            await uow.bind(tenant_id=tenant, user_id=None)
            await uow.usage.add(month, questions=1, tokens=TokenUsage(10, 5))
            await uow.commit()

    await asyncio.gather(*(add() for _ in range(15)))
    async with container.unit_of_work() as uow:
        await uow.bind(tenant_id=tenant, user_id=None)
        usage = await uow.usage.get(month)
    assert (usage.questions, usage.input_tokens, usage.output_tokens) == (15, 150, 75)
    assert usage.tokens == 225
