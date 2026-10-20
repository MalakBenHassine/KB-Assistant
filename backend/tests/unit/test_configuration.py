import importlib

import pytest
from pydantic import ValidationError

from kb_assistant.settings import Settings, WorkerSettings


def _env(monkeypatch: pytest.MonkeyPatch, *, with_auth: bool = True) -> None:
    monkeypatch.setenv("KB_DATABASE__URL", "postgresql+asyncpg://app:Zq9-db-password@db:5432/kb")
    monkeypatch.setenv("KB_REDIS__URL", "redis://redis:6379/0")
    monkeypatch.setenv("KB_STORAGE__ACCESS_KEY", "s3-access")
    monkeypatch.setenv("KB_STORAGE__SECRET_KEY", "Zq9-s3-secret")
    if with_auth:
        monkeypatch.setenv("KB_AUTH__PRIVATE_KEY_FILE", "/run/secrets/jwt_signing_key")
        monkeypatch.setenv("KB_AUTH__KEY_ID", "2026-10")


def test_settings_load_from_nested_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    settings = Settings()
    assert settings.auth.key_id == "2026-10"
    assert settings.auth.access_ttl_s == 600


def test_secrets_never_appear_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    assert "Zq9-db-password" not in repr(Settings())
    assert "Zq9-s3-secret" not in repr(Settings())
    assert "Zq9-db-password" not in str(Settings().model_dump())


def test_missing_values_fail_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, with_auth=False)
    with pytest.raises(ValidationError):
        Settings()


def test_access_token_lifetime_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    monkeypatch.setenv("KB_AUTH__ACCESS_TTL_S", str(24 * 3600))
    with pytest.raises(ValidationError):
        Settings()


def test_worker_does_not_need_the_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, with_auth=False)
    WorkerSettings()


def test_celery_is_configured_for_safe_at_least_once_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch, with_auth=False)
    worker = importlib.import_module("kb_assistant.worker.app")
    conf = worker.app.conf
    assert conf.task_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.task_time_limit > conf.task_soft_time_limit
    assert "kb.ping" in worker.app.tasks
    assert worker.INGEST_TASK in worker.app.tasks


def test_the_api_and_the_worker_agree_on_the_task_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """They share a contract, not code: this test is what keeps the contract honest."""
    _env(monkeypatch, with_auth=False)
    from kb_assistant.infrastructure.jobs import INGEST_TASK

    worker = importlib.import_module("kb_assistant.worker.app")
    assert worker.INGEST_TASK == INGEST_TASK


def test_hosted_providers_require_their_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, with_auth=False)
    monkeypatch.setenv("KB_EMBEDDINGS__PROVIDER", "openai")
    with pytest.raises(ValidationError, match="api_key"):
        WorkerSettings()
    monkeypatch.setenv("KB_EMBEDDINGS__PROVIDER", "hashing")
    monkeypatch.setenv("KB_VECTOR_STORE__PROVIDER", "pinecone")
    with pytest.raises(ValidationError, match="pinecone"):
        WorkerSettings()
