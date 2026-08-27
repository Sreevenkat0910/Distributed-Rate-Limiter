"""Orchestrates the complete-Redis-outage test: starts k6 running
outage.js, then independently stops the redis container mid-run, holds
the outage, restarts it, and polls /admin/limiter-status to detect
recovery -- all timed by wall clock from this controller, same pattern as
Phase 7's run_degradation_test.py, but the fault here is a real container
stop/start rather than a simulated Toxiproxy latency toxic.

Usage:
    python3 loadtest/run_outage_test.py
"""

import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
LOADTEST_DIR = Path(__file__).parent

BASE_URL = "http://localhost:8080"
OUTAGE_AFTER_SECONDS = 45  # 15s into the 60s hold stage
OUTAGE_HOLD_SECONDS = 20

K6_SCRIPT = LOADTEST_DIR / "outage.js"
METRICS_PATH = LOADTEST_DIR / "outage-metrics.jsonl"
SUMMARY_PATH = LOADTEST_DIR / "outage-summary.json"
PHASES_PATH = LOADTEST_DIR / "outage-phases.json"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _compose(*args: str) -> None:
    subprocess.run(["docker", "compose", "-f", str(COMPOSE_FILE), *args], check=True)


def _limiter_status() -> dict | None:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/admin/limiter-status", timeout=2) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return None


def _poll_until_recovered(timeout_seconds: float = 60.0, consecutive_needed: int = 6) -> datetime | None:
    """Polls /admin/limiter-status repeatedly (nginx round-robins across
    replicas) until `consecutive_needed` consecutive polls all report
    breaker_state == "closed" -- since each replica has its own
    independent breaker, a handful of consecutive closed reads is a
    reasonable signal that all three have recovered, not just whichever
    one happened to answer first."""
    deadline = time.monotonic() + timeout_seconds
    consecutive = 0
    first_closed_at = None
    while time.monotonic() < deadline:
        status = _limiter_status()
        if status is not None and status.get("breaker_state") == "closed":
            if consecutive == 0:
                first_closed_at = datetime.now(timezone.utc)
            consecutive += 1
            if consecutive >= consecutive_needed:
                return first_closed_at
        else:
            consecutive = 0
            first_closed_at = None
        time.sleep(0.3)
    return None


def main() -> None:
    METRICS_PATH.unlink(missing_ok=True)
    SUMMARY_PATH.unlink(missing_ok=True)

    print(f"Starting k6 ({K6_SCRIPT.name})...")
    test_start = datetime.now(timezone.utc)
    proc = subprocess.Popen(
        ["k6", "run", str(K6_SCRIPT), f"--out=json={METRICS_PATH}", f"--summary-export={SUMMARY_PATH}"],
        cwd=REPO_ROOT,
    )

    print(f"Sleeping {OUTAGE_AFTER_SECONDS}s before stopping redis...")
    time.sleep(OUTAGE_AFTER_SECONDS)

    print("\n*** STOPPING THE REDIS CONTAINER NOW (real, brief outage) ***\n")
    stop_issued_at = datetime.now(timezone.utc)
    _compose("stop", "redis")
    stop_completed_at = datetime.now(timezone.utc)
    print(f"[{stop_completed_at.isoformat()}] redis stopped.")

    print(f"Holding outage for {OUTAGE_HOLD_SECONDS}s...")
    time.sleep(OUTAGE_HOLD_SECONDS)

    start_issued_at = datetime.now(timezone.utc)
    _compose("start", "redis")
    start_completed_at = datetime.now(timezone.utc)
    print(f"[{start_completed_at.isoformat()}] redis restarted (container command returned).")

    print("Polling /admin/limiter-status for recovery...")
    recovery_detected_at = _poll_until_recovered()
    if recovery_detected_at:
        lag = (recovery_detected_at - start_completed_at).total_seconds()
        print(f"[{recovery_detected_at.isoformat()}] Recovery detected -- {lag:.1f}s after redis restart completed.")
    else:
        print("WARNING: recovery not detected within the polling window.")

    print("Waiting for k6 to finish...")
    proc.wait()
    test_end = datetime.now(timezone.utc)

    phases = {
        "test_start": _iso(test_start),
        "stop_issued_at": _iso(stop_issued_at),
        "stop_completed_at": _iso(stop_completed_at),
        "start_issued_at": _iso(start_issued_at),
        "start_completed_at": _iso(start_completed_at),
        "recovery_detected_at": _iso(recovery_detected_at) if recovery_detected_at else None,
        "test_end": _iso(test_end),
    }
    PHASES_PATH.write_text(json.dumps(phases, indent=2))
    print(f"Wrote {PHASES_PATH}")
    print(f"Wrote {SUMMARY_PATH}")
    print(f"Wrote {METRICS_PATH}")
    print(json.dumps(phases, indent=2))


if __name__ == "__main__":
    main()
