from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.modules.dashboard.application import DashboardService


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["dashboard"])
    web = container.web

    @router.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
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
        return {"status": "ok", "version": "0.3.0"}

    @router.get("/health/ready")
    def health_ready():
        checks: dict[str, str] = {"database": "ok"}
        try:
            container.database.healthcheck()
        except Exception as exc:
            checks["database"] = f"error: {exc}"
            return JSONResponse({"status": "error", "checks": checks}, status_code=503)
        checks["bigbluebutton"] = container.conference.name
        checks["materials"] = container.materials.name
        checks["queue"] = container.jobs.name
        return {"status": "ok", "checks": checks}

    return router
