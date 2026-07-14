from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from tutor_assistant_web.bootstrap.container import AppContainer


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["portal"])
    web = container.web

    def recipient_service(request: Request):
        return container.portal_service(web.principal_required(request))

    @router.get("/portal", response_class=HTMLResponse)
    def portal_home(request: Request):
        blocked = web.require_recipient(request)
        if blocked:
            return blocked
        home = recipient_service(request).home()
        return container.templates.TemplateResponse(
            request=request,
            name="portal.html",
            context=web.context(request, home=home),
        )

    @router.get("/portal/deliveries/{delivery_id}", response_class=HTMLResponse)
    def portal_delivery(request: Request, delivery_id: str):
        blocked = web.require_recipient(request)
        if blocked:
            return blocked
        delivery = recipient_service(request).delivery(delivery_id)
        return container.templates.TemplateResponse(
            request=request,
            name="portal_delivery.html",
            context=web.context(request, delivery=delivery),
        )

    @router.get("/portal/artifacts/{artifact_id}/download")
    def portal_artifact(request: Request, artifact_id: str):
        blocked = web.require_recipient(request)
        if blocked:
            return blocked
        artifact, content = recipient_service(request).artifact(artifact_id)
        return Response(
            content,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{artifact.kind}-v{artifact.version}.{artifact.kind}"'
                ),
                "X-Content-SHA256": artifact.sha256,
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/portal/artifacts/{artifact_id}/preview")
    def portal_artifact_preview(request: Request, artifact_id: str):
        blocked = web.require_recipient(request)
        if blocked:
            return blocked
        artifact, content = recipient_service(request).artifact(artifact_id)
        if artifact.kind != "html":
            return RedirectResponse(f"/portal/artifacts/{artifact_id}/download", status_code=303)
        return Response(
            content,
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": (
                    "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
                ),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/api/notifications")
    def notifications(request: Request):
        blocked = web.require_recipient(request)
        if blocked:
            return blocked
        home = recipient_service(request).home()
        return JSONResponse(
            {
                "unread_count": home.unread_count,
                "items": [
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "title": item.title,
                        "body": item.body,
                        "delivery_id": item.delivery_id,
                        "read_at": item.read_at.isoformat() if item.read_at else None,
                        "created_at": item.created_at.isoformat(),
                    }
                    for item in home.notifications
                ],
            }
        )

    @router.post("/api/notifications/{notification_id}/read")
    async def notification_read(request: Request, notification_id: str):
        blocked = web.require_recipient(request)
        if blocked:
            return blocked
        await web.validated_form(request)
        recipient_service(request).mark_notification_read(notification_id)
        return RedirectResponse("/portal", status_code=303)

    @router.post("/generation-runs/{run_id}/publish")
    async def publish_run(request: Request, run_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        await web.validated_form(request)
        principal = web.principal_required(request)
        run = container.publication_service(principal.organization_id).publish(
            run_id, principal.user_id
        )
        if container.settings.task_eager:
            container.outbox_service().dispatch_pending(container.settings.outbox_batch_size)
        return RedirectResponse(f"/lessons/{run.lesson_id}", status_code=303)

    @router.post("/generation-runs/{run_id}/revoke")
    async def revoke_run(request: Request, run_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        await web.validated_form(request)
        principal = web.principal_required(request)
        run = container.publication_service(principal.organization_id).revoke(
            run_id, principal.user_id
        )
        if container.settings.task_eager:
            container.outbox_service().dispatch_pending(container.settings.outbox_batch_size)
        return RedirectResponse(f"/lessons/{run.lesson_id}", status_code=303)

    return router
