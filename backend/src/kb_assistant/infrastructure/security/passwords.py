"""Argon2id password hashing.

argon2-cffi's defaults follow RFC 9106's recommended low-memory profile
(3 passes, 64 MiB, 4 lanes), above the OWASP Password Storage Cheat Sheet
minimum. Tests pass cheaper parameters; production never does.
"""

import asyncio
import secrets
from dataclasses import replace

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.profiles import RFC_9106_LOW_MEMORY


class Argon2PasswordHasher:
    def __init__(
        self,
        *,
        time_cost: int | None = None,
        memory_cost: int | None = None,
        parallelism: int | None = None,
    ) -> None:
        profile = RFC_9106_LOW_MEMORY
        self._argon2 = _Argon2.from_parameters(
            replace(
                profile,
                time_cost=time_cost or profile.time_cost,
                memory_cost=memory_cost or profile.memory_cost,
                parallelism=parallelism or profile.parallelism,
            )
        )
        # Verified against for unknown emails, so they cost the same time as known ones.
        self._dummy_hash = self._argon2.hash(secrets.token_urlsafe(16))

    # argon2-cffi releases the GIL, so a worker thread gives real parallelism
    # and keeps the event loop free while a hash takes tens of milliseconds.

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._argon2.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        return await asyncio.to_thread(self._verify_sync, password_hash, password)

    async def verify_dummy(self, password: str) -> None:
        await self.verify(self._dummy_hash, password)

    def _verify_sync(self, password_hash: str, password: str) -> bool:
        try:
            return self._argon2.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._argon2.check_needs_rehash(password_hash)
