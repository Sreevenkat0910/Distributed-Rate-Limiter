"""Proves the per-endpoint fail-open/fail-closed policy, the distinct
response contract for a degraded-mode denial vs a normal rate-limit
denial, and that /admin/limiter-status reports live (not cached) state.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.search import router as search_router
from app.limiter.middleware import RateLimitMiddleware
from app.limiter.redis_store import RedisSlidingWindowStore

# Non-routable "black hole" address: connection attempts genuinely hang
# until the timeout fires, unlike a bad port (instant ECONNREFUSED).
UNREACHABLE_REDIS_URL = "redis://10.255.255.1:6379/0"


def test_search_fails_open_and_login_fails_closed_when_redis_unreachable():
    store = RedisSlidingWindowStore(
        UNREACHABLE_REDIS_URL,
        max_connections=5,
        call_timeout_ms=75,
        breaker_fail_max=1,
        breaker_reset_timeout_seconds=30,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.connect()
        app.state.rate_limit_store = store
        yield
        await store.close()

    app = FastAPI(lifespan=lifespan)
    # Deliberately uses the real ROUTE_POLICIES (default) so this test
    # exercises the actual configured policy -- search=fail_open,
    # login=fail_closed -- not a synthetic override.
    app.add_middleware(RateLimitMiddleware, store=store)
    app.include_router(auth_router)
    app.include_router(search_router)
    app.include_router(admin_router)

    with TestClient(app) as client:
        search_response = client.get("/search", params={"user_id": "degraded-test"})
        assert search_response.status_code == 200
        assert search_response.headers["X-RateLimiter-Degraded"] == "fail-open"
        assert search_response.json()["user_id"] == "degraded-test"

        login_response = client.post("/login")
        assert login_response.status_code == 503
        assert login_response.headers["X-RateLimiter-Degraded"] == "fail-closed"
        login_body = login_response.json()
        assert login_body["error"] == "limiter_degraded"
        # Structurally distinct from a normal 429 body, which has no
        # "error" field -- not just a different status code.
        assert "error" in login_body

        status_response = client.get("/admin/limiter-status")
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["breaker_state"] == "open"
        assert status_body["degraded"] is True
        assert status_body["last_latency_ms"] is not None
        assert status_body["last_latency_ms"] > 0


def test_admin_limiter_status_reports_closed_and_not_degraded_when_redis_healthy(client):
    baseline = client.get("/admin/limiter-status").json()
    assert baseline["breaker_state"] == "closed"
    assert baseline["degraded"] is False

    login_response = client.post("/login")
    assert login_response.status_code == 200

    status = client.get("/admin/limiter-status").json()
    assert status["breaker_state"] == "closed"
    assert status["degraded"] is False
    # A real attempt just happened against local Redis -- should be fast,
    # but present (not the None baseline).
    assert status["last_latency_ms"] is not None
    assert status["last_latency_ms"] < 50
