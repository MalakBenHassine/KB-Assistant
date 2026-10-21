"""Governance: feedback, usage and budgets, rate limits, the audit chain, analytics."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum, StrEnum
from typing import Any
from uuid import UUID

from kb_assistant.domain.ids import FeedbackId, MembershipId, MessageId, TenantId, UserId

# ----------------------------------------------------------------------------- feedback


class Rating(IntEnum):
    UP = 1
    DOWN = -1


class FeedbackReason(StrEnum):
    INCORRECT = "incorrect"
    INCOMPLETE = "incomplete"
    NOT_RELEVANT = "not_relevant"
    OUTDATED = "outdated"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Feedback:
    id: FeedbackId
    tenant_id: TenantId
    message_id: MessageId
    membership_id: MembershipId
    rating: Rating
    reason: FeedbackReason | None
    comment: str | None
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------------------- usage


@dataclass(frozen=True, slots=True)
class Usage:
    """Questions and tokens of one tenant in one calendar month (UTC)."""

    month: date
    questions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def month_of(moment: datetime) -> date:
    return date(moment.year, moment.month, 1)


def next_month(month: date) -> date:
    return date(month.year + month.month // 12, month.month % 12 + 1, 1)


@dataclass(frozen=True, slots=True)
class TenantSettings:
    tenant_id: TenantId
    monthly_token_budget: int | None  # None: no budget


# ----------------------------------------------------------------------------- rate limits


@dataclass(frozen=True, slots=True)
class Limit:
    """A token bucket: `burst` requests at once, refilled at `per_minute`."""

    per_minute: float
    burst: int

    @property
    def refill_per_second(self) -> float:
        return self.per_minute / 60


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    retry_after_s: float = 0.0


# ----------------------------------------------------------------------------- audit chain

GENESIS_HASH = "0" * 64


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: UUID
    tenant_id: TenantId
    seq: int | None  # None: written before the chain existed
    actor_user_id: UserId | None
    action: str
    target: str | None
    ip: str | None
    details: dict[str, Any]
    created_at: datetime
    prev_hash: str | None
    hash: str | None


def entry_hash(
    *,
    prev_hash: str,
    seq: int,
    tenant_id: TenantId,
    actor_user_id: UserId | None,
    action: str,
    target: str | None,
    ip: str | None,
    details: dict[str, Any],
    created_at: datetime,
) -> str:
    """SHA-256 over the previous hash and a canonical encoding of the entry.

    Canonical: sorted keys, no whitespace, ISO-8601 timestamps, so the value is
    the same when computed at write time and when recomputed from the database.
    """
    payload = json.dumps(
        {
            "seq": seq,
            "tenant_id": str(tenant_id),
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "action": action,
            "target": target,
            "ip": ip,
            "details": details,
            "created_at": created_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(f"{prev_hash}{payload}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ChainVerdict:
    valid: bool
    checked: int
    first_broken_seq: int | None = None
    reason: str | None = None
    # Where the next batch continues (verification reads the chain in pages).
    last_hash: str = GENESIS_HASH
    next_seq: int = 1


def verify_chain(
    entries: list[AuditEntry], *, previous_hash: str = GENESIS_HASH, next_seq: int = 1
) -> ChainVerdict:
    """Check entries given in `seq` order, continuing from `previous_hash`.

    A deleted row shows as a gap in `seq` or a `prev_hash` that does not match;
    an edited row shows as a `hash` that no longer matches its content. Deleting
    the newest rows leaves no gap: that needs the head hash anchored elsewhere
    (ADR-0019).
    """
    prev, expected_seq, checked = previous_hash, next_seq, 0
    for entry in entries:
        if entry.seq is None or entry.hash is None or entry.prev_hash is None:
            continue  # written before the chain existed
        broken = None
        if entry.seq != expected_seq:
            broken = "a sequence number is missing"
        elif entry.prev_hash != prev:
            broken = "the link to the previous entry is broken"
        elif entry.hash != entry_hash(
            prev_hash=entry.prev_hash,
            seq=entry.seq,
            tenant_id=entry.tenant_id,
            actor_user_id=entry.actor_user_id,
            action=entry.action,
            target=entry.target,
            ip=entry.ip,
            details=entry.details,
            created_at=entry.created_at,
        ):
            broken = "the entry was modified"
        if broken:
            return ChainVerdict(False, checked, entry.seq, broken, prev, expected_seq)
        prev, expected_seq, checked = entry.hash, entry.seq + 1, checked + 1
    return ChainVerdict(True, checked, last_hash=prev, next_seq=expected_seq)


# ----------------------------------------------------------------------------- analytics


@dataclass(frozen=True, slots=True)
class DailyCount:
    day: date
    questions: int
    abstained: int


@dataclass(frozen=True, slots=True)
class KnowledgeGap:
    """A question people keep asking that the documents do not answer."""

    question: str
    times_asked: int
    last_asked_at: datetime


@dataclass(frozen=True, slots=True)
class Analytics:
    questions: int
    answered: int
    abstained: int
    abstentions_by_reason: dict[str, int]
    feedback_up: int
    feedback_down: int
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    input_tokens: int
    output_tokens: int
    daily: list[DailyCount]
    knowledge_gaps: list[KnowledgeGap]

    @property
    def answer_rate(self) -> float | None:
        return self.answered / self.questions if self.questions else None

    @property
    def satisfaction(self) -> float | None:
        rated = self.feedback_up + self.feedback_down
        return self.feedback_up / rated if rated else None
