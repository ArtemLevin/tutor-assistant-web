from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.modules.scheduling.application import CreateLesson, SchedulingService
from tutor_assistant_web.modules.students.application import StudentService
from tutor_assistant_web.shared.errors import ValidationError


def _parse_local(value: str, timezone) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("Некорректные дата и время") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["scheduling"])
    web = container.web

    def services(request: Request):
        organization_id = web.organization_id(request)
        return (
            SchedulingService(container.database, container.timezone, organization_id),
            StudentService(container.database, organization_id),
        )

    @router.get("/schedule", response_class=HTMLResponse)
    def schedule(request: Request, week: str = ""):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        try:
            selected = date.fromisoformat(week) if week else datetime.now(container.timezone).date()
        except ValueError as exc:
            raise ValidationError("Некорректная дата недели") from exc
        scheduling, students = services(request)
        view = scheduling.week(selected)
        default_start = datetime.now(container.timezone).replace(
            minute=0, second=0, microsecond=0
        ) + timedelta(hours=1)
        return container.templates.TemplateResponse(
            request=request,
            name="schedule.html",
            context=web.context(
                request,
                monday=view.monday,
                previous_week=view.monday - timedelta(days=7),
                next_week=view.monday + timedelta(days=7),
                week_end=view.monday + timedelta(days=6),
                days=view.days,
                lessons_by_day=view.lessons_by_day,
                students=students.list_active(),
                default_start=default_start,
                default_end=default_start + timedelta(hours=1),
            ),
        )

    @router.post("/lessons")
    async def create_lesson(request: Request):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        form = await web.validated_form(request)
        scheduling, _ = services(request)
        lesson = scheduling.create(
            CreateLesson(
                student_id=str(form.get("student_id", "")),
                title=str(form.get("title", "Занятие")),
                topic=str(form.get("topic", "")),
                starts_at=_parse_local(str(form.get("starts_at", "")), container.timezone),
                ends_at=_parse_local(str(form.get("ends_at", "")), container.timezone),
                record_enabled=str(form.get("record_enabled", "")) == "on",
            )
        )
        return RedirectResponse(f"/lessons/{lesson.id}", status_code=303)

    return router
