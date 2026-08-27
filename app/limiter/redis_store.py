from __future__ import annotations

import time
from dataclasses import dataclass

import redis.asyncio as redis
from redis.commands.core import AsyncScript

# KEYS[1] = current fixed-window counter key
# KEYS[2] = previous fixed-window counter key
# ARGV[1] = limit
# ARGV[2] = window_seconds
# ARGV[3] = now (unix seconds, float)
#
# Sliding window counter: estimates the request count over the trailing
# window as current_count + previous_count * (1 - elapsed_fraction). The
# whole check-and-increment runs inside this script so the read, the limit
# comparison, and the write are a single atomic Redis operation.
SLIDING_WINDOW_LUA = """
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local window_index = math.floor(now / window)
local elapsed = now - (window_index * window)
local elapsed_fraction = elapsed / window

local current_count = tonumber(redis.call('GET', KEYS[1])) or 0
local previous_count = tonumber(redis.call('GET', KEYS[2])) or 0

local estimated_count = current_count + previous_count * (1 - elapsed_fraction)
local reset_at = (window_index + 1) * window

if estimated_count + 1 > limit then
    return {0, limit, 0, reset_at}
end

local new_current = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], window * 2)

local remaining = limit - math.floor(estimated_count) - 1
if remaining < 0 then
    remaining = 0
end

return {1, limit, remaining, reset_at}
"""


@dataclass
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_at: float
    retry_after: float | None


class RedisSlidingWindowStore:
    """Sliding-window-counter rate limiter backed by Redis.

    The check-and-increment is a single atomic Lua script call (registered
    once via `register_script`, cached server-side) — never a separate
    GET followed by a separate INCR from Python.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: redis.Redis | None = None
        self._script: AsyncScript | None = None

    async def connect(self) -> None:
        self._redis = redis.from_url(self._redis_url, decode_responses=True)
        self._script = self._redis.register_script(SLIDING_WINDOW_LUA)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
        self._redis = None
        self._script = None

    async def check(self, policy_name: str, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        if self._script is None:
            raise RuntimeError("RedisSlidingWindowStore.connect() was not called")

        now = time.time()
        window_index = int(now // window_seconds)
        current_key = f"rl:{policy_name}:{key}:{window_index}"
        previous_key = f"rl:{policy_name}:{key}:{window_index - 1}"

        allowed, out_limit, remaining, reset_at = await self._script(
            keys=[current_key, previous_key],
            args=[limit, window_seconds, now],
        )

        reset_at = float(reset_at)
        if allowed:
            return RateLimitDecision(
                allowed=True,
                limit=int(out_limit),
                remaining=int(remaining),
                reset_at=reset_at,
                retry_after=None,
            )
        return RateLimitDecision(
            allowed=False,
            limit=int(out_limit),
            remaining=int(remaining),
            reset_at=reset_at,
            retry_after=max(reset_at - now, 0),
        )
