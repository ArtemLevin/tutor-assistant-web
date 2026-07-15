import http from "k6/http";
import { check } from "k6";

const baseUrl = __ENV.BASE_URL || "http://localhost:8000";
const lessonIds = (__ENV.LESSON_IDS || "").split(",").filter(Boolean);

export const options = {
  scenarios: {
    parallel_lessons: {
      executor: "per-vu-iterations",
      vus: 20,
      iterations: 1,
      maxDuration: "2m",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500"],
  },
};

export default function () {
  if (!lessonIds.length) {
    throw new Error("Set LESSON_IDS to 20 comma-separated staging lesson ids");
  }
  if (!__ENV.SESSION_COOKIE) {
    throw new Error("Set SESSION_COOKIE to an authenticated staging tutor session");
  }
  const lessonId = lessonIds[(__VU - 1) % lessonIds.length];
  const response = http.get(`${baseUrl}/api/lessons/${lessonId}/workflow`, {
    headers: { Cookie: __ENV.SESSION_COOKIE || "" },
    tags: { flow: "parallel-lessons" },
  });
  check(response, { "workflow endpoint responds": (r) => r.status === 200 });
}
