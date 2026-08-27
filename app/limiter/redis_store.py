from __future__ import annotations

import time
from dataclasses import dataclass

import pybreaker
import redis.asyncio as redis
from redis.commands.core import AsyncScript

from app.limiter.circuit_breaker import AsyncCircuitBreaker, LoggingListener

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


class RateLimiterUnavailableError(Exception):
    """Raised when the Redis-backed check couldn't complete -- the circuit
    breaker is open, or the call itself timed out. Callers (the middleware)
    are expected to treat this uniformly as "can't determine the rate
    limit right now"; per-cause fail-open/fail-closed policy is layered on
    top of this, not decided here."""


class RedisSlidingWindowStore:
    """Sliding-window-counter rate limiter backed by Redis.

    The check-and-increment is a single atomic Lua script call (registered
    once via `register_script`, cached server-side) — never a separate
    GET followed by a separate INCR from Python. The call is wrapped in a
    circuit breaker with a hard timeout so a degraded Redis can't stall
    every request behind it.
    """

    def __init__(
        self,
        redis_url: str,
        max_connections: int = 50,
        call_timeout_ms: int = 75,
        breaker_fail_max: int = 5,
        breaker_reset_timeout_seconds: float = 30,
    ) -> None:
        self._redis_url = redis_url
        self._max_connections = max_connections
        self._call_timeout_seconds = call_timeout_ms / 1000
        self._redis: redis.Redis | None = None
        self._script: AsyncScript | None = None

        breaker = pybreaker.CircuitBreaker(
            fail_max=breaker_fail_max,
            reset_timeout=breaker_reset_timeout_seconds,
            listeners=[LoggingListener()],
            name="redis_rate_limiter",
        )
        self._circuit_breaker = AsyncCircuitBreaker(breaker, self._call_timeout_seconds)

    async def connect(self) -> None:
        # BlockingConnectionPool, not the default (non-blocking) pool: with
        # the default pool, a burst of concurrent callers beyond
        # max_connections either raises MaxConnectionsError immediately, or
        # (if max_connections is set high enough to avoid that) forces that
        # many *simultaneous fresh TCP connections*, which -- measured
        # directly -- can itself take well over this store's 75ms call
        # timeout under real concurrency (asyncio's DNS-resolution thread
        # pool becomes the bottleneck, not Redis). With a bounded pool,
        # excess callers instead queue briefly for a connection to free up;
        # that queue wait is still fully covered by the asyncio.wait_for in
        # AsyncCircuitBreaker.call(), which cancels the whole operation --
        # queueing included -- at the configured timeout regardless of the
        # pool's own (much more generous) default wait.
        pool = redis.BlockingConnectionPool.from_url(
            self._redis_url,
            decode_responses=True,
            max_connections=self._max_connections,
            # The client-level socket bound -- not just the asyncio.wait_for
            # wrapper in AsyncCircuitBreaker.call() -- so a hung/slow socket
            # can't sit past the configured timeout regardless of how it's
            # awaited.
            socket_timeout=self._call_timeout_seconds,
            socket_connect_timeout=self._call_timeout_seconds,
        )
        self._redis = redis.Redis(connection_pool=pool)
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

        try:
            allowed, out_limit, remaining, reset_at = await self._circuit_breaker.call(
                self._script,
                keys=[current_key, previous_key],
                args=[limit, window_seconds, now],
            )
        except Exception as exc:
            # Deliberately broad: at this phase, breaker-open, a timed-out
            # call, and a connection failure are all handled identically
            # (a 503 from the middleware) -- distinguishing them belongs to
            # the fail-open/fail-closed policy work, not here.
            raise RateLimiterUnavailableError(str(exc)) from exc

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
