import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.modules.dashboard.application import (
    DashboardService,
    QueueMetricsService,
    ReadinessService,
)


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["dashboard"])
    web = container.web

    @router.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        principal = web.principal(request)
        if principal and principal.role in {"student", "parent"}:
            return RedirectResponse("/portal", status_code=303)
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        data = DashboardService(container.database, web.organization_id(request)).load()
        return container.templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=web.context(
                request,
                upcoming=data.upcoming,
                students_count=data.students_count,
                pending_jobs=data.pending_jobs,
                artifacts_count=data.artifacts_count,
                now=data.now,
            ),
        )

    @router.get("/health/live")
    def health_live():
        return {"status": "ok", "version": "0.11.0"}

    @router.get("/health/ready")
    def health_ready():
        ready, checks = ReadinessService(
            container.database,
            container.settings,
            container.conference,
            container.artifact_storage,
            container.materials.name,
        ).check()
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
        ReadinessService(
            container.database,
            container.settings,
            container.conference,
            container.artifact_storage,
            container.materials.name,
        ).check()
        QueueMetricsService(container.database, container.settings).refresh()
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return router
