import importlib

import pytest
from pydantic import ValidationError

from kb_assistant.settings import Settings, WorkerSettings


def _env(monkeypatch: pytest.MonkeyPatch, *, with_auth: bool = True) -> None:
    monkeypatch.setenv("KB_DATABASE__URL", "postgresql+asyncpg://app:Zq9-db-password@db:5432/kb")
    monkeypatch.setenv("KB_REDIS__URL", "redis://redis:6379/0")
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
