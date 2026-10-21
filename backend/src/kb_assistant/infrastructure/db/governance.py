"""Repositories for governance data: audit chain, feedback, settings, usage, analytics.

Same rules as `uow.py`: every query filters on the bound tenant explicitly, and
row-level security is the safety net behind it.
"""

import base64
import ipaddress
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import insert, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from kb_assistant.domain.chat import TokenUsage
from kb_assistant.domain.governance import (
    GENESIS_HASH,
    Analytics,
    AuditEntry,
    DailyCount,
    Feedback,
    FeedbackReason,
    KnowledgeGap,
    Rating,
    TenantSettings,
    Usage,
    entry_hash,
)
from kb_assistant.domain.ids import (
    DepartmentId,
    FeedbackId,
    MembershipId,
    MessageId,
    TenantId,
    UserId,
    new_id,
)
from kb_assistant.domain.models import AuditEvent
from kb_assistant.infrastructure.db.tables import (
    AuditLogRow,
    FeedbackRow,
    TenantSettingsRow,
    TenantUsageRow,
)


class TenantContext(Protocol):
    @property
    def tenant_id(self) -> TenantId: ...


def _valid_ip(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


# ----------------------------------------------------------------------------- audit log


def _entry(row: AuditLogRow) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        tenant_id=TenantId(row.tenant_id),
        seq=row.seq,
        actor_user_id=UserId(row.actor_user_id) if row.actor_user_id else None,
        action=row.action,
        target=row.target,
        ip=str(row.ip) if row.ip is not None else None,
        details=dict(row.details),
        created_at=row.created_at,
        prev_hash=row.prev_hash,
        hash=row.hash,
    )


def _encode_cursor(entry: AuditEntry) -> str:
    raw = f"{entry.created_at.isoformat()}|{entry.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        moment, ident = raw.split("|")
        return datetime.fromisoformat(moment), UUID(ident)
    except ValueError:
        return None


class AuditLog:
    """Append-only, hash-chained per tenant (SECURITY.md T11, ADR-0019).

    Writers for one tenant are serialised by a transaction-scoped advisory
    lock, so two requests cannot both extend the chain from the same entry.
    The application role has INSERT and SELECT on `audit_log`, never UPDATE or
    DELETE; the chain makes a change by anyone else visible.
    """

    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self._s = session
        self._ctx = context

    async def record(self, event: AuditEvent) -> None:
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"audit:{event.tenant_id}"},
        )
        last = (
            await self._s.execute(
                select(AuditLogRow.seq, AuditLogRow.hash)
                .where(AuditLogRow.tenant_id == event.tenant_id, AuditLogRow.seq.is_not(None))
                .order_by(AuditLogRow.seq.desc())
                .limit(1)
            )
        ).first()
        seq = last.seq + 1 if last else 1
        prev_hash = last.hash if last else GENESIS_HASH
        ip = _valid_ip(event.ip)
        created_at = datetime.now(UTC)
        await self._s.execute(
            insert(AuditLogRow).values(
                id=new_id(),
                tenant_id=event.tenant_id,
                seq=seq,
                actor_user_id=event.actor_user_id,
                action=event.action,
                target=event.target,
                ip=ip,
                details=event.details,
                created_at=created_at,
                prev_hash=prev_hash,
                hash=entry_hash(
                    prev_hash=prev_hash,
                    seq=seq,
                    tenant_id=event.tenant_id,
                    actor_user_id=event.actor_user_id,
                    action=event.action,
                    target=event.target,
                    ip=ip,
                    details=event.details,
                    created_at=created_at,
                ),
            )
        )

    async def page(
        self, *, cursor: str | None, limit: int, action: str | None
    ) -> tuple[list[AuditEntry], str | None]:
        query = select(AuditLogRow).where(AuditLogRow.tenant_id == self._ctx.tenant_id)
        if action:
            query = query.where(AuditLogRow.action == action)
        position = _decode_cursor(cursor) if cursor else None
        if position is not None:
            query = query.where(tuple_(AuditLogRow.created_at, AuditLogRow.id) < position)
        rows = list(
            await self._s.scalars(
                query.order_by(AuditLogRow.created_at.desc(), AuditLogRow.id.desc()).limit(
                    limit + 1
                )
            )
        )
        entries = [_entry(r) for r in rows[:limit]]
        more = len(rows) > limit
        return entries, _encode_cursor(entries[-1]) if more and entries else None

    async def chain(self, *, after_seq: int, limit: int) -> list[AuditEntry]:
        rows = await self._s.scalars(
            select(AuditLogRow)
            .where(
                AuditLogRow.tenant_id == self._ctx.tenant_id,
                AuditLogRow.seq.is_not(None),
                AuditLogRow.seq > after_seq,
            )
            .order_by(AuditLogRow.seq)
            .limit(limit)
        )
        return [_entry(r) for r in rows]


