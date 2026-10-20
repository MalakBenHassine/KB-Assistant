"""Document management: upload, new version, list, detail, retry.

Who may do what: a department admin manages the documents of the departments
they belong to; a tenant admin manages all of them. Anything else, including a
document of another department, is a 404 (no existence oracle).

Upload order: store the file, commit the rows, then enqueue the job. If the
enqueue fails after the commit, the version stays `pending` and an admin can
retry it; a transactional outbox would close that gap (ADR-0015).
"""

import hashlib
import logging
from dataclasses import dataclass

from kb_assistant.application.authorization import require_role
from kb_assistant.application.content_type import detect_mime_type
from kb_assistant.domain.documents import Document, DocumentVersion, MimeType, VersionStatus
from kb_assistant.domain.errors import (
    ConflictError,
    DocumentTooLargeError,
    ForbiddenError,
    NotFoundError,
    PolicyViolationError,
)
from kb_assistant.domain.ids import DepartmentId, DocumentId, VersionId, new_id
from kb_assistant.domain.models import AuditEvent, Role
from kb_assistant.domain.ports import BlobStore, Clock, JobQueue, UnitOfWork
from kb_assistant.domain.principal import Principal

log = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 300


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    max_bytes: int


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    version: DocumentVersion
    created: bool  # False when the exact same content was already there


@dataclass(frozen=True, slots=True)
class DocumentDetail:
    document: Document
    versions: list[DocumentVersion]


async def _manageable_departments(uow: UnitOfWork, principal: Principal) -> set[DepartmentId]:
    require_role(principal, Role.DEPT_ADMIN)
    if principal.role is Role.TENANT_ADMIN:
        return {d.id for d in await uow.departments.list_all()}
    return set(principal.department_ids)


async def _manageable_document(
    uow: UnitOfWork, principal: Principal, document_id: DocumentId
) -> Document:
    require_role(principal, Role.DEPT_ADMIN)
    document = await uow.documents.get(document_id)
    if document is None or not principal.manages_department(document.department_id):
        raise NotFoundError("Document not found.")
    return document


class _Uploader:
    """Shared by a first upload and a new version."""

    def __init__(
        self,
        uow: UnitOfWork,
        blobs: BlobStore,
        jobs: JobQueue,
        clock: Clock,
        policy: UploadPolicy,
    ) -> None:
        self._uow = uow
        self._blobs = blobs
        self._jobs = jobs
        self._clock = clock
        self._policy = policy

    def inspect(self, filename: str, data: bytes) -> tuple[MimeType, str]:
        if len(data) > self._policy.max_bytes:
            raise DocumentTooLargeError(
                f"Files are limited to {self._policy.max_bytes // (1024 * 1024)} MiB."
            )
        if not data:
            raise PolicyViolationError("The file is empty.")
        return detect_mime_type(data, filename), hashlib.sha256(data).hexdigest()

    async def store_version(
        self,
        principal: Principal,
        document_id: DocumentId,
        *,
        number: int,
        filename: str,
        data: bytes,
        mime_type: MimeType,
        checksum: str,
    ) -> DocumentVersion:
        version_id = VersionId(new_id())
        blob_key = f"{principal.tenant_id}/{document_id}/{version_id}"
        await self._blobs.put(blob_key, data, content_type=mime_type.value)
        version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            tenant_id=principal.tenant_id,
            version=number,
            checksum=checksum,
            filename=filename[:255],
            mime_type=mime_type,
            size_bytes=len(data),
            blob_key=blob_key,
            status=VersionStatus.PENDING,
            failure=None,
            attempts=0,
            chunk_count=None,
            embedding_model=None,
            embedding_tokens=None,
            created_by=principal.membership_id,
            created_at=self._clock.now(),
            processing_started_at=None,
            indexed_at=None,
            archived_at=None,
        )
        await self._uow.versions.add(version)
        return version

    async def enqueue(self, version: DocumentVersion) -> None:
        try:
            await self._jobs.enqueue_ingestion(version.tenant_id, version.id)
        except Exception:
            log.exception("ingestion.enqueue_failed", extra={"version_id": str(version.id)})


