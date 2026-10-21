"""Ports: what the application needs from the outside world.

Infrastructure implements these Protocols; tests implement them with fakes.
The application layer never imports an adapter.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from kb_assistant.domain.chat import (
    Conversation,
    HydratedChunk,
    JsonSchema,
    Message,
    RetrievedChunk,
    StreamChunk,
    StructuredReply,
    TokenUsage,
)
from kb_assistant.domain.documents import (
    Chunk,
    Document,
    DocumentVersion,
    EmbeddingBatch,
    IngestionFailure,
    MimeType,
    ParsedDocument,
    RetrievalScope,
    VectorMatch,
    VectorRecord,
)
from kb_assistant.domain.governance import (
    Analytics,
    AuditEntry,
    Feedback,
    Limit,
    RateDecision,
    TenantSettings,
    Usage,
)
from kb_assistant.domain.ids import (
    ConversationId,
    DepartmentId,
    DocumentId,
    MembershipId,
    MessageId,
    RefreshTokenId,
    TenantId,
    TokenFamilyId,
    UserId,
    VersionId,
)
from kb_assistant.domain.models import (
    AuditEvent,
    Department,
    Invitation,
    Membership,
    RefreshToken,
    Tenant,
    User,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class PasswordHasher(Protocol):
    """Async because a good hash is deliberately slow: it must not block the event loop."""

    async def hash(self, password: str) -> str: ...

    async def verify(self, password_hash: str, password: str) -> bool: ...

    async def verify_dummy(self, password: str) -> None:
        """Spend the same time as a real verification (no timing oracle on unknown emails)."""
        ...

    def needs_rehash(self, password_hash: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: UserId
    tenant_id: TenantId
    token_version: int


class AccessTokenService(Protocol):
    def issue(self, *, user_id: UserId, tenant_id: TenantId, token_version: int) -> str: ...

    def verify(self, token: str) -> AccessClaims:
        """Raise `NotAuthenticatedError` for any invalid token, whatever the reason."""
        ...

    @property
    def access_ttl_seconds(self) -> int: ...

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        """Public verification keys, published at /.well-known/jwks.json."""
        ...


class TenantRepository(Protocol):
    async def get(self, tenant_id: TenantId) -> Tenant | None: ...

    async def get_many(self, tenant_ids: Sequence[TenantId]) -> list[Tenant]: ...


class UserRepository(Protocol):
    async def get(self, user_id: UserId) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None: ...

    async def add(self, user: User) -> None: ...

    async def set_password_hash(self, user_id: UserId, password_hash: str) -> None: ...


class MembershipRepository(Protocol):
    async def get(self, membership_id: MembershipId) -> Membership | None:
        """Within the bound tenant only."""
        ...

    async def get_for_user(self, user_id: UserId, tenant_id: TenantId) -> Membership | None: ...

    async def list_for_tenant(self) -> list[Membership]: ...

    async def list_active_tenants_of_user(self, user_id: UserId) -> list[TenantId]:
        """Every tenant the user belongs to, most recently used first.

        Only works inside `UnitOfWork.allow_cross_tenant_identity` (login, /me).
        """
        ...

    async def add(self, membership: Membership) -> None: ...

    async def touch(self, membership_id: MembershipId, at: datetime) -> None: ...


class DepartmentRepository(Protocol):
    async def list_all(self) -> list[Department]: ...

    async def get_many(self, department_ids: Sequence[DepartmentId]) -> list[Department]: ...

    async def get_by_slug(self, slug: str) -> Department | None: ...

    async def add(self, department: Department) -> None: ...


class InvitationRepository(Protocol):
    async def get_by_token_hash_for_update(self, token_hash: str) -> Invitation | None: ...

    async def add(self, invitation: Invitation) -> None: ...

    async def mark_accepted(self, invitation: Invitation, at: datetime) -> None: ...


class RefreshTokenRepository(Protocol):
    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None: ...

    async def add(self, token: RefreshToken) -> None: ...

    async def mark_rotated(
        self, token_id: RefreshTokenId, replaced_by: RefreshTokenId, at: datetime
    ) -> None: ...

    async def revoke_family(self, family_id: TokenFamilyId, at: datetime) -> int: ...


class AuditLog(Protocol):
    async def record(self, event: AuditEvent) -> None:
        """Append to the tenant's hash chain (ADR-0019)."""
        ...

    async def page(
        self, *, cursor: str | None, limit: int, action: str | None
    ) -> tuple[list[AuditEntry], str | None]:
        """Newest first; returns the cursor of the next page, or None."""
        ...

    async def chain(self, *, after_seq: int, limit: int) -> list[AuditEntry]:
        """Chained entries in `seq` order, for verification."""
        ...


