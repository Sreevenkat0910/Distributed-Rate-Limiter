"""Orchestrates the Redis-degradation load test: starts k6 running
degradation.js, then independently injects a Toxiproxy latency toxic on
the app-to-Redis connection at a fixed point mid-run, holds it, and
removes it -- all timed by wall clock from this controller, not from
inside the k6 script.

Why external orchestration instead of an HTTP call from within
degradation.js: k6's VU/iteration model has no clean way to say "make
exactly one HTTP call, once, at wall-clock time T" without fragile
shared-state hacks across VUs. A controller that just watches a clock is
far more reliable, and it keeps the load-generation script itself nearly
identical to normal.js.

Usage:
    python3 loadtest/run_degradation_test.py
"""

import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOXIPROXY_URL = "http://localhost:8474"
PROXY_NAME = "redis"
TOXIC_NAME = "redis-latency"

TOXIC_LATENCY_MS = 1200
TOXIC_JITTER_MS = 100
INJECT_AFTER_SECONDS = 45  # 15s into the 60s hold stage
HOLD_SECONDS = 20  # toxic active for this long, then removed

LOADTEST_DIR = Path(__file__).parent
K6_SCRIPT = LOADTEST_DIR / "degradation.js"
METRICS_PATH = LOADTEST_DIR / "degradation-metrics.jsonl"
PHASES_PATH = LOADTEST_DIR / "degradation-phases.json"


def _http_json(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{TOXIPROXY_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def _wait_for_toxiproxy_ready(timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            _http_json("GET", "/version")
            return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.3)
    raise RuntimeError("toxiproxy admin API never became reachable")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def main() -> None:
    print("Waiting for toxiproxy admin API...")
    _wait_for_toxiproxy_ready()

    # Sanity: the proxy exists and starts with no toxics.
    proxy = _http_json("GET", f"/proxies/{PROXY_NAME}")
    if proxy.get("toxics"):
        raise RuntimeError(f"proxy '{PROXY_NAME}' already has toxics active -- clean up before running")

    METRICS_PATH.unlink(missing_ok=True)

    print(f"Starting k6 ({K6_SCRIPT.name})...")
    test_start = datetime.now(timezone.utc)
    proc = subprocess.Popen(
        ["k6", "run", str(K6_SCRIPT), f"--out=json={METRICS_PATH}"],
        cwd=LOADTEST_DIR.parent,
    )

    print(f"Sleeping {INJECT_AFTER_SECONDS}s before injecting the toxic...")
    time.sleep(INJECT_AFTER_SECONDS)

    inject_at = datetime.now(timezone.utc)
    print(f"[{inject_at.isoformat()}] Injecting {TOXIC_LATENCY_MS}ms latency toxic on '{PROXY_NAME}'...")
    _http_json(
        "POST",
        f"/proxies/{PROXY_NAME}/toxics",
        {
            "name": TOXIC_NAME,
            "type": "latency",
            "stream": "downstream",
            "attributes": {"latency": TOXIC_LATENCY_MS, "jitter": TOXIC_JITTER_MS},
        },
    )

    print(f"Holding toxic for {HOLD_SECONDS}s...")
    time.sleep(HOLD_SECONDS)

    remove_at = datetime.now(timezone.utc)
    print(f"[{remove_at.isoformat()}] Removing toxic...")
    _http_json("DELETE", f"/proxies/{PROXY_NAME}/toxics/{TOXIC_NAME}")

    print("Waiting for k6 to finish...")
    proc.wait()
    test_end = datetime.now(timezone.utc)

    phases = {
        "test_start": _iso(test_start),
        "inject_at": _iso(inject_at),
        "remove_at": _iso(remove_at),
        "test_end": _iso(test_end),
        "toxic_latency_ms": TOXIC_LATENCY_MS,
    }
    PHASES_PATH.write_text(json.dumps(phases, indent=2))
    print(f"Wrote {PHASES_PATH}")
    print(f"Wrote {METRICS_PATH}")
    print(json.dumps(phases, indent=2))


if __name__ == "__main__":
    main()
