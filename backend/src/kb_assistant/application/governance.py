"""Governance use cases: feedback, limits and budgets, settings, audit log, analytics.

Who may do what (ARCHITECTURE §6.4):
- feedback: anyone, on answers in their own conversations;
- analytics: tenant admins for the tenant, department admins for the questions
  that searched a department they manage;
- settings, audit log: tenant admins.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from kb_assistant.application.authorization import require_role
from kb_assistant.application.telemetry import NullTelemetry
from kb_assistant.domain.chat import TokenUsage
from kb_assistant.domain.errors import (
    BudgetExhaustedError,
    ForbiddenError,
    NotFoundError,
    PolicyViolationError,
    RateLimitedError,
)
from kb_assistant.domain.governance import (
    GENESIS_HASH,
    Analytics,
    AuditEntry,
    ChainVerdict,
    Feedback,
    FeedbackReason,
    Limit,
    Rating,
    TenantSettings,
    Usage,
    month_of,
    next_month,
    verify_chain,
)
from kb_assistant.domain.ids import DepartmentId, FeedbackId, MessageId, new_id
from kb_assistant.domain.models import AuditEvent, Role
from kb_assistant.domain.ports import Clock, RateLimiter, Telemetry, UnitOfWork
from kb_assistant.domain.principal import Principal

MAX_FEEDBACK_COMMENT = 1_000
MAX_ANALYTICS_DAYS = 365
KNOWLEDGE_GAPS_SHOWN = 10
AUDIT_PAGE_MAX = 200
AUDIT_VERIFY_BATCH = 1_000

# ----------------------------------------------------------------------------- feedback


class SubmitFeedback:
    """Rate an answer. Asking again replaces the earlier rating (one per person)."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(
        self,
        principal: Principal,
        message_id: MessageId,
        rating: Rating,
        reason: FeedbackReason | None,
        comment: str | None,
    ) -> Feedback:
        comment = (comment or "").strip() or None
        if comment is not None and len(comment) > MAX_FEEDBACK_COMMENT:
            raise PolicyViolationError(
                f"Comments are limited to {MAX_FEEDBACK_COMMENT} characters."
            )
        # Feedback names a message, never a chunk: what it refers to is derived
        # server side (SECURITY.md T6). Someone else's message is a 404.
        message = await self._uow.messages.get(message_id)
        conversation = (
            await self._uow.conversations.get(message.conversation_id) if message else None
        )
        if (
            message is None
            or message.role != "assistant"
            or conversation is None
            or conversation.membership_id != principal.membership_id
        ):
            raise NotFoundError("Message not found.")
        now = self._clock.now()
        stored = await self._uow.feedback.upsert(
            Feedback(
                id=FeedbackId(new_id()),
                tenant_id=principal.tenant_id,
                message_id=message_id,
                membership_id=principal.membership_id,
                rating=rating,
                reason=reason,
                comment=comment,
                created_at=now,
                updated_at=now,
            )
        )
        await self._uow.commit()
        return stored


# ----------------------------------------------------------------------------- limits


@dataclass(frozen=True, slots=True)
class ChatLimits:
    per_user: Limit
    per_tenant: Limit
    default_monthly_token_budget: int | None  # when a tenant has no setting of its own


class ChatGuard:
    """Rate limits and the monthly budget, checked before any model is called (T8)."""

    def __init__(
        self, limiter: RateLimiter, limits: ChatLimits, telemetry: Telemetry | None = None
    ) -> None:
        self._limiter = limiter
        self._limits = limits
        self._telemetry: Telemetry = telemetry or NullTelemetry()

    async def check_rate(self, principal: Principal) -> None:
        for kind, key, limit in (
            ("user", f"chat:user:{principal.user_id}", self._limits.per_user),
            ("tenant", f"chat:tenant:{principal.tenant_id}", self._limits.per_tenant),
        ):
            decision = await self._limiter.hit(key, limit)
            if not decision.allowed:
                self._telemetry.rate_limited(kind)
                raise RateLimitedError(decision.retry_after_s)

    async def check_budget(self, uow: UnitOfWork, now: datetime) -> None:
        budget = await effective_budget(uow, self._limits.default_monthly_token_budget)
        if budget is None:
            return
        usage = await uow.usage.get(month_of(now))
        if usage.tokens >= budget:
            self._telemetry.rate_limited("budget")
            reset = datetime.combine(next_month(usage.month), datetime.min.time(), tzinfo=UTC)
            raise BudgetExhaustedError((reset - now).total_seconds())


