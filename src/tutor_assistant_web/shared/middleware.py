from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from time import monotonic, perf_counter

import redis
from fastapi import Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware

from tutor_assistant_web.config import Settings
from tutor_assistant_web.observability import (
    HTTP_DURATION,
    HTTP_REQUESTS,
    bind_correlation,
    correlation_id_var,
    record_exception,
    reset_correlation,
)

logger = logging.getLogger(__name__)


class SecurityAndCorrelationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        token = bind_correlation(supplied)
        trace.get_current_span().set_attribute("app.correlation_id", correlation_id_var.get())
        started = perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
                status = response.status_code
            except Exception as exc:
                record_exception("http", exc)
                logger.exception("Unhandled HTTP error", extra={"path": request.url.path})
                raise
            finally:
                route = getattr(request.scope.get("route"), "path", request.url.path)
                HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
                HTTP_DURATION.labels(request.method, route).observe(perf_counter() - started)
            response.headers["X-Request-ID"] = correlation_id_var.get()
            response.headers.setdefault("Content-Security-Policy", self.settings.security_csp)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            response.headers.setdefault(
                "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
            )
            response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
            response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
            if self.settings.session_cookie_secure:
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains"
                )
            return response
        finally:
            reset_correlation(token)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.redis = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.3,
            socket_timeout=0.3,
            decode_responses=True,
        )
        self.memory: dict[str, list[float]] = defaultdict(list)
        self.lock = threading.Lock()

    def _rule(self, request: Request) -> tuple[str, int] | None:
        path = request.url.path
        if request.method == "POST" and path == "/login":
            return "login", self.settings.rate_limit_login
        if "invitation" in path:
            return "invitations", self.settings.rate_limit_invitations
        if path.startswith("/webhooks/"):
            return "callbacks", self.settings.rate_limit_callbacks
        if request.method == "GET" and ("download" in path or "artifact" in path):
            return "downloads", self.settings.rate_limit_downloads
        return None

    async def dispatch(self, request: Request, call_next):
        rule = self._rule(request)
        if rule:
            category, limit = rule
            client = request.client.host if request.client else "unknown"
            key = f"ratelimit:{category}:{client}"
            count = await asyncio.to_thread(self._increment, key)
            if category == "login" and count > 3:
                await asyncio.sleep(min(0.05 * (count - 3), 0.5))
            if count > limit:
                logger.warning("Rate limit exceeded", extra={"category": category})
                return JSONResponse(
                    {"detail": "Слишком много запросов. Повторите попытку позже."},
                    status_code=429,
                    headers={"Retry-After": str(self.settings.rate_limit_window_seconds)},
                )
        return await call_next(request)

    def _increment(self, key: str) -> int:
        try:
            pipeline = self.redis.pipeline()
            pipeline.incr(key)
            pipeline.expire(key, self.settings.rate_limit_window_seconds, nx=True)
            return int(pipeline.execute()[0])
        except redis.RedisError:
            now = monotonic()
            cutoff = now - self.settings.rate_limit_window_seconds
            with self.lock:
                if len(self.memory) > 10_000:
                    self.memory = defaultdict(
                        list,
                        {
                            item_key: [item for item in timestamps if item >= cutoff]
                            for item_key, timestamps in self.memory.items()
                            if any(item >= cutoff for item in timestamps)
                        },
                    )
                values = [item for item in self.memory[key] if item >= cutoff]
                values.append(now)
                self.memory[key] = values
                return len(values)
