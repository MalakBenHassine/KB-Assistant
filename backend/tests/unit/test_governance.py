"""Governance rules without I/O: the audit chain, budgets, limits, memory."""

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest

from kb_assistant.application.answering.memory import (
    DEFAULT_TITLE,
    MAX_TITLE_CHARS,
    MEMORY_TURNS,
    remember,
    title_from,
)
from kb_assistant.application.governance import ChatGuard, ChatLimits
from kb_assistant.domain.chat import TokenUsage
from kb_assistant.domain.errors import BudgetExhaustedError, RateLimitedError
from kb_assistant.domain.governance import (
    GENESIS_HASH,
    Analytics,
    AuditEntry,
    Limit,
    RateDecision,
    TenantSettings,
    Usage,
    entry_hash,
    month_of,
    next_month,
    verify_chain,
)
from kb_assistant.domain.ids import MembershipId, TenantId, UserId
from kb_assistant.domain.models import Role
from kb_assistant.domain.principal import Principal

TENANT = TenantId(uuid4())
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

# ----------------------------------------------------------------------------- audit chain


def chain(length: int) -> list[AuditEntry]:
    entries, prev = [], GENESIS_HASH
    for seq in range(1, length + 1):
        fields: dict[str, Any] = {
            "seq": seq,
            "tenant_id": TENANT,
            "actor_user_id": None,
            "action": f"test.{seq}",
            "target": None,
            "ip": "10.0.0.1",
            "details": {"n": seq, "nested": {"b": 2, "a": 1}},
            "created_at": NOW,
        }
        digest = entry_hash(prev_hash=prev, **fields)
        entries.append(AuditEntry(id=uuid4(), prev_hash=prev, hash=digest, **fields))
        prev = digest
    return entries


def test_an_intact_chain_verifies() -> None:
    verdict = verify_chain(chain(5))
    assert verdict.valid
    assert verdict.checked == 5
    assert verdict.next_seq == 6


def test_a_deleted_entry_is_found() -> None:
    entries = chain(5)
    del entries[2]
    verdict = verify_chain(entries)
    assert not verdict.valid
    assert verdict.first_broken_seq == 4
    assert verdict.reason == "a sequence number is missing"


def test_an_edited_entry_is_found() -> None:
    entries = chain(5)
    entries[1] = replace(entries[1], details={"n": 999})
    verdict = verify_chain(entries)
    assert not verdict.valid
    assert verdict.first_broken_seq == 2
    assert verdict.reason == "the entry was modified"


def test_an_entry_rewritten_with_a_fresh_hash_breaks_the_next_link() -> None:
    entries = chain(4)
    forged = replace(entries[1], action="forged")
    entries[1] = replace(forged, hash=entry_hash(**_hash_fields(forged)))
    verdict = verify_chain(entries)
    assert not verdict.valid
    assert verdict.first_broken_seq == 3
    assert verdict.reason == "the link to the previous entry is broken"


def _hash_fields(e: AuditEntry) -> dict[str, Any]:
    assert e.prev_hash is not None
    assert e.seq is not None
    return {
        "prev_hash": e.prev_hash,
        "seq": e.seq,
        "tenant_id": e.tenant_id,
        "actor_user_id": e.actor_user_id,
        "action": e.action,
        "target": e.target,
        "ip": e.ip,
        "details": e.details,
        "created_at": e.created_at,
    }


def test_verification_resumes_across_pages() -> None:
    entries = chain(6)
    first = verify_chain(entries[:3])
    second = verify_chain(entries[3:], previous_hash=first.last_hash, next_seq=first.next_seq)
    assert second.valid
    assert second.next_seq == 7
    gap = verify_chain(entries[4:], previous_hash=first.last_hash, next_seq=first.next_seq)
    assert not gap.valid


def test_entries_written_before_the_chain_are_skipped() -> None:
    legacy = replace(chain(1)[0], seq=None, prev_hash=None, hash=None)
    assert verify_chain([legacy, *chain(2)]).valid


def test_the_hash_does_not_depend_on_key_order() -> None:
    a = {"x": 1, "y": {"b": 2, "a": 1}}
    b = {"y": {"a": 1, "b": 2}, "x": 1}
    common: dict[str, Any] = {
        "prev_hash": GENESIS_HASH,
        "seq": 1,
        "tenant_id": TENANT,
        "actor_user_id": UserId(uuid4()),
        "action": "a",
        "target": "t",
        "ip": None,
        "created_at": NOW,
    }
    assert entry_hash(details=a, **common) == entry_hash(details=b, **common)


# ----------------------------------------------------------------------------- months, analytics


