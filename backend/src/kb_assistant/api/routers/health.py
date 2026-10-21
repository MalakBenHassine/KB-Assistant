"""Probes for the orchestrator.

Liveness answers "is the process alive?" and checks nothing else: restarting a
healthy API because Postgres is down would only add load. Readiness answers
"should traffic come here?" and checks the dependencies, reporting booleans
only, never connection strings or error messages.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from kb_assistant.api.deps import ContainerDep, public_endpoint
from kb_assistant.api.schemas import HealthOut

router = APIRouter(tags=["health"], dependencies=[Depends(public_endpoint)])


@router.get("/healthz", response_model=HealthOut)
async def liveness() -> HealthOut:
    return HealthOut(status="ok")


@router.get("/metrics", include_in_schema=False)
async def metrics(container: ContainerDep) -> Response:
    """Prometheus scrape. Not routed by the ingress: only the cluster reaches it."""
    body, media_type = container.telemetry.exposition()
    return Response(body, media_type=media_type)


@router.get("/readyz", response_model=HealthOut, responses={503: {"model": HealthOut}})
async def readiness(container: ContainerDep) -> JSONResponse:
    result = await container.readiness()
    body = HealthOut(
        status="ok" if result.ready else "unavailable",
        checks={"database": result.database, "redis": result.redis},
    )
    return JSONResponse(body.model_dump(), status_code=200 if result.ready else 503)
