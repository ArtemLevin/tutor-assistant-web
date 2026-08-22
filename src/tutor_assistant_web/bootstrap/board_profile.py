from __future__ import annotations

import secrets

import redis
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from tutor_assistant_web.bootstrap.container import BoardAppContainer
from tutor_assistant_web.modules.boards.routes import create_router as create_boards_router
from tutor_assistant_web.observability import READINESS
from tutor_assistant_web.version import __version__

_BOARD_HTTP_ROUTES = {
    ("GET", "/j/{secret}"),
    ("GET", "/api/v1/boards/context"),
    ("POST", "/api/v1/boards"),
    ("GET", "/api/v1/boards"),
    ("GET", "/api/v1/boards/{document_id}"),
    ("PATCH", "/api/v1/boards/{document_id}"),
    ("DELETE", "/api/v1/boards/{document_id}"),
    ("POST", "/api/v1/boards/{document_id}/archive"),
    ("POST", "/api/v1/boards/{document_id}/unarchive"),
    ("POST", "/api/v1/boards/{document_id}/invitations"),
    ("GET", "/api/v1/boards/{document_id}/invitations"),
    ("PATCH", "/api/v1/boards/{document_id}/invitations/{invitation_id}"),
    ("POST", "/api/v1/boards/{document_id}/invitations/{invitation_id}/revoke"),
    ("POST", "/api/v1/boards/{document_id}/invitations/{invitation_id}/rotate"),
    ("GET", "/api/v1/boards/{document_id}/commands"),
    ("POST", "/api/v1/boards/{document_id}/commands"),
    ("POST", "/api/v1/boards/{document_id}/snapshots"),
    ("POST", "/api/v1/boards/{document_id}/collaboration-ticket"),
}
_BOARD_WEBSOCKET_ROUTES = {"/api/v1/boards/{document_id}/collaboration"}


def create_board_profile_router(container: BoardAppContainer) -> APIRouter:
    """Expose the reviewed standalone-board route inventory only."""

    router = APIRouter()
    router.include_router(_identity_router(container))
    router.include_router(_health_router(container))
    source = create_boards_router(container)  # type: ignore[arg-type]
    for route in _flatten_routes(source):
        methods = getattr(route, "methods", None)
        if methods is None:
            if getattr(route, "path", None) in _BOARD_WEBSOCKET_ROUTES:
                router.routes.append(route)
            continue
        if any((method, route.path) in _BOARD_HTTP_ROUTES for method in methods):
            router.routes.append(route)
    return router


def _flatten_routes(router: APIRouter):
    for route in router.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _flatten_routes(included)
        else:
            yield route


def _identity_router(container: BoardAppContainer) -> APIRouter:
    router = APIRouter(tags=["identity"])
    web = container.web

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        if web.is_authorized(request):
            return RedirectResponse("/", status_code=303)
        return container.templates.TemplateResponse(
            request=request,
            name="board_login.html",
            context=web.context(request, next=next, error=""),
        )

    @router.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        form = await web.validated_form(request)
        email = str(form.get("email", ""))
        target = str(form.get("next", "/"))
        principal = container.identity.authenticate(email, str(form.get("password", "")))
        if principal is None:
            return container.templates.TemplateResponse(
                request=request,
                name="board_login.html",
                context=web.context(
                    request,
                    next=target,
                    email=email,
                    error="Неверный email или пароль",
                ),
                status_code=401,
            )
        request.session.clear()
        web.set_principal(request, principal)
        web.csrf_token(request)
        if not target.startswith("/") or target.startswith("//"):
            target = "/"
        return RedirectResponse(target, status_code=303)

    @router.post("/logout")
    async def logout(request: Request):
        if not web.is_authorized(request):
            return RedirectResponse("/login", status_code=303)
        await web.validated_form(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    return router


def _health_router(container: BoardAppContainer) -> APIRouter:
    router = APIRouter(tags=["health"])

    def readiness() -> tuple[bool, dict[str, str]]:
        checks: dict[str, str] = {}
        dependencies = {
            "postgresql": container.database.healthcheck,
            "redis": _redis_healthcheck,
            "s3": container.artifact_storage.healthcheck,
        }
        for name, check in dependencies.items():
            try:
                check()
            except Exception:
                checks[name] = "error"
                READINESS.labels(dependency=name).set(0)
            else:
                checks[name] = "ok"
                READINESS.labels(dependency=name).set(1)
        return all(value == "ok" for value in checks.values()), checks

    def _redis_healthcheck() -> None:
        client = redis.Redis.from_url(
            container.settings.redis_url,
            socket_connect_timeout=container.settings.readiness_timeout_seconds,
            socket_timeout=container.settings.readiness_timeout_seconds,
        )
        try:
            client.ping()
        finally:
            client.close()

    @router.get("/health/live")
    def health_live():
        return {"status": "ok", "version": __version__, "profile": "board"}

    @router.get("/health/ready")
    def health_ready():
        ready, checks = readiness()
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
        readiness()
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return router
