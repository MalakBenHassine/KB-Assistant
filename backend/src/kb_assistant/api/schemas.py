"""Wire format.

Request models forbid unknown fields: a client that sends `tenant_id`,
`role` or `departments` where they are not expected gets a 422, not a silently
ignored (or worse, honoured) value (SECURITY.md, T1 and T3).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from kb_assistant.application.auth import PASSWORD_MAX_LENGTH, TokenPair
from kb_assistant.application.tenancy import IssuedInvitation, Profile
from kb_assistant.domain.models import Department, Membership, Role, Tenant

Password = Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]
OpaqueToken = Annotated[str, Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(frozen=True)


# ----------------------------------------------------------------------------- auth


class LoginRequest(RequestModel):
    email: EmailStr
    password: Password
    tenant_id: UUID | None = Field(
        default=None, description="Optional: which of the user's organizations to sign in to."
    )


class RefreshRequest(RequestModel):
    refresh_token: OpaqueToken


class RegisterRequest(RequestModel):
    invitation_token: OpaqueToken
    password: Password


class SwitchTenantRequest(RequestModel):
    tenant_id: UUID


class TokenResponse(ResponseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - a scheme name, not a secret
    expires_in: int
    tenant_id: UUID

    @classmethod
    def of(cls, pair: TokenPair) -> "TokenResponse":
        return cls(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=pair.expires_in,
            tenant_id=pair.tenant_id,
        )


# ----------------------------------------------------------------------------- tenancy


class TenantOut(ResponseModel):
    id: UUID
    name: str
    slug: str

    @classmethod
    def of(cls, tenant: Tenant) -> "TenantOut":
        return cls(id=tenant.id, name=tenant.name, slug=tenant.slug)


class DepartmentCreate(RequestModel):
    slug: Slug
    name: Name


class DepartmentOut(ResponseModel):
    id: UUID
    slug: str
    name: str

    @classmethod
    def of(cls, department: Department) -> "DepartmentOut":
        return cls(id=department.id, slug=department.slug, name=department.name)


class InvitationCreate(RequestModel):
    email: EmailStr
    role: Role
    department_ids: Annotated[list[UUID], Field(max_length=50)] = []


class InvitationOut(ResponseModel):
    id: UUID
    email: str
    role: Role
    department_ids: list[UUID]
    expires_at: datetime
    token: str = Field(
        description="Shown once. Deliver it to the invitee; only its hash is stored."
    )

    @classmethod
    def of(cls, issued: IssuedInvitation) -> "InvitationOut":
        inv = issued.invitation
        return cls(
            id=inv.id,
            email=inv.email,
            role=inv.role,
            department_ids=sorted(inv.department_ids),
            expires_at=inv.expires_at,
            token=issued.token,
        )


class MembershipOut(ResponseModel):
    id: UUID
    user_id: UUID
    role: Role
    is_active: bool
    department_ids: list[UUID]

    @classmethod
    def of(cls, membership: Membership) -> "MembershipOut":
        return cls(
            id=membership.id,
            user_id=membership.user_id,
            role=membership.role,
            is_active=membership.is_active,
            department_ids=sorted(membership.department_ids),
        )


class MeOut(ResponseModel):
    user_id: UUID
    email: str
    membership_id: UUID
    role: Role
    tenant: TenantOut
    departments: list[DepartmentOut]
    other_tenants: list[TenantOut]

    @classmethod
    def of(cls, profile: Profile) -> "MeOut":
        return cls(
            user_id=profile.membership.user_id,
            email=profile.email,
            membership_id=profile.membership.id,
            role=profile.membership.role,
            tenant=TenantOut.of(profile.tenant),
            departments=[DepartmentOut.of(d) for d in profile.departments],
            other_tenants=[TenantOut.of(t) for t in profile.other_tenants],
        )


class Page[T](ResponseModel):
    """List envelope: room for `next_cursor` without breaking clients."""

    items: list[T]


class HealthOut(ResponseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, bool] = {}
