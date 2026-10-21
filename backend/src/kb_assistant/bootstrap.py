"""Composition root: the one place where settings become concrete adapters.

Nothing else instantiates a client, so tests can swap any dependency by
building a container with fakes, and there are no global clients.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from openai import AsyncOpenAI
from pinecone import AsyncIndex, AsyncPinecone
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from kb_assistant.application.answering.graph import AnswerPolicy, GraphDeps, build_answer_graph
from kb_assistant.application.answering.service import AskQuestion, hydrate
from kb_assistant.application.auth import AuthPolicy
from kb_assistant.application.chunking import Chunker, ChunkingPolicy
from kb_assistant.application.documents import UploadPolicy
from kb_assistant.application.governance import ChatGuard, ChatLimits
from kb_assistant.application.ingestion import IngestDocumentVersion, IngestionPolicy
from kb_assistant.domain.governance import Limit
from kb_assistant.domain.ports import (
    AccessTokenService,
    BlobStore,
    ChatModel,
    Clock,
    DocumentParser,
    Embedder,
    JobQueue,
    PasswordHasher,
    Reranker,
    Tokenizer,
    UnitOfWork,
    VectorStore,
)
from kb_assistant.infrastructure.clock import SystemClock
from kb_assistant.infrastructure.db.engine import build_engine, build_sessionmaker
from kb_assistant.infrastructure.db.tables import EMBEDDING_DIMENSION
from kb_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWork
from kb_assistant.infrastructure.embeddings import HashingEmbedder, OpenAIEmbedder
from kb_assistant.infrastructure.jobs import CeleryJobQueue, celery_producer
from kb_assistant.infrastructure.llm.extractive import ExtractiveChatModel
from kb_assistant.infrastructure.llm.openai_chat import OpenAIChatModel
from kb_assistant.infrastructure.observability.logging import configure_logging
from kb_assistant.infrastructure.parsing import DocumentParserImpl
from kb_assistant.infrastructure.rate_limit import RedisRateLimiter
from kb_assistant.infrastructure.rerank import PineconeReranker
from kb_assistant.infrastructure.resilience import CircuitBreaker
from kb_assistant.infrastructure.security.jwt import JwtAccessTokenService, SigningKeys
from kb_assistant.infrastructure.security.passwords import Argon2PasswordHasher
from kb_assistant.infrastructure.storage import S3BlobStore
from kb_assistant.infrastructure.tokenizer import TiktokenTokenizer
from kb_assistant.infrastructure.vector.pgvector import PgVectorStore
from kb_assistant.infrastructure.vector.pinecone import PineconeVectorStore
from kb_assistant.settings import (
    EmbeddingSettings,
    LLMSettings,
    RerankerSettings,
    RetrievalSettings,
    Settings,
    StorageSettings,
    VectorStoreSettings,
    WorkerSettings,
)

READINESS_TIMEOUT_S = 2.0
Closer = Callable[[], Awaitable[None]]


def configure_runtime(settings: Settings | WorkerSettings) -> None:
    """Process-wide setup shared by the API and the worker."""
    configure_logging(settings.log_level, json=settings.environment != "local")


# ----------------------------------------------------------------------------- adapters


def build_blob_store(settings: StorageSettings) -> S3BlobStore:
    return S3BlobStore(
        bucket=settings.bucket,
        endpoint_url=settings.endpoint_url,
        access_key=settings.access_key.get_secret_value(),
        secret_key=settings.secret_key.get_secret_value(),
        region=settings.region,
        timeout_s=settings.timeout_s,
    )


def build_embedder(settings: EmbeddingSettings, tokenizer: Tokenizer) -> Embedder:
    if settings.provider == "hashing":
        return HashingEmbedder(settings.dimension)
    if settings.api_key is None:  # also rejected by EmbeddingSettings' validator
        raise ValueError("embeddings.api_key is required for openai")
    client = AsyncOpenAI(api_key=settings.api_key.get_secret_value(), max_retries=0)
    return OpenAIEmbedder(
        client,
        model=settings.model,
        dimension=settings.dimension,
        tokenizer=tokenizer,
        max_inputs=settings.batch_max_inputs,
        max_request_tokens=settings.batch_max_tokens,
        timeout_s=settings.timeout_s,
    )


def build_vector_store(
    settings: VectorStoreSettings, engine: AsyncEngine, dimension: int
) -> tuple[VectorStore, Closer | None]:
    if settings.provider == "pinecone":
        if not (settings.pinecone_api_key and settings.pinecone_index_host):
            raise ValueError("Pinecone needs an API key and an index host")
        store = PineconeVectorStore(
            AsyncIndex(
                host=settings.pinecone_index_host,
                api_key=settings.pinecone_api_key.get_secret_value(),
                timeout=settings.timeout_s,
            ),
            timeout_s=settings.timeout_s,
        )
        return store, store.aclose
    if dimension != EMBEDDING_DIMENSION:
        # The pgvector column has a fixed width (migration 0002): fail at startup,
        # not on the first upsert.
        raise ValueError(
            f"embeddings.dimension={dimension} does not match the pgvector index "
            f"({EMBEDDING_DIMENSION}); changing it needs a migration and a re-ingestion"
        )
    return PgVectorStore(engine), None


def build_chat_model(settings: LLMSettings) -> ChatModel:
    if settings.provider == "extractive":
        return ExtractiveChatModel()
    if settings.api_key is None:  # also rejected by LLMSettings' validator
        raise ValueError("llm.api_key is required for openai")
    return OpenAIChatModel(
        AsyncOpenAI(api_key=settings.api_key.get_secret_value(), max_retries=0),
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        timeout_s=settings.timeout_s,
        breaker=CircuitBreaker(
            failure_threshold=settings.breaker_failure_threshold,
            reset_after_s=settings.breaker_reset_s,
            clock=time.monotonic,
        ),
    )


def build_reranker(settings: RerankerSettings) -> tuple[Reranker | None, Closer | None]:
    if settings.provider == "none":
        return None, None
    if settings.pinecone_api_key is None:
        raise ValueError("reranker.pinecone_api_key is required")
    reranker = PineconeReranker(
        AsyncPinecone(api_key=settings.pinecone_api_key.get_secret_value()), model=settings.model
    )
    return reranker, reranker.aclose


def chat_limits(settings: Settings) -> ChatLimits:
    limits = settings.limits
    return ChatLimits(
        per_user=Limit(per_minute=limits.chat_user_per_minute, burst=limits.chat_user_burst),
        per_tenant=Limit(per_minute=limits.chat_tenant_per_minute, burst=limits.chat_tenant_burst),
        default_monthly_token_budget=limits.default_monthly_token_budget,
    )


def answer_policy(settings: RetrievalSettings) -> AnswerPolicy:
    return AnswerPolicy(
        top_k=settings.top_k,
        rerank_top_n=settings.rerank_top_n,
        min_score=settings.min_score,
        max_retries=settings.max_retries,
        llm_relevance_check=settings.llm_relevance_check,
        max_llm_calls=settings.max_llm_calls,
        answer_max_tokens=settings.answer_max_tokens,
    )


# ----------------------------------------------------------------------------- API


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
    blobs: BlobStore
    jobs: JobQueue
    upload_policy: UploadPolicy
    embedder: Embedder
    vectors: VectorStore
    chat: ChatModel
    reranker: Reranker | None
    answer_policy: AnswerPolicy
    answer_graph: Any = None
    chat_guard: ChatGuard | None = None
    closers: list[Closer] = field(default_factory=list)
    startup_hooks: list[Closer] = field(default_factory=list)

    async def start(self) -> None:
        for hook in self.startup_hooks:
            await hook()

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[UnitOfWork]:
        """A transaction; rolled back unless the use case committed."""
        async with self.sessionmaker() as session:
            try:
                yield SqlAlchemyUnitOfWork(session)
            finally:
                await session.rollback()

    def graph_for(self, policy: AnswerPolicy) -> Any:
        """The answering graph wired to this container's adapters, under `policy`."""
        return build_answer_graph(
            GraphDeps(
                embedder=self.embedder,
                vectors=self.vectors,
                hydrate=lambda ids, scope: hydrate(self.unit_of_work, ids, scope),
                chat=self.chat,
                reranker=self.reranker,
                policy=policy,
            )
        )

    def ask(self) -> AskQuestion:
        return AskQuestion(
            uow_factory=self.unit_of_work,
            graph=self.answer_graph,
            policy=self.answer_policy,
            clock=self.clock,
            model_id=self.chat.model_id,
            guard=self.chat_guard,
        )

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
        for close in self.closers:
            await close()
        await self.redis.aclose()
        await self.engine.dispose()


