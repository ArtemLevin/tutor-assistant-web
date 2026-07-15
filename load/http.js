import http from "k6/http";
import { check, sleep } from "k6";

const baseUrl = __ENV.BASE_URL || "http://localhost:8000";

export const options = {
  scenarios: {
    user_sessions: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || 100),
      duration: __ENV.DURATION || "5m",
      gracefulStop: "30s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.005"],
    http_req_duration: ["p(95)<500"],
    checks: ["rate>0.995"],
  },
};

export default function () {
  const home = http.get(`${baseUrl}/`, { redirects: 0, tags: { page: "home" } });
  check(home, { "home is served or redirects to login": (r) => [200, 303, 307].includes(r.status) });
  const live = http.get(`${baseUrl}/health/live`, { tags: { page: "liveness" } });
  check(live, { "liveness is healthy": (r) => r.status === 200 && r.json("status") === "ok" });
  sleep(Math.random() * 2 + 0.5);
}
