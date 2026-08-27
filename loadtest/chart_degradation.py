"""Renders the degradation-test evidence chart: per-request latency over
time with the before/during/after phases clearly shaded and labeled, plus
the fail-open (degraded) response rate over the same timeline.

Reads loadtest/degradation-metrics.jsonl (k6's raw --out json= time
series) and loadtest/degradation-phases.json (the orchestrator's recorded
wall-clock injection/removal timestamps) written by
run_degradation_test.py. Writes loadtest/degradation-chart.png.

Usage:
    python3 loadtest/chart_degradation.py
"""

import json
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from mpl_toolkits.axes_grid1.inset_locator import mark_inset, inset_axes

LOADTEST_DIR = Path(__file__).parent
METRICS_PATH = LOADTEST_DIR / "degradation-metrics.jsonl"
PHASES_PATH = LOADTEST_DIR / "degradation-phases.json"
OUTPUT_PATH = LOADTEST_DIR / "degradation-chart.png"

BUCKET_SECONDS = 1.0


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load_points(metric_name: str) -> list[tuple[float, float]]:
    """Returns (unix_timestamp, value) pairs for the given metric name."""
    points = []
    with open(METRICS_PATH) as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("type") != "Point" or obj.get("metric") != metric_name:
                continue
            t = _parse_iso(obj["data"]["time"]).timestamp()
            points.append((t, obj["data"]["value"]))
    return points


def main() -> None:
    phases = json.loads(PHASES_PATH.read_text())
    test_start = _parse_iso(phases["test_start"]).timestamp()
    inject_at = _parse_iso(phases["inject_at"]).timestamp()
    remove_at = _parse_iso(phases["remove_at"]).timestamp()
    test_end = _parse_iso(phases["test_end"]).timestamp()
    toxic_latency_ms = phases["toxic_latency_ms"]

    latency_points = load_points("http_req_duration")
    degraded_points = load_points("degraded_responses")

    latency_x = [(t - test_start) for t, _ in latency_points]
    latency_y = [v for _, v in latency_points]

    # Per-second p95: the bulk of requests barely move (the circuit
    # breaker keeps them fast even while Redis is slow), so the raw
    # scatter alone doesn't make the brief real spike visible against
    # 45,000 points. A per-bucket p95 line does: it clearly pokes up in
    # the exact 1s bucket where the breaker is still closed and each
    # request is genuinely waiting out the call timeout, then drops back
    # immediately once the breaker opens.
    latency_buckets: dict[int, list[float]] = {}
    for t, v in latency_points:
        b = int((t - test_start) // BUCKET_SECONDS)
        latency_buckets.setdefault(b, []).append(v)
    p95_buckets = sorted(latency_buckets)
    p95_x = [b * BUCKET_SECONDS + BUCKET_SECONDS / 2 for b in p95_buckets]
    p95_y = []
    for b in p95_buckets:
        vals = sorted(latency_buckets[b])
        p95_y.append(vals[int(len(vals) * 0.95)])

    # Bucket degraded_responses into BUCKET_SECONDS windows and compute the
    # fraction of requests in each bucket that were degraded (fail-open).
    bucket_totals: dict[int, int] = {}
    bucket_degraded: dict[int, float] = {}
    for t, v in degraded_points:
        bucket = int((t - test_start) // BUCKET_SECONDS)
        bucket_totals[bucket] = bucket_totals.get(bucket, 0) + 1
        bucket_degraded[bucket] = bucket_degraded.get(bucket, 0) + v
    buckets_sorted = sorted(bucket_totals)
    bucket_x = [b * BUCKET_SECONDS for b in buckets_sorted]
    bucket_pct = [100 * bucket_degraded[b] / bucket_totals[b] for b in buckets_sorted]

    inject_x = inject_at - test_start
    remove_x = remove_at - test_start
    end_x = test_end - test_start

    fig, (ax_latency, ax_degraded) = plt.subplots(
        2, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]}
    )

    # -- Phase shading (shared across both panels) --
    phase_bands = [
        (0, inject_x, "#e8f4ea", "BEFORE\n(healthy Redis)"),
        (inject_x, remove_x, "#fdeceb", f"DURING\n({toxic_latency_ms}ms Redis latency injected)"),
        (remove_x, end_x, "#e8eef7", "AFTER\n(Redis recovered)"),
    ]
    for ax in (ax_latency, ax_degraded):
        for x0, x1, color, _ in phase_bands:
            ax.axvspan(x0, x1, color=color, zorder=0)
        ax.axvline(inject_x, color="#c0392b", linestyle="--", linewidth=1.2, zorder=1)
        ax.axvline(remove_x, color="#2c3e50", linestyle="--", linewidth=1.2, zorder=1)

    # -- Top panel: per-request latency, plus a per-second p95 line to
    # surface the brief real spike that's invisible in the raw scatter --
    ax_latency.scatter(latency_x, latency_y, s=4, alpha=0.2, color="#34495e", zorder=2, label="individual requests")
    ax_latency.plot(
        p95_x, p95_y, color="#c0392b", linewidth=1.8, zorder=3,
        label="p95 per 1s bucket",
    )
    ax_latency.legend(loc="upper right", fontsize=8, framealpha=0.9)
    ax_latency.set_ylabel("Request latency (ms)")
    ax_latency.set_yscale("log")
    ax_latency.yaxis.set_major_formatter(mticker.ScalarFormatter())
    ax_latency.set_title(
        "GET /search latency through a Redis degradation event\n"
        f"({toxic_latency_ms}ms latency toxic injected on the app→Redis connection, held 20s, then removed)",
        fontsize=12,
    )
    for x0, x1, _, label in phase_bands:
        mid = (x0 + x1) / 2
        ax_latency.text(
            mid, ax_latency.get_ylim()[1] * 0.6, label,
            ha="center", va="top", fontsize=9, color="#333333",
        )

    # -- Zoomed inset: the actual moment the breaker trips, up close.
    # Individual failed (timeout-bound) requests are ~1200x rarer than
    # fast ones, so even the p95 line only hints at them -- this inset
    # shows the raw points in the handful of seconds around injection.
    zoom_x0, zoom_x1 = inject_x - 1, inject_x + 4
    inset = inset_axes(ax_latency, width="30%", height="45%", loc="center right", borderpad=2.2)
    zoom_mask = [(x, y) for x, y in zip(latency_x, latency_y) if zoom_x0 <= x <= zoom_x1]
    inset.scatter([x for x, _ in zoom_mask], [y for _, y in zoom_mask], s=10, alpha=0.5, color="#34495e")
    inset.axvline(inject_x, color="#c0392b", linestyle="--", linewidth=1)
    inset.set_xlim(zoom_x0, zoom_x1)
    inset.set_title("zoom: the trip moment", fontsize=8)
    inset.tick_params(labelsize=7)
    inset.set_facecolor("white")
    mark_inset(ax_latency, inset, loc1=2, loc2=3, fc="none", ec="#888888", linewidth=0.8)

    # -- Bottom panel: fail-open (degraded) response rate --
    ax_degraded.bar(bucket_x, bucket_pct, width=BUCKET_SECONDS * 0.9, color="#c0392b", zorder=2)
    ax_degraded.set_ylabel("Fail-open (degraded)\nresponses per 1s (%)")
    ax_degraded.set_xlabel("Elapsed time since test start (s)")
    ax_degraded.set_ylim(0, 105)

    fig.tight_layout()
    fig.savefig(OUTPUT_PATH, dpi=150)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
