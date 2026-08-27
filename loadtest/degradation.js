// Same load profile as normal.js (0->500 RPS over 30s, hold 500 for 60s,
// ramp down over 30s) against GET /search through nginx, but this run is
// meant to be executed while an external controller injects a Toxiproxy
// latency toxic on the app-to-Redis connection partway through -- see
// loadtest/run_degradation_test.py, which starts this script as a
// subprocess and drives the fault injection around it using absolute
// wall-clock timestamps (not offsets from this script's own start).
//
// The one addition over normal.js: a degraded_responses metric tracking
// the X-RateLimiter-Degraded response header, so the fail-open signal
// during the toxic window is directly plottable, not inferred from
// latency alone.
//
//   python3 loadtest/run_degradation_test.py

import http from "k6/http";
import { check } from "k6";
import { Counter, Rate } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";
const KEY_POOL_SIZE = 50;

const rateLimited = new Rate("rate_limited_responses");
const degradedResponses = new Rate("degraded_responses");
const replicaApp1 = new Counter("replica_app1_requests");
const replicaApp2 = new Counter("replica_app2_requests");
const replicaApp3 = new Counter("replica_app3_requests");
const replicaUnknown = new Counter("replica_unknown_requests");

export const options = {
  scenarios: {
    search_ramp: {
      executor: "ramping-arrival-rate",
      startRate: 0,
      timeUnit: "1s",
      preAllocatedVUs: 50,
      maxVUs: 300,
      stages: [
        { target: 500, duration: "30s" }, // ramp up
        { target: 500, duration: "60s" }, // hold -- toxic injected/removed within this window
        { target: 0, duration: "30s" }, // ramp down
      ],
    },
  },
  summaryTrendStats: ["avg", "min", "med", "p(50)", "p(95)", "p(99)", "max"],
};

export default function () {
  const userId = `loadtest-user-${__VU % KEY_POOL_SIZE}`;
  const res = http.get(`${BASE_URL}/search?user_id=${userId}`);

  rateLimited.add(res.status === 429);
  degradedResponses.add(res.headers["X-Ratelimiter-Degraded"] === "fail-open");

  const replicaId = res.headers["X-Replica-Id"];
  if (replicaId === "app1") replicaApp1.add(1);
  else if (replicaId === "app2") replicaApp2.add(1);
  else if (replicaId === "app3") replicaApp3.add(1);
  else replicaUnknown.add(1);

  check(res, {
    "status is 200 or 429": (r) => r.status === 200 || r.status === 429,
  });
}
