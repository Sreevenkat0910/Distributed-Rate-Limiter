# Load test / fault injection evidence

Captured artifacts from the three load-testing phases, in `/loadtest`'s
scripts but archived here so they survive independent of re-running
anything.

## phase6-normal-load/

Baseline latency characterization (`loadtest/normal.js`) run twice with
`RATE_LIMITER_ENABLED=false` (true baseline, no Redis/breaker involvement)
and twice with it enabled (real limit). k6's `--summary-export` output for
all four runs — p50/p95/p99, 429 rate, per-replica request counts.

## phase7-degradation/

Toxiproxy latency-injection test (`loadtest/degradation.js` +
`run_degradation_test.py`): a 1200ms latency toxic injected on the
app-to-Redis connection mid-run, held 20s, removed. `degradation-chart.png`
is the two-panel evidence chart (per-request latency with a per-second p95
line + zoomed inset on the trip moment; fail-open response rate over
time). `summary.json` is a compact aggregate derived from the run's raw
per-request time series.

`summary.json`'s `source` field explains what's *not* here: k6's raw
`--out json=` time series for this run was 194MB — over GitHub's 100MB
single-file limit, and not practical to keep in a git repo regardless.
The chart and summary are what's meant for archival; the raw series is
regenerable by rerunning `run_degradation_test.py` (writes to
`loadtest/degradation-metrics.jsonl`, gitignored).

## phase8-outage/

Complete Redis outage test (`loadtest/outage.js` + `run_outage_test.py`):
`docker compose stop redis` mid-run, held 20s, `docker compose start
redis`, then polled `/admin/limiter-status` until recovery. Mixes traffic
across both endpoints so both policies show up in one run.

- `outage-chart.png` — two stacked panels, one per endpoint, status code
  over time. `/login` (fail-closed) shows a clean, bounded block of 503s;
  `/search` (fail-open) never returns 503, stays 200/429 throughout.
- `outage-summary.json` — k6's `--summary-export` output (small enough to
  commit directly; this run's raw per-request series is also gitignored,
  same reasoning as phase7).
- `outage-phases.json` — exact wall-clock timestamps for stop-issued,
  stop-completed, start-issued, start-completed, and recovery-detected.
  Recovery was detected 9.7s after the restart command completed — the
  visible outage window (bounded by the actual 503 responses) ran
  slightly longer than the container's real downtime, because the
  circuit breaker's cooldown (`BREAKER_RESET_TIMEOUT_SECONDS`, 30s
  default) doesn't retry immediately when Redis comes back; it only
  probes again once each replica's own cooldown has elapsed.
- `circuit-breaker-log-excerpt.log` — the actual JSON log lines showing
  each replica's breaker opening at the outage and closing again at
  recovery. Also shows one benign, self-corrected race on `app3`
  (`half-open -> open -> closed` within the same millisecond): pybreaker
  doesn't gate concurrent half-open trial requests to just one in flight,
  so two concurrent probes landed at nearly the same instant and briefly
  disagreed before settling. Didn't affect the outcome; noted here rather
  than silently omitted.
