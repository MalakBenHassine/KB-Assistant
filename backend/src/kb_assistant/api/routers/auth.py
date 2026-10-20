from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status

from kb_assistant.api.deps import (
    ContainerDep,
    PrincipalDep,
    UowDep,
    client_ip,
    public_endpoint,
)
from kb_assistant.api.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    SwitchTenantRequest,
    TokenResponse,
)
from kb_assistant.application.auth import Login, Logout, Refresh, Register, SwitchTenant
from kb_assistant.domain.ids import TenantId

router = APIRouter(prefix="/v1/auth", tags=["auth"])
public = APIRouter(prefix="/v1/auth", tags=["auth"], dependencies=[Depends(public_endpoint)])

_UNAUTHORIZED: dict[int | str, dict[str, Any]] = {401: {"description": "Not authenticated"}}


@public.post("/login", response_model=TokenResponse, responses=_UNAUTHORIZED)
async def login(
    body: LoginRequest, request: Request, uow: UowDep, c: ContainerDep
) -> TokenResponse:
    pair = await Login(uow, c.hasher, c.tokens, c.clock, c.auth_policy).execute(
        email=body.email,
        password=body.password,
        tenant_id=TenantId(body.tenant_id) if body.tenant_id else None,
        ip=client_ip(request),
    )
    return TokenResponse.of(pair)


@public.post("/refresh", response_model=TokenResponse, responses=_UNAUTHORIZED)
async def refresh(
    body: RefreshRequest, request: Request, uow: UowDep, c: ContainerDep
) -> TokenResponse:
    pair = await Refresh(uow, c.tokens, c.clock, c.auth_policy).execute(
        refresh_token=body.refresh_token, ip=client_ip(request)
    )
    return TokenResponse.of(pair)


@public.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, request: Request, uow: UowDep, c: ContainerDep) -> Response:
    await Logout(uow, c.clock).execute(refresh_token=body.refresh_token, ip=client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@public.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, request: Request, uow: UowDep, c: ContainerDep
) -> TokenResponse:
    pair = await Register(uow, c.hasher, c.tokens, c.clock, c.auth_policy).execute(
        invitation_token=body.invitation_token, password=body.password, ip=client_ip(request)
    )
    return TokenResponse.of(pair)


@router.post("/switch-tenant", response_model=TokenResponse, responses=_UNAUTHORIZED)
async def switch_tenant(
    body: SwitchTenantRequest,
    request: Request,
    principal: PrincipalDep,
    uow: UowDep,
    c: ContainerDep,
) -> TokenResponse:
    pair = await SwitchTenant(uow, c.tokens, c.clock, c.auth_policy).execute(
        principal, TenantId(body.tenant_id), client_ip(request)
    )
    return TokenResponse.of(pair)
