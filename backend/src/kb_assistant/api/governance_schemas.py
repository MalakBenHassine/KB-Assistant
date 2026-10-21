"""Request and response bodies for feedback and administration."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from kb_assistant.application.governance import SettingsView
from kb_assistant.domain.governance import (
    Analytics,
    AuditEntry,
    ChainVerdict,
    Feedback,
    FeedbackReason,
    Rating,
)


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Rating = Field(description="1 for helpful, -1 for not helpful")
    reason: FeedbackReason | None = None
    comment: str | None = Field(default=None, max_length=1_000)


class FeedbackOut(_Out):
    id: UUID
    message_id: UUID
    rating: Rating
    reason: FeedbackReason | None
    comment: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, f: Feedback) -> "FeedbackOut":
        return cls(
            id=f.id,
            message_id=f.message_id,
            rating=f.rating,
            reason=f.reason,
            comment=f.comment,
            created_at=f.created_at,
            updated_at=f.updated_at,
        )


class UsageOut(_Out):
    month: date
    questions: int
    input_tokens: int
    output_tokens: int
    tokens: int


class SettingsOut(_Out):
    monthly_token_budget: int | None = Field(description="Tokens per calendar month; null: none")
    budget_is_default: bool
    usage: UsageOut

    @classmethod
    def of(cls, view: SettingsView) -> "SettingsOut":
        u = view.usage
        return cls(
            monthly_token_budget=view.monthly_token_budget,
            budget_is_default=view.budget_is_default,
            usage=UsageOut(
                month=u.month,
                questions=u.questions,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                tokens=u.tokens,
            ),
        )


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_token_budget: int | None = Field(ge=0)


class AuditEntryOut(_Out):
    id: UUID
    seq: int | None
    action: str
    actor_user_id: UUID | None
    target: str | None
    ip: str | None
    details: dict[str, Any]
    created_at: datetime
    hash: str | None

    @classmethod
    def of(cls, e: AuditEntry) -> "AuditEntryOut":
        return cls(
            id=e.id,
            seq=e.seq,
            action=e.action,
            actor_user_id=e.actor_user_id,
            target=e.target,
            ip=e.ip,
            details=e.details,
            created_at=e.created_at,
            hash=e.hash,
        )


class AuditPageOut(_Out):
    items: list[AuditEntryOut]
    next_cursor: str | None


class ChainVerdictOut(_Out):
    valid: bool
    checked: int
    first_broken_seq: int | None
    reason: str | None
    head_hash: str = Field(description="Keep it elsewhere to detect deletion of the newest entries")

    @classmethod
    def of(cls, v: ChainVerdict) -> "ChainVerdictOut":
        return cls(
            valid=v.valid,
            checked=v.checked,
            first_broken_seq=v.first_broken_seq,
            reason=v.reason,
            head_hash=v.last_hash,
        )


class DailyOut(_Out):
    day: date
    questions: int
    abstained: int


class GapOut(_Out):
    question: str
    times_asked: int
    last_asked_at: datetime


class AnalyticsOut(_Out):
    days: int
    questions: int
    answered: int
    abstained: int
    answer_rate: float | None
    abstentions_by_reason: dict[str, int]
    feedback_up: int
    feedback_down: int
    satisfaction: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    input_tokens: int
    output_tokens: int
    daily: list[DailyOut]
    knowledge_gaps: list[GapOut] = Field(
        description="Questions the documents did not answer, most asked first"
    )

    @classmethod
    def of(cls, a: Analytics, days: int) -> "AnalyticsOut":
        return cls(
            days=days,
            questions=a.questions,
            answered=a.answered,
            abstained=a.abstained,
            answer_rate=a.answer_rate,
            abstentions_by_reason=a.abstentions_by_reason,
            feedback_up=a.feedback_up,
            feedback_down=a.feedback_down,
            satisfaction=a.satisfaction,
            latency_p50_ms=a.latency_p50_ms,
            latency_p95_ms=a.latency_p95_ms,
            input_tokens=a.input_tokens,
            output_tokens=a.output_tokens,
            daily=[
                DailyOut(day=d.day, questions=d.questions, abstained=d.abstained) for d in a.daily
            ],
            knowledge_gaps=[
                GapOut(
                    question=g.question, times_asked=g.times_asked, last_asked_at=g.last_asked_at
                )
                for g in a.knowledge_gaps
            ],
        )
