"""Public signing keys, in the same shape an OIDC provider publishes them (ADR-0005)."""

from typing import Any

from fastapi import APIRouter, Depends, Response

from kb_assistant.api.deps import ContainerDep, public_endpoint

router = APIRouter(tags=["auth"], dependencies=[Depends(public_endpoint)])


@router.get("/.well-known/jwks.json")
async def jwks(container: ContainerDep, response: Response) -> dict[str, Any]:
    # Public keys may be cached briefly; rotation keeps the old key published for
    # one access-token lifetime, which is longer than this.
    response.headers["cache-control"] = "public, max-age=300"
    return container.tokens.jwks()
