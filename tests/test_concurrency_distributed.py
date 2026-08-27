"""Proves the "distributed" claim of the project: the sliding-window limit
is enforced correctly in aggregate across multiple app replicas sharing one
Redis, not per-replica.

Precondition: the full stack must already be running via
`docker compose -f infra/docker-compose.yml up` (redis + app1/app2/app3 +
nginx on localhost:8080). This test does not start that stack itself --
it's a system-level proof against the live containers, not an isolated
unit test.
"""

import asyncio
import uuid
from collections import Counter

import pytest
from httpx import AsyncClient

NGINX_BASE_URL = "http://localhost:8080"


@pytest.mark.asyncio
async def test_concurrent_requests_across_replicas_allow_exactly_the_limit():
    limit = 100
    concurrent_requests = 200
    user_id = f"distributed-concurrency-test-{uuid.uuid4().hex}"

    async with AsyncClient(base_url=NGINX_BASE_URL, timeout=30.0) as client:
        responses = await asyncio.gather(
            *(client.get("/search", params={"user_id": user_id}) for _ in range(concurrent_requests))
        )

    status_codes = [r.status_code for r in responses]
    allowed = status_codes.count(200)
    denied = status_codes.count(429)

    replica_hits = Counter(r.headers.get("x-replica-id", "missing") for r in responses)
    print(f"\nreplica distribution: {dict(replica_hits)}")
    print(f"result: allowed={allowed} denied={denied} (fired {concurrent_requests}, limit {limit})")

    # If nginx (or connection reuse) quietly pinned every request to one
    # container, a correct count wouldn't actually prove anything
    # "distributed" -- so require real spread across replicas first.
    assert len(replica_hits) > 1, f"all requests landed on one replica: {replica_hits}"

    assert allowed == limit, f"expected exactly {limit} allowed, got {allowed} ({replica_hits})"
    assert denied == concurrent_requests - limit, (
        f"expected exactly {concurrent_requests - limit} denied, got {denied} ({replica_hits})"
    )
    assert allowed + denied == concurrent_requests
