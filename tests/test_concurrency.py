import asyncio
import uuid

import pytest

from app.core.config import get_settings
from app.limiter.redis_store import RedisSlidingWindowStore


@pytest.mark.asyncio
async def test_concurrent_requests_allow_exactly_the_limit():
    """Proves the Lua script's check-and-increment is atomic: 200 concurrent
    calls against a single shared key with limit=100 must produce exactly
    100 allowed and exactly 100 denied. Any deviation means the script (or
    the Python code around it) has a race, not "approximately right"."""
    limit = 100
    concurrent_requests = 200
    window_seconds = 60
    policy_name = f"concurrency-test-{uuid.uuid4().hex}"
    key = "single-shared-key"

    settings = get_settings()
    store = RedisSlidingWindowStore(settings.redis_url, max_connections=settings.redis_max_connections)
    await store.connect()
    try:
        decisions = await asyncio.gather(
            *(store.check(policy_name, key, limit, window_seconds) for _ in range(concurrent_requests))
        )
    finally:
        await store.close()

    allowed = sum(1 for decision in decisions if decision.allowed)
    denied = sum(1 for decision in decisions if not decision.allowed)
    print(f"\nconcurrency result: allowed={allowed} denied={denied} (fired {concurrent_requests}, limit {limit})")

    assert allowed == limit, f"expected exactly {limit} allowed, got {allowed}"
    assert denied == concurrent_requests - limit, f"expected exactly {concurrent_requests - limit} denied, got {denied}"
    assert allowed + denied == concurrent_requests
