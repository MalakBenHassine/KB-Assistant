from uuid import uuid4

import pytest

from kb_assistant.application.authorization import ensure_can_invite, require_role
from kb_assistant.domain.errors import ForbiddenError
from kb_assistant.domain.ids import DepartmentId, MembershipId, TenantId, UserId
from kb_assistant.domain.models import Role
from kb_assistant.domain.principal import Principal

ENGINEERING = DepartmentId(uuid4())
HR = DepartmentId(uuid4())


def principal(role: Role, *departments: DepartmentId) -> Principal:
    return Principal(
        user_id=UserId(uuid4()),
        tenant_id=TenantId(uuid4()),
        membership_id=MembershipId(uuid4()),
        role=role,
        department_ids=frozenset(departments),
    )


@pytest.mark.parametrize(
    ("role", "minimum", "allowed"),
    [
        (Role.EMPLOYEE, Role.EMPLOYEE, True),
        (Role.EMPLOYEE, Role.DEPT_ADMIN, False),
        (Role.DEPT_ADMIN, Role.TENANT_ADMIN, False),
        (Role.TENANT_ADMIN, Role.DEPT_ADMIN, True),
        (Role.TENANT_ADMIN, Role.TENANT_ADMIN, True),
    ],
)
def test_require_role(role: Role, minimum: Role, allowed: bool) -> None:
    if allowed:
        require_role(principal(role), minimum)
    else:
        with pytest.raises(ForbiddenError):
            require_role(principal(role), minimum)


def test_tenant_admin_may_invite_any_role_anywhere() -> None:
    for role in Role:
        ensure_can_invite(principal(Role.TENANT_ADMIN), role, [ENGINEERING, HR])


def test_employee_may_not_invite() -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_invite(principal(Role.EMPLOYEE, ENGINEERING), Role.EMPLOYEE, [ENGINEERING])


def test_dept_admin_may_invite_employees_into_own_department() -> None:
    ensure_can_invite(principal(Role.DEPT_ADMIN, ENGINEERING), Role.EMPLOYEE, [ENGINEERING])


@pytest.mark.parametrize("role", [Role.DEPT_ADMIN, Role.TENANT_ADMIN])
def test_dept_admin_cannot_grant_a_role_at_or_above_their_own(role: Role) -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_invite(principal(Role.DEPT_ADMIN, ENGINEERING), role, [ENGINEERING])


def test_dept_admin_cannot_invite_into_another_department() -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_invite(principal(Role.DEPT_ADMIN, ENGINEERING), Role.EMPLOYEE, [ENGINEERING, HR])


def test_dept_admin_must_name_a_department() -> None:
    with pytest.raises(ForbiddenError):
        ensure_can_invite(principal(Role.DEPT_ADMIN, ENGINEERING), Role.EMPLOYEE, [])


def test_manages_department() -> None:
    assert principal(Role.TENANT_ADMIN).manages_department(HR)
    assert principal(Role.DEPT_ADMIN, HR).manages_department(HR)
    assert not principal(Role.DEPT_ADMIN, HR).manages_department(ENGINEERING)
    assert not principal(Role.EMPLOYEE, HR).manages_department(HR)
