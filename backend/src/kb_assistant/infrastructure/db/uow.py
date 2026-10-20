"""Unit of work and repositories over one `AsyncSession`.

Row-level security context
--------------------------
Postgres policies read four transaction-local settings (see migration 0001):

- `app.tenant_id`              the tenant this transaction acts in;
- `app.user_id`                the acting user;
- `app.cross_tenant_user_id`   lets the user see their *own* memberships in
                               every tenant (login and profile only);
- `app.token_hash`             lets the transaction find the one refresh token
                               or invitation whose hash it already knows.

They are set with `set_config(..., is_local => true)`, so they vanish at the end
of the transaction and can never leak to the next user of a pooled connection.
An `after_begin` listener re-applies them to every new transaction of this unit
of work.

Repositories also filter on the bound tenant explicitly. RLS is the safety net,
not the only mechanism: a query that relies on it alone is a bug.
"""

import ipaddress
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, event, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from kb_assistant.domain.ids import (
    DepartmentId,
    InvitationId,
    MembershipId,
    RefreshTokenId,
    TenantId,
    TokenFamilyId,
    UserId,
    new_id,
)
from kb_assistant.domain.models import (
    AuditEvent,
    Department,
    Invitation,
    Membership,
    RefreshToken,
    Role,
    Tenant,
    User,
)
from kb_assistant.infrastructure.db.tables import (
    AuditLogRow,
    DepartmentRow,
    InvitationRow,
    MembershipDepartmentRow,
    MembershipRow,
    RefreshTokenRow,
    TenantRow,
    UserRow,
)

_SET_CONTEXT = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true),"
    " set_config('app.user_id', :user_id, true),"
    " set_config('app.cross_tenant_user_id', :cross_tenant_user_id, true),"
    " set_config('app.token_hash', :token_hash, true)"
)


class UnboundTenantError(RuntimeError):
    """A tenant-scoped query ran before `bind()`: a programming error, never user input."""


class _Context:
    def __init__(self) -> None:
        self.values: dict[str, str] = {
            "tenant_id": "",
            "user_id": "",
            "cross_tenant_user_id": "",
            "token_hash": "",
        }

    @property
    def tenant_id(self) -> TenantId:
        raw = self.values["tenant_id"]
        if not raw:
            raise UnboundTenantError("tenant-scoped query without a bound tenant")
        return TenantId(UUID(raw))


class SqlAlchemyUnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._context = _Context()
        event.listen(session.sync_session, "after_begin", self._apply_context)

        self.tenants = _Tenants(session)
        self.users = _Users(session)
        self.memberships = _Memberships(session, self._context)
        self.departments = _Departments(session, self._context)
        self.invitations = _Invitations(session, self._context)
        self.refresh_tokens = _RefreshTokens(session)
        self.audit = _Audit(session)

    def _apply_context(
        self, _session: Session, _transaction: SessionTransaction, connection: Connection
    ) -> None:
        connection.execute(_SET_CONTEXT, self._context.values)

    async def _set(self, **values: str) -> None:
        self._context.values.update(values)
        await self._session.execute(_SET_CONTEXT, self._context.values)

    async def bind(self, *, tenant_id: TenantId | None, user_id: UserId | None) -> None:
        await self._set(
            tenant_id=str(tenant_id) if tenant_id else "",
            user_id=str(user_id) if user_id else "",
            cross_tenant_user_id="",
            token_hash="",
        )

    async def allow_cross_tenant_identity(self, user_id: UserId) -> None:
        await self._set(cross_tenant_user_id=str(user_id))

    async def allow_token_lookup(self, token_hash: str) -> None:
        await self._set(token_hash=token_hash)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


# --------------------------------------------------------------------------- mapping


def _tenant(row: TenantRow) -> Tenant:
    return Tenant(id=TenantId(row.id), name=row.name, slug=row.slug)


def _user(row: UserRow) -> User:
    return User(
        id=UserId(row.id),
        email=row.email,
        password_hash=row.password_hash,
        is_active=row.is_active,
        token_version=row.token_version,
    )


def _department(row: DepartmentRow) -> Department:
    return Department(
        id=DepartmentId(row.id), tenant_id=TenantId(row.tenant_id), slug=row.slug, name=row.name
    )


def _invitation(row: InvitationRow) -> Invitation:
    return Invitation(
        id=InvitationId(row.id),
        tenant_id=TenantId(row.tenant_id),
        email=row.email,
        role=Role(row.role),
        department_ids=frozenset(DepartmentId(d) for d in row.department_ids),
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        invited_by=MembershipId(row.invited_by),
    )


