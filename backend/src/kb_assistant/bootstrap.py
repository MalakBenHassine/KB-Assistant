"""Composition root: the one place where settings become concrete adapters.

Nothing else instantiates a client, so tests can swap any dependency by
building a `Container` with fakes, and there are no global clients.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from kb_assistant.application.auth import AuthPolicy
from kb_assistant.domain.ports import AccessTokenService, Clock, PasswordHasher, UnitOfWork
from kb_assistant.infrastructure.clock import SystemClock
from kb_assistant.infrastructure.db.engine import build_engine, build_sessionmaker
from kb_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWork
from kb_assistant.infrastructure.observability.logging import configure_logging
from kb_assistant.infrastructure.security.jwt import JwtAccessTokenService, SigningKeys
from kb_assistant.infrastructure.security.passwords import Argon2PasswordHasher
from kb_assistant.settings import Settings, WorkerSettings

READINESS_TIMEOUT_S = 2.0


def configure_runtime(settings: Settings | WorkerSettings) -> None:
    """Process-wide setup shared by the API and the worker."""
    configure_logging(settings.log_level, json=settings.environment != "local")


@dataclass(frozen=True, slots=True)
class Readiness:
    database: bool
    redis: bool

    @property
    def ready(self) -> bool:
        return self.database and self.redis


@dataclass(slots=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis
    tokens: AccessTokenService
    hasher: PasswordHasher
    clock: Clock
    auth_policy: AuthPolicy

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[UnitOfWork]:
        """A transaction; rolled back unless the use case committed."""
        async with self.sessionmaker() as session:
            try:
                yield SqlAlchemyUnitOfWork(session)
            finally:
                await session.rollback()

    async def readiness(self) -> Readiness:
        database, redis = await asyncio.gather(self._database_ok(), self._redis_ok())
        return Readiness(database=database, redis=redis)

    async def _database_ok(self) -> bool:
        try:
            async with asyncio.timeout(READINESS_TIMEOUT_S), self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - any failure means "not ready"; details go to logs
            return False

    async def _redis_ok(self) -> bool:
        try:
            async with asyncio.timeout(READINESS_TIMEOUT_S):
                return bool(await self.redis.ping())
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        await self.redis.aclose()
        await self.engine.dispose()


def build_container(
    settings: Settings,
    *,
    hasher: PasswordHasher | None = None,
    clock: Clock | None = None,
    signing_keys: SigningKeys | None = None,
) -> Container:
    clock = clock or SystemClock()
    engine = build_engine(settings.database, application_name="kb-assistant-api")
    keys = signing_keys or SigningKeys.from_pem_file(
        settings.auth.key_id, settings.auth.private_key_file
    )
    return Container(
        settings=settings,
        engine=engine,
        sessionmaker=build_sessionmaker(engine),
        redis=Redis.from_url(
            settings.redis.url.get_secret_value(),
            socket_timeout=settings.redis.socket_timeout_s,
            socket_connect_timeout=settings.redis.socket_timeout_s,
        ),
        tokens=JwtAccessTokenService(
            keys=keys,
            issuer=settings.auth.issuer,
            audience=settings.auth.audience,
            ttl_seconds=settings.auth.access_ttl_s,
            clock_skew_seconds=settings.auth.clock_skew_s,
            clock=clock,
        ),
        hasher=hasher or Argon2PasswordHasher(),
        clock=clock,
        auth_policy=AuthPolicy(
            refresh_idle_ttl=settings.auth.refresh_idle_ttl,
            refresh_absolute_ttl=settings.auth.refresh_absolute_ttl,
            invitation_ttl=settings.auth.invitation_ttl,
        ),
    )
