from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from tutor_assistant_web.bootstrap.board_container import build_board_container
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.health_routes import create_board_health_router
from tutor_assistant_web.modules.boards.standalone_contracts import StandaloneBoardProblem
from tutor_assistant_web.modules.boards.standalone_routes import create_standalone_router
from tutor_assistant_web.modules.identity.board_routes import create_board_identity_router
from tutor_assistant_web.observability import configure_logging, configure_telemetry
from tutor_assistant_web.shared.errors import ApplicationError
from tutor_assistant_web.shared.middleware import RateLimitMiddleware, SecurityAndCorrelationMiddleware
from tutor_assistant_web.version import __version__

PACKAGE_DIR = Path(__file__).parent.parent
BOARD_PROFILE_MODULES = ("identity", "audit", "boards", "health", "metrics")
BOARD_PROFILE_PROVIDERS = (
    "database",
    "web",
    "identity",
    "audit",
    "board-persistence",
    "guest-access",
    "artifact-storage",
    "collaboration",
)


def create_board_app(settings: Settings, database: Database | None = None) -> FastAPI:
    configure_logging(settings)
    database = database or Database.from_settings(settings)
    timezone = ZoneInfo(settings.app_timezone)
    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    container = build_board_container(settings, database, templates, timezone)
    templates.env.filters["local_dt"] = lambda value, fmt="%d.%m %H:%M": container.web.localize(
        value
    ).strftime(fmt)
    templates.env.globals["app_name"] = "TutorBoard"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.auto_migrate:
            database.migrate()
        container.identity.bootstrap(settings)
        try:
            yield
        finally:
            await container.collaboration.close()

    app = FastAPI(
        title="TutorBoard API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.container = container
    app.state.app_profile = "board"
    app.state.installed_modules = list(BOARD_PROFILE_MODULES)
    app.state.installed_providers = list(BOARD_PROFILE_PROVIDERS)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        max_age=settings.session_max_age,
        session_cookie=settings.session_cookie_name,
        same_site=settings.session_same_site,
        https_only=settings.session_cookie_secure,
    )
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(SecurityAndCorrelationMiddleware, settings=settings)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[item.strip() for item in settings.trusted_hosts.split(",") if item.strip()],
    )
    app.add_middleware(
        ProxyHeadersMiddleware,
        trusted_hosts={item.strip() for item in settings.trusted_proxy_ips.split(",") if item.strip()},
    )

    @app.exception_handler(ApplicationError)
    async def handle_application_error(_request: Request, exc: ApplicationError):
        if isinstance(exc, StandaloneBoardProblem):
            response = JSONResponse(
                {"code": exc.code, "detail": str(exc)},
                status_code=exc.status_code,
                headers={"Cache-Control": "private, no-store"},
            )
            if exc.code in {"guest_session_invalid", "guest_session_version_mismatch"}:
                container.board_guest_access_service().clear_guest_cookie(response)
            return response
        return JSONResponse(
            {"error": {"code": exc.__class__.__name__, "message": str(exc)}},
            status_code=exc.status_code,
        )

    app.include_router(create_board_identity_router(container))
    app.include_router(create_standalone_router(container))
    app.include_router(create_board_health_router(container))
    configure_telemetry(app, settings, database.engine)
    return app
