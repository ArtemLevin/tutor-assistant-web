from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tutor_assistant_web.bootstrap.container import AppContainer


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["classroom"])
    web = container.web

    def service(request: Request):
        return container.classroom_service(web.organization_id(request))

    @router.get("/lessons/{lesson_id}", response_class=HTMLResponse)
    def lesson_detail(request: Request, lesson_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        classroom = service(request)
        lesson = classroom.detail(lesson_id)
        return container.templates.TemplateResponse(
            request=request,
            name="lesson_detail.html",
            context=web.context(
                request,
                lesson=lesson,
                student_url=classroom.student_link(lesson),
            ),
        )

    @router.post("/lessons/{lesson_id}/notes")
    async def update_notes(request: Request, lesson_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        form = await web.validated_form(request)
        service(request).update_notes(
            lesson_id,
            str(form.get("topic", "")),
            str(form.get("tutor_notes", "")),
        )
        principal = web.principal_required(request)
        container.audit_service(principal.organization_id).record(
            principal.user_id, "lesson.notes_updated", "lesson", lesson_id
        )
        return RedirectResponse(f"/lessons/{lesson_id}", status_code=303)

    @router.get("/lessons/{lesson_id}/join/tutor")
    def join_as_tutor(request: Request, lesson_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        return RedirectResponse(service(request).join_tutor(lesson_id), status_code=303)

    @router.get("/join/{lesson_id}/{token}")
    def join_as_student(lesson_id: str, token: str):
        return RedirectResponse(
            container.classroom_service(None).join_student(lesson_id, token), status_code=303
        )

    @router.post("/lessons/{lesson_id}/end")
    async def end_lesson(request: Request, lesson_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        await web.validated_form(request)
        service(request).end(lesson_id)
        principal = web.principal_required(request)
        container.audit_service(principal.organization_id).record(
            principal.user_id, "lesson.ended", "lesson", lesson_id
        )
        return RedirectResponse(f"/lessons/{lesson_id}", status_code=303)

    @router.get("/demo-room/{lesson_id}", response_class=HTMLResponse)
    def demo_room(request: Request, lesson_id: str, role: str = "student"):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        lesson = service(request).demo_room(lesson_id)
        return container.templates.TemplateResponse(
            request=request,
            name="demo_room.html",
            context={"request": request, "lesson": lesson, "role": role},
        )

    @router.get("/lesson-finished", response_class=HTMLResponse)
    def lesson_finished(request: Request):
        return container.templates.TemplateResponse(
            request=request,
            name="finished.html",
            context={"request": request},
        )

    return router