class UploadDocument:
    def __init__(
        self,
        uow: UnitOfWork,
        blobs: BlobStore,
        jobs: JobQueue,
        clock: Clock,
        policy: UploadPolicy,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._uploader = _Uploader(uow, blobs, jobs, clock, policy)

    async def execute(
        self,
        principal: Principal,
        *,
        department_id: DepartmentId,
        title: str | None,
        filename: str,
        data: bytes,
    ) -> UploadResult:
        require_role(principal, Role.DEPT_ADMIN)
        if not await self._uow.departments.get_many([department_id]):
            raise PolicyViolationError("This department does not exist.")
        if not principal.manages_department(department_id):
            raise ForbiddenError("You can only upload to departments you manage.")
        mime_type, checksum = self._uploader.inspect(filename, data)

        existing = await self._uow.documents.find_by_checksum(department_id, checksum)
        if existing is not None:
            latest = await self._uow.versions.latest(existing.id)
            if latest is not None:
                return UploadResult(document=existing, version=latest, created=False)

        document = Document(
            id=DocumentId(new_id()),
            tenant_id=principal.tenant_id,
            department_id=department_id,
            title=(title or filename).strip()[:MAX_TITLE_LENGTH] or filename,
            created_by=principal.membership_id,
            created_at=self._clock.now(),
            current_version_id=None,
        )
        await self._uow.documents.add(document)
        version = await self._uploader.store_version(
            principal,
            document.id,
            number=1,
            filename=filename,
            data=data,
            mime_type=mime_type,
            checksum=checksum,
        )
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=principal.tenant_id,
                action="document.uploaded",
                actor_user_id=principal.user_id,
                target=str(document.id),
                details={"version": 1, "size_bytes": len(data), "mime_type": mime_type.value},
            )
        )
        await self._uow.commit()
        await self._uploader.enqueue(version)
        return UploadResult(document=document, version=version, created=True)


class UploadNewVersion:
    def __init__(
        self,
        uow: UnitOfWork,
        blobs: BlobStore,
        jobs: JobQueue,
        clock: Clock,
        policy: UploadPolicy,
    ) -> None:
        self._uow = uow
        self._uploader = _Uploader(uow, blobs, jobs, clock, policy)

    async def execute(
        self, principal: Principal, document_id: DocumentId, *, filename: str, data: bytes
    ) -> UploadResult:
        document = await _manageable_document(self._uow, principal, document_id)
        mime_type, checksum = self._uploader.inspect(filename, data)

        latest = await self._uow.versions.latest(document.id)
        # Same bytes as the latest usable version: nothing to do (idempotent re-upload).
        if (
            latest is not None
            and latest.checksum == checksum
            and latest.status is not VersionStatus.FAILED
        ):
            return UploadResult(document=document, version=latest, created=False)

        number = (latest.version if latest else 0) + 1
        version = await self._uploader.store_version(
            principal,
            document.id,
            number=number,
            filename=filename,
            data=data,
            mime_type=mime_type,
            checksum=checksum,
        )
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=principal.tenant_id,
                action="document.version_uploaded",
                actor_user_id=principal.user_id,
                target=str(document.id),
                details={"version": number, "size_bytes": len(data)},
            )
        )
        await self._uow.commit()
        await self._uploader.enqueue(version)
        return UploadResult(document=document, version=version, created=True)


class ListDocuments:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self, principal: Principal, *, department_id: DepartmentId | None = None
    ) -> list[tuple[Document, DocumentVersion | None]]:
        departments = await _manageable_departments(self._uow, principal)
        if department_id is not None:
            departments &= {department_id}
        documents = await self._uow.documents.list_in_departments(sorted(departments))
        latest = await self._uow.versions.latest_for_documents([d.id for d in documents])
        return [(d, latest.get(d.id)) for d in documents]


class GetDocument:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, principal: Principal, document_id: DocumentId) -> DocumentDetail:
        document = await _manageable_document(self._uow, principal, document_id)
        versions = await self._uow.versions.list_for_document(document.id)
        return DocumentDetail(document=document, versions=versions)


class RetryIngestion:
    """Re-queue a failed version, or a pending one whose job was lost."""

    def __init__(self, uow: UnitOfWork, jobs: JobQueue) -> None:
        self._uow = uow
        self._jobs = jobs

    async def execute(
        self, principal: Principal, document_id: DocumentId, version_id: VersionId
    ) -> DocumentVersion:
        document = await _manageable_document(self._uow, principal, document_id)
        version = await self._uow.versions.get(version_id)
        if version is None or version.document_id != document.id:
            raise NotFoundError("Version not found.")
        if version.status not in (VersionStatus.FAILED, VersionStatus.PENDING):
            raise ConflictError(f"A version that is {version.status.value} cannot be retried.")
        await self._uow.versions.reset_to_pending(version.id)
        await self._uow.audit.record(
            AuditEvent(
                tenant_id=principal.tenant_id,
                action="document.ingestion_retried",
                actor_user_id=principal.user_id,
                target=str(version.id),
            )
        )
        await self._uow.commit()
        await self._jobs.enqueue_ingestion(version.tenant_id, version.id)
        refreshed = await self._uow.versions.get(version.id)
        return refreshed or version
