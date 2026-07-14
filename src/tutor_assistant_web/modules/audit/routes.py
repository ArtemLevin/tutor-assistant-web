from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from tutor_assistant_web.bootstrap.container import AppContainer


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["audit"])
    web = container.web

    @router.get("/settings/audit", response_class=HTMLResponse)
    def audit_page(request: Request):
        blocked = web.require_admin(request)
        if blocked:
            return blocked
        principal = web.principal_required(request)
        events = container.audit_service(principal.organization_id).recent()
        return container.templates.TemplateResponse(
            request=request,
            name="audit.html",
            context=web.context(request, events=events),
        )

    return router