@pytest.mark.parametrize(
    ("month", "following"),
    [(date(2026, 10, 1), date(2026, 11, 1)), (date(2026, 12, 1), date(2027, 1, 1))],
)
def test_next_month_rolls_over_the_year(month: date, following: date) -> None:
    assert next_month(month) == following


def test_month_of_is_the_first_day() -> None:
    assert month_of(NOW) == date(2026, 10, 1)


def test_rates_are_undefined_without_data() -> None:
    empty = Analytics(0, 0, 0, {}, 0, 0, None, None, 0, 0, [], [])
    assert empty.answer_rate is None
    assert empty.satisfaction is None
    assert replace(empty, questions=4, answered=3).answer_rate == 0.75
    assert replace(empty, feedback_up=1, feedback_down=3).satisfaction == 0.25


# ----------------------------------------------------------------------------- chat guard


class FakeLimiter:
    def __init__(self, denied_key_prefix: str | None = None) -> None:
        self.denied = denied_key_prefix
        self.keys: list[str] = []

    async def hit(self, key: str, limit: Limit) -> RateDecision:
        self.keys.append(key)
        if self.denied and key.startswith(self.denied):
            return RateDecision(allowed=False, retry_after_s=2.5)
        return RateDecision(allowed=True)


class FakeSettings:
    def __init__(self, budget: int | None, configured: bool = True) -> None:
        self.value = TenantSettings(TENANT, budget) if configured else None

    async def get(self) -> TenantSettings | None:
        return self.value


class FakeUsage:
    def __init__(self, tokens: int) -> None:
        self.tokens = tokens

    async def get(self, month: date) -> Usage:
        return Usage(month=month, questions=1, input_tokens=self.tokens)

    async def add(self, month: date, *, questions: int, tokens: TokenUsage) -> None:
        raise NotImplementedError


class FakeUow:
    def __init__(self, settings: FakeSettings, usage: FakeUsage) -> None:
        self.tenant_settings = settings
        self.usage = usage


LIMITS = ChatLimits(
    per_user=Limit(per_minute=10, burst=5),
    per_tenant=Limit(per_minute=300, burst=50),
    default_monthly_token_budget=1_000,
)
PRINCIPAL = Principal(UserId(uuid4()), TENANT, MembershipId(uuid4()), Role.EMPLOYEE, frozenset())


async def test_both_buckets_are_checked_and_a_denial_carries_its_wait() -> None:
    limiter = FakeLimiter(denied_key_prefix="chat:tenant:")
    with pytest.raises(RateLimitedError) as caught:
        await ChatGuard(limiter, LIMITS).check_rate(PRINCIPAL)
    assert caught.value.retry_after_s == 2.5
    assert limiter.keys == [f"chat:user:{PRINCIPAL.user_id}", f"chat:tenant:{TENANT}"]


@pytest.mark.parametrize(
    ("settings", "spent", "blocked"),
    [
        (FakeSettings(500), 499, False),
        (FakeSettings(500), 500, True),
        (FakeSettings(None), 10**9, False),  # explicitly unlimited
        (FakeSettings(None, configured=False), 999, False),  # default budget 1000
        (FakeSettings(None, configured=False), 1_000, True),
    ],
)
async def test_the_budget_blocks_once_spent(
    settings: FakeSettings, spent: int, blocked: bool
) -> None:
    guard = ChatGuard(FakeLimiter(), LIMITS)
    uow: Any = FakeUow(settings, FakeUsage(spent))
    if blocked:
        with pytest.raises(BudgetExhaustedError) as caught:
            await guard.check_budget(uow, NOW)
        # Resets on 1 November, 00:00 UTC.
        assert (
            caught.value.retry_after_s == (datetime(2026, 11, 1, tzinfo=UTC) - NOW).total_seconds()
        )
    else:
        await guard.check_budget(uow, NOW)


# ----------------------------------------------------------------------------- memory


def test_memory_keeps_the_last_questions_in_order() -> None:
    summary = None
    for n in range(MEMORY_TURNS + 2):
        summary = remember(summary, f"question   {n}\n")
    assert summary is not None
    lines = summary.splitlines()
    assert lines[1:] == [f"- question {n}" for n in range(2, MEMORY_TURNS + 2)]


def test_a_title_comes_from_the_first_question_only() -> None:
    assert title_from(DEFAULT_TITLE, "How long is parental leave?") == "How long is parental leave?"
    assert title_from("My own title", "Anything") is None
    long = title_from(DEFAULT_TITLE, "word " * 40)
    assert long is not None
    assert long.endswith("…")
    assert len(long) <= MAX_TITLE_CHARS + 1
