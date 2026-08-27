// Baseline load characterization for GET /search through nginx (3
// replicas), before any fault injection. Run this exact script twice --
// once against a stack started with RATE_LIMITER_ENABLED=false (true
// baseline: no Redis/breaker involvement at all) and once with the real
// limiter enabled (RATE_LIMITER_ENABLED=true, the default) -- and compare
// p99 http_req_duration between the two runs to isolate the limiter's
// actual added latency.
//
//   RATE_LIMITER_ENABLED=false docker compose -f infra/docker-compose.yml up -d
//   k6 run loadtest/normal.js --summary-export=loadtest/results-disabled.json
//
//   RATE_LIMITER_ENABLED=true docker compose -f infra/docker-compose.yml up -d
//   k6 run loadtest/normal.js --summary-export=loadtest/results-enabled.json

import http from "k6/http";
import { check } from "k6";
import { Counter, Rate } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";

// A fixed, reused pool of keys -- not a unique key per request. A unique
// key per request would mean every request is "first ever" for that key
// and always allowed, producing zero 429s and defeating the point of
// observing the limiter's behavior under load.
const KEY_POOL_SIZE = 50;

const rateLimited = new Rate("rate_limited_responses");
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
        { target: 500, duration: "60s" }, // hold
        { target: 0, duration: "30s" }, // ramp down
      ],
    },
  },
  // k6's default summary omits p99 -- this phase explicitly wants
  // p50/p95/p99 reported.
  summaryTrendStats: ["avg", "min", "med", "p(50)", "p(95)", "p(99)", "max"],
};

export default function () {
  const userId = `loadtest-user-${__VU % KEY_POOL_SIZE}`;
  const res = http.get(`${BASE_URL}/search?user_id=${userId}`);

  rateLimited.add(res.status === 429);

  const replicaId = res.headers["X-Replica-Id"];
  if (replicaId === "app1") replicaApp1.add(1);
  else if (replicaId === "app2") replicaApp2.add(1);
  else if (replicaId === "app3") replicaApp3.add(1);
  else replicaUnknown.add(1);

  check(res, {
    "status is 200 or 429": (r) => r.status === 200 || r.status === 429,
  });
}
