"""RS256 access tokens (ADR-0005).

Verification never trusts the token's header beyond the key id: the algorithm
is pinned to RS256, so `alg: none` and the HS256-with-the-public-key confusion
attack are both rejected. Time claims are checked against the injected clock,
which keeps expiry behaviour testable.
"""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from jwt.algorithms import RSAAlgorithm

from kb_assistant.domain.errors import NotAuthenticatedError
from kb_assistant.domain.ids import TenantId, UserId, new_id
from kb_assistant.domain.ports import AccessClaims, Clock

ALGORITHM = "RS256"
MIN_RSA_BITS = 2048
_REQUIRED_CLAIMS = ("iss", "aud", "sub", "tid", "ver", "iat", "nbf", "exp", "jti")
_INVALID = "Invalid or expired access token."


@dataclass(frozen=True, slots=True)
class SigningKeys:
    """The active signing key, plus every public key still accepted (rotation)."""

    active_kid: str
    private_key: RSAPrivateKey
    public_keys: dict[str, RSAPublicKey]

    @classmethod
    def from_private_key(cls, kid: str, private_key: RSAPrivateKey) -> "SigningKeys":
        if private_key.key_size < MIN_RSA_BITS:
            raise ValueError(f"RSA signing keys must be at least {MIN_RSA_BITS} bits")
        return cls(
            active_kid=kid, private_key=private_key, public_keys={kid: private_key.public_key()}
        )

    @classmethod
    def from_pem_file(cls, kid: str, path: Path) -> "SigningKeys":
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, RSAPrivateKey):
            raise ValueError("the signing key must be an RSA private key")
        return cls.from_private_key(kid, key)


class JwtAccessTokenService:
    def __init__(
        self,
        *,
        keys: SigningKeys,
        issuer: str,
        audience: str,
        ttl_seconds: int,
        clock_skew_seconds: int,
        clock: Clock,
    ) -> None:
        self._keys = keys
        self._issuer = issuer
        self._audience = audience
        self._ttl = timedelta(seconds=ttl_seconds)
        self._skew = timedelta(seconds=clock_skew_seconds)
        self._clock = clock

    @property
    def access_ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(self, *, user_id: UserId, tenant_id: TenantId, token_version: int) -> str:
        now = self._clock.now()
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(user_id),
            "tid": str(tenant_id),
            "ver": token_version,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
            "jti": str(new_id()),
        }
        return jwt.encode(
            payload,
            self._keys.private_key,
            algorithm=ALGORITHM,
            headers={"kid": self._keys.active_kid},
        )

    def verify(self, token: str) -> AccessClaims:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            key = self._keys.public_keys.get(kid) if isinstance(kid, str) else None
            if key is None:
                raise NotAuthenticatedError(_INVALID)
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": list(_REQUIRED_CLAIMS),
                    # Checked below against the injected clock.
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
            now = self._clock.now().timestamp()
            skew = self._skew.total_seconds()
            if now >= float(claims["exp"]) + skew or now < float(claims["nbf"]) - skew:
                raise NotAuthenticatedError(_INVALID)
            version = claims["ver"]
            if not isinstance(version, int) or isinstance(version, bool):
                raise NotAuthenticatedError(_INVALID)
            return AccessClaims(
                user_id=UserId(UUID(claims["sub"])),
                tenant_id=TenantId(UUID(claims["tid"])),
                token_version=version,
            )
        except NotAuthenticatedError:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise NotAuthenticatedError(_INVALID) from exc

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        keys = []
        for kid, public_key in sorted(self._keys.public_keys.items()):
            jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
            keys.append({**jwk, "kid": kid, "use": "sig", "alg": ALGORITHM})
        return {"keys": keys}