def build_container(
    settings: Settings,
    *,
    hasher: PasswordHasher | None = None,
    clock: Clock | None = None,
    signing_keys: SigningKeys | None = None,
    jobs: JobQueue | None = None,
    blobs: BlobStore | None = None,
    chat: ChatModel | None = None,
    embedder: Embedder | None = None,
    vectors: VectorStore | None = None,
    reranker: Reranker | None = None,
) -> Container:
    clock = clock or SystemClock()
    engine = build_engine(settings.database, application_name="kb-assistant-api")
    keys = signing_keys or SigningKeys.from_pem_file(
        settings.auth.key_id, settings.auth.private_key_file
    )
    closers: list[Closer] = []
    startup_hooks: list[Closer] = []
    if blobs is None:
        s3 = build_blob_store(settings.storage)
        closers.append(s3.aclose)
        if settings.storage.create_bucket:
            startup_hooks.append(s3.ensure_bucket)
        blobs = s3
    embedder = embedder or build_embedder(settings.embeddings, TiktokenTokenizer())
    if vectors is None:
        vectors, close_vectors = build_vector_store(
            settings.vector_store, engine, embedder.dimension
        )
        if close_vectors is not None:
            closers.append(close_vectors)
    if reranker is None:
        reranker, close_reranker = build_reranker(settings.reranker)
        if close_reranker is not None:
            closers.append(close_reranker)
    container = Container(
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
        blobs=blobs,
        jobs=jobs or CeleryJobQueue(celery_producer(settings.redis.url.get_secret_value())),
        upload_policy=UploadPolicy(max_bytes=settings.ingestion.max_upload_mib * 1024 * 1024),
        embedder=embedder,
        vectors=vectors,
        chat=chat or build_chat_model(settings.llm),
        reranker=reranker,
        answer_policy=answer_policy(settings.retrieval),
        closers=closers,
        startup_hooks=startup_hooks,
    )
    container.answer_graph = container.graph_for(container.answer_policy)
    container.chat_guard = ChatGuard(RedisRateLimiter(container.redis), chat_limits(settings))
    return container


