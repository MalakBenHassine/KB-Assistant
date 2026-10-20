"""The authenticated caller, as resolved by the server."""

from dataclasses import dataclass

from kb_assistant.domain.ids import DepartmentId, MembershipId, TenantId, UserId
from kb_assistant.domain.models import Role


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling, in which tenant, with which rights.

    Built only from a verified access token **plus** the membership row read from
    the database on this request (ADR-0005). Nothing here comes from the request
    body, so a client cannot claim a tenant, a role or a department.
    """

    user_id: UserId
    tenant_id: TenantId
    membership_id: MembershipId
    role: Role
    department_ids: frozenset[DepartmentId]

    def manages_department(self, department_id: DepartmentId) -> bool:
        if self.role is Role.TENANT_ADMIN:
            return True
        return self.role is Role.DEPT_ADMIN and department_id in self.department_ids
