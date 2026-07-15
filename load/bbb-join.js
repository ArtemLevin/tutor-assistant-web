import http from "k6/http";
import { check } from "k6";

const urls = (__ENV.JOIN_URLS || "").split(",").filter(Boolean);

export const options = {
  scenarios: {
    bbb_control_plane: {
      executor: "per-vu-iterations",
      vus: 20,
      iterations: 1,
      maxDuration: "2m",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<2000"],
    checks: ["rate>0.99"],
  },
};

export default function () {
  if (urls.length < 20) {
    throw new Error("JOIN_URLS must contain 20 signed staging lesson URLs");
  }
  const response = http.get(urls[__VU - 1], { redirects: 0, tags: { flow: "bbb-join" } });
  check(response, {
    "app creates room and redirects to BBB": (r) => r.status === 303 && Boolean(r.headers.Location),
  });
}
