from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tutor_assistant_web.bootstrap.container import AppContainer
from tutor_assistant_web.shared.errors import ApplicationError


def create_router(container: AppContainer) -> APIRouter:
    router = APIRouter(tags=["identity"])
    web = container.web
    identity = container.identity

    def team_context(request: Request, invitation_url: str = ""):
        principal = web.principal_required(request)
        memberships, invitations = identity.team(principal.organization_id)
        return web.context(
            request,
            memberships=memberships,
            invitations=invitations,
            invitation_url=invitation_url,
            roles=("admin", "tutor"),
            settings_invitation_ttl=container.settings.invitation_ttl_hours,
        )

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
        web.set_principal(request, principal)
        web.csrf_token(request)
        if not target.startswith("/") or target.startswith("//"):
            target = "/"
        if target == "/" and principal.role in {"student", "parent"}:
            target = "/portal"
        return RedirectResponse(target, status_code=303)

    @router.post("/logout")
    async def logout(request: Request):
        if not web.is_authorized(request):
            return RedirectResponse("/login", status_code=303)
        await web.validated_form(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @router.post("/workspace/switch")
    async def switch_workspace(request: Request):
        principal = web.principal_required(request)
        form = await web.validated_form(request)
        selected = identity.switch_workspace(
            principal.user_id, str(form.get("organization_id", ""))
        )
        web.set_principal(request, selected)
        container.audit_service(selected.organization_id).record(
            selected.user_id,
            "workspace.switched",
            "organization",
            selected.organization_id,
        )
        target = "/portal" if principal.role in {"student", "parent"} else "/"
        return RedirectResponse(target, status_code=303)

    @router.get("/settings/team", response_class=HTMLResponse)
    def team_page(request: Request):
        blocked = web.require_admin(request)
        if blocked:
            return blocked
        return container.templates.TemplateResponse(
            request=request,
            name="team.html",
            context=team_context(request),
        )

    @router.post("/settings/team/invitations", response_class=HTMLResponse)
    async def invite_member(request: Request):
        blocked = web.require_admin(request)
        if blocked:
            return blocked
        principal = web.principal_required(request)
        form = await web.validated_form(request)
        created = identity.create_invitation(
            principal.organization_id,
            principal.user_id,
            str(form.get("email", "")),
            str(form.get("role", "tutor")),
            container.settings.invitation_ttl_hours,
        )
        invitation_url = (
            f"{container.settings.public_base_url.rstrip('/')}/accept-invitation/{created.token}"
        )
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "invitation.created",
            "invitation",
            created.invitation.id,
            {"email": created.invitation.email, "role": created.invitation.role},
        )
        return container.templates.TemplateResponse(
            request=request,
            name="team.html",
            context=team_context(request, invitation_url),
            status_code=201,
        )

    @router.post("/settings/team/members/{membership_id}")
    async def update_member(request: Request, membership_id: str):
        blocked = web.require_admin(request)
        if blocked:
            return blocked
        principal = web.principal_required(request)
        form = await web.validated_form(request)
        membership = identity.update_membership(
            principal.organization_id,
            principal.user_id,
            membership_id,
            str(form.get("role", "tutor")),
            str(form.get("active", "")) == "on",
        )
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "membership.updated",
            "membership",
            membership.id,
            {"role": membership.role, "active": membership.active},
        )
        return RedirectResponse("/settings/team", status_code=303)

    @router.post("/settings/team/invitations/{invitation_id}/revoke")
    async def revoke_invitation(request: Request, invitation_id: str):
        blocked = web.require_admin(request)
        if blocked:
            return blocked
        principal = web.principal_required(request)
        await web.validated_form(request)
        invitation = identity.revoke_invitation(principal.organization_id, invitation_id)
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "invitation.revoked",
            "invitation",
            invitation.id,
        )
        return RedirectResponse("/settings/team", status_code=303)

    @router.get("/accept-invitation/{token}", response_class=HTMLResponse)
    def invitation_page(request: Request, token: str):
        invitation = identity.invitation(token)
        return container.templates.TemplateResponse(
            request=request,
            name="accept_invitation.html",
            context=web.context(request, invitation=invitation, token=token, error=""),
        )

    @router.post("/accept-invitation/{token}", response_class=HTMLResponse)
    async def accept_invitation(request: Request, token: str):
        form = await web.validated_form(request)
        invitation = identity.invitation(token)
        try:
            principal = identity.accept_invitation(
                token,
                str(form.get("full_name", "")),
                str(form.get("password", "")),
            )
        except ApplicationError as exc:
            return container.templates.TemplateResponse(
                request=request,
                name="accept_invitation.html",
                context=web.context(
                    request,
                    invitation=invitation,
                    token=token,
                    error=str(exc),
                    full_name=str(form.get("full_name", "")),
                ),
                status_code=exc.status_code,
            )
        request.session.clear()
        web.set_principal(request, principal)
        web.csrf_token(request)
        container.audit_service(principal.organization_id).record(
            principal.user_id,
            "invitation.accepted",
            "membership",
            principal.user_id,
            {
                "email": principal.email,
                "role": principal.role,
                "student_id": invitation.student_id,
            },
        )
        return RedirectResponse("/", status_code=303)

    return router
