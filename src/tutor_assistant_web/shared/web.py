from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from tutor_assistant_web.config import Settings
from tutor_assistant_web.modules.identity.application import IdentityService, Principal
from tutor_assistant_web.shared.errors import ApplicationError


class WebSupport:
    def __init__(
        self,
        settings: Settings,
        templates: Jinja2Templates,
        timezone: ZoneInfo,
        identity: IdentityService,
    ) -> None:
        self.settings = settings
        self.templates = templates
        self.timezone = timezone
        self.identity = identity

    def localize(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(self.timezone)

    def csrf_token(self, request: Request) -> str:
        token = request.session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(24)
            request.session["csrf"] = token
        return token

    def context(self, request: Request, **values: object) -> dict[str, object]:
        principal = self.principal(request)
        return {
            "request": request,
            "csrf_token": self.csrf_token(request),
            "demo_mode": self.settings.bbb_demo_mode,
            "timezone_name": self.settings.app_timezone,
            "principal": principal,
            "workspaces": self.identity.workspaces(principal.user_id) if principal else [],
            **values,
        }

    def is_authorized(self, request: Request) -> bool:
        return self.principal(request) is not None

    def principal(self, request: Request) -> Principal | None:
        if hasattr(request.state, "validated_principal"):
            return request.state.validated_principal
        session = request.session
        now = int(time.time())
        created = int(session.get("session_created", now))
        seen = int(session.get("session_seen", now))
        if (
            now - created > self.settings.session_max_age
            or now - seen > self.settings.session_idle_timeout
        ):
            request.session.clear()
            request.state.validated_principal = None
            return None
        required = ("user_id", "organization_id", "role", "email", "full_name")
        if not all(session.get(key) for key in required):
            request.state.validated_principal = None
            return None
        try:
            principal = self.identity.switch_workspace(
                str(session["user_id"]), str(session["organization_id"])
            )
        except ApplicationError:
            request.session.clear()
            request.state.validated_principal = None
            return None
        self.set_principal(request, principal)
        request.session["session_seen"] = now
        rotated = int(request.session.get("session_rotated", created))
        if now - rotated >= self.settings.session_rotation_seconds:
            request.session["session_id"] = secrets.token_urlsafe(24)
            request.session["session_rotated"] = now
        request.state.validated_principal = principal
        return principal

    def organization_id(self, request: Request) -> str:
        return self.principal_required(request).organization_id

    def principal_required(self, request: Request) -> Principal:
        principal = self.principal(request)
        if principal is None:
            raise HTTPException(401, "Требуется авторизация")
        return principal

    def require_tutor(self, request: Request) -> RedirectResponse | None:
        principal = self.principal(request)
        if principal and principal.role in {"admin", "tutor"}:
            return None
        if principal:
            raise HTTPException(403, "Для этого раздела требуется роль преподавателя")
        target = quote(request.url.path, safe="/")
        return RedirectResponse(f"/login?next={target}", status_code=303)

    def require_admin(self, request: Request) -> RedirectResponse | None:
        principal = self.principal(request)
        if principal and principal.role == "admin":
            return None
        if principal:
            raise HTTPException(403, "Для этого раздела требуется роль администратора")
        target = quote(request.url.path, safe="/")
        return RedirectResponse(f"/login?next={target}", status_code=303)

    def require_recipient(self, request: Request) -> RedirectResponse | None:
        principal = self.principal(request)
        if principal and principal.role in {"student", "parent"}:
            return None
        if principal:
            raise HTTPException(403, "Раздел доступен ученикам и родителям")
        target = quote(request.url.path, safe="/")
        return RedirectResponse(f"/login?next={target}", status_code=303)

    def set_principal(self, request: Request, principal: Principal) -> None:
        now = int(time.time())
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
        request.session.setdefault("session_id", secrets.token_urlsafe(24))
        request.session.setdefault("session_created", now)
        request.session.setdefault("session_rotated", now)
        request.session["session_seen"] = now
        request.state.validated_principal = principal

    async def validated_form(self, request: Request):
        form = await request.form()
        supplied = str(form.get("csrf_token", ""))
        expected = str(request.session.get("csrf", ""))
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(403, "Форма устарела. Обновите страницу и повторите действие.")
        return form
