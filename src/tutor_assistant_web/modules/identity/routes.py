from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tutor_assistant_web.bootstrap.container import AppContainer


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["identity"])
    web = container.web
    identity = container.identity

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        if web.is_authorized(request):
            return RedirectResponse("/", status_code=303)
        return container.templates.TemplateResponse(
            request=request,
            name="login.html",
            context=web.context(request, next=next, error=""),
        )

    @router.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        form = await web.validated_form(request)
        email = str(form.get("email", ""))
        password = str(form.get("password", ""))
        target = str(form.get("next", "/"))
        principal = identity.authenticate(email, password)
        if principal is None:
            return container.templates.TemplateResponse(
                request=request,
                name="login.html",
                context=web.context(
                    request,
                    next=target,
                    email=email,
                    error="Неверный email или пароль",
                ),
                status_code=401,
            )
        request.session.clear()
        request.session.update(
            {
                "user_id": principal.user_id,
                "organization_id": principal.organization_id,
                "organization_name": principal.organization_name,
                "role": principal.role,
                "email": principal.email,
                "full_name": principal.full_name,
            }
        )
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