# ----------------------------------------------------------------------------- worker


@dataclass(slots=True)
class WorkerContainer:
    settings: WorkerSettings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    blobs: BlobStore
    parser: DocumentParser
    chunker: Chunker
    embedder: Embedder
    vectors: VectorStore
    clock: Clock
    ingestion_policy: IngestionPolicy
    closers: list[Closer] = field(default_factory=list)

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[UnitOfWork]:
        async with self.sessionmaker() as session:
            try:
                yield SqlAlchemyUnitOfWork(session)
            finally:
                await session.rollback()

    def ingest(self) -> IngestDocumentVersion:
        return IngestDocumentVersion(
            uow_factory=self.unit_of_work,
            blobs=self.blobs,
            parser=self.parser,
            chunker=self.chunker,
            embedder=self.embedder,
            vectors=self.vectors,
            clock=self.clock,
            policy=self.ingestion_policy,
        )

    async def aclose(self) -> None:
        for close in self.closers:
            await close()
        await self.engine.dispose()


def build_worker_container(
    settings: WorkerSettings,
    *,
    embedder: Embedder | None = None,
    blobs: BlobStore | None = None,
    vectors: VectorStore | None = None,
    clock: Clock | None = None,
) -> WorkerContainer:
    engine = build_engine(settings.database, application_name="kb-assistant-worker")
    tokenizer = TiktokenTokenizer()
    closers: list[Closer] = []
    if blobs is None:
        s3 = build_blob_store(settings.storage)
        closers.append(s3.aclose)
        blobs = s3
    embedder = embedder or build_embedder(settings.embeddings, tokenizer)
    if vectors is None:
        vectors, close_vectors = build_vector_store(
            settings.vector_store, engine, embedder.dimension
        )
        if close_vectors is not None:
            closers.append(close_vectors)
    ingestion = settings.ingestion
    return WorkerContainer(
        settings=settings,
        engine=engine,
        sessionmaker=build_sessionmaker(engine),
        blobs=blobs,
        parser=DocumentParserImpl(),
        chunker=Chunker(
            tokenizer,
            ChunkingPolicy(
                max_tokens=ingestion.chunk_max_tokens,
                overlap_tokens=ingestion.chunk_overlap_tokens,
                min_tokens=ingestion.chunk_min_tokens,
            ),
        ),
        embedder=embedder,
        vectors=vectors,
        clock=clock or SystemClock(),
        ingestion_policy=IngestionPolicy(
            max_attempts=ingestion.max_attempts,
            stale_after=timedelta(seconds=ingestion.stale_after_s),
        ),
        closers=closers,
    )
