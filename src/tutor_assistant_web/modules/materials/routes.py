from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from tutor_assistant_web.bootstrap.container import AppContainer


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["materials"])
    web = container.web

    def service(request: Request):
        return container.materials_service(web.organization_id(request))

    @router.post("/lessons/{lesson_id}/process")
    async def process_lesson(request: Request, lesson_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        await web.validated_form(request)
        job = service(request).enqueue(lesson_id)
        if job is not None:
            principal = web.principal_required(request)
            container.audit_service(principal.organization_id).record(
                principal.user_id,
                "materials.enqueued",
                "processing_job",
                job.id,
                {"lesson_id": lesson_id},
            )
        return RedirectResponse(f"/lessons/{lesson_id}", status_code=303)

    @router.get("/api/jobs/{job_id}")
    def job_status(request: Request, job_id: str):
        if not web.is_authorized(request):
            raise HTTPException(401, "Требуется авторизация")
        job = service(request).status(job_id)
        return {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "attempt_count": job.attempt_count,
            "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
            "message": job.message,
            "error": job.error,
        }

    @router.get("/artifacts/{artifact_id}.md", response_class=PlainTextResponse)
    def artifact_markdown(request: Request, artifact_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        artifact = service(request).artifact(artifact_id)
        return PlainTextResponse(
            artifact.content,
            headers={
                "Content-Disposition": (f'attachment; filename="{artifact.kind}-{artifact.id}.md"')
            },
        )

    return router
