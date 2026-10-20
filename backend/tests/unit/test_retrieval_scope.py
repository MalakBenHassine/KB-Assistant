"""The only door to a RetrievalScope (ARCHITECTURE §6.2): it narrows, it never widens."""

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from kb_assistant.application.answering.service import retrieval_scope
from kb_assistant.domain.errors import ForbiddenError, PolicyViolationError
from kb_assistant.domain.ids import DepartmentId, MembershipId, TenantId, UserId
from kb_assistant.domain.models import Role
from kb_assistant.domain.principal import Principal

TENANT = TenantId(uuid4())
HR, ENG, LEGAL = (DepartmentId(uuid4()) for _ in range(3))


class _Departments:
    async def list_all(self) -> list[Any]:
        return [SimpleNamespace(id=d) for d in (HR, ENG, LEGAL)]


UOW: Any = SimpleNamespace(departments=_Departments())


def principal(role: Role, *departments: DepartmentId) -> Principal:
    return Principal(
        user_id=UserId(uuid4()),
        tenant_id=TENANT,
        membership_id=MembershipId(uuid4()),
        role=role,
        department_ids=frozenset(departments),
    )


async def test_by_default_an_employee_searches_their_departments() -> None:
    scope = await retrieval_scope(UOW, principal(Role.EMPLOYEE, HR, ENG), None)
    assert scope.tenant_id == TENANT
    assert scope.department_ids == {HR, ENG}


async def test_a_tenant_admin_searches_every_department() -> None:
    scope = await retrieval_scope(UOW, principal(Role.TENANT_ADMIN), None)
    assert scope.department_ids == {HR, ENG, LEGAL}


async def test_a_dept_admin_reads_like_an_employee() -> None:
    scope = await retrieval_scope(UOW, principal(Role.DEPT_ADMIN, ENG), None)
    assert scope.department_ids == {ENG}


async def test_a_request_can_narrow_the_scope() -> None:
    scope = await retrieval_scope(UOW, principal(Role.EMPLOYEE, HR, ENG), frozenset({ENG}))
    assert scope.department_ids == {ENG}


@pytest.mark.parametrize("requested", [{LEGAL}, {HR, LEGAL}])
async def test_a_request_can_never_widen_the_scope(requested: set[DepartmentId]) -> None:
    with pytest.raises(ForbiddenError):
        await retrieval_scope(UOW, principal(Role.EMPLOYEE, HR, ENG), frozenset(requested))


async def test_an_empty_request_is_rejected_rather_than_guessed() -> None:
    with pytest.raises(PolicyViolationError):
        await retrieval_scope(UOW, principal(Role.EMPLOYEE, HR), frozenset())


async def test_a_member_of_no_department_gets_an_empty_scope() -> None:
    scope = await retrieval_scope(UOW, principal(Role.EMPLOYEE), None)
    assert scope.department_ids == frozenset()