# ----------------------------------------------------------------------------- feedback


def _feedback(row: FeedbackRow) -> Feedback:
    return Feedback(
        id=FeedbackId(row.id),
        tenant_id=TenantId(row.tenant_id),
        message_id=MessageId(row.message_id),
        membership_id=MembershipId(row.membership_id),
        rating=Rating(row.rating),
        reason=FeedbackReason(row.reason) if row.reason else None,
        comment=row.comment,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class FeedbackRepository:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self._s = session
        self._ctx = context

    async def upsert(self, feedback: Feedback) -> Feedback:
        """Insert, or replace the same person's rating of the same message."""
        values = {
            "rating": int(feedback.rating),
            "reason": feedback.reason.value if feedback.reason else None,
            "comment": feedback.comment,
            "updated_at": feedback.updated_at,
        }
        statement = (
            pg_insert(FeedbackRow)
            .values(
                id=feedback.id,
                tenant_id=self._ctx.tenant_id,
                message_id=feedback.message_id,
                membership_id=feedback.membership_id,
                created_at=feedback.created_at,
                **values,
            )
            .on_conflict_do_update(constraint="uq_feedback_message_id_membership_id", set_=values)
            .returning(FeedbackRow)
        )
        row = (await self._s.scalars(statement)).one()
        return _feedback(row)


# ----------------------------------------------------------------------------- settings, usage


class TenantSettingsRepository:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self._s = session
        self._ctx = context

    async def get(self) -> TenantSettings | None:
        row = await self._s.get(TenantSettingsRow, self._ctx.tenant_id)
        if row is None:
            return None
        return TenantSettings(TenantId(row.tenant_id), row.monthly_token_budget)

    async def put(self, settings: TenantSettings, *, now: datetime) -> None:
        values = {"monthly_token_budget": settings.monthly_token_budget, "updated_at": now}
        await self._s.execute(
            pg_insert(TenantSettingsRow)
            .values(tenant_id=self._ctx.tenant_id, **values)
            .on_conflict_do_update(index_elements=["tenant_id"], set_=values)
        )


class UsageRepository:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self._s = session
        self._ctx = context

    async def get(self, month: date) -> Usage:
        row = await self._s.get(TenantUsageRow, (self._ctx.tenant_id, month))
        if row is None:
            return Usage(month=month)
        return Usage(month, row.questions, row.input_tokens, row.output_tokens)

    async def add(self, month: date, *, questions: int, tokens: TokenUsage) -> None:
        """Atomic increment: concurrent answers never lose an update."""
        table = TenantUsageRow.__table__
        await self._s.execute(
            pg_insert(TenantUsageRow)
            .values(
                tenant_id=self._ctx.tenant_id,
                month=month,
                questions=questions,
                input_tokens=tokens.input_tokens,
                output_tokens=tokens.output_tokens,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "month"],
                set_={
                    "questions": table.c.questions + questions,
                    "input_tokens": table.c.input_tokens + tokens.input_tokens,
                    "output_tokens": table.c.output_tokens + tokens.output_tokens,
                },
            )
        )


# ----------------------------------------------------------------------------- analytics

# Abstentions that mean "the documents do not answer this", as opposed to a
# refusal (out of scope), no access, or an outage.
_GAP_REASONS = (
    "no_relevant_documents",
    "weak_evidence",
    "insufficient_evidence",
    "unsupported_answer",
)


