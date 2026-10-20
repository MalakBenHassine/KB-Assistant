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

from sqlalchemy import Connection, event, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from kb_assistant.domain.documents import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionFailure,
    MimeType,
    VersionStatus,
)
from kb_assistant.domain.errors import ConflictError
from kb_assistant.domain.ids import (
    DepartmentId,
    DocumentId,
    InvitationId,
    MembershipId,
    RefreshTokenId,
    TenantId,
    TokenFamilyId,
    UserId,
    VersionId,
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
    ChunkRow,
    DepartmentRow,
    DocumentRow,
    DocumentVersionRow,
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
        self.documents = _Documents(session, self._context)
        self.versions = _Versions(session, self._context)
        self.chunks = _Chunks(session, self._context)

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


# --------------------------------------------------------------------------- documents


def _document(row: DocumentRow) -> Document:
    return Document(
        id=DocumentId(row.id),
        tenant_id=TenantId(row.tenant_id),
        department_id=DepartmentId(row.department_id),
        title=row.title,
        created_by=MembershipId(row.created_by),
        created_at=row.created_at,
        current_version_id=VersionId(row.current_version_id) if row.current_version_id else None,
    )


def _version(row: DocumentVersionRow) -> DocumentVersion:
    failure = (
        IngestionFailure(row.error_code, row.error_message or "", bool(row.error_retryable))
        if row.error_code
        else None
    )
    return DocumentVersion(
        id=VersionId(row.id),
        document_id=DocumentId(row.document_id),
        tenant_id=TenantId(row.tenant_id),
        version=row.version,
        checksum=row.checksum,
        filename=row.filename,
        mime_type=MimeType(row.mime_type),
        size_bytes=row.size_bytes,
        blob_key=row.blob_key,
        status=VersionStatus(row.status),
        failure=failure,
        attempts=row.attempts,
        chunk_count=row.chunk_count,
        embedding_model=row.embedding_model,
        embedding_tokens=row.embedding_tokens,
        created_by=MembershipId(row.created_by),
        created_at=row.created_at,
        processing_started_at=row.processing_started_at,
        indexed_at=row.indexed_at,
        archived_at=row.archived_at,
    )


class _Documents:
    def __init__(self, session: AsyncSession, context: _Context) -> None:
        self._s = session
        self._ctx = context

    async def add(self, document: Document) -> None:
        await self._s.execute(
            insert(DocumentRow).values(
                id=document.id,
                tenant_id=document.tenant_id,
                department_id=document.department_id,
                title=document.title,
                created_by=document.created_by,
                created_at=document.created_at,
            )
        )

    async def get(self, document_id: DocumentId) -> Document | None:
        row = await self._s.scalar(
            select(DocumentRow).where(
                DocumentRow.id == document_id, DocumentRow.tenant_id == self._ctx.tenant_id
            )
        )
        return _document(row) if row else None

    async def list_in_departments(self, department_ids: Sequence[DepartmentId]) -> list[Document]:
        if not department_ids:
            return []
        rows = await self._s.scalars(
            select(DocumentRow)
            .where(
                DocumentRow.tenant_id == self._ctx.tenant_id,
                DocumentRow.department_id.in_(department_ids),
            )
            .order_by(DocumentRow.title, DocumentRow.id)
        )
        return [_document(r) for r in rows]

    async def find_by_checksum(self, department_id: DepartmentId, checksum: str) -> Document | None:
        latest = (
            select(
                DocumentVersionRow.document_id,
                func.max(DocumentVersionRow.version).label("version"),
            )
            .where(DocumentVersionRow.tenant_id == self._ctx.tenant_id)
            .group_by(DocumentVersionRow.document_id)
            .subquery()
        )
        row = await self._s.scalar(
            select(DocumentRow)
            .join(latest, latest.c.document_id == DocumentRow.id)
            .join(
                DocumentVersionRow,
                (DocumentVersionRow.document_id == latest.c.document_id)
                & (DocumentVersionRow.version == latest.c.version),
            )
            .where(
                DocumentRow.tenant_id == self._ctx.tenant_id,
                DocumentRow.department_id == department_id,
                DocumentVersionRow.checksum == checksum,
                DocumentVersionRow.status != VersionStatus.FAILED.value,
            )
            .limit(1)
        )
        return _document(row) if row else None

    async def set_current_version(self, document_id: DocumentId, version_id: VersionId) -> None:
        await self._s.execute(
            update(DocumentRow)
            .where(DocumentRow.id == document_id, DocumentRow.tenant_id == self._ctx.tenant_id)
            .values(current_version_id=version_id)
        )


class _Versions:
    def __init__(self, session: AsyncSession, context: _Context) -> None:
        self._s = session
        self._ctx = context

    def _where(self, version_id: VersionId) -> tuple[Any, ...]:
        return (
            DocumentVersionRow.id == version_id,
            DocumentVersionRow.tenant_id == self._ctx.tenant_id,
        )

    async def add(self, version: DocumentVersion) -> None:
        try:
            async with self._s.begin_nested():
                await self._s.execute(
                    insert(DocumentVersionRow).values(
                        id=version.id,
                        document_id=version.document_id,
                        tenant_id=version.tenant_id,
                        version=version.version,
                        checksum=version.checksum,
                        filename=version.filename,
                        mime_type=version.mime_type.value,
                        size_bytes=version.size_bytes,
                        blob_key=version.blob_key,
                        status=version.status.value,
                        created_by=version.created_by,
                        created_at=version.created_at,
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(
                "Another version of this document was uploaded at the same time. Try again."
            ) from exc

    async def get(self, version_id: VersionId) -> DocumentVersion | None:
        row = await self._s.scalar(select(DocumentVersionRow).where(*self._where(version_id)))
        return _version(row) if row else None

    async def latest(self, document_id: DocumentId) -> DocumentVersion | None:
        row = await self._s.scalar(
            select(DocumentVersionRow)
            .where(
                DocumentVersionRow.document_id == document_id,
                DocumentVersionRow.tenant_id == self._ctx.tenant_id,
            )
            .order_by(DocumentVersionRow.version.desc())
            .limit(1)
        )
        return _version(row) if row else None

    async def list_for_document(self, document_id: DocumentId) -> list[DocumentVersion]:
        rows = await self._s.scalars(
            select(DocumentVersionRow)
            .where(
                DocumentVersionRow.document_id == document_id,
                DocumentVersionRow.tenant_id == self._ctx.tenant_id,
            )
            .order_by(DocumentVersionRow.version.desc())
        )
        return [_version(r) for r in rows]

    async def latest_for_documents(
        self, document_ids: Sequence[DocumentId]
    ) -> dict[DocumentId, DocumentVersion]:
        if not document_ids:
            return {}
        rows = await self._s.scalars(
            select(DocumentVersionRow)
            .where(
                DocumentVersionRow.document_id.in_(document_ids),
                DocumentVersionRow.tenant_id == self._ctx.tenant_id,
            )
            .order_by(DocumentVersionRow.document_id, DocumentVersionRow.version.desc())
            .ext(distinct_on(DocumentVersionRow.document_id))
        )
        return {DocumentId(r.document_id): _version(r) for r in rows}

    async def claim(self, version_id: VersionId, *, now: datetime, stale_before: datetime) -> bool:
        claimable = or_(
            DocumentVersionRow.status == VersionStatus.PENDING.value,
            (DocumentVersionRow.status == VersionStatus.FAILED.value)
            & DocumentVersionRow.error_retryable.is_(True),
            (DocumentVersionRow.status == VersionStatus.PROCESSING.value)
            & (DocumentVersionRow.processing_started_at < stale_before),
        )
        result = await self._s.execute(
            update(DocumentVersionRow)
            .where(*self._where(version_id), claimable)
            .values(
                status=VersionStatus.PROCESSING.value,
                processing_started_at=now,
                attempts=DocumentVersionRow.attempts + 1,
            )
            .returning(DocumentVersionRow.id)
        )
        return result.scalar_one_or_none() is not None

    async def mark_indexed(
        self,
        version_id: VersionId,
        *,
        now: datetime,
        chunk_count: int,
        chunker_version: str,
        embedding_model: str,
        embedding_tokens: int,
    ) -> None:
        await self._s.execute(
            update(DocumentVersionRow)
            .where(*self._where(version_id))
            .values(
                status=VersionStatus.INDEXED.value,
                indexed_at=now,
                chunk_count=chunk_count,
                chunker_version=chunker_version,
                embedding_model=embedding_model,
                embedding_tokens=embedding_tokens,
                error_code=None,
                error_message=None,
                error_retryable=None,
            )
        )

    async def mark_failed(self, version_id: VersionId, failure: IngestionFailure) -> None:
        await self._s.execute(
            update(DocumentVersionRow)
            .where(*self._where(version_id))
            .values(
                status=VersionStatus.FAILED.value,
                error_code=failure.code,
                error_message=failure.message[:1000],
                error_retryable=failure.retryable,
            )
        )

    async def reset_to_pending(self, version_id: VersionId) -> bool:
        result = await self._s.execute(
            update(DocumentVersionRow)
            .where(
                *self._where(version_id),
                DocumentVersionRow.status.in_(
                    [VersionStatus.FAILED.value, VersionStatus.PENDING.value]
                ),
            )
            .values(
                status=VersionStatus.PENDING.value,
                attempts=0,
                error_code=None,
                error_message=None,
                error_retryable=None,
            )
            .returning(DocumentVersionRow.id)
        )
        return result.scalar_one_or_none() is not None

    async def archive(self, version_id: VersionId, *, now: datetime) -> None:
        await self._s.execute(
            update(DocumentVersionRow)
            .where(*self._where(version_id), DocumentVersionRow.archived_at.is_(None))
            .values(archived_at=now)
        )

    async def mark_vectors_deactivated(self, version_id: VersionId, *, now: datetime) -> None:
        await self._s.execute(
            update(DocumentVersionRow)
            .where(*self._where(version_id))
            .values(vectors_deactivated_at=now)
        )


class _Chunks:
    def __init__(self, session: AsyncSession, context: _Context) -> None:
        self._s = session
        self._ctx = context

    async def add_many(self, chunks: Sequence[Chunk]) -> None:
        if not chunks:
            return
        await self._s.execute(
            insert(ChunkRow),
            [
                {
                    "id": c.id,
                    "vector_id": c.vector_id,
                    "tenant_id": c.tenant_id,
                    "department_id": c.department_id,
                    "document_id": c.document_id,
                    "document_version_id": c.version_id,
                    "chunk_index": c.chunk_index,
                    "text": c.text,
                    "text_hash": c.text_hash,
                    "heading_path": list(c.heading_path),
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "token_count": c.token_count,
                }
                for c in chunks
            ],
        )

    async def count_for_version(self, version_id: VersionId) -> int:
        count = await self._s.scalar(
            select(func.count())
            .select_from(ChunkRow)
            .where(
                ChunkRow.document_version_id == version_id,
                ChunkRow.tenant_id == self._ctx.tenant_id,
            )
        )
        return int(count or 0)
