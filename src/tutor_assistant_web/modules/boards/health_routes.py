from __future__ import annotations

import secrets

import redis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from tutor_assistant_web.bootstrap.board_container import BoardAppContainer
from tutor_assistant_web.observability import READINESS
from tutor_assistant_web.version import __version__


def _board_readiness(container: BoardAppContainer) -> tuple[bool, dict[str, str]]:
    checks: dict[str, str] = {}

    try:
        container.database.healthcheck()
    except Exception:
        checks["postgresql"] = "error"
        READINESS.labels(dependency="postgresql").set(0)
    else:
        checks["postgresql"] = "ok"
        READINESS.labels(dependency="postgresql").set(1)

    client = redis.Redis.from_url(
        container.settings.redis_url,
        socket_connect_timeout=container.settings.readiness_timeout_seconds,
        socket_timeout=container.settings.readiness_timeout_seconds,
    )
    try:
        client.ping()
    except redis.RedisError:
        checks["redis"] = "error"
        READINESS.labels(dependency="redis").set(0)
    else:
        checks["redis"] = "ok"
        READINESS.labels(dependency="redis").set(1)
    finally:
        client.close()

    try:
        container.artifact_storage.healthcheck()
    except Exception:
        checks["s3"] = "error"
        READINESS.labels(dependency="s3").set(0)
    else:
        checks["s3"] = "ok" if container.artifact_storage.name == "s3" else "local"
        READINESS.labels(dependency="s3").set(1)

    return all(value != "error" for value in checks.values()), checks


def create_board_health_router(container: BoardAppContainer) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    def health_live():
        return {"status": "ok", "version": __version__}

    @router.get("/health/ready")
    def health_ready():
        ready, checks = _board_readiness(container)
        return JSONResponse(
            {"status": "ok" if ready else "error", "checks": checks},
            status_code=200 if ready else 503,
        )

    @router.get("/metrics")
    def metrics(request: Request):
        if not container.settings.metrics_enabled:
            return Response(status_code=404)
        expected = container.settings.metrics_bearer_token
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if expected and not secrets.compare_digest(supplied, expected):
            return Response(status_code=401)
        _board_readiness(container)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return router
