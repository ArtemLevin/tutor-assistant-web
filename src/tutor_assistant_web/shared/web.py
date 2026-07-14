from __future__ import annotations

import secrets
from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from tutor_assistant_web.config import Settings


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
            **values,
        }

    def is_authorized(self, request: Request) -> bool:
        return not self.settings.app_access_token or bool(request.session.get("authorized"))

    def require_tutor(self, request: Request) -> RedirectResponse | None:
        if self.is_authorized(request):
            return None
        target = quote(request.url.path, safe="/")
        return RedirectResponse(f"/login?next={target}", status_code=303)

    async def validated_form(self, request: Request):
        form = await request.form()
        supplied = str(form.get("csrf_token", ""))
        expected = str(request.session.get("csrf", ""))
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(403, "Форма устарела. Обновите страницу и повторите действие.")
        return form
