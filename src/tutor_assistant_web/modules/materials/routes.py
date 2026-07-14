from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from tutor_assistant_web.bootstrap.container import AppContainer


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["materials"])
    service = container.materials_service()
    web = container.web

    @router.post("/lessons/{lesson_id}/process")
    async def process_lesson(request: Request, lesson_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        await web.validated_form(request)
        service.enqueue(lesson_id)
        return RedirectResponse(f"/lessons/{lesson_id}", status_code=303)

    @router.get("/api/jobs/{job_id}")
    def job_status(request: Request, job_id: str):
        if not web.is_authorized(request):
            raise HTTPException(401, "Требуется авторизация")
        job = service.status(job_id)
        return {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "message": job.message,
            "error": job.error,
        }

    @router.get("/artifacts/{artifact_id}.md", response_class=PlainTextResponse)
    def artifact_markdown(request: Request, artifact_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        artifact = service.artifact(artifact_id)
        return PlainTextResponse(
            artifact.content,
            headers={
                "Content-Disposition": (f'attachment; filename="{artifact.kind}-{artifact.id}.md"')
            },
        )

    return router
