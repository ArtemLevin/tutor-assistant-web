from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tutor_assistant_web.bootstrap.board_container import BoardAppContainer


def create_board_identity_router(container: BoardAppContainer) -> APIRouter:
    router = APIRouter(tags=["identity"])
    web = container.web
    identity = container.identity

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/boards"):
        if web.is_authorized(request):
            return RedirectResponse("/boards", status_code=303)
        return container.templates.TemplateResponse(
            request=request,
            name="board_login.html",
            context=web.context(request, next=next, error=""),
            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
        )

    @router.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        form = await web.validated_form(request)
        email = str(form.get("email", ""))
        password = str(form.get("password", ""))
        target = str(form.get("next", "/boards"))
        principal = identity.authenticate(email, password)
        if principal is None or principal.role not in {"admin", "tutor"}:
            request.session.clear()
            return container.templates.TemplateResponse(
                request=request,
                name="board_login.html",
                context=web.context(
                    request,
                    next=target,
                    email=email,
                    error="Неверный email или пароль",
                ),
                status_code=401,
                headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
            )
        request.session.clear()
        web.set_principal(request, principal)
        web.csrf_token(request)
        if not target.startswith("/") or target.startswith("//"):
            target = "/boards"
        if target == "/":
            target = "/boards"
        return RedirectResponse(target, status_code=303)

    @router.post("/logout")
    async def logout(request: Request):
        if not web.is_authorized(request):
            return RedirectResponse("/login", status_code=303)
        await web.validated_form(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    return router
