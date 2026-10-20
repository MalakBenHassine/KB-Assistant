"""Ingest one document version: parse, clean, chunk, embed, index, switch (ADR-0015).

Postgres is the source of truth; the vector index is a projection of it.
The steps are ordered so that every failure leaves a consistent state:

1. **Claim** (short transaction): `pending → processing`, atomically, so two
   deliveries of the same job cannot both run.
2. **Work** (no transaction open): read the file, parse, chunk, embed, upsert
   vectors. Vector ids are deterministic (`{version_id}:{chunk_index}`), so a
   retry overwrites instead of duplicating.
3. **Switch** (one transaction): insert the chunk rows, mark the version
   `indexed`, point the document at it, archive the previous version.
   Retrieval hydrates chunks from Postgres and only accepts the current
   version, so readers move from old to new in one commit.
4. **Deactivate** the previous version's vectors. If this fails the old vectors
   stay active in the index, but hydration already ignores them; the version
   keeps `vectors_deactivated_at = NULL` so the step can be replayed.

Nothing is ever deleted here: archived versions remain for audit.
"""

import asyncio
import hashlib
import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta

from kb_assistant.application.chunking import CHUNKER_VERSION, Chunker
from kb_assistant.application.text_cleaning import clean_document
from kb_assistant.domain.documents import (
    Chunk,
    ChunkDraft,
    Document,
    DocumentVersion,
    IngestionFailure,
    VectorRecord,
    VersionStatus,
    vector_id,
)
from kb_assistant.domain.errors import EmptyDocumentError, IngestionError
from kb_assistant.domain.ids import ChunkId, TenantId, VersionId, new_id
from kb_assistant.domain.models import AuditEvent
from kb_assistant.domain.ports import (
    BlobStore,
    Clock,
    DocumentParser,
    Embedder,
    UnitOfWork,
    VectorStore,
)

log = logging.getLogger(__name__)

UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[UnitOfWork]]


@dataclass(frozen=True, slots=True)
class IngestionPolicy:
    max_attempts: int = 3
    # A version still `processing` after this long belongs to a worker that died.
    stale_after: timedelta = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    status: VersionStatus | None  # None: nothing to do (unknown, already done, taken)
    retry: bool = False


@dataclass(frozen=True, slots=True)
class _Claimed:
    document: Document
    version: DocumentVersion


