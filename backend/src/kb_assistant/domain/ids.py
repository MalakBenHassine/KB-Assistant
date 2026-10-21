"""Typed identifiers.

`NewType` costs nothing at runtime, and lets mypy reject a `UserId` passed
where a `TenantId` is expected: the most dangerous mix-up in a multi-tenant
system is a type error here, not a bug found in production.
"""

from typing import NewType
from uuid import UUID

from uuid_utils.compat import uuid7

TenantId = NewType("TenantId", UUID)
UserId = NewType("UserId", UUID)
MembershipId = NewType("MembershipId", UUID)
DepartmentId = NewType("DepartmentId", UUID)
InvitationId = NewType("InvitationId", UUID)
RefreshTokenId = NewType("RefreshTokenId", UUID)
TokenFamilyId = NewType("TokenFamilyId", UUID)
DocumentId = NewType("DocumentId", UUID)
VersionId = NewType("VersionId", UUID)
ChunkId = NewType("ChunkId", UUID)
ConversationId = NewType("ConversationId", UUID)
MessageId = NewType("MessageId", UUID)
FeedbackId = NewType("FeedbackId", UUID)


def new_id() -> UUID:
    """UUIDv7: time-ordered (index-friendly) and not sequential (no volume oracle)."""
    return uuid7()
