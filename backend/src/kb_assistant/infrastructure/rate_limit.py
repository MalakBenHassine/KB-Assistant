"""Token-bucket rate limiting in Redis (SECURITY.md T8, ADR-0018).

One Lua script reads, refills and spends the bucket atomically, using the
Redis server's clock, so API replicas share one limit and never disagree on
time. A bucket expires once it would be full again, so idle keys cost nothing.

If Redis is unreachable the request is allowed and a warning is logged: a rate
limit protects capacity and cost, and the monthly token budget (Postgres)
still bounds the cost. Refusing every question because a cache is down would
turn a Redis incident into a full outage.
"""

import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from kb_assistant.domain.governance import Limit, RateDecision

log = logging.getLogger(__name__)

KEY_PREFIX = "kb:rl:"

# KEYS[1]: bucket; ARGV[1]: capacity; ARGV[2]: refill per second.
# Returns {allowed (0|1), seconds until a token is available (string)}.
_BUCKET_SCRIPT = """
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  wait = (1 - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 1)
return {allowed, tostring(wait)}
"""


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._script: Any = redis.register_script(_BUCKET_SCRIPT)

    async def hit(self, key: str, limit: Limit) -> RateDecision:
        try:
            allowed, wait = await self._script(
                keys=[KEY_PREFIX + key], args=[limit.burst, limit.refill_per_second]
            )
        except RedisError:
            log.warning("rate_limit.unavailable_allowing", extra={"key_kind": key.split(":")[0]})
            return RateDecision(allowed=True)
        return RateDecision(allowed=bool(int(allowed)), retry_after_s=float(wait))