def _refresh_token(row: RefreshTokenRow) -> RefreshToken:
    return RefreshToken(
        id=RefreshTokenId(row.id),
        family_id=TokenFamilyId(row.family_id),
        user_id=UserId(row.user_id),
        tenant_id=TenantId(row.tenant_id),
        membership_id=MembershipId(row.membership_id),
        token_hash=row.token_hash,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        family_expires_at=row.family_expires_at,
        revoked_at=row.revoked_at,
        replaced_by=RefreshTokenId(row.replaced_by) if row.replaced_by else None,
    )


# --------------------------------------------------------------------------- repositories


class _Tenants:
    """Tenants are platform-level rows: readable, never written by the API role."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        row = await self._s.get(TenantRow, tenant_id)
        return _tenant(row) if row else None

    async def get_many(self, tenant_ids: Sequence[TenantId]) -> list[Tenant]:
        if not tenant_ids:
            return []
        rows = await self._s.scalars(select(TenantRow).where(TenantRow.id.in_(tenant_ids)))
        return [_tenant(r) for r in rows]


class _Users:
    """Users are global identities (ADR-0012): no tenant filter applies here."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: UserId) -> User | None:
        row = await self._s.get(UserRow, user_id)
        return _user(row) if row else None

    async def get_by_email(self, email: str) -> User | None:
        row = await self._s.scalar(select(UserRow).where(UserRow.email == email))
        return _user(row) if row else None

    async def add(self, user: User) -> None:
        await self._s.execute(
            insert(UserRow).values(
                id=user.id,
                email=user.email,
                password_hash=user.password_hash,
                is_active=user.is_active,
                token_version=user.token_version,
            )
        )

    async def set_password_hash(self, user_id: UserId, password_hash: str) -> None:
        await self._s.execute(
            update(UserRow).where(UserRow.id == user_id).values(password_hash=password_hash)
        )


class _Memberships:
    def __init__(self, session: AsyncSession, context: _Context) -> None:
        self._s = session
        self._ctx = context

    async def _departments_of(self, membership_ids: Sequence[Any]) -> dict[Any, set[Any]]:
        result: dict[Any, set[Any]] = {m: set() for m in membership_ids}
        if not membership_ids:
            return result
        rows = await self._s.execute(
            select(
                MembershipDepartmentRow.membership_id, MembershipDepartmentRow.department_id
            ).where(MembershipDepartmentRow.membership_id.in_(membership_ids))
        )
        for membership_id, department_id in rows:
            result[membership_id].add(department_id)
        return result

    def _to_domain(self, row: MembershipRow, departments: set[Any]) -> Membership:
        return Membership(
            id=MembershipId(row.id),
            user_id=UserId(row.user_id),
            tenant_id=TenantId(row.tenant_id),
            role=Role(row.role),
            is_active=row.is_active,
            department_ids=frozenset(DepartmentId(d) for d in departments),
        )

    async def get(self, membership_id: MembershipId) -> Membership | None:
        row = await self._s.scalar(
            select(MembershipRow).where(
                MembershipRow.id == membership_id,
                MembershipRow.tenant_id == self._ctx.tenant_id,
            )
        )
        if row is None:
            return None
        departments = await self._departments_of([row.id])
        return self._to_domain(row, departments[row.id])

    async def get_for_user(self, user_id: UserId, tenant_id: TenantId) -> Membership | None:
        row = await self._s.scalar(
            select(MembershipRow).where(
                MembershipRow.user_id == user_id, MembershipRow.tenant_id == tenant_id
            )
        )
        if row is None:
            return None
        departments = await self._departments_of([row.id])
        return self._to_domain(row, departments[row.id])

    async def list_for_tenant(self) -> list[Membership]:
        rows = list(
            await self._s.scalars(
                select(MembershipRow)
                .where(MembershipRow.tenant_id == self._ctx.tenant_id)
                .order_by(MembershipRow.created_at)
            )
        )
        departments = await self._departments_of([r.id for r in rows])
        return [self._to_domain(r, departments[r.id]) for r in rows]

    async def list_active_tenants_of_user(self, user_id: UserId) -> list[TenantId]:
        rows = await self._s.scalars(
            select(MembershipRow.tenant_id)
            .where(MembershipRow.user_id == user_id, MembershipRow.is_active.is_(True))
            .order_by(MembershipRow.last_used_at.desc().nulls_last(), MembershipRow.created_at)
        )
        return [TenantId(t) for t in rows]

    async def add(self, membership: Membership) -> None:
        await self._s.execute(
            insert(MembershipRow).values(
                id=membership.id,
                user_id=membership.user_id,
                tenant_id=membership.tenant_id,
                role=membership.role.value,
                is_active=membership.is_active,
            )
        )
        if membership.department_ids:
            await self._s.execute(
                insert(MembershipDepartmentRow),
                [
                    {
                        "membership_id": membership.id,
                        "department_id": d,
                        "tenant_id": membership.tenant_id,
                    }
                    for d in sorted(membership.department_ids)
                ],
            )

    async def touch(self, membership_id: MembershipId, at: datetime) -> None:
        await self._s.execute(
            update(MembershipRow).where(MembershipRow.id == membership_id).values(last_used_at=at)
        )


