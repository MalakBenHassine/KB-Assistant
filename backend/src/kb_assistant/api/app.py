"""Application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from kb_assistant.api.errors import install_error_handlers
from kb_assistant.api.middleware import RequestContextMiddleware
from kb_assistant.api.routers import auth, health, jwks, tenancy
from kb_assistant.bootstrap import Container, build_container, configure_runtime
from kb_assistant.settings import Settings


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    if container is None:
        settings = settings or Settings()
        configure_runtime(settings)
        container = build_container(settings)
    resolved = container

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await resolved.aclose()

    docs = resolved.settings.expose_docs
    app = FastAPI(
        title="KB Assistant API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.container = resolved
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(jwks.router)
    app.include_router(auth.public)
    app.include_router(auth.router)
    app.include_router(tenancy.router)
    return app