class IngestDocumentVersion:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        blobs: BlobStore,
        parser: DocumentParser,
        chunker: Chunker,
        embedder: Embedder,
        vectors: VectorStore,
        clock: Clock,
        policy: IngestionPolicy,
    ) -> None:
        self._uow_factory = uow_factory
        self._blobs = blobs
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._vectors = vectors
        self._clock = clock
        self._policy = policy

    async def execute(self, tenant_id: TenantId, version_id: VersionId) -> IngestionOutcome:
        claimed = await self._claim(tenant_id, version_id)
        if claimed is None:
            return IngestionOutcome(status=None)
        version = claimed.version
        log.info("ingestion.started", extra={"version_id": str(version_id)})
        try:
            drafts = await self._chunks(version)
            embeddings = await self._embedder.embed_documents([d.embedding_text for d in drafts])
            if len(embeddings.vectors) != len(drafts):
                raise IngestionError(
                    "embedding_mismatch", "The provider returned the wrong count.", retryable=True
                )
            await self._vectors.upsert(
                self._records(claimed.document, version, drafts, embeddings.vectors)
            )
        except IngestionError as exc:
            return await self._fail(claimed, IngestionFailure(exc.code, exc.message, exc.retryable))
        except Exception:
            log.exception("ingestion.unexpected_error", extra={"version_id": str(version_id)})
            failure = IngestionFailure(
                "internal_error", "Unexpected error while processing the document.", True
            )
            return await self._fail(claimed, failure)

        previous = await self._switch(claimed, drafts, embeddings.total_tokens)
        if previous is not None:
            await self._deactivate(tenant_id, previous)
        log.info(
            "ingestion.indexed",
            extra={"version_id": str(version_id), "chunks": len(drafts)},
        )
        return IngestionOutcome(status=VersionStatus.INDEXED)

    # ------------------------------------------------------------------ steps

    async def _claim(self, tenant_id: TenantId, version_id: VersionId) -> _Claimed | None:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await uow.bind(tenant_id=tenant_id, user_id=None)
            version = await uow.versions.get(version_id)
            if version is None or version.status is VersionStatus.INDEXED:
                return None
            if not await uow.versions.claim(
                version_id, now=now, stale_before=now - self._policy.stale_after
            ):
                return None
            document = await uow.documents.get(version.document_id)
            claimed = await uow.versions.get(version_id)
            await uow.commit()
        if document is None or claimed is None:
            return None
        return _Claimed(document=document, version=claimed)

    async def _chunks(self, version: DocumentVersion) -> list[ChunkDraft]:
        data = await self._blobs.get(version.blob_key)
        # Parsing and chunking are CPU-bound: keep the event loop responsive.
        parsed = await asyncio.to_thread(self._parser.parse, data, version.mime_type)
        drafts = await asyncio.to_thread(self._chunker.chunk, clean_document(parsed))
        if not drafts:
            raise EmptyDocumentError()
        return drafts

    def _records(
        self,
        document: Document,
        version: DocumentVersion,
        drafts: Sequence[ChunkDraft],
        vectors: Sequence[tuple[float, ...]],
    ) -> list[VectorRecord]:
        return [
            VectorRecord(
                id=vector_id(version.id, draft.index),
                values=values,
                tenant_id=document.tenant_id,
                department_id=document.department_id,
                document_id=document.id,
                version_id=version.id,
                chunk_index=draft.index,
                page=draft.page_start,
            )
            for draft, values in zip(drafts, vectors, strict=True)
        ]

    async def _switch(
        self, claimed: _Claimed, drafts: Sequence[ChunkDraft], embedding_tokens: int
    ) -> VersionId | None:
        """Commit the new version; return the version it replaced, if any."""
        document, version = claimed.document, claimed.version
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await uow.bind(tenant_id=document.tenant_id, user_id=None)
            await uow.chunks.add_many(
                [
                    Chunk(
                        id=ChunkId(new_id()),
                        vector_id=vector_id(version.id, d.index),
                        tenant_id=document.tenant_id,
                        department_id=document.department_id,
                        document_id=document.id,
                        version_id=version.id,
                        chunk_index=d.index,
                        text=d.text,
                        text_hash=hashlib.sha256(d.text.encode()).hexdigest(),
                        heading_path=d.heading_path,
                        page_start=d.page_start,
                        page_end=d.page_end,
                        token_count=d.token_count,
                    )
                    for d in drafts
                ]
            )
            await uow.versions.mark_indexed(
                version.id,
                now=now,
                chunk_count=len(drafts),
                chunker_version=CHUNKER_VERSION,
                embedding_model=self._embedder.model_id,
                embedding_tokens=embedding_tokens,
            )
            current = await uow.documents.get(document.id)
            current_version = (
                await uow.versions.get(current.current_version_id)
                if current and current.current_version_id
                else None
            )
            replaced: VersionId | None
            if current_version is not None and current_version.version > version.version:
                # A newer version finished first: this one is already superseded.
                await uow.versions.archive(version.id, now=now)
                replaced = version.id
            else:
                await uow.documents.set_current_version(document.id, version.id)
                replaced = current_version.id if current_version else None
                if replaced is not None:
                    await uow.versions.archive(replaced, now=now)
            await uow.audit.record(
                AuditEvent(
                    tenant_id=document.tenant_id,
                    action="document.indexed",
                    actor_user_id=None,
                    target=str(version.id),
                    details={"chunks": len(drafts), "embedding_tokens": embedding_tokens},
                )
            )
            await uow.commit()
        return replaced

    async def _deactivate(self, tenant_id: TenantId, version_id: VersionId) -> None:
        try:
            await self._vectors.deactivate_version(tenant_id, version_id)
        except IngestionError:
            log.warning(
                "ingestion.deactivate_failed",
                extra={"version_id": str(version_id)},
                exc_info=True,
            )
            return
        async with self._uow_factory() as uow:
            await uow.bind(tenant_id=tenant_id, user_id=None)
            await uow.versions.mark_vectors_deactivated(version_id, now=self._clock.now())
            await uow.commit()

    async def _fail(self, claimed: _Claimed, failure: IngestionFailure) -> IngestionOutcome:
        version = claimed.version
        # `attempts` was incremented by the claim.
        retry = failure.retryable and version.attempts < self._policy.max_attempts
        stored = IngestionFailure(failure.code, failure.message, retryable=retry)
        async with self._uow_factory() as uow:
            await uow.bind(tenant_id=version.tenant_id, user_id=None)
            await uow.versions.mark_failed(version.id, stored)
            await uow.audit.record(
                AuditEvent(
                    tenant_id=version.tenant_id,
                    action="document.ingestion_failed",
                    actor_user_id=None,
                    target=str(version.id),
                    details={"code": failure.code, "attempt": version.attempts, "retry": retry},
                )
            )
            await uow.commit()
        log.warning(
            "ingestion.failed",
            extra={"version_id": str(version.id), "code": failure.code, "retry": retry},
        )
        return IngestionOutcome(status=VersionStatus.FAILED, retry=retry)
