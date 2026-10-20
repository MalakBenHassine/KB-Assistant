"""Fixtures.

Unit tests need nothing. Integration and end-to-end tests share one PostgreSQL
(with pgvector), one Redis and one MinIO started by testcontainers for the
whole session, migrated once with the real Alembic migrations, and connect
with the real non-owner application role, so row-level security is exercised
exactly as in production.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from kb_assistant.api.app import create_app
from kb_assistant.bootstrap import (
    Container,
    WorkerContainer,
    build_container,
    build_worker_container,
)
from kb_assistant.infrastructure.security.jwt import SigningKeys
from kb_assistant.settings import (
    AuthSettings,
    DatabaseSettings,
    RedisSettings,
    Settings,
    StorageSettings,
    WorkerSettings,
)
from tests.fakes import RecordingJobQueue
from tests.support import DEFAULT_PASSWORD, Seeder, fast_hasher

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_ROLE = "kb_app"
APP_PASSWORD = "app-role-test-password"
POSTGRES_IMAGE = "pgvector/pgvector:0.8.7-pg17"
REDIS_IMAGE = "redis:8-alpine"
# MinIO stopped publishing community images in 2025; SeaweedFS is the S3 stand-in (ADR-0015).
S3_IMAGE = "chrislusf/seaweedfs:4.48"
S3_ACCESS_KEY = "test-access-key"
S3_SECRET_KEY = "test-secret-key"


# ----------------------------------------------------------------------------- keys


@pytest.fixture(scope="session")
def rsa_private_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def signing_keys(rsa_private_key: RSAPrivateKey) -> SigningKeys:
    return SigningKeys.from_private_key("test-key", rsa_private_key)


# ----------------------------------------------------------------------------- containers


@dataclass(frozen=True)
class Database:
    owner_url: str
    app_url: str
    host: str
    port: int


def _asyncpg_url(user: str, password: str, host: str, port: int, db: str) -> str:
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def run_migrations(owner_url: str, *args: str) -> None:
    """Run Alembic in a subprocess, exactly as the deployment does."""
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "KB_DATABASE__MIGRATION_URL": owner_url},
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(args)} failed:\n{result.stderr}")


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as pg:
        host, port = pg.get_container_host_ip(), int(pg.get_exposed_port(5432))
        owner = _asyncpg_url(pg.username, pg.password, host, port, pg.dbname)
        pg.exec(
            [
                "psql",
                "-U",
                pg.username,
                "-d",
                pg.dbname,
                "-c",
                f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"
                " NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
            ]
        )
        run_migrations(owner, "upgrade", "head")
        yield Database(
            owner_url=owner,
            app_url=_asyncpg_url(APP_ROLE, APP_PASSWORD, host, port, pg.dbname),
            host=host,
            port=port,
        )


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer(REDIS_IMAGE) as redis:
        yield f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"


@pytest.fixture(scope="session")
def storage_settings() -> Iterator[StorageSettings]:
    s3 = (
        DockerContainer(S3_IMAGE)
        .with_command("server -s3 -dir=/data")
        .with_env("AWS_ACCESS_KEY_ID", S3_ACCESS_KEY)
        .with_env("AWS_SECRET_ACCESS_KEY", S3_SECRET_KEY)
        .with_exposed_ports(8333)
        .waiting_for(HttpWaitStrategy(8333).for_status_code(403))
    )
    with s3:
        endpoint = f"http://{s3.get_container_host_ip()}:{s3.get_exposed_port(8333)}"
        yield StorageSettings(
            bucket="kb-test",
            endpoint_url=endpoint,
            access_key=SecretStr(S3_ACCESS_KEY),
            secret_key=SecretStr(S3_SECRET_KEY),
            create_bucket=True,
        )


@pytest.fixture(scope="session")
async def owner_engine(database: Database) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database.owner_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def app_engine(database: Database) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database.app_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def seeder(owner_engine: AsyncEngine) -> Seeder:
    return Seeder(owner_engine, await fast_hasher().hash(DEFAULT_PASSWORD))


# ----------------------------------------------------------------------------- application


def make_settings(database: Database, redis_url: str, storage: StorageSettings) -> Settings:
    return Settings(
        environment="test",
        database=DatabaseSettings(url=SecretStr(database.app_url)),
        redis=RedisSettings(url=SecretStr(redis_url)),
        storage=storage,
        auth=AuthSettings(private_key_file=Path("/nonexistent"), key_id="test-key"),
    )


def make_worker_settings(
    database: Database, redis_url: str, storage: StorageSettings
) -> WorkerSettings:
    return WorkerSettings(
        environment="test",
        database=DatabaseSettings(url=SecretStr(database.app_url)),
        redis=RedisSettings(url=SecretStr(redis_url)),
        storage=storage,
    )


@pytest.fixture(scope="session")
def jobs() -> RecordingJobQueue:
    return RecordingJobQueue()


@pytest.fixture(scope="session")
async def container(
    database: Database,
    redis_url: str,
    storage_settings: StorageSettings,
    signing_keys: SigningKeys,
    jobs: RecordingJobQueue,
) -> AsyncIterator[Container]:
    built = build_container(
        make_settings(database, redis_url, storage_settings),
        hasher=fast_hasher(),
        signing_keys=signing_keys,
        jobs=jobs,
    )
    await built.start()
    yield built
    await built.aclose()


@pytest.fixture(scope="session")
async def worker(
    database: Database, redis_url: str, storage_settings: StorageSettings, container: Container
) -> AsyncIterator[WorkerContainer]:
    # Shares the API's blob store so the bucket is the same and already exists.
    built = build_worker_container(
        make_worker_settings(database, redis_url, storage_settings), blobs=container.blobs
    )
    yield built
    await built.aclose()


@pytest.fixture(scope="session")
async def client(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container=container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