# The answers in scope: one tenant, a period, and optionally the questions that
# searched at least one of the given departments. Every value is a bound
# parameter; `:departments` NULL means the whole tenant.
_SCOPED = (
    "WITH scoped AS ("
    " SELECT * FROM messages m"
    " WHERE m.tenant_id = :tenant AND m.role = 'assistant' AND m.created_at >= :since"
    " AND (CAST(:departments AS uuid[]) IS NULL"
    "      OR m.scope_department_ids && CAST(:departments AS uuid[])))"
)
_TOTALS = (
    " SELECT count(*) AS questions,"
    " count(*) FILTER (WHERE cardinality(m.cited_chunk_ids) > 0) AS answered,"
    " count(*) FILTER (WHERE m.abstained) AS abstained,"
    " percentile_cont(0.5) WITHIN GROUP (ORDER BY m.latency_ms) AS p50,"
    " percentile_cont(0.95) WITHIN GROUP (ORDER BY m.latency_ms) AS p95,"
    " coalesce(sum((m.token_usage->>'input_tokens')::bigint), 0) AS input_tokens,"
    " coalesce(sum((m.token_usage->>'output_tokens')::bigint), 0) AS output_tokens"
    " FROM scoped m"
)
_REASONS = " SELECT m.abstain_reason, count(*) FROM scoped m WHERE m.abstained GROUP BY 1"
_VOTES = (
    " SELECT count(*) FILTER (WHERE f.rating = 1), count(*) FILTER (WHERE f.rating = -1)"
    " FROM feedback f JOIN scoped m ON m.id = f.message_id WHERE f.tenant_id = :tenant"
)
_DAILY = (
    " SELECT (m.created_at AT TIME ZONE 'UTC')::date AS day, count(*),"
    " count(*) FILTER (WHERE m.abstained) FROM scoped m GROUP BY 1 ORDER BY 1"
)
# The question is the user message of the same request in the same conversation.
_GAPS = (
    " SELECT lower(btrim(u.content)) AS question, count(*) AS asked,"
    " max(u.created_at) AS last_asked"
    " FROM scoped m JOIN messages u"
    "   ON u.conversation_id = m.conversation_id AND u.tenant_id = m.tenant_id"
    "  AND u.request_id = m.request_id AND u.role = 'user'"
    " WHERE m.abstained AND m.abstain_reason = ANY(:gap_reasons)"
    " GROUP BY 1 ORDER BY asked DESC, last_asked DESC LIMIT :gaps"
)


class AnalyticsQueries:
    def __init__(self, session: AsyncSession, context: TenantContext) -> None:
        self._s = session
        self._ctx = context

    async def _rows(self, query: str, params: dict[str, Any]) -> list[Any]:
        # Constant SQL text; every value is bound.
        return list((await self._s.execute(text(_SCOPED + query), params)).all())

    async def summarize(
        self, *, since: datetime, department_ids: Sequence[DepartmentId] | None, gaps: int
    ) -> Analytics:
        params: dict[str, Any] = {
            "tenant": self._ctx.tenant_id,
            "since": since,
            "departments": None if department_ids is None else list(department_ids),
            "gaps": gaps,
            "gap_reasons": list(_GAP_REASONS),
        }
        (totals,) = await self._rows(_TOTALS, params)
        reasons = await self._rows(_REASONS, params)
        (votes,) = await self._rows(_VOTES, params)
        daily = await self._rows(_DAILY, params)
        gap_rows = await self._rows(_GAPS, params)
        return Analytics(
            questions=totals.questions,
            answered=totals.answered,
            abstained=totals.abstained,
            abstentions_by_reason={str(r[0]): int(r[1]) for r in reasons if r[0]},
            feedback_up=int(votes[0]),
            feedback_down=int(votes[1]),
            latency_p50_ms=float(totals.p50) if totals.p50 is not None else None,
            latency_p95_ms=float(totals.p95) if totals.p95 is not None else None,
            input_tokens=int(totals.input_tokens),
            output_tokens=int(totals.output_tokens),
            daily=[DailyCount(r[0], int(r[1]), int(r[2])) for r in daily],
            knowledge_gaps=[KnowledgeGap(r.question, int(r.asked), r.last_asked) for r in gap_rows],
        )
