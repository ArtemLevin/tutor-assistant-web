from __future__ import annotations

import secrets
from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from tutor_assistant_web.config import Settings
from tutor_assistant_web.modules.identity.application import Principal


class WebSupport:
    def __init__(
        self,
        settings: Settings,
        templates: Jinja2Templates,
        timezone: ZoneInfo,
    ) -> None:
        self.settings = settings
        self.templates = templates
        self.timezone = timezone

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
        return {
            "request": request,
            "csrf_token": self.csrf_token(request),
            "demo_mode": self.settings.bbb_demo_mode,
            "timezone_name": self.settings.app_timezone,
            "principal": self.principal(request),
            **values,
        }

    def is_authorized(self, request: Request) -> bool:
        return self.principal(request) is not None

    def principal(self, request: Request) -> Principal | None:
        session = request.session
        required = ("user_id", "organization_id", "role", "email", "full_name")
        if not all(session.get(key) for key in required):
            return None
        return Principal(
            user_id=str(session["user_id"]),
            organization_id=str(session["organization_id"]),
            organization_name=str(session.get("organization_name", "")),
            role=str(session["role"]),
            email=str(session["email"]),
            full_name=str(session["full_name"]),
        )

    def organization_id(self, request: Request) -> str:
        principal = self.principal(request)
        if principal is None:
            raise HTTPException(401, "Требуется авторизация")
        return principal.organization_id

    def require_tutor(self, request: Request) -> RedirectResponse | None:
        principal = self.principal(request)
        if principal and principal.role in {"admin", "tutor"}:
            return None
        if principal:
            raise HTTPException(403, "Для этого раздела требуется роль преподавателя")
        target = quote(request.url.path, safe="/")
        return RedirectResponse(f"/login?next={target}", status_code=303)

    async def validated_form(self, request: Request):
        form = await request.form()
        supplied = str(form.get("csrf_token", ""))
        expected = str(request.session.get("csrf", ""))
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(403, "Форма устарела. Обновите страницу и повторите действие.")
        return form
