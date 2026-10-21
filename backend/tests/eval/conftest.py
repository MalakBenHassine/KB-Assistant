"""Evaluation fixtures: the shared containers, switched to the chosen profile.

`KB_EVAL_PROFILE=offline` (the default, and CI) uses the hashing embedder and
the extractive model; `openai` uses the hosted models and needs
`OPENAI_API_KEY`. Retrieval settings come from the environment like in
production (`KB_RETRIEVAL__MIN_SCORE`, ...), so a candidate configuration is
evaluated without editing code.
"""

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import SecretStr

from kb_assistant.bootstrap import (
    Container,
    WorkerContainer,
    build_container,
    build_worker_container,
)
from kb_assistant.infrastructure.security.jwt import SigningKeys
from kb_assistant.settings import EmbeddingSettings, LLMSettings, StorageSettings
from tests.conftest import Database, make_settings, make_worker_settings
from tests.fakes import RecordingJobQueue
from tests.support import fast_hasher

PROFILES = ("offline", "openai")


@pytest.fixture(scope="session")
def profile() -> str:
    name = os.environ.get("KB_EVAL_PROFILE", "offline")
    if name not in PROFILES:
        raise pytest.UsageError(f"KB_EVAL_PROFILE must be one of {PROFILES}, not {name!r}")
    return name


def _overrides(profile: str, *, api: bool) -> dict[str, Any]:
    if profile == "offline":
        return {}
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise pytest.UsageError("the openai profile needs OPENAI_API_KEY")
    overrides: dict[str, Any] = {
        "embeddings": EmbeddingSettings(provider="openai", api_key=SecretStr(key))
    }
    if api:
        model = os.environ.get("KB_EVAL_LLM_MODEL", LLMSettings().model)
        overrides["llm"] = LLMSettings(provider="openai", api_key=SecretStr(key), model=model)
    return overrides


@pytest.fixture(scope="session")
async def container(
    database: Database,
    redis_url: str,
    storage_settings: StorageSettings,
    signing_keys: SigningKeys,
    jobs: RecordingJobQueue,
    profile: str,
) -> AsyncIterator[Container]:
    settings = make_settings(database, redis_url, storage_settings)
    built = build_container(
        settings.model_copy(update=_overrides(profile, api=True)),
        hasher=fast_hasher(),
        signing_keys=signing_keys,
        jobs=jobs,
    )
    await built.start()
    yield built
    await built.aclose()


@pytest.fixture(scope="session")
async def worker(
    database: Database,
    redis_url: str,
    storage_settings: StorageSettings,
    container: Container,
    profile: str,
) -> AsyncIterator[WorkerContainer]:
    settings = make_worker_settings(database, redis_url, storage_settings)
    built = build_worker_container(
        settings.model_copy(update=_overrides(profile, api=False)), blobs=container.blobs
    )
    yield built
    await built.aclose()
