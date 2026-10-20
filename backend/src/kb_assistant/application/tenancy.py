"""Use cases inside one tenant: departments, invitations, memberships, profile.

Every use case receives a `UnitOfWork` already bound to the principal's tenant,
so Postgres row-level security backs up every check made here.
"""

from dataclasses import dataclass

from kb_assistant.application.auth import AuthPolicy, normalize_email
from kb_assistant.application.authorization import ensure_can_invite, require_role
from kb_assistant.application.secrets import hash_secret, new_secret
from kb_assistant.domain.errors import ConflictError, NotFoundError, PolicyViolationError
from kb_assistant.domain.ids import DepartmentId, InvitationId, MembershipId, new_id
from kb_assistant.domain.models import (
    AuditEvent,
    Department,
    Invitation,
    Membership,
    Role,
    Tenant,
)
from kb_assistant.domain.ports import Clock, UnitOfWork
from kb_assistant.domain.principal import Principal


class CreateDepartment:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, principal: Principal, *, slug: str, name: str) -> Department:
        require_role(principal, Role.TENANT_ADMIN)
        if await self._uow.departments.get_by_slug(slug) is not None:
            raise ConflictError("A department with this identifier already exists.")
        department = Department(
            id=DepartmentId(new_id()), tenant_id=principal.tenant_id, slug=slug, name=name
        )
        await self._uow.departments.add(department)
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=principal.tenant_id,
                action="department.created",
                actor_user_id=principal.user_id,
                target=str(department.id),
            )
        )
        await self._uow.commit()
        return department


class ListDepartments:
    """Admins see every department; other members see the ones they belong to."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, principal: Principal) -> list[Department]:
        departments = await self._uow.departments.list_all()
        if principal.role is Role.TENANT_ADMIN:
            return departments
        return [d for d in departments if d.id in principal.department_ids]


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    invitation: Invitation
    token: str


class CreateInvitation:
    """Invite someone into the tenant with a role and departments.

    v1 has no mail delivery, so the one-time token is returned to the inviting
    admin, over HTTPS, exactly once. Only its hash is stored.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, policy: AuthPolicy) -> None:
        self._uow = uow
        self._clock = clock
        self._policy = policy

    async def execute(
        self,
        principal: Principal,
        *,
        email: str,
        role: Role,
        department_ids: frozenset[DepartmentId],
    ) -> IssuedInvitation:
        ensure_can_invite(principal, role, department_ids)
        found = await self._uow.departments.get_many(sorted(department_ids))
        if len(found) != len(department_ids):
            # Unknown, or another tenant's: RLS made them invisible either way.
            raise PolicyViolationError("One or more departments do not exist.")

        email = normalize_email(email)
        existing = await self._uow.users.get_by_email(email)
        if existing is not None:
            membership = await self._uow.memberships.get_for_user(existing.id, principal.tenant_id)
            if membership is not None:
                raise ConflictError("This person is already a member of the organization.")

        now = self._clock.now()
        token = new_secret()
        invitation = Invitation(
            id=InvitationId(new_id()),
            tenant_id=principal.tenant_id,
            email=email,
            role=role,
            department_ids=department_ids,
            token_hash=hash_secret(token),
            expires_at=now + self._policy.invitation_ttl,
            accepted_at=None,
            invited_by=principal.membership_id,
        )
        await self._uow.invitations.add(invitation)
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=principal.tenant_id,
                action="invitation.created",
                actor_user_id=principal.user_id,
                target=str(invitation.id),
                details={"role": role.value},
            )
        )
        await self._uow.commit()
        return IssuedInvitation(invitation=invitation, token=token)


class GetMembership:
    """A member may read their own membership; a tenant admin may read any in the tenant.

    Anything else is a 404, never a 403, so membership IDs cannot be probed.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, principal: Principal, membership_id: MembershipId) -> Membership:
        membership = await self._uow.memberships.get(membership_id)
        if membership is None or membership.tenant_id != principal.tenant_id:
            raise NotFoundError("Membership not found.")
        if principal.role is not Role.TENANT_ADMIN and membership.id != principal.membership_id:
            raise NotFoundError("Membership not found.")
        return membership


class ListMemberships:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, principal: Principal) -> list[Membership]:
        require_role(principal, Role.TENANT_ADMIN)
        return await self._uow.memberships.list_for_tenant()


@dataclass(frozen=True, slots=True)
class Profile:
    email: str
    tenant: Tenant
    membership: Membership
    departments: list[Department]
    other_tenants: list[Tenant]


class GetProfile:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, principal: Principal) -> Profile:
        user = await self._uow.users.get(principal.user_id)
        tenant = await self._uow.tenants.get(principal.tenant_id)
        membership = await self._uow.memberships.get(principal.membership_id)
        if user is None or tenant is None or membership is None:
            raise NotFoundError("Profile not found.")
        departments = await self._uow.departments.get_many(sorted(membership.department_ids))

        await self._uow.allow_cross_tenant_identity(principal.user_id)
        tenant_ids = await self._uow.memberships.list_active_tenants_of_user(principal.user_id)
        others = await self._uow.tenants.get_many(
            [t for t in tenant_ids if t != principal.tenant_id]
        )
        return Profile(
            email=user.email,
            tenant=tenant,
            membership=membership,
            departments=sorted(departments, key=lambda d: d.name),
            other_tenants=sorted(others, key=lambda t: t.name),
        )
