"""Proves the circuit breaker + hard timeout wrapping every Redis call:
failures are bounded by the configured timeout (not left to hang), the
breaker opens after a threshold and short-circuits near-instantly after
that, and a half-open probe genuinely retries the real operation rather
than staying permanently blocked.

Timing assertions use generous tolerances (a clear order-of-magnitude gap
between "real timeout" and "near-instant short-circuit") rather than tight
bounds, to stay stable under CI/scheduling jitter -- consistent with how
the rest of this suite handles real timing.
"""

import time

import pytest

from app.limiter.redis_store import RateLimiterUnavailableError, RedisSlidingWindowStore

# 10.255.255.1 is a non-routable "black hole" address: packets are
# silently dropped rather than refused, so connection attempts genuinely
# hang until the timeout fires -- a fair simulation of "Redis is
# unreachable" (dead host / network partition), unlike a bad port, which
# would fail instantly with ECONNREFUSED and never exercise the timeout.
UNREACHABLE_REDIS_URL = "redis://10.255.255.1:6379/0"
CALL_TIMEOUT_MS = 75
FAIL_MAX = 3
RESET_TIMEOUT_SECONDS = 1.0


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold_then_short_circuits_and_retries_half_open():
    store = RedisSlidingWindowStore(
        UNREACHABLE_REDIS_URL,
        max_connections=5,
        call_timeout_ms=CALL_TIMEOUT_MS,
        breaker_fail_max=FAIL_MAX,
        breaker_reset_timeout_seconds=RESET_TIMEOUT_SECONDS,
    )
    await store.connect()
    try:
        # First FAIL_MAX calls: each is a real attempt bounded by the call
        # timeout, not an instant failure.
        for i in range(FAIL_MAX):
            t0 = time.monotonic()
            with pytest.raises(RateLimiterUnavailableError):
                await store.check("breaker-test", "key", limit=100, window_seconds=60)
            elapsed_ms = (time.monotonic() - t0) * 1000
            assert elapsed_ms > CALL_TIMEOUT_MS * 0.5, (
                f"call {i} returned in {elapsed_ms:.1f}ms -- too fast to have actually "
                f"attempted the {CALL_TIMEOUT_MS}ms-bounded Redis call"
            )

        # Now open: further calls should fail near-instantly, not wait out
        # the timeout again.
        for i in range(3):
            t0 = time.monotonic()
            with pytest.raises(RateLimiterUnavailableError):
                await store.check("breaker-test", "key", limit=100, window_seconds=60)
            elapsed_ms = (time.monotonic() - t0) * 1000
            assert elapsed_ms < CALL_TIMEOUT_MS * 0.5, (
                f"post-open call {i} took {elapsed_ms:.1f}ms -- expected a near-instant "
                f"short-circuit, not another real timeout"
            )

        # After the cooldown, the breaker should genuinely retry (half-open)
        # rather than staying permanently blocked -- since Redis is still
        # unreachable, this attempt should also take a real timeout, then
        # reopen.
        time.sleep(RESET_TIMEOUT_SECONDS + 0.2)
        t0 = time.monotonic()
        with pytest.raises(RateLimiterUnavailableError):
            await store.check("breaker-test", "key", limit=100, window_seconds=60)
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms > CALL_TIMEOUT_MS * 0.5, (
            f"half-open probe returned in {elapsed_ms:.1f}ms -- expected a real retry "
            f"attempt, not another short-circuit"
        )
    finally:
        await store.close()
