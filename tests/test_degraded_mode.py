"""Proves the per-endpoint fail-open/fail-closed policy, the distinct
response contract for a degraded-mode denial vs a normal rate-limit
denial, and that /admin/limiter-status reports live (not cached) state.
"""

import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.search import router as search_router
from app.limiter.middleware import RateLimitMiddleware
from app.limiter.redis_store import RedisSlidingWindowStore

# Non-routable "black hole" address: connection attempts genuinely hang
# until the timeout fires, unlike a bad port (instant ECONNREFUSED). Good
# for proving timing; useless for proving recovery, since it can never
# "become reachable."
UNREACHABLE_REDIS_URL = "redis://10.255.255.1:6379/0"


def _build_test_app(store: RedisSlidingWindowStore) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.connect()
        app.state.rate_limit_store = store
        yield
        await store.close()

    app = FastAPI(lifespan=lifespan)
    # Deliberately uses the real ROUTE_POLICIES (default) so these tests
    # exercise the actual configured policy -- search=fail_open,
    # login=fail_closed -- not a synthetic override.
    app.add_middleware(RateLimitMiddleware, store=store)
    app.include_router(auth_router)
    app.include_router(search_router)
    app.include_router(admin_router)
    return app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_redis_ready(port: int, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["redis-cli", "-p", str(port), "ping"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.stdout.strip() == "PONG":
            return
        time.sleep(0.05)
    raise RuntimeError(f"redis-server on port {port} did not become ready in time")


def test_search_fails_open_and_login_fails_closed_when_redis_unreachable():
    store = RedisSlidingWindowStore(
        UNREACHABLE_REDIS_URL,
        max_connections=5,
        call_timeout_ms=75,
        breaker_fail_max=1,
        breaker_reset_timeout_seconds=30,
    )
    app = _build_test_app(store)

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


def test_open_breaker_short_circuits_near_instantly_through_http():
    """Once the breaker is open, /search (fail-open) and /login
    (fail-closed) must both return near-instantly -- not wait out the
    timeout again -- even though they reach opposite allow/deny
    decisions. Asserted on real elapsed time, not assumed from the code."""
    call_timeout_ms = 75
    fail_max = 3
    store = RedisSlidingWindowStore(
        UNREACHABLE_REDIS_URL,
        max_connections=5,
        call_timeout_ms=call_timeout_ms,
        breaker_fail_max=fail_max,
        breaker_reset_timeout_seconds=30,
    )
    app = _build_test_app(store)

    with TestClient(app) as client:
        # Pre-threshold: each of the first fail_max calls is a real
        # attempt bounded by the timeout -- meaningfully slower than the
        # post-open short-circuit will be.
        for i in range(fail_max):
            t0 = time.monotonic()
            response = client.get("/search", params={"user_id": "timing-test"})
            elapsed_ms = (time.monotonic() - t0) * 1000
            assert response.status_code == 200
            assert elapsed_ms > call_timeout_ms * 0.5, (
                f"pre-open call {i} returned in {elapsed_ms:.1f}ms -- too fast to have "
                f"actually attempted the {call_timeout_ms}ms-bounded Redis call"
            )

        # Post-threshold: breaker is open. Both endpoints should
        # short-circuit near-instantly despite opposite decisions.
        t0 = time.monotonic()
        search_response = client.get("/search", params={"user_id": "timing-test"})
        search_elapsed_ms = (time.monotonic() - t0) * 1000
        assert search_response.status_code == 200
        assert search_elapsed_ms < call_timeout_ms * 0.5, (
            f"post-open /search took {search_elapsed_ms:.1f}ms -- expected a near-instant "
            f"short-circuit, not another real timeout"
        )

        t0 = time.monotonic()
        login_response = client.post("/login")
        login_elapsed_ms = (time.monotonic() - t0) * 1000
        assert login_response.status_code == 503
        assert login_elapsed_ms < call_timeout_ms * 0.5, (
            f"post-open /login took {login_elapsed_ms:.1f}ms -- expected a near-instant "
            f"short-circuit, not another real timeout"
        )


def test_breaker_recovers_and_resumes_real_enforcement_when_redis_returns():
    """Full lifecycle: Redis down -> breaker opens -> Redis comes back up
    -> breaker closes on its own -> real Redis-backed enforcement resumes
    (proven by actually exhausting the real limit and getting a genuine
    429, not just by reading the reported state)."""
    port = _free_port()
    redis_url = f"redis://127.0.0.1:{port}/0"
    breaker_reset_timeout_seconds = 1.5

    store = RedisSlidingWindowStore(
        redis_url,
        max_connections=5,
        call_timeout_ms=200,
        breaker_fail_max=2,
        breaker_reset_timeout_seconds=breaker_reset_timeout_seconds,
    )
    app = _build_test_app(store)

    redis_process: subprocess.Popen | None = None
    tmp_dir = tempfile.mkdtemp(prefix="rate-limiter-test-redis-")
    try:
        with TestClient(app) as client:
            # Nothing is listening on `port` yet -- calls fail fast via
            # ECONNREFUSED, opening the breaker after breaker_fail_max.
            for _ in range(2):
                assert client.post("/login").status_code == 503

            status = client.get("/admin/limiter-status").json()
            assert status["breaker_state"] == "open"
            assert status["degraded"] is True

            # Bring Redis up on the exact port the store is already
            # pointed at -- same store/breaker instance throughout.
            redis_process = subprocess.Popen(
                ["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"],
                cwd=tmp_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _wait_for_redis_ready(port)

            # Wait past the breaker's cooldown so the next call goes
            # half-open and genuinely retries against the now-live Redis.
            time.sleep(breaker_reset_timeout_seconds + 0.3)

            recovery_response = client.post("/login")
            assert recovery_response.status_code == 200

            status = client.get("/admin/limiter-status").json()
            assert status["breaker_state"] == "closed"
            assert status["degraded"] is False

            # Prove real enforcement resumed, not just a relabeled state:
            # exhaust the real 5/min limit (1 used by recovery_response
            # above) and confirm a genuine 429.
            for _ in range(4):
                assert client.post("/login").status_code == 200
            assert client.post("/login").status_code == 429
    finally:
        if redis_process is not None:
            redis_process.terminate()
            try:
                redis_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                redis_process.kill()
                redis_process.wait(timeout=5)
        shutil.rmtree(tmp_dir, ignore_errors=True)
