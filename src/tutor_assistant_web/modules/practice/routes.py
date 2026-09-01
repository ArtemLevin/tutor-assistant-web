from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.modules.identity.models import MembershipRole
from tutor_assistant_web.modules.practice.analytics import (
    PracticeAnalytics,
    ensure_practice_analytics_access,
)
from tutor_assistant_web.modules.practice.application import (
    PracticeRevisionConflict,
    PracticeSyncService,
)
from tutor_assistant_web.modules.practice.features import (
    practice_analytics_enabled,
    practice_retention_index_version,
    practice_sync_enabled,
)
from tutor_assistant_web.modules.practice.schemas import (
    BootstrapResponse,
    EventBatchRequest,
    EventBatchResponse,
    PracticeAnalyticsMetadataDocument,
    PracticeAnalyticsMetadataResponse,
    StateResponse,
    StateUpdateRequest,
)
from tutor_assistant_web.modules.students.application import StudentService


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["practice"])

    def require_sync() -> None:
        if not practice_sync_enabled():
            raise HTTPException(503, "Practice sync is disabled")

    def require_analytics() -> None:
        if not practice_analytics_enabled():
            raise HTTPException(503, "Practice analytics is disabled")

    def sync_service(request: Request) -> PracticeSyncService:
        require_sync()
        principal = container.web.principal_required(request)
        return PracticeSyncService(
            container.database,
            principal,
            container.audit_service(principal.organization_id),
        )

    def analytics_service(request: Request) -> tuple[PracticeAnalytics, object]:
        require_analytics()
        principal = container.web.principal_required(request)
        return (
            PracticeAnalytics(
                container.database,
                principal.organization_id,
                retention_index_version=practice_retention_index_version(),
            ),
            principal,
        )

    @router.get("/api/v1/practice/me/bootstrap", response_model=BootstrapResponse)
    def bootstrap(request: Request):
        return sync_service(request).bootstrap()

    @router.get("/api/v1/practice/me/state", response_model=StateResponse)
    def get_state(request: Request):
        return sync_service(request).state()

    @router.post("/api/v1/practice/me/events:batch", response_model=EventBatchResponse)
    def post_events(request: Request, payload: EventBatchRequest):
        container.web.validate_csrf_header(request)
        return sync_service(request).ingest_events(payload)

    @router.put("/api/v1/practice/me/state", response_model=StateResponse)
    def put_state(request: Request, payload: StateUpdateRequest):
        container.web.validate_csrf_header(request)
        try:
            return sync_service(request).update_state(payload)
        except PracticeRevisionConflict as conflict:
            canonical = conflict.response.model_dump(mode="json")
            return JSONResponse(
                status_code=409,
                content={
                    "schemaVersion": 1,
                    "error": "revision-conflict",
                    "revision": canonical["revision"],
                    "state": canonical["state"],
                    "serverTime": canonical["serverTime"],
                },
                headers={"Cache-Control": "private, no-store"},
            )

    @router.put(
        "/api/v1/practice/me/metadata",
        response_model=PracticeAnalyticsMetadataResponse,
    )
    def put_my_metadata(request: Request, payload: PracticeAnalyticsMetadataDocument):
        container.web.validate_csrf_header(request)
        service = sync_service(request)
        student_id = service.student_id()
        analytics, principal = analytics_service(request)
        ensure_practice_analytics_access(container.database, principal, student_id)
        result = analytics.save_metadata(student_id, payload)
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "practice.analytics_metadata.updated",
            "student",
            student_id,
            {"source_revision": payload.sourceRevision},
        )
        return result

    @router.get("/api/v1/practice/students/{student_id}/analytics")
    def student_analytics(request: Request, student_id: str):
        analytics, principal = analytics_service(request)
        ensure_practice_analytics_access(container.database, principal, student_id)
        return analytics.student_report(student_id)

    @router.get("/api/v1/practice/students/{student_id}/brief")
    def student_practice_brief(request: Request, student_id: str):
        analytics, principal = analytics_service(request)
        ensure_practice_analytics_access(container.database, principal, student_id)
        return analytics.build_pre_lesson_practice_brief(student_id)

    @router.put(
        "/api/v1/practice/students/{student_id}/metadata",
        response_model=PracticeAnalyticsMetadataResponse,
    )
    def put_student_metadata(
        request: Request,
        student_id: str,
        payload: PracticeAnalyticsMetadataDocument,
    ):
        container.web.validate_csrf_header(request)
        analytics, principal = analytics_service(request)
        if principal.role not in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            raise HTTPException(403, "Teacher role is required")
        ensure_practice_analytics_access(container.database, principal, student_id)
        result = analytics.save_metadata(student_id, payload)
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "practice.analytics_metadata.updated",
            "student",
            student_id,
            {"source_revision": payload.sourceRevision},
        )
        return result

    @router.get("/students/{student_id}/practice", response_class=HTMLResponse)
    def student_practice_page(request: Request, student_id: str):
        require_analytics()
        blocked = container.web.require_tutor(request)
        if blocked:
            return blocked
        principal = container.web.principal_required(request)
        ensure_practice_analytics_access(container.database, principal, student_id)
        student = StudentService(container.database, principal.organization_id).get(student_id)
        analytics = PracticeAnalytics(
            container.database,
            principal.organization_id,
            retention_index_version=practice_retention_index_version(),
        )
        report = analytics.student_report(student_id)
        brief = analytics.build_pre_lesson_practice_brief(student_id)
        return container.templates.TemplateResponse(
            request=request,
            name="student_practice.html",
            context=container.web.context(
                request,
                student=student,
                practice=report,
                practice_brief=brief,
            ),
        )

    return router
