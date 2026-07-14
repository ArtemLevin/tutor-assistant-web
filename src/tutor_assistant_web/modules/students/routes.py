from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.modules.students.application import StudentData, StudentService
from tutor_assistant_web.shared.errors import ValidationError


def _rate(value: object) -> Decimal:
    try:
        return max(Decimal(str(value).replace(",", ".") or "0"), Decimal("0"))
    except InvalidOperation as exc:
        raise ValidationError("Некорректная ставка") from exc


def _student_data(form, *, fallback=None) -> StudentData:
    def value(name: str, default: object = "") -> str:
        return str(form.get(name, default))

    return StudentData(
        full_name=value("full_name", fallback.full_name if fallback else ""),
        grade=value("grade", fallback.grade if fallback else ""),
        subject=value("subject", fallback.subject if fallback else "Математика"),
        goal=value("goal", fallback.goal if fallback else ""),
        guardian_name=value("guardian_name", fallback.guardian_name if fallback else ""),
        guardian_phone=value("guardian_phone", fallback.guardian_phone if fallback else ""),
        email=value("email", fallback.email if fallback else ""),
        social_links=value("social_links", fallback.social_links if fallback else ""),
        hourly_rate=_rate(form.get("hourly_rate", fallback.hourly_rate if fallback else "0")),
        notes=value("notes", fallback.notes if fallback else ""),
    )


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/students", tags=["students"])
    web = container.web

    def service(request: Request) -> StudentService:
        return StudentService(container.database, web.organization_id(request))

    @router.get("", response_class=HTMLResponse)
    def students_page(request: Request, q: str = ""):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        return container.templates.TemplateResponse(
            request=request,
            name="students.html",
            context=web.context(request, students=service(request).list_active(q), query=q),
        )

    @router.post("")
    async def create_student(request: Request):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        form = await web.validated_form(request)
        student = service(request).create(_student_data(form))
        principal = web.principal_required(request)
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "student.created",
            "student",
            student.id,
            {"full_name": student.full_name},
        )
        return RedirectResponse(f"/students/{student.id}", status_code=303)

    @router.get("/{student_id}", response_class=HTMLResponse)
    def student_detail(request: Request, student_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        student = service(request).get(student_id, with_lessons=True)
        return container.templates.TemplateResponse(
            request=request,
            name="student_detail.html",
            context=web.context(request, student=student),
        )

    @router.post("/{student_id}")
    async def update_student(request: Request, student_id: str):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        form = await web.validated_form(request)
        current = service(request).get(student_id)
        service(request).update(student_id, _student_data(form, fallback=current))
        principal = web.principal_required(request)
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "student.updated",
            "student",
            student_id,
        )
        return RedirectResponse(f"/students/{student_id}", status_code=303)

    return router
