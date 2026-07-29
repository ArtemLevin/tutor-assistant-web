from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.modules.identity.models import MembershipRole
from tutor_assistant_web.observability import correlation_id

_MAX_GEOMETRY_BODY_BYTES = 2 * 1024 * 1024
_ALLOWED_RESPONSE_TYPES = {
    "application/json",
    "application/problem+json",
}


def create_geometry_gateway_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/geometryos", tags=["geometryos-gateway"])

    def require_geometry_access(request: Request) -> None:
        principal = container.web.principal_required(request)
        if principal.role not in {
            MembershipRole.admin.value,
            MembershipRole.tutor.value,
        }:
            raise HTTPException(403, "GeometryOS is available to tutors and administrators")

    async def proxy(request: Request, method: str, path: str) -> Response:
        require_geometry_access(request)
        request_id = correlation_id()
        headers = {
            "Accept": "application/json, application/problem+json",
            "X-Request-ID": request_id,
        }
        content = b""
        if method == "POST":
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise HTTPException(415, "GeometryOS gateway requires application/json")
            chunks = bytearray()
            async for chunk in request.stream():
                chunks.extend(chunk)
                if len(chunks) > _MAX_GEOMETRY_BODY_BYTES:
                    raise HTTPException(413, "GeometryOS request exceeds the size limit")
            content = bytes(chunks)
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(
                base_url=container.settings.geometryos_base_url.rstrip("/"),
                timeout=container.settings.geometryos_request_timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                upstream = await client.request(
                    method,
                    path,
                    content=content,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise HTTPException(504, "GeometryOS request timed out") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(503, "GeometryOS is unavailable") from exc
        media_type = upstream.headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type not in _ALLOWED_RESPONSE_TYPES:
            raise HTTPException(502, "GeometryOS returned an unsupported content type")
        if len(upstream.content) > _MAX_GEOMETRY_BODY_BYTES:
            raise HTTPException(502, "GeometryOS response exceeds the size limit")
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=media_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Request-ID": upstream.headers.get("x-request-id", request_id),
            },
        )

    @router.get("/ready")
    async def geometry_ready(request: Request):
        return await proxy(request, "GET", "/health/ready")

    @router.post("/api/v1/generate")
    async def geometry_generate(request: Request):
        return await proxy(request, "POST", "/api/v1/generate")

    @router.post("/api/v1/layout")
    async def geometry_layout(request: Request):
        return await proxy(request, "POST", "/api/v1/layout")

    return router
