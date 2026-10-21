"""Feedback on answers, and the administration endpoints (ARCHITECTURE §9)."""

import csv
import io
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse

from kb_assistant.api.deps import ContainerDep, PrincipalDep, UowDep
from kb_assistant.api.governance_schemas import (
    AnalyticsOut,
    AuditEntryOut,
    AuditPageOut,
    ChainVerdictOut,
    FeedbackIn,
    FeedbackOut,
    SettingsIn,
    SettingsOut,
)
from kb_assistant.application.governance import (
    AUDIT_PAGE_MAX,
    GetTenantSettings,
    ReadAnalytics,
    ReadAuditLog,
    SubmitFeedback,
    UpdateTenantSettings,
    VerifyAuditLog,
)
from kb_assistant.domain.ids import MessageId

feedback = APIRouter(prefix="/v1/messages", tags=["chat"])
admin = APIRouter(prefix="/v1/admin", tags=["administration"])

# Bounds the export so one request cannot hold a connection for minutes.
AUDIT_EXPORT_MAX_ROWS = 50_000
# A spreadsheet runs a cell that starts with one of these as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@feedback.put("/{message_id}/feedback", response_model=FeedbackOut)
async def rate_answer(
    message_id: UUID, body: FeedbackIn, principal: PrincipalDep, uow: UowDep, c: ContainerDep
) -> FeedbackOut:
    """Rate an answer in one of your conversations; rating again replaces the earlier one."""
    stored = await SubmitFeedback(uow, c.clock).execute(
        principal, MessageId(message_id), body.rating, body.reason, body.comment
    )
    return FeedbackOut.of(stored)


@admin.get("/analytics", response_model=AnalyticsOut)
async def analytics(
    principal: PrincipalDep,
    uow: UowDep,
    c: ContainerDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> AnalyticsOut:
    """Usage, answer rate, abstentions, satisfaction and knowledge gaps.

    Tenant admins see the tenant; department admins see the questions that
    searched a department they manage.
    """
    return AnalyticsOut.of(await ReadAnalytics(uow, c.clock).execute(principal, days), days)


@admin.get("/settings", response_model=SettingsOut)
async def get_settings(principal: PrincipalDep, uow: UowDep, c: ContainerDep) -> SettingsOut:
    default = c.settings.limits.default_monthly_token_budget
    return SettingsOut.of(await GetTenantSettings(uow, c.clock, default).execute(principal))


@admin.put("/settings", response_model=SettingsOut, status_code=status.HTTP_200_OK)
async def put_settings(
    body: SettingsIn, principal: PrincipalDep, uow: UowDep, c: ContainerDep
) -> SettingsOut:
    await UpdateTenantSettings(uow, c.clock).execute(principal, body.monthly_token_budget)
    async with c.unit_of_work() as fresh:
        await fresh.bind(tenant_id=principal.tenant_id, user_id=principal.user_id)
        default = c.settings.limits.default_monthly_token_budget
        return SettingsOut.of(await GetTenantSettings(fresh, c.clock, default).execute(principal))


@admin.get("/audit-log", response_model=AuditPageOut)
async def audit_log(
    principal: PrincipalDep,
    uow: UowDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=AUDIT_PAGE_MAX)] = 50,
    action: Annotated[str | None, Query(max_length=100)] = None,
) -> AuditPageOut:
    entries, next_cursor = await ReadAuditLog(uow).execute(
        principal, cursor=cursor, limit=limit, action=action
    )
    return AuditPageOut(items=[AuditEntryOut.of(e) for e in entries], next_cursor=next_cursor)


@admin.get("/audit-log/verify", response_model=ChainVerdictOut)
async def verify_audit_log(principal: PrincipalDep, uow: UowDep) -> ChainVerdictOut:
    """Recompute the hash chain; reports the first entry that was removed or altered."""
    return ChainVerdictOut.of(await VerifyAuditLog(uow).execute(principal))


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


@admin.get(
    "/audit-log.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}}},
)
async def export_audit_log(
    principal: PrincipalDep, c: ContainerDep, action: str | None = None
) -> StreamingResponse:
    """The audit log as CSV, newest first, formula-safe for spreadsheets."""
    # Authorize before the first byte: a 403 must not become a broken download.
    async with c.unit_of_work() as uow:
        await uow.bind(tenant_id=principal.tenant_id, user_id=principal.user_id)
        await ReadAuditLog(uow).execute(principal, cursor=None, limit=1, action=action)

    async def rows() -> AsyncIterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["seq", "created_at", "action", "actor_user_id", "target", "ip", "details", "hash"]
        )
        cursor: str | None = None
        sent = 0
        async with c.unit_of_work() as uow:
            await uow.bind(tenant_id=principal.tenant_id, user_id=principal.user_id)
            while sent < AUDIT_EXPORT_MAX_ROWS:
                entries, cursor = await ReadAuditLog(uow).execute(
                    principal, cursor=cursor, limit=AUDIT_PAGE_MAX, action=action
                )
                for e in entries:
                    writer.writerow(
                        [
                            _cell(v)
                            for v in (
                                e.seq,
                                e.created_at.isoformat(),
                                e.action,
                                e.actor_user_id,
                                e.target,
                                e.ip,
                                json.dumps(e.details, sort_keys=True),
                                e.hash,
                            )
                        ]
                    )
                sent += len(entries)
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate()
                if cursor is None:
                    break

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )
