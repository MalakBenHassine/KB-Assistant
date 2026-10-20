"""Entities and value objects.

Frozen dataclasses: an entity changes through a repository call that the use
case makes explicitly, never by mutating an object that something else holds.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from kb_assistant.domain.ids import (
    DepartmentId,
    InvitationId,
    MembershipId,
    RefreshTokenId,
    TenantId,
    TokenFamilyId,
    UserId,
)


class Role(StrEnum):
    EMPLOYEE = "employee"
    DEPT_ADMIN = "dept_admin"
    TENANT_ADMIN = "tenant_admin"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def at_least(self, other: "Role") -> bool:
        return self.rank >= other.rank


_ROLE_RANK = {Role.EMPLOYEE: 0, Role.DEPT_ADMIN: 1, Role.TENANT_ADMIN: 2}


@dataclass(frozen=True, slots=True)
class Tenant:
    id: TenantId
    name: str
    slug: str


@dataclass(frozen=True, slots=True)
class User:
    """A person with credentials. Global: carries no tenant, role or department (ADR-0012)."""

    id: UserId
    email: str
    password_hash: str | None
    is_active: bool
    token_version: int


@dataclass(frozen=True, slots=True)
class Department:
    id: DepartmentId
    tenant_id: TenantId
    slug: str
    name: str


@dataclass(frozen=True, slots=True)
class Membership:
    """A user's role and departments in one tenant."""

    id: MembershipId
    user_id: UserId
    tenant_id: TenantId
    role: Role
    is_active: bool
    department_ids: frozenset[DepartmentId]


@dataclass(frozen=True, slots=True)
class Invitation:
    id: InvitationId
    tenant_id: TenantId
    email: str
    role: Role
    department_ids: frozenset[DepartmentId]
    token_hash: str
    expires_at: datetime
    accepted_at: datetime | None
    invited_by: MembershipId

    def is_usable(self, now: datetime) -> bool:
        return self.accepted_at is None and now < self.expires_at


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """One link in a rotation chain. Only the SHA-256 of the secret is stored."""

    id: RefreshTokenId
    family_id: TokenFamilyId
    user_id: UserId
    tenant_id: TenantId
    membership_id: MembershipId
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    family_expires_at: datetime
    revoked_at: datetime | None
    replaced_by: RefreshTokenId | None

    @property
    def was_rotated(self) -> bool:
        return self.replaced_by is not None

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at or now >= self.family_expires_at


@dataclass(frozen=True, slots=True)
class AuditEvent:
    tenant_id: TenantId
    action: str
    actor_user_id: UserId | None
    target: str | None = None
    ip: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
