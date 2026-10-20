"""Opaque one-time secrets (refresh tokens, invitation tokens).

The raw value goes to the client once; only its SHA-256 is stored. A plain,
unsalted hash is enough here because the secret has 256 bits of entropy:
there is nothing to brute-force, unlike a password.
"""

import hashlib
import secrets

_SECRET_BYTES = 32


def new_secret() -> str:
    return secrets.token_urlsafe(_SECRET_BYTES)


def hash_secret(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