class DocumentRepository(Protocol):
    async def add(self, document: Document) -> None: ...

    async def get(self, document_id: DocumentId) -> Document | None:
        """Within the bound tenant only."""
        ...

    async def list_in_departments(
        self, department_ids: Sequence[DepartmentId]
    ) -> list[Document]: ...

    async def find_by_checksum(self, department_id: DepartmentId, checksum: str) -> Document | None:
        """A document of this department whose latest usable version has this content."""
        ...

    async def set_current_version(self, document_id: DocumentId, version_id: VersionId) -> None: ...


class VersionRepository(Protocol):
    async def add(self, version: DocumentVersion) -> None: ...

    async def get(self, version_id: VersionId) -> DocumentVersion | None: ...

    async def latest(self, document_id: DocumentId) -> DocumentVersion | None: ...

    async def list_for_document(self, document_id: DocumentId) -> list[DocumentVersion]: ...

    async def latest_for_documents(
        self, document_ids: Sequence[DocumentId]
    ) -> dict[DocumentId, DocumentVersion]: ...

    async def claim(self, version_id: VersionId, *, now: datetime, stale_before: datetime) -> bool:
        """Move to `processing` if pending, failed-and-retryable, or stuck since `stale_before`.

        Atomic (a conditional UPDATE), so two deliveries of the same job cannot
        both process it.
        """
        ...

    async def mark_indexed(
        self,
        version_id: VersionId,
        *,
        now: datetime,
        chunk_count: int,
        chunker_version: str,
        embedding_model: str,
        embedding_tokens: int,
        quarantined_passages: int,
    ) -> None: ...

    async def mark_failed(self, version_id: VersionId, failure: IngestionFailure) -> None: ...

    async def reset_to_pending(self, version_id: VersionId) -> bool: ...

    async def archive(self, version_id: VersionId, *, now: datetime) -> None: ...

    async def mark_vectors_deactivated(self, version_id: VersionId, *, now: datetime) -> None: ...


class ChunkRepository(Protocol):
    async def add_many(self, chunks: Sequence[Chunk]) -> None: ...

    async def hydrate(
        self, vector_ids: Sequence[str], department_ids: Sequence[DepartmentId]
    ) -> list[HydratedChunk]:
        """Load chunks by vector id, repeating every isolation predicate (ARCHITECTURE §6.3).

        Returns only chunks of the bound tenant, of the given departments, and of
        their document's *current* version. Anything else the vector store
        returned is silently absent: the caller compares counts to detect it.
        """
        ...

    async def count_for_version(self, version_id: VersionId) -> int: ...


class ConversationRepository(Protocol):
    async def add(self, conversation: Conversation) -> None: ...

    async def get(self, conversation_id: ConversationId) -> Conversation | None: ...

    async def list_for_member(self, membership_id: MembershipId) -> list[Conversation]: ...

    async def touch(self, conversation_id: ConversationId, at: datetime) -> None: ...

    async def remember(
        self, conversation_id: ConversationId, *, at: datetime, summary: str, title: str | None
    ) -> None:
        """Store the rolling summary; set the title too when one is given."""
        ...


class MessageRepository(Protocol):
    async def add(self, message: Message) -> None: ...

    async def get(self, message_id: MessageId) -> Message | None: ...

    async def list_for_conversation(self, conversation_id: ConversationId) -> list[Message]: ...


class FeedbackRepository(Protocol):
    async def upsert(self, feedback: Feedback) -> Feedback:
        """Insert, or replace this person's earlier rating of the same message."""
        ...


class TenantSettingsRepository(Protocol):
    async def get(self) -> TenantSettings | None: ...

    async def put(self, settings: TenantSettings, *, now: datetime) -> None: ...


class UsageRepository(Protocol):
    async def get(self, month: date) -> Usage: ...

    async def add(self, month: date, *, questions: int, tokens: TokenUsage) -> None:
        """Atomic increment."""
        ...


class AnalyticsQueries(Protocol):
    async def summarize(
        self, *, since: datetime, department_ids: Sequence[DepartmentId] | None, gaps: int
    ) -> Analytics:
        """`department_ids=None`: the whole tenant."""
        ...


