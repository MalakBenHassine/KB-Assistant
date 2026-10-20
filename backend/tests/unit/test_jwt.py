"""Access tokens: every way a token can be wrong must end in the same 401."""

import base64
import json
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from kb_assistant.domain.errors import NotAuthenticatedError
from kb_assistant.domain.ids import TenantId, UserId
from kb_assistant.infrastructure.security.jwt import JwtAccessTokenService, SigningKeys
from tests.support import FakeClock

USER = UserId(uuid4())
TENANT = TenantId(uuid4())


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(signing_keys: SigningKeys, clock: FakeClock) -> JwtAccessTokenService:
    return JwtAccessTokenService(
        keys=signing_keys,
        issuer="kb-assistant",
        audience="kb-assistant-api",
        ttl_seconds=600,
        clock_skew_seconds=30,
        clock=clock,
    )


def _b64(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_round_trip(service: JwtAccessTokenService) -> None:
    claims = service.verify(service.issue(user_id=USER, tenant_id=TENANT, token_version=3))
    assert (claims.user_id, claims.tenant_id, claims.token_version) == (USER, TENANT, 3)


def test_expired_token_is_rejected(service: JwtAccessTokenService, clock: FakeClock) -> None:
    token = service.issue(user_id=USER, tenant_id=TENANT, token_version=0)
    clock.advance(seconds=600 + 31)
    with pytest.raises(NotAuthenticatedError):
        service.verify(token)


def test_clock_skew_is_tolerated(service: JwtAccessTokenService, clock: FakeClock) -> None:
    token = service.issue(user_id=USER, tenant_id=TENANT, token_version=0)
    clock.advance(seconds=600 + 10)
    service.verify(token)


def test_token_from_the_future_is_rejected(
    service: JwtAccessTokenService, clock: FakeClock
) -> None:
    token = service.issue(user_id=USER, tenant_id=TENANT, token_version=0)
    clock.advance(seconds=-120)
    with pytest.raises(NotAuthenticatedError):
        service.verify(token)


@pytest.mark.parametrize(
    ("issuer", "audience"), [("evil", "kb-assistant-api"), ("kb-assistant", "other-api")]
)
def test_wrong_issuer_or_audience_is_rejected(
    signing_keys: SigningKeys, clock: FakeClock, issuer: str, audience: str
) -> None:
    other = JwtAccessTokenService(
        keys=signing_keys,
        issuer=issuer,
        audience=audience,
        ttl_seconds=600,
        clock_skew_seconds=0,
        clock=clock,
    )
    token = other.issue(user_id=USER, tenant_id=TENANT, token_version=0)
    service = JwtAccessTokenService(
        keys=signing_keys,
        issuer="kb-assistant",
        audience="kb-assistant-api",
        ttl_seconds=600,
        clock_skew_seconds=0,
        clock=clock,
    )
    with pytest.raises(NotAuthenticatedError):
        service.verify(token)


def test_token_signed_by_another_key_is_rejected(
    service: JwtAccessTokenService, clock: FakeClock
) -> None:
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = JwtAccessTokenService(
        keys=SigningKeys.from_private_key("test-key", stranger),
        issuer="kb-assistant",
        audience="kb-assistant-api",
        ttl_seconds=600,
        clock_skew_seconds=0,
        clock=clock,
    ).issue(user_id=USER, tenant_id=TENANT, token_version=0)
    with pytest.raises(NotAuthenticatedError):
        service.verify(forged)


def test_unknown_key_id_is_rejected(service: JwtAccessTokenService) -> None:
    token = service.issue(user_id=USER, tenant_id=TENANT, token_version=0)
    _header, payload, signature = token.split(".")
    tampered = f"{_b64({'alg': 'RS256', 'typ': 'JWT', 'kid': 'other'})}.{payload}.{signature}"
    with pytest.raises(NotAuthenticatedError):
        service.verify(tampered)


def test_alg_none_is_rejected(service: JwtAccessTokenService) -> None:
    token = service.issue(user_id=USER, tenant_id=TENANT, token_version=0)
    payload = token.split(".")[1]
    unsigned = f"{_b64({'alg': 'none', 'typ': 'JWT', 'kid': 'test-key'})}.{payload}."
    with pytest.raises(NotAuthenticatedError):
        service.verify(unsigned)


def test_hs256_signed_with_the_public_key_is_rejected(
    service: JwtAccessTokenService, rsa_private_key: RSAPrivateKey
) -> None:
    """The classic algorithm-confusion attack: the public key is public."""
    public_pem = rsa_private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    claims = jwt.decode(
        service.issue(user_id=USER, tenant_id=TENANT, token_version=0),
        options={"verify_signature": False},
    )
    # PyJWT refuses to HMAC with a PEM key, so build the forged token by hand.
    import hashlib
    import hmac

    signing_input = f"{_b64({'alg': 'HS256', 'typ': 'JWT', 'kid': 'test-key'})}.{_b64(claims)}"
    mac = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    forged = f"{signing_input}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"
    with pytest.raises(NotAuthenticatedError):
        service.verify(forged)


def test_tampered_payload_is_rejected(service: JwtAccessTokenService) -> None:
    token = service.issue(user_id=USER, tenant_id=TENANT, token_version=0)
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["tid"] = str(uuid4())
    with pytest.raises(NotAuthenticatedError):
        service.verify(f"{header}.{_b64(claims)}.{signature}")


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x"])
def test_garbage_is_rejected(service: JwtAccessTokenService, garbage: str) -> None:
    with pytest.raises(NotAuthenticatedError):
        service.verify(garbage)


def test_jwks_publishes_the_public_key_only(service: JwtAccessTokenService) -> None:
    (key,) = service.jwks()["keys"]
    assert key["kid"] == "test-key"
    assert key["alg"] == "RS256"
    assert key["use"] == "sig"
    assert "d" not in key  # the private exponent


def test_short_rsa_keys_are_refused() -> None:
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505 - deliberately weak
    with pytest.raises(ValueError, match="at least"):
        SigningKeys.from_private_key("weak", weak)
