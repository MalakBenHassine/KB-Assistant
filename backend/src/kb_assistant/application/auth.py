"""Authentication use cases: login, refresh, logout, register, switch tenant.

Refresh tokens rotate on every use and belong to a family. Presenting a token
that was already rotated means two parties hold the same family: the legitimate
client and a thief. We cannot tell which is which, so the whole family is
revoked and the user logs in again (ADR-0005).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from kb_assistant.application.secrets import hash_secret, new_secret
from kb_assistant.domain.errors import (
    ConflictError,
    InvalidCredentialsError,
    InvalidInvitationError,
    NotAuthenticatedError,
    NotFoundError,
    PolicyViolationError,
)
from kb_assistant.domain.ids import (
    MembershipId,
    RefreshTokenId,
    TenantId,
    TokenFamilyId,
    UserId,
    new_id,
)
from kb_assistant.domain.models import AuditEvent, Membership, RefreshToken, User
from kb_assistant.domain.ports import (
    AccessClaims,
    AccessTokenService,
    Clock,
    PasswordHasher,
    UnitOfWork,
)
from kb_assistant.domain.principal import Principal

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256


@dataclass(frozen=True, slots=True)
class AuthPolicy:
    refresh_idle_ttl: timedelta
    refresh_absolute_ttl: timedelta
    invitation_ttl: timedelta


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    tenant_id: TenantId


def normalize_email(email: str) -> str:
    return email.strip().lower()


def check_password_policy(password: str, email: str) -> None:
    """Length over composition rules, as NIST SP 800-63B recommends."""
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise PolicyViolationError(
            f"Passwords must be {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters long."
        )
    if password.strip().lower() == normalize_email(email):
        raise PolicyViolationError("The password must not be the email address.")


class _TokenIssuer:
    """Shared by every use case that ends with a fresh token pair."""

    def __init__(
        self,
        uow: UnitOfWork,
        tokens: AccessTokenService,
        clock: Clock,
        policy: AuthPolicy,
    ) -> None:
        self._uow = uow
        self._tokens = tokens
        self._clock = clock
        self._policy = policy

    async def issue(
        self,
        user: User,
        membership: Membership,
        *,
        family: tuple[TokenFamilyId, datetime] | None = None,
    ) -> tuple[TokenPair, RefreshTokenId]:
        now = self._clock.now()
        family_id, family_expires_at = family or (
            TokenFamilyId(new_id()),
            now + self._policy.refresh_absolute_ttl,
        )
        raw = new_secret()
        token_id = RefreshTokenId(new_id())
        await self._uow.refresh_tokens.add(
            RefreshToken(
                id=token_id,
                family_id=family_id,
                user_id=user.id,
                tenant_id=membership.tenant_id,
                membership_id=membership.id,
                token_hash=hash_secret(raw),
                issued_at=now,
                expires_at=min(now + self._policy.refresh_idle_ttl, family_expires_at),
                family_expires_at=family_expires_at,
                revoked_at=None,
                replaced_by=None,
            )
        )
        await self._uow.memberships.touch(membership.id, now)
        access = self._tokens.issue(
            user_id=user.id, tenant_id=membership.tenant_id, token_version=user.token_version
        )
        pair = TokenPair(
            access_token=access,
            refresh_token=raw,
            expires_in=self._tokens.access_ttl_seconds,
            tenant_id=membership.tenant_id,
        )
        return pair, token_id


class Login:
    def __init__(
        self,
        uow: UnitOfWork,
        hasher: PasswordHasher,
        tokens: AccessTokenService,
        clock: Clock,
        policy: AuthPolicy,
    ) -> None:
        self._uow = uow
        self._hasher = hasher
        self._issuer = _TokenIssuer(uow, tokens, clock, policy)

    async def execute(
        self, *, email: str, password: str, tenant_id: TenantId | None, ip: str | None
    ) -> TokenPair:
        user = await self._uow.users.get_by_email(normalize_email(email))
        if user is None or user.password_hash is None:
            await self._hasher.verify_dummy(password)
            raise InvalidCredentialsError()
        # Verify first: skipping the hash for inactive users would be a timing oracle.
        password_ok = await self._hasher.verify(user.password_hash, password)
        if not password_ok or not user.is_active:
            raise InvalidCredentialsError()

        membership = await self._choose_membership(user.id, tenant_id)
        if self._hasher.needs_rehash(user.password_hash):
            await self._uow.users.set_password_hash(user.id, await self._hasher.hash(password))

        pair, _ = await self._issuer.issue(user, membership)
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=membership.tenant_id, action="auth.login", actor_user_id=user.id, ip=ip
            )
        )
        await self._uow.commit()
        return pair

    async def _choose_membership(self, user_id: UserId, requested: TenantId | None) -> Membership:
        await self._uow.allow_cross_tenant_identity(user_id)
        tenants = await self._uow.memberships.list_active_tenants_of_user(user_id)
        if requested is not None:
            tenants = [t for t in tenants if t == requested]
        if not tenants:
            # No usable membership: same answer as a wrong password.
            raise InvalidCredentialsError()
        tenant_id = tenants[0]
        await self._uow.bind(tenant_id=tenant_id, user_id=user_id)
        membership = await self._uow.memberships.get_for_user(user_id, tenant_id)
        if membership is None or not membership.is_active:
            raise InvalidCredentialsError()
        return membership


class Refresh:
    def __init__(
        self, uow: UnitOfWork, tokens: AccessTokenService, clock: Clock, policy: AuthPolicy
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._issuer = _TokenIssuer(uow, tokens, clock, policy)

    async def execute(self, *, refresh_token: str, ip: str | None) -> TokenPair:
        token_hash = hash_secret(refresh_token)
        await self._uow.allow_token_lookup(token_hash)
        current = await self._uow.refresh_tokens.get_by_hash_for_update(token_hash)
        if current is None:
            raise NotAuthenticatedError("The session has expired. Please sign in again.")
        await self._uow.bind(tenant_id=current.tenant_id, user_id=current.user_id)
        now = self._clock.now()

        if current.revoked_at is not None:
            if current.was_rotated:
                await self._uow.refresh_tokens.revoke_family(current.family_id, now)
                await self._uow.audit.record(
                    AuditEvent(
                        tenant_id=current.tenant_id,
                        action="auth.refresh_token_reuse_detected",
                        actor_user_id=current.user_id,
                        ip=ip,
                        details={"family_id": str(current.family_id)},
                    )
                )
                # Commit the revocation before refusing: the error must not roll it back.
                await self._uow.commit()
            raise NotAuthenticatedError("The session has expired. Please sign in again.")

        if current.is_expired(now):
            raise NotAuthenticatedError("The session has expired. Please sign in again.")

        user = await self._uow.users.get(current.user_id)
        membership = await self._uow.memberships.get_for_user(current.user_id, current.tenant_id)
        if user is None or not user.is_active or membership is None or not membership.is_active:
            await self._uow.refresh_tokens.revoke_family(current.family_id, now)
            await self._uow.commit()
            raise NotAuthenticatedError("The session has expired. Please sign in again.")

        pair, new_token_id = await self._issuer.issue(
            user, membership, family=(current.family_id, current.family_expires_at)
        )
        await self._uow.refresh_tokens.mark_rotated(current.id, new_token_id, now)
        await self._uow.commit()
        return pair


class Logout:
    """Revoke the whole family. Idempotent: an unknown token is not an error."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, *, refresh_token: str, ip: str | None) -> None:
        token_hash = hash_secret(refresh_token)
        await self._uow.allow_token_lookup(token_hash)
        current = await self._uow.refresh_tokens.get_by_hash_for_update(token_hash)
        if current is None:
            return
        await self._uow.bind(tenant_id=current.tenant_id, user_id=current.user_id)
        await self._uow.refresh_tokens.revoke_family(current.family_id, self._clock.now())
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=current.tenant_id,
                action="auth.logout",
                actor_user_id=current.user_id,
                ip=ip,
            )
        )
        await self._uow.commit()


