from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from kb_assistant.settings import DatabaseSettings


def build_engine(settings: DatabaseSettings, *, application_name: str) -> AsyncEngine:
    return create_async_engine(
        settings.url.get_secret_value(),
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout_s,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "application_name": application_name,
                # A runaway query fails fast instead of holding a connection.
                "statement_timeout": str(settings.statement_timeout_ms),
            },
        },
    )


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
