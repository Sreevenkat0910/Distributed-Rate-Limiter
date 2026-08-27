import asyncio

import pytest
import redis.asyncio as redis
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


async def _flush_rate_limit_keys() -> None:
    settings = get_settings()
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        keys = await client.keys("rl:*")
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
def clear_rate_limit_store():
    """The rate limiter's Redis keys persist across tests, so they must be
    flushed between tests to keep them isolated from each other."""
    asyncio.run(_flush_rate_limit_keys())
    yield
    asyncio.run(_flush_rate_limit_keys())


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
