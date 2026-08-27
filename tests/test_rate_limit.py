import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.limiter.middleware import RateLimitMiddleware
from app.limiter.policy import RateLimitPolicy, client_ip_key


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
    # real 60s sleep or touch the real app's policy table.
    short_window_policies = {
        ("POST", "/login"): RateLimitPolicy(
            name="login-reset-test",
            limit=1,
            window_seconds=2,
            key_func=client_ip_key,
        )
    }
    test_app = FastAPI()
    test_app.add_middleware(RateLimitMiddleware, policies=short_window_policies)
    test_app.include_router(auth_router)

    with TestClient(test_app) as short_window_client:
        first = short_window_client.post("/login")
        assert first.status_code == 200

        blocked = short_window_client.post("/login")
        assert blocked.status_code == 429

        time.sleep(2.3)

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
