from uuid import UUID

from fastapi import APIRouter, status

from kb_assistant.api.deps import ContainerDep, PrincipalDep, UowDep
from kb_assistant.api.schemas import (
    DepartmentCreate,
    DepartmentOut,
    InvitationCreate,
    InvitationOut,
    MembershipOut,
    MeOut,
    Page,
)
from kb_assistant.application.tenancy import (
    CreateDepartment,
    CreateInvitation,
    GetMembership,
    GetProfile,
    ListDepartments,
    ListMemberships,
)
from kb_assistant.domain.ids import DepartmentId, MembershipId

router = APIRouter(prefix="/v1", tags=["tenancy"])


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, uow: UowDep) -> MeOut:
    return MeOut.of(await GetProfile(uow).execute(principal))


@router.get("/departments", response_model=Page[DepartmentOut])
async def list_departments(principal: PrincipalDep, uow: UowDep) -> Page[DepartmentOut]:
    departments = await ListDepartments(uow).execute(principal)
    return Page(items=[DepartmentOut.of(d) for d in departments])


@router.post("/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
async def create_department(
    body: DepartmentCreate, principal: PrincipalDep, uow: UowDep
) -> DepartmentOut:
    department = await CreateDepartment(uow).execute(principal, slug=body.slug, name=body.name)
    return DepartmentOut.of(department)


@router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    body: InvitationCreate, principal: PrincipalDep, uow: UowDep, c: ContainerDep
) -> InvitationOut:
    issued = await CreateInvitation(uow, c.clock, c.auth_policy).execute(
        principal,
        email=body.email,
        role=body.role,
        department_ids=frozenset(DepartmentId(d) for d in body.department_ids),
    )
    return InvitationOut.of(issued)


@router.get("/memberships", response_model=Page[MembershipOut])
async def list_memberships(principal: PrincipalDep, uow: UowDep) -> Page[MembershipOut]:
    memberships = await ListMemberships(uow).execute(principal)
    return Page(items=[MembershipOut.of(m) for m in memberships])


@router.get("/memberships/{membership_id}", response_model=MembershipOut)
async def get_membership(
    membership_id: UUID, principal: PrincipalDep, uow: UowDep
) -> MembershipOut:
    membership = await GetMembership(uow).execute(principal, MembershipId(membership_id))
    return MembershipOut.of(membership)
