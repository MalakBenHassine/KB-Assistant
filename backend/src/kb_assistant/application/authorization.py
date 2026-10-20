"""Authorization policies.

Every rule about who may do what lives here, as plain functions over a
`Principal`, so it is unit-tested without HTTP or a database. Use cases call
these before touching data; routers never decide anything.
"""

from collections.abc import Iterable

from kb_assistant.domain.errors import ForbiddenError
from kb_assistant.domain.ids import DepartmentId
from kb_assistant.domain.models import Role
from kb_assistant.domain.principal import Principal


def require_role(principal: Principal, minimum: Role) -> None:
    if not principal.role.at_least(minimum):
        raise ForbiddenError("Your role does not allow this action.")


def ensure_can_invite(
    principal: Principal, role: Role, department_ids: Iterable[DepartmentId]
) -> None:
    """Who may invite whom.

    - A tenant admin may invite any role, into any department of the tenant.
    - A department admin may invite employees only, into departments they manage.
      Granting a role at or above one's own would be privilege escalation.
    - An employee may not invite.
    """
    if principal.role is Role.TENANT_ADMIN:
        return
    if principal.role is not Role.DEPT_ADMIN:
        raise ForbiddenError("Your role does not allow inviting members.")
    if role is not Role.EMPLOYEE:
        raise ForbiddenError("A department admin can only invite employees.")
    departments = set(department_ids)
    if not departments:
        raise ForbiddenError("A department admin must invite into one of their departments.")
    if not all(principal.manages_department(d) for d in departments):
        raise ForbiddenError("You can only invite into departments you manage.")
