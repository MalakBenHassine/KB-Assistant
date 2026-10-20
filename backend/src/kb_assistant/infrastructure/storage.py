"""S3-compatible object storage for uploaded originals (MinIO locally, S3 in production).

The client is created lazily on first use, because opening it is
asynchronous and the composition root is not. Keys are
`{tenant_id}/{document_id}/{version_id}`: unguessable, and a tenant's files
share a prefix, which makes a per-tenant export or deletion one listing.
"""

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
)
from botocore.exceptions import (
    ConnectionError as BotoConnectionError,
)

from kb_assistant.domain.errors import IngestionError, ProviderUnavailableError

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

_UNAVAILABLE = (EndpointConnectionError, BotoConnectionError, asyncio.TimeoutError)


class S3BlobStore:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        access_key: str,
        secret_key: str,
        region: str,
        timeout_s: float = 10.0,
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._timeout_s = timeout_s
        self._stack = AsyncExitStack()
        self._client: S3Client | None = None
        self._lock = asyncio.Lock()

    async def _s3(self) -> "S3Client":
        async with self._lock:
            if self._client is None:
                config = AioConfig(
                    connect_timeout=self._timeout_s,
                    read_timeout=self._timeout_s,
                    retries={"max_attempts": 3, "mode": "standard"},
                    s3={"addressing_style": "path"},
                    signature_version="s3v4",
                )
                self._client = await self._stack.enter_async_context(
                    get_session().create_client(
                        "s3",
                        endpoint_url=self._endpoint_url,
                        region_name=self._region,
                        aws_access_key_id=self._access_key,
                        aws_secret_access_key=self._secret_key,
                        config=config,
                    )
                )
            return self._client

    async def ensure_bucket(self) -> None:
        """Local and test environments only; production buckets are provisioned by IaC."""
        s3 = await self._s3()
        try:
            await s3.head_bucket(Bucket=self._bucket)
        except ClientError:
            await s3.create_bucket(Bucket=self._bucket)

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        try:
            s3 = await self._s3()
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        except _UNAVAILABLE as exc:
            raise ProviderUnavailableError(
                "object_storage", "Object storage is unavailable."
            ) from exc

    async def get(self, key: str) -> bytes:
        try:
            s3 = await self._s3()
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            async with response["Body"] as stream:
                return await stream.read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise IngestionError(
                    "original_missing",
                    "The uploaded file is missing from storage.",
                    retryable=False,
                ) from exc
            raise ProviderUnavailableError(
                "object_storage", "Object storage refused the read."
            ) from exc
        except (*_UNAVAILABLE, BotoCoreError) as exc:
            raise ProviderUnavailableError(
                "object_storage", "Object storage is unavailable."
            ) from exc

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._client = None
