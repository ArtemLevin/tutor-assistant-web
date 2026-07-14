from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.modules.automation.application import (
    BigBlueButtonWebhookVerifier,
    InvalidWebhookSignature,
)


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["automation"])
    web = container.web

    @router.post("/webhooks/bigbluebutton/recording-ready")
    async def recording_ready(request: Request):
        form = await request.form()
        signed_parameters = str(form.get("signed_parameters", ""))
        try:
            meeting_id, record_id = BigBlueButtonWebhookVerifier(
                container.settings.bbb_secret
            ).decode(signed_parameters)
        except InvalidWebhookSignature as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        result = container.recording_ready_service().accept(meeting_id, record_id)
        if container.settings.task_eager and not result.duplicate:
            container.outbox_service().dispatch_pending(limit=1)
        return JSONResponse(
            {"accepted": True, "duplicate": result.duplicate, "job_id": result.job_id},
            status_code=200 if result.duplicate else 202,
        )

    @router.get("/api/lessons/{lesson_id}/workflow")
    def workflow_status(request: Request, lesson_id: str):
        if not web.is_authorized(request):
            raise HTTPException(401, "Требуется авторизация")
        organization_id = web.organization_id(request)
        lesson = container.classroom_service(organization_id).detail(lesson_id)
        job = lesson.jobs[0] if lesson.jobs else None
        transcript = lesson.transcript
        return {
            "job": (
                {
                    "id": job.id,
                    "status": job.status,
                    "stage": job.stage,
                    "progress": job.progress,
                    "attempt_count": job.attempt_count,
                    "next_retry_at": (job.next_retry_at.isoformat() if job.next_retry_at else None),
                    "message": job.message,
                    "error": job.error,
                }
                if job
                else None
            ),
            "transcript": (
                {
                    "status": transcript.status,
                    "provider": transcript.provider,
                    "model": transcript.model,
                    "language": transcript.language,
                    "text": transcript.text,
                    "segments": transcript.segments,
                    "error": transcript.error,
                }
                if transcript
                else None
            ),
        }

    @router.post("/lessons/{lesson_id}/transcript")
    async def update_transcript(request: Request, lesson_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        form = await web.validated_form(request)
        text = str(form.get("text", ""))[:500_000]
        principal = web.principal_required(request)
        service = container.workflow_service(principal.organization_id)
        transcript = service.update_text(lesson_id, text)
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "transcript.updated",
            "lesson_transcript",
            transcript.id,
            {"lesson_id": lesson_id},
        )
        return RedirectResponse(f"/lessons/{lesson_id}", status_code=303)

    return router
