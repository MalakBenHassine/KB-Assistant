import pytest

from kb_assistant.application.auth import check_password_policy
from kb_assistant.domain.errors import PolicyViolationError
from tests.support import fast_hasher


async def test_hash_then_verify() -> None:
    hasher = fast_hasher()
    hashed = await hasher.hash("a long enough passphrase")
    assert hashed.startswith("$argon2id$")
    assert await hasher.verify(hashed, "a long enough passphrase")
    assert not await hasher.verify(hashed, "another passphrase")


async def test_malformed_hash_is_a_failed_verification_not_a_crash() -> None:
    assert not await fast_hasher().verify("not-a-hash", "whatever")


async def test_dummy_verification_does_not_raise() -> None:
    await fast_hasher().verify_dummy("anything")


async def test_weaker_parameters_trigger_a_rehash() -> None:
    from kb_assistant.infrastructure.security.passwords import Argon2PasswordHasher

    weak_hash = await fast_hasher().hash("a long enough passphrase")
    assert Argon2PasswordHasher().needs_rehash(weak_hash)


@pytest.mark.parametrize("password", ["short", "x" * 257])
def test_password_length_policy(password: str) -> None:
    with pytest.raises(PolicyViolationError):
        check_password_policy(password, "someone@example.com")


def test_password_cannot_be_the_email() -> None:
    with pytest.raises(PolicyViolationError):
        check_password_policy("Someone@Example.com", "someone@example.com")


def test_long_passphrase_is_accepted() -> None:
    check_password_policy("correct horse battery staple", "someone@example.com")
