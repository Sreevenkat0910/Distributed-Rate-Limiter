import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.core.config import get_settings
from app.limiter.middleware import RateLimitMiddleware
from app.limiter.policy import RateLimitPolicy, client_ip_key
from app.limiter.redis_store import RedisSlidingWindowStore


def test_login_under_limit_succeeds_with_headers(client):
    response = client.post("/login")

    assert response.status_code == 200
    assert response.json()["token"].startswith("demo-token-")
    assert response.headers["X-RateLimit-Limit"] == "5"
    assert response.headers["X-RateLimit-Remaining"] == "4"
    assert int(response.headers["X-RateLimit-Reset"]) >= int(time.time())


def test_search_under_limit_succeeds_with_headers(client):
    response = client.get("/search", params={"user_id": "under-limit-check"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "under-limit-check"
    assert response.headers["X-RateLimit-Limit"] == "100"
    assert response.headers["X-RateLimit-Remaining"] == "99"
    assert int(response.headers["X-RateLimit-Reset"]) >= int(time.time())


def test_login_over_limit_returns_429_with_retry_after(client):
    for _ in range(5):
        allowed = client.post("/login")
        assert allowed.status_code == 200

    blocked = client.post("/login")

    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Limit"] == "5"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in blocked.headers

    retry_after = float(blocked.headers["Retry-After"])
    assert 0 < retry_after <= 60

    assert "detail" in blocked.json()


def test_limit_resets_after_window_elapses():
    # Isolated test app + short-window policy so this doesn't require a
    # real 60s sleep or touch the real app's policy table. The store gets
    # its own lifespan so connect()/close() run on the same event loop
    # TestClient uses to drive requests.
    window_seconds = 2
    short_window_policies = {
        ("POST", "/login"): RateLimitPolicy(
            name="login-reset-test",
            limit=1,
            window_seconds=window_seconds,
            key_func=client_ip_key,
        )
    }
    settings = get_settings()
    store = RedisSlidingWindowStore(settings.redis_url, max_connections=settings.redis_max_connections)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.connect()
        yield
        await store.close()

    test_app = FastAPI(lifespan=lifespan)
    test_app.add_middleware(RateLimitMiddleware, store=store, policies=short_window_policies)
    test_app.include_router(auth_router)

    with TestClient(test_app) as short_window_client:
        first = short_window_client.post("/login")
        assert first.status_code == 200

        blocked = short_window_client.post("/login")
        assert blocked.status_code == 429

        # The sliding-window-counter formula weighs the *previous* fixed
        # window's count by (1 - elapsed_fraction), so its contribution
        # decays gradually across the whole next window rather than
        # dropping to zero the instant window_seconds elapses. To
        # deterministically observe a reset we wait past two full windows
        # (plus a small buffer for scheduling jitter): by then the window
        # immediately preceding "now" was never written to, so its count
        # is genuinely 0 regardless of exactly when in window W the first
        # request landed.
        time.sleep(2 * window_seconds + 0.3)

        after_reset = short_window_client.post("/login")
        assert after_reset.status_code == 200


def test_login_and_search_limits_are_independent(client):
    for _ in range(5):
        client.post("/login")
    blocked_login = client.post("/login")
    assert blocked_login.status_code == 429

    search_response = client.get("/search", params={"user_id": "independence-check"})

    assert search_response.status_code == 200
    assert search_response.headers["X-RateLimit-Remaining"] == "99"