async def effective_budget(uow: UnitOfWork, default: int | None) -> int | None:
    settings = await uow.tenant_settings.get()
    return settings.monthly_token_budget if settings is not None else default


async def record_usage(uow: UnitOfWork, now: datetime, tokens: TokenUsage) -> None:
    await uow.usage.add(month_of(now), questions=1, tokens=tokens)


# ----------------------------------------------------------------------------- settings


@dataclass(frozen=True, slots=True)
class SettingsView:
    monthly_token_budget: int | None
    budget_is_default: bool
    usage: Usage


class GetTenantSettings:
    def __init__(self, uow: UnitOfWork, clock: Clock, default_budget: int | None) -> None:
        self._uow = uow
        self._clock = clock
        self._default = default_budget

    async def execute(self, principal: Principal) -> SettingsView:
        require_role(principal, Role.TENANT_ADMIN)
        settings = await self._uow.tenant_settings.get()
        return SettingsView(
            monthly_token_budget=settings.monthly_token_budget if settings else self._default,
            budget_is_default=settings is None,
            usage=await self._uow.usage.get(month_of(self._clock.now())),
        )


class UpdateTenantSettings:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, principal: Principal, monthly_token_budget: int | None) -> None:
        require_role(principal, Role.TENANT_ADMIN)
        if monthly_token_budget is not None and monthly_token_budget < 0:
            raise PolicyViolationError("The budget cannot be negative.")
        await self._uow.tenant_settings.put(
            TenantSettings(principal.tenant_id, monthly_token_budget), now=self._clock.now()
        )
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=principal.tenant_id,
                action="tenant.settings_updated",
                actor_user_id=principal.user_id,
                details={"monthly_token_budget": monthly_token_budget},
            )
        )
        await self._uow.commit()


# ----------------------------------------------------------------------------- audit log


class ReadAuditLog:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self,
        principal: Principal,
        *,
        cursor: str | None,
        limit: int,
        action: str | None,
    ) -> tuple[list[AuditEntry], str | None]:
        """A page, newest first, and the cursor of the next page (None at the end)."""
        require_role(principal, Role.TENANT_ADMIN)
        limit = max(1, min(limit, AUDIT_PAGE_MAX))
        return await self._uow.audit.page(cursor=cursor, limit=limit, action=action)


class VerifyAuditLog:
    """Recompute the tenant's hash chain from its first entry (SECURITY.md T11)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, principal: Principal) -> ChainVerdict:
        require_role(principal, Role.TENANT_ADMIN)
        verdict = ChainVerdict(True, 0, last_hash=GENESIS_HASH, next_seq=1)
        checked = 0
        while True:
            batch = await self._uow.audit.chain(
                after_seq=verdict.next_seq - 1, limit=AUDIT_VERIFY_BATCH
            )
            if not batch:
                return ChainVerdict(
                    True, checked, last_hash=verdict.last_hash, next_seq=verdict.next_seq
                )
            verdict = verify_chain(
                batch, previous_hash=verdict.last_hash, next_seq=verdict.next_seq
            )
            checked += verdict.checked
            if not verdict.valid:
                return ChainVerdict(
                    False,
                    checked,
                    verdict.first_broken_seq,
                    verdict.reason,
                    verdict.last_hash,
                    verdict.next_seq,
                )


# ----------------------------------------------------------------------------- analytics


class ReadAnalytics:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, principal: Principal, days: int) -> Analytics:
        if not 1 <= days <= MAX_ANALYTICS_DAYS:
            raise PolicyViolationError(f"Choose between 1 and {MAX_ANALYTICS_DAYS} days.")
        departments: Sequence[DepartmentId] | None
        if principal.role is Role.TENANT_ADMIN:
            departments = None
        elif principal.role is Role.DEPT_ADMIN:
            departments = sorted(principal.department_ids)
        else:
            raise ForbiddenError("Analytics are for administrators.")
        since = self._clock.now() - timedelta(days=days)
        return await self._uow.analytics.summarize(
            since=since, department_ids=departments, gaps=KNOWLEDGE_GAPS_SHOWN
        )
