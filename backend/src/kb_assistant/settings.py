"""Configuration, validated once at startup.

Every value comes from the environment (`KB_` prefix, `__` for nesting, e.g.
`KB_DATABASE__URL`). Secrets are `SecretStr` so they never appear in a repr or
a log line. A missing or malformed value stops the process before it serves a
single request.
"""

from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    # Connects as the application role, which does not own the tables and is
    # therefore subject to row-level security.
    url: SecretStr
    # Connects as the owner; used by Alembic only, never by the running API.
    migration_url: SecretStr | None = None
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout_s: float = Field(default=5.0, gt=0)
    statement_timeout_ms: int = Field(default=5_000, gt=0)


class RedisSettings(BaseModel):
    url: SecretStr
    socket_timeout_s: float = Field(default=2.0, gt=0)


class AuthSettings(BaseModel):
    issuer: str = "kb-assistant"
    audience: str = "kb-assistant-api"
    private_key_file: Path
    key_id: str = Field(min_length=1)
    access_ttl_s: int = Field(default=600, ge=60, le=3_600)
    refresh_idle_ttl_s: int = Field(default=14 * 24 * 3_600, ge=3_600)
    refresh_absolute_ttl_s: int = Field(default=30 * 24 * 3_600, ge=3_600)
    invitation_ttl_s: int = Field(default=72 * 3_600, ge=3_600)
    clock_skew_s: int = Field(default=30, ge=0, le=120)

    @property
    def refresh_idle_ttl(self) -> timedelta:
        return timedelta(seconds=self.refresh_idle_ttl_s)

    @property
    def refresh_absolute_ttl(self) -> timedelta:
        return timedelta(seconds=self.refresh_absolute_ttl_s)

    @property
    def invitation_ttl(self) -> timedelta:
        return timedelta(seconds=self.invitation_ttl_s)


class StorageSettings(BaseModel):
    """S3-compatible object storage for uploaded originals."""

    bucket: str = "kb-documents"
    endpoint_url: str | None = None  # None means AWS S3
    access_key: SecretStr
    secret_key: SecretStr
    region: str = "us-east-1"
    timeout_s: float = Field(default=10.0, gt=0)
    create_bucket: bool = False  # local and test only


class EmbeddingSettings(BaseModel):
    # `hashing` runs offline with no key (tests, CI, demo); `openai` is the real model.
    provider: Literal["hashing", "openai"] = "hashing"
    model: str = "text-embedding-3-small"
    dimension: int = Field(default=1536, gt=0)
    api_key: SecretStr | None = None
    timeout_s: float = Field(default=30.0, gt=0)
    batch_max_inputs: int = Field(default=256, ge=1, le=2_048)
    batch_max_tokens: int = Field(default=250_000, ge=1, le=300_000)

    @model_validator(mode="after")
    def _key_for_hosted_models(self) -> "EmbeddingSettings":
        if self.provider == "openai" and not (self.api_key and self.api_key.get_secret_value()):
            raise ValueError("embeddings.api_key is required when provider is openai")
        return self


class VectorStoreSettings(BaseModel):
    provider: Literal["pgvector", "pinecone"] = "pgvector"
    pinecone_api_key: SecretStr | None = None
    pinecone_index_host: str | None = None
    timeout_s: float = Field(default=10.0, gt=0)

    @model_validator(mode="after")
    def _pinecone_needs_credentials(self) -> "VectorStoreSettings":
        if self.provider == "pinecone" and not (self.pinecone_api_key and self.pinecone_index_host):
            raise ValueError("pinecone_api_key and pinecone_index_host are required for pinecone")
        return self


class IngestionSettings(BaseModel):
    """Chunking values are placeholders until Phase 4 calibrates them (ADR-0004)."""

    max_upload_mib: int = Field(default=20, ge=1, le=200)
    chunk_max_tokens: int = Field(default=512, ge=64, le=8_000)
    chunk_overlap_tokens: int = Field(default=64, ge=0)
    chunk_min_tokens: int = Field(default=50, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    stale_after_s: int = Field(default=600, ge=60)


class _ProcessSettings(BaseSettings):
    """What every process needs: the API, the worker and the CLI."""

    model_config = SettingsConfigDict(
        env_prefix="KB_",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database: DatabaseSettings
    redis: RedisSettings
    storage: StorageSettings
    embeddings: EmbeddingSettings = EmbeddingSettings()
    vector_store: VectorStoreSettings = VectorStoreSettings()
    ingestion: IngestionSettings = IngestionSettings()


class WorkerSettings(_ProcessSettings):
    """The worker signs no tokens, so it is never given the signing key."""


class Settings(_ProcessSettings):
    """API settings."""

    auth: AuthSettings
    # Number of reverse proxies in front of the API whose X-Forwarded-For we trust.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=3)

    @property
    def expose_docs(self) -> bool:
        return self.environment in ("local", "test")
