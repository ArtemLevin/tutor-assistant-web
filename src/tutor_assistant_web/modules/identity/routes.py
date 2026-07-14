from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tutor_assistant_web.bootstrap.container import AppContainer


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["identity"])
    web = container.web
    settings = container.settings

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        if web.is_authorized(request):
            return RedirectResponse("/", status_code=303)
        return container.templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"request": request, "next": next, "error": ""},
        )

    @router.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        form = await request.form()
        token = str(form.get("token", ""))
        target = str(form.get("next", "/"))
        if not secrets.compare_digest(token, settings.app_access_token):
            return container.templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"request": request, "next": target, "error": "Неверный код доступа"},
                status_code=401,
            )
        request.session.clear()
        request.session["authorized"] = True
        web.csrf_token(request)
        if not target.startswith("/") or target.startswith("//"):
            target = "/"
        return RedirectResponse(target, status_code=303)

    @router.post("/logout")
    async def logout(request: Request):
        blocked = web.require_tutor(request)
        if blocked:
            return blocked
        await web.validated_form(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    return router
