// Same load profile as normal.js/degradation.js, but mixes traffic across
// BOTH endpoints -- this phase specifically needs to observe /search
// staying up (fail-open) and /login degrading to 503 (fail-closed) in
// the same run, not just one endpoint. Meant to run while an external
// controller stops and restarts the redis container mid-test -- see
// loadtest/run_outage_test.py.
//
//   python3 loadtest/run_outage_test.py

import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";
const KEY_POOL_SIZE = 50;
const LOGIN_TRAFFIC_FRACTION = 0.2;

const searchStatus = new Counter("search_status");
const loginStatus = new Counter("login_status");

export const options = {
  scenarios: {
    mixed_ramp: {
      executor: "ramping-arrival-rate",
      startRate: 0,
      timeUnit: "1s",
      preAllocatedVUs: 50,
      maxVUs: 300,
      stages: [
        { target: 500, duration: "30s" },
        { target: 500, duration: "60s" }, // outage happens within this window
        { target: 0, duration: "30s" },
      ],
    },
  },
  summaryTrendStats: ["avg", "min", "med", "p(50)", "p(95)", "p(99)", "max"],
};

export default function () {
  if (Math.random() < LOGIN_TRAFFIC_FRACTION) {
    const res = http.post(`${BASE_URL}/login`);
    loginStatus.add(1, { status: String(res.status) });
    check(res, {
      "login status is 200, 429, or 503": (r) => [200, 429, 503].includes(r.status),
    });
  } else {
    const userId = `loadtest-user-${__VU % KEY_POOL_SIZE}`;
    const res = http.get(`${BASE_URL}/search?user_id=${userId}`);
    searchStatus.add(1, { status: String(res.status) });
    check(res, {
      "search status is 200 or 429": (r) => [200, 429].includes(r.status),
    });
  }
}