class _Departments:
    def __init__(self, session: AsyncSession, context: _Context) -> None:
        self._s = session
        self._ctx = context

    async def list_all(self) -> list[Department]:
        rows = await self._s.scalars(
            select(DepartmentRow)
            .where(DepartmentRow.tenant_id == self._ctx.tenant_id)
            .order_by(DepartmentRow.name)
        )
        return [_department(r) for r in rows]

    async def get_many(self, department_ids: Sequence[DepartmentId]) -> list[Department]:
        if not department_ids:
            return []
        rows = await self._s.scalars(
            select(DepartmentRow).where(
                DepartmentRow.id.in_(department_ids),
                DepartmentRow.tenant_id == self._ctx.tenant_id,
            )
        )
        return [_department(r) for r in rows]

    async def get_by_slug(self, slug: str) -> Department | None:
        row = await self._s.scalar(
            select(DepartmentRow).where(
                DepartmentRow.slug == slug, DepartmentRow.tenant_id == self._ctx.tenant_id
            )
        )
        return _department(row) if row else None

    async def add(self, department: Department) -> None:
        await self._s.execute(
            insert(DepartmentRow).values(
                id=department.id,
                tenant_id=department.tenant_id,
                slug=department.slug,
                name=department.name,
            )
        )


class _Invitations:
    def __init__(self, session: AsyncSession, context: _Context) -> None:
        self._s = session
        self._ctx = context

    async def get_by_token_hash_for_update(self, token_hash: str) -> Invitation | None:
        row = await self._s.scalar(
            select(InvitationRow).where(InvitationRow.token_hash == token_hash).with_for_update()
        )
        return _invitation(row) if row else None

    async def add(self, invitation: Invitation) -> None:
        await self._s.execute(
            insert(InvitationRow).values(
                id=invitation.id,
                tenant_id=invitation.tenant_id,
                email=invitation.email,
                role=invitation.role.value,
                department_ids=sorted(invitation.department_ids),
                token_hash=invitation.token_hash,
                expires_at=invitation.expires_at,
                invited_by=invitation.invited_by,
            )
        )

    async def mark_accepted(self, invitation: Invitation, at: datetime) -> None:
        await self._s.execute(
            update(InvitationRow)
            .where(
                InvitationRow.id == invitation.id,
                InvitationRow.tenant_id == self._ctx.tenant_id,
            )
            .values(accepted_at=at)
        )


class _RefreshTokens:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        row = await self._s.scalar(
            select(RefreshTokenRow)
            .where(RefreshTokenRow.token_hash == token_hash)
            .with_for_update()
        )
        return _refresh_token(row) if row else None

    async def add(self, token: RefreshToken) -> None:
        await self._s.execute(
            insert(RefreshTokenRow).values(
                id=token.id,
                family_id=token.family_id,
                user_id=token.user_id,
                tenant_id=token.tenant_id,
                membership_id=token.membership_id,
                token_hash=token.token_hash,
                issued_at=token.issued_at,
                expires_at=token.expires_at,
                family_expires_at=token.family_expires_at,
            )
        )

    async def mark_rotated(
        self, token_id: RefreshTokenId, replaced_by: RefreshTokenId, at: datetime
    ) -> None:
        await self._s.execute(
            update(RefreshTokenRow)
            .where(RefreshTokenRow.id == token_id)
            .values(revoked_at=at, replaced_by=replaced_by)
        )

    async def revoke_family(self, family_id: TokenFamilyId, at: datetime) -> int:
        result = await self._s.execute(
            update(RefreshTokenRow)
            .where(RefreshTokenRow.family_id == family_id, RefreshTokenRow.revoked_at.is_(None))
            .values(revoked_at=at)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class _Audit:
    """Append-only: the API role has INSERT on `audit_log`, not UPDATE or DELETE."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, event: AuditEvent) -> None:
        await self._s.execute(
            insert(AuditLogRow).values(
                id=new_id(),
                tenant_id=event.tenant_id,
                actor_user_id=event.actor_user_id,
                action=event.action,
                target=event.target,
                ip=_valid_ip(event.ip),
                details=event.details,
            )
        )


def _valid_ip(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None
