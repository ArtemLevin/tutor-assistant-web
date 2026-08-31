from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.modules.practice.application import PracticeRevisionConflict
from tutor_assistant_web.modules.practice.schemas import (
    BootstrapResponse,
    EventBatchRequest,
    EventBatchResponse,
    StateResponse,
    StateUpdateRequest,
)


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/v1/practice/me", tags=["practice"])

    def service(request: Request):
        principal = container.web.principal_required(request)
        return container.practice_service(principal)

    @router.get("/bootstrap", response_model=BootstrapResponse)
    def bootstrap(request: Request):
        return service(request).bootstrap()

    @router.get("/state", response_model=StateResponse)
    def get_state(request: Request):
        return service(request).state()

    @router.post("/events:batch", response_model=EventBatchResponse)
    def post_events(request: Request, payload: EventBatchRequest):
        container.web.validate_csrf_header(request)
        return service(request).ingest_events(payload)

    @router.put("/state", response_model=StateResponse)
    def put_state(request: Request, payload: StateUpdateRequest):
        container.web.validate_csrf_header(request)
        try:
            return service(request).update_state(payload)
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

    return router