class Telemetry(Protocol):
    """Counters and timings for operators (ARCHITECTURE §10). Never content."""

    def request_finished(
        self, *, method: str, route: str | None, status: int, seconds: float
    ) -> None: ...

    def answer_finished(
        self,
        *,
        answered: bool,
        grade: str | None,
        abstain_reason: str | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        hydration_rejections: int,
        seconds: float,
    ) -> None: ...

    def rate_limited(self, kind: str) -> None: ...

    def ingestion_finished(self, *, status: str, seconds: float) -> None: ...

    def exposition(self) -> tuple[bytes, str]:
        """The current samples and their media type, for a scrape."""
        ...


class NodeTracer(Protocol):
    def span(self, name: str) -> AbstractContextManager[Any]:
        """A trace span around one step of the answering graph."""
        ...


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: Limit) -> RateDecision:
        """Spend one request from the bucket `key`."""
        ...


class ChatModel(Protocol):
    """A language model, reduced to the two things the workflow needs (ADR-0006)."""

    @property
    def model_id(self) -> str: ...

    async def structured(
        self, *, system: str, user: str, schema: JsonSchema, max_output_tokens: int
    ) -> StructuredReply:
        """One JSON object matching `schema`. Raises `ProviderUnavailableError` on outage."""
        ...

    def stream(
        self, *, system: str, user: str, max_output_tokens: int
    ) -> AsyncIterator[StreamChunk]:
        """Text deltas, then one final chunk carrying the token usage."""
        ...


class Reranker(Protocol):
    @property
    def model_id(self) -> str: ...

    async def rerank(
        self, query: str, chunks: Sequence[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]: ...


class BlobStore(Protocol):
    """Original uploaded files (S3-compatible in every environment)."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...


class DocumentParser(Protocol):
    def parse(self, data: bytes, mime_type: MimeType) -> ParsedDocument:
        """Raise `UnreadableDocumentError` for anything that is not a valid document."""
        ...


class Tokenizer(Protocol):
    """Counts tokens the way the embedding model does (ADR-0004)."""

    def count(self, text: str) -> int: ...

    def split(self, text: str, max_tokens: int) -> list[str]:
        """Hard split on token boundaries, for text with no natural break."""
        ...

    def tail(self, text: str, max_tokens: int) -> str:
        """The last max_tokens tokens of text (used for chunk overlap)."""
        ...


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingBatch:
        """Batches internally to respect provider limits; one vector per text, in order."""
        ...

    async def embed_query(self, text: str) -> tuple[float, ...]: ...


class VectorStore(Protocol):
    """ADR-0002 and ADR-0003. Never accepts a raw filter: only a `RetrievalScope`."""

    async def upsert(self, records: Sequence[VectorRecord]) -> None: ...

    async def deactivate_version(self, tenant_id: TenantId, version_id: VersionId) -> None: ...

    async def query(
        self, vector: Sequence[float], scope: RetrievalScope, top_k: int
    ) -> list[VectorMatch]: ...


class JobQueue(Protocol):
    async def enqueue_ingestion(self, tenant_id: TenantId, version_id: VersionId) -> None: ...


class UnitOfWork(Protocol):
    """One database transaction, with the row-level-security context bound to it.

    `bind` must be called before touching tenant-scoped data; Postgres RLS
    returns nothing otherwise, which is the point (ARCHITECTURE §6.3).
    Committing ends the unit of work: a use case commits once, at the end.
    """

    @property
    def tenants(self) -> TenantRepository: ...

    @property
    def users(self) -> UserRepository: ...

    @property
    def memberships(self) -> MembershipRepository: ...

    @property
    def departments(self) -> DepartmentRepository: ...

    @property
    def invitations(self) -> InvitationRepository: ...

    @property
    def refresh_tokens(self) -> RefreshTokenRepository: ...

    @property
    def audit(self) -> AuditLog: ...

    @property
    def documents(self) -> DocumentRepository: ...

    @property
    def versions(self) -> VersionRepository: ...

    @property
    def chunks(self) -> ChunkRepository: ...

    @property
    def conversations(self) -> ConversationRepository: ...

    @property
    def messages(self) -> MessageRepository: ...

    @property
    def feedback(self) -> FeedbackRepository: ...

    @property
    def tenant_settings(self) -> TenantSettingsRepository: ...

    @property
    def usage(self) -> UsageRepository: ...

    @property
    def analytics(self) -> AnalyticsQueries: ...

    async def bind(self, *, tenant_id: TenantId | None, user_id: UserId | None) -> None:
        """Scope the transaction to one tenant; clears any wider grant made before."""
        ...

    async def allow_cross_tenant_identity(self, user_id: UserId) -> None:
        """Let this transaction see `user_id`'s memberships in every tenant."""
        ...

    async def allow_token_lookup(self, token_hash: str) -> None:
        """Let this transaction find the refresh token or invitation with this hash."""
        ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
