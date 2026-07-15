from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from starlette.responses import StreamingResponse

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

    @router.get("/artifact-versions/{artifact_id}/download")
    def artifact_version_download(request: Request, artifact_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        artifact, content = service(request).stream_artifact_version(artifact_id)
        return StreamingResponse(
            content,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{artifact.kind}-v{artifact.version}.{artifact.kind}"'
                ),
                "X-Content-SHA256": artifact.sha256,
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/generation-runs/{run_id}/approve")
    async def approve_run(request: Request, run_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        await web.validated_form(request)
        principal = web.principal_required(request)
        run = service(request).approve(run_id, principal.user_id)
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "materials.approved",
            "generation_run",
            run.id,
            {"lesson_id": run.lesson_id},
        )
        return RedirectResponse(f"/lessons/{run.lesson_id}", status_code=303)

    return router
