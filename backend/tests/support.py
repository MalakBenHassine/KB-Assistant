"""Test helpers shared by unit, integration and end-to-end tests."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncEngine

from kb_assistant.domain.ids import DepartmentId, MembershipId, TenantId, UserId, new_id
from kb_assistant.infrastructure.db.tables import (
    DepartmentRow,
    MembershipDepartmentRow,
    MembershipRow,
    TenantRow,
    UserRow,
)
from kb_assistant.infrastructure.security.passwords import Argon2PasswordHasher

DEFAULT_PASSWORD = "correct horse battery staple"


def fast_hasher() -> Argon2PasswordHasher:
    """Minimum Argon2 cost: tests exercise the logic, not the hardness."""
    return Argon2PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


@dataclass
class FakeClock:
    current: datetime = field(default_factory=lambda: datetime(2026, 10, 20, 12, 0, tzinfo=UTC))

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class Seeder:
    """Creates rows as the schema owner, bypassing the API and row-level security.

    Each test builds its own tenants with unique slugs, so tests never depend on
    each other's data and can share one database.
    """

    def __init__(self, owner_engine: AsyncEngine, password_hash: str) -> None:
        self._engine = owner_engine
        self._password_hash = password_hash

    async def _insert(self, table: Any, **values: Any) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(insert(table).values(**values))

    async def execute(self, sql: str, **params: Any) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(sql), params)

    async def tenant(self, name: str | None = None) -> TenantId:
        tenant_id = TenantId(new_id())
        slug = unique("t")
        await self._insert(TenantRow, id=tenant_id, name=name or slug, slug=slug)
        return tenant_id

    async def department(self, tenant_id: TenantId, slug: str | None = None) -> DepartmentId:
        department_id = DepartmentId(new_id())
        slug = slug or unique("d")
        await self._insert(
            DepartmentRow, id=department_id, tenant_id=tenant_id, slug=slug, name=slug.title()
        )
        return department_id

    async def user(self, email: str | None = None, *, active: bool = True) -> tuple[UserId, str]:
        user_id = UserId(new_id())
        email = email or f"{unique('user')}@example.com"
        await self._insert(
            UserRow, id=user_id, email=email, password_hash=self._password_hash, is_active=active
        )
        return user_id, email

    async def membership(
        self,
        user_id: UserId,
        tenant_id: TenantId,
        role: str = "employee",
        departments: tuple[DepartmentId, ...] = (),
        *,
        active: bool = True,
    ) -> MembershipId:
        membership_id = MembershipId(new_id())
        await self._insert(
            MembershipRow,
            id=membership_id,
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
            is_active=active,
        )
        for department_id in departments:
            await self._insert(
                MembershipDepartmentRow,
                membership_id=membership_id,
                department_id=department_id,
                tenant_id=tenant_id,
            )
        return membership_id

    async def member(
        self,
        tenant_id: TenantId,
        role: str = "employee",
        departments: tuple[DepartmentId, ...] = (),
    ) -> "Member":
        user_id, email = await self.user()
        membership_id = await self.membership(user_id, tenant_id, role, departments)
        return Member(user_id, email, tenant_id, membership_id)


@dataclass(frozen=True)
class Member:
    user_id: UserId
    email: str
    tenant_id: TenantId
    membership_id: MembershipId


def as_uuid(value: str) -> UUID:
    return UUID(value)
