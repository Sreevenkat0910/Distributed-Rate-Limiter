"""Renders the complete-outage evidence chart: two simple stacked panels
showing per-endpoint response status over time, color-coded. Much
simpler than Phase 7's degradation chart -- a hard outage produces an
obvious block of 503s, not a one-second spike that needs surfacing.

Reads loadtest/outage-metrics.jsonl and loadtest/outage-phases.json
written by run_outage_test.py. Writes loadtest/outage-chart.png.

Usage:
    python3 loadtest/chart_outage.py
"""

import json
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt

LOADTEST_DIR = Path(__file__).parent
METRICS_PATH = LOADTEST_DIR / "outage-metrics.jsonl"
PHASES_PATH = LOADTEST_DIR / "outage-phases.json"
OUTPUT_PATH = LOADTEST_DIR / "outage-chart.png"

STATUS_COLORS = {"200": "#2e7d32", "429": "#f39c12", "503": "#c0392b"}


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load_status_points(metric_name: str, test_start: float) -> dict[str, list[float]]:
    """Returns {status: [elapsed_seconds, ...]} for the given metric."""
    by_status: dict[str, list[float]] = {}
    with open(METRICS_PATH) as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("type") != "Point" or obj.get("metric") != metric_name:
                continue
            status = obj["data"]["tags"].get("status", "unknown")
            t = _parse_iso(obj["data"]["time"]).timestamp() - test_start
            by_status.setdefault(status, []).append(t)
    return by_status


def main() -> None:
    phases = json.loads(PHASES_PATH.read_text())
    test_start = _parse_iso(phases["test_start"]).timestamp()
    stop_x = _parse_iso(phases["stop_completed_at"]).timestamp() - test_start
    start_x = _parse_iso(phases["start_completed_at"]).timestamp() - test_start
    recovery_x = (
        _parse_iso(phases["recovery_detected_at"]).timestamp() - test_start
        if phases.get("recovery_detected_at")
        else None
    )
    test_end_x = _parse_iso(phases["test_end"]).timestamp() - test_start

    login_by_status = load_status_points("login_status", test_start)
    search_by_status = load_status_points("search_status", test_start)

    fig, (ax_login, ax_search) = plt.subplots(2, 1, figsize=(13, 6), sharex=True)

    for ax, by_status, title in (
        (ax_login, login_by_status, "POST /login (fail-closed)"),
        (ax_search, search_by_status, "GET /search (fail-open)"),
    ):
        ax.axvspan(stop_x, start_x, color="#fdeceb", zorder=0, label="redis container down")
        for status in ("200", "429", "503"):
            xs = by_status.get(status)
            if not xs:
                continue
            ax.scatter(xs, [status] * len(xs), s=6, alpha=0.4, color=STATUS_COLORS[status])
        ax.axvline(stop_x, color="#c0392b", linestyle="--", linewidth=1.2)
        ax.axvline(start_x, color="#2c3e50", linestyle="--", linewidth=1.2)
        if recovery_x is not None:
            ax.axvline(recovery_x, color="#2e7d32", linestyle=":", linewidth=1.4)
        ax.set_title(title, fontsize=11, loc="left")
        ax.set_ylabel("status code")
        ax.set_yticks(["200", "429", "503"])
        ax.set_xlim(0, test_end_x)

    ax_search.set_xlabel("Elapsed time since test start (s)")

    fig.suptitle(
        "Complete Redis outage: docker compose stop/start redis mid-load-test\n"
        "red dashed = redis stopped · dark dashed = redis restarted · green dotted = recovery detected",
        fontsize=11,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    fig.savefig(OUTPUT_PATH, dpi=150)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
