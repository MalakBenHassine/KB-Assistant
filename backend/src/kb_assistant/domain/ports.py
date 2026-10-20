"""Ports: what the application needs from the outside world.

Infrastructure implements these Protocols; tests implement them with fakes.
The application layer never imports an adapter.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from kb_assistant.domain.ids import (
    DepartmentId,
    MembershipId,
    RefreshTokenId,
    TenantId,
    TokenFamilyId,
    UserId,
)
from kb_assistant.domain.models import (
    AuditEvent,
    Department,
    Invitation,
    Membership,
    RefreshToken,
    Tenant,
    User,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class PasswordHasher(Protocol):
    """Async because a good hash is deliberately slow: it must not block the event loop."""

    async def hash(self, password: str) -> str: ...

    async def verify(self, password_hash: str, password: str) -> bool: ...

    async def verify_dummy(self, password: str) -> None:
        """Spend the same time as a real verification (no timing oracle on unknown emails)."""
        ...

    def needs_rehash(self, password_hash: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: UserId
    tenant_id: TenantId
    token_version: int


class AccessTokenService(Protocol):
    def issue(self, *, user_id: UserId, tenant_id: TenantId, token_version: int) -> str: ...

    def verify(self, token: str) -> AccessClaims:
        """Raise `NotAuthenticatedError` for any invalid token, whatever the reason."""
        ...

    @property
    def access_ttl_seconds(self) -> int: ...

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        """Public verification keys, published at /.well-known/jwks.json."""
        ...


class TenantRepository(Protocol):
    async def get(self, tenant_id: TenantId) -> Tenant | None: ...

    async def get_many(self, tenant_ids: Sequence[TenantId]) -> list[Tenant]: ...


class UserRepository(Protocol):
    async def get(self, user_id: UserId) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def add(self, user: User) -> None: ...

    async def set_password_hash(self, user_id: UserId, password_hash: str) -> None: ...


class MembershipRepository(Protocol):
    async def get(self, membership_id: MembershipId) -> Membership | None:
        """Within the bound tenant only."""
        ...

    async def get_for_user(self, user_id: UserId, tenant_id: TenantId) -> Membership | None: ...

    async def list_for_tenant(self) -> list[Membership]: ...

    async def list_active_tenants_of_user(self, user_id: UserId) -> list[TenantId]:
        """Every tenant the user belongs to, most recently used first.

        Only works inside `UnitOfWork.allow_cross_tenant_identity` (login, /me).
        """
        ...

    async def add(self, membership: Membership) -> None: ...

    async def touch(self, membership_id: MembershipId, at: datetime) -> None: ...


class DepartmentRepository(Protocol):
    async def list_all(self) -> list[Department]: ...

    async def get_many(self, department_ids: Sequence[DepartmentId]) -> list[Department]: ...

    async def get_by_slug(self, slug: str) -> Department | None: ...

    async def add(self, department: Department) -> None: ...


class InvitationRepository(Protocol):
    async def get_by_token_hash_for_update(self, token_hash: str) -> Invitation | None: ...

    async def add(self, invitation: Invitation) -> None: ...

    async def mark_accepted(self, invitation: Invitation, at: datetime) -> None: ...


class RefreshTokenRepository(Protocol):
    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None: ...

    async def add(self, token: RefreshToken) -> None: ...

    async def mark_rotated(
        self, token_id: RefreshTokenId, replaced_by: RefreshTokenId, at: datetime
    ) -> None: ...

    async def revoke_family(self, family_id: TokenFamilyId, at: datetime) -> int: ...


class AuditLog(Protocol):
    async def record(self, event: AuditEvent) -> None: ...


class UnitOfWork(Protocol):
    """One database transaction, with the row-level-security context bound to it.

    `bind` must be called before touching tenant-scoped data; Postgres RLS
    returns nothing otherwise, which is the point (ARCHITECTURE §6.3).
    Committing ends the unit of work: a use case commits once, at the end.
    """

    @property
    def tenants(self) -> TenantRepository: ...

    @property
    def users(self) -> UserRepository: ...

    @property
    def memberships(self) -> MembershipRepository: ...

    @property
    def departments(self) -> DepartmentRepository: ...

    @property
    def invitations(self) -> InvitationRepository: ...

    @property
    def refresh_tokens(self) -> RefreshTokenRepository: ...

    @property
    def audit(self) -> AuditLog: ...

    async def bind(self, *, tenant_id: TenantId | None, user_id: UserId | None) -> None:
        """Scope the transaction to one tenant; clears any wider grant made before."""
        ...

    async def allow_cross_tenant_identity(self, user_id: UserId) -> None:
        """Let this transaction see `user_id`'s memberships in every tenant."""
        ...

    async def allow_token_lookup(self, token_hash: str) -> None:
        """Let this transaction find the refresh token or invitation with this hash."""
        ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
