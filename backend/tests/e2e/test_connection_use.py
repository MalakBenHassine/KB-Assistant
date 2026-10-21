"""Authentication must not hold a database connection for the rest of the request."""

import httpx
from fastapi.security import HTTPAuthorizationCredentials

from kb_assistant.api.deps import current_principal
from kb_assistant.bootstrap import Container
from tests.e2e.helpers import login
from tests.support import Seeder


async def test_resolving_the_principal_returns_its_connection_to_the_pool(
    client: httpx.AsyncClient, container: Container, seeder: Seeder
) -> None:
    tenant = await seeder.tenant()
    member = await seeder.member(tenant, "employee", (await seeder.department(tenant),))
    token = (await login(client, member.email, tenant_id=tenant))["access_token"]
    pool = container.engine.sync_engine.pool
    before = pool.checkedout()  # type: ignore[attr-defined]
    async with container.unit_of_work() as uow:
        principal = await current_principal(
            uow, container, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
        # Still inside the request: a streamed answer could run for many seconds here.
        assert pool.checkedout() == before  # type: ignore[attr-defined]
        # The bound context survives: the next query still sees the tenant's rows.
        assert await uow.conversations.list_for_member(principal.membership_id) == []