class Register:
    """Accept an invitation: create the user if needed, then the membership.

    If the email already has an account, the caller must prove they own it with
    its current password; an invitation alone must not let someone take over an
    existing identity.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        hasher: PasswordHasher,
        tokens: AccessTokenService,
        clock: Clock,
        policy: AuthPolicy,
    ) -> None:
        self._uow = uow
        self._hasher = hasher
        self._clock = clock
        self._issuer = _TokenIssuer(uow, tokens, clock, policy)

    async def execute(self, *, invitation_token: str, password: str, ip: str | None) -> TokenPair:
        token_hash = hash_secret(invitation_token)
        await self._uow.allow_token_lookup(token_hash)
        invitation = await self._uow.invitations.get_by_token_hash_for_update(token_hash)
        now = self._clock.now()
        if invitation is None or not invitation.is_usable(now):
            raise InvalidInvitationError()
        await self._uow.bind(tenant_id=invitation.tenant_id, user_id=None)

        user = await self._uow.users.get_by_email(invitation.email)
        if user is None:
            check_password_policy(password, invitation.email)
            user = User(
                id=UserId(new_id()),
                email=invitation.email,
                password_hash=await self._hasher.hash(password),
                is_active=True,
                token_version=0,
            )
            await self._uow.users.add(user)
        elif (
            user.password_hash is None
            or not user.is_active
            or not await self._hasher.verify(user.password_hash, password)
        ):
            raise InvalidCredentialsError()

        await self._uow.bind(tenant_id=invitation.tenant_id, user_id=user.id)
        if await self._uow.memberships.get_for_user(user.id, invitation.tenant_id) is not None:
            raise ConflictError("You are already a member of this organization.")

        departments = await self._uow.departments.get_many(sorted(invitation.department_ids))
        membership = Membership(
            id=MembershipId(new_id()),
            user_id=user.id,
            tenant_id=invitation.tenant_id,
            role=invitation.role,
            is_active=True,
            # A department deleted since the invitation was sent is silently dropped.
            department_ids=frozenset(d.id for d in departments),
        )
        await self._uow.memberships.add(membership)
        await self._uow.invitations.mark_accepted(invitation, now)
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=invitation.tenant_id,
                action="membership.created",
                actor_user_id=user.id,
                target=str(membership.id),
                ip=ip,
                details={"role": membership.role.value, "invitation_id": str(invitation.id)},
            )
        )
        pair, _ = await self._issuer.issue(user, membership)
        await self._uow.commit()
        return pair


class SwitchTenant:
    """Mint a token pair for another of the caller's memberships (ADR-0012)."""

    def __init__(
        self, uow: UnitOfWork, tokens: AccessTokenService, clock: Clock, policy: AuthPolicy
    ) -> None:
        self._uow = uow
        self._issuer = _TokenIssuer(uow, tokens, clock, policy)

    async def execute(self, principal: Principal, target: TenantId, ip: str | None) -> TokenPair:
        user = await self._uow.users.get(principal.user_id)
        await self._uow.bind(tenant_id=target, user_id=principal.user_id)
        membership = await self._uow.memberships.get_for_user(principal.user_id, target)
        if user is None or membership is None or not membership.is_active:
            # Not a member there: indistinguishable from a tenant that does not exist.
            raise NotFoundError("Organization not found.")
        pair, _ = await self._issuer.issue(user, membership)
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=target,
                action="auth.switch_tenant",
                actor_user_id=user.id,
                ip=ip,
                details={"from_tenant_id": str(principal.tenant_id)},
            )
        )
        await self._uow.commit()
        return pair


class ResolvePrincipal:
    """Turn verified token claims into a `Principal`, from the database (ADR-0005).

    Role and departments are read from the membership on every request, so a
    demotion or a removal takes effect immediately, not when the token expires.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, claims: AccessClaims) -> Principal:
        await self._uow.bind(tenant_id=claims.tenant_id, user_id=claims.user_id)
        user = await self._uow.users.get(claims.user_id)
        if user is None or not user.is_active or user.token_version != claims.token_version:
            raise NotAuthenticatedError("Invalid or expired access token.")
        membership = await self._uow.memberships.get_for_user(claims.user_id, claims.tenant_id)
        if membership is None or not membership.is_active:
            raise NotAuthenticatedError("Invalid or expired access token.")
        return Principal(
            user_id=user.id,
            tenant_id=membership.tenant_id,
            membership_id=membership.id,
            role=membership.role,
            department_ids=membership.department_ids,
        )
