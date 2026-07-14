from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from tutor_assistant_web.bbb import BigBlueButtonError
from tutor_assistant_web.bootstrap.container import build_container
from tutor_assistant_web.bootstrap.registry import ModuleRegistry
from tutor_assistant_web.bootstrap.seed import seed_data
from tutor_assistant_web.config import Settings, get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.audit.module import MODULE as AUDIT_MODULE
from tutor_assistant_web.modules.automation.module import MODULE as AUTOMATION_MODULE
from tutor_assistant_web.modules.classroom.module import MODULE as CLASSROOM_MODULE
from tutor_assistant_web.modules.dashboard.module import MODULE as DASHBOARD_MODULE
from tutor_assistant_web.modules.identity.models import DEFAULT_ORGANIZATION_ID
from tutor_assistant_web.modules.identity.module import MODULE as IDENTITY_MODULE
from tutor_assistant_web.modules.materials.module import MODULE as MATERIALS_MODULE
from tutor_assistant_web.modules.scheduling.module import MODULE as SCHEDULING_MODULE
from tutor_assistant_web.modules.students.module import MODULE as STUDENTS_MODULE
from tutor_assistant_web.shared.errors import ApplicationError

PACKAGE_DIR = Path(__file__).parent.parent
ALL_MODULES = (
    IDENTITY_MODULE,
    AUDIT_MODULE,
    STUDENTS_MODULE,
    SCHEDULING_MODULE,
    CLASSROOM_MODULE,
    MATERIALS_MODULE,
    AUTOMATION_MODULE,
    DASHBOARD_MODULE,
)


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = database or Database(settings.database_url)
    timezone = ZoneInfo(settings.app_timezone)
    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    container = build_container(settings, database, templates, timezone)
    templates.env.filters["local_dt"] = lambda value, fmt="%d.%m %H:%M": container.web.localize(
        value
    ).strftime(fmt)
    templates.env.globals["app_name"] = settings.app_name

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.auto_migrate:
            database.migrate()
        container.identity.bootstrap(settings)
        if settings.seed_demo_data:
            with database.sessions() as session:
                seed_data(session, DEFAULT_ORGANIZATION_ID)
        yield

    app = FastAPI(title=settings.app_name, version="0.6.0", lifespan=lifespan)
    app.state.container = container
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )

    @app.exception_handler(ApplicationError)
    async def handle_application_error(request: Request, exc: ApplicationError):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context=container.web.context(
                request,
                title="Операция не выполнена",
                message=str(exc),
                hint="Проверьте введённые данные и повторите действие.",
            ),
            status_code=exc.status_code,
        )

    @app.exception_handler(BigBlueButtonError)
    async def handle_bbb_error(request: Request, exc: BigBlueButtonError):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context=container.web.context(
                request,
                title="BigBlueButton недоступен",
                message=str(exc),
                hint="Проверьте BBB_BASE_URL, BBB_SECRET и доступность сервера.",
            ),
            status_code=502,
        )

    enabled = {item.strip() for item in settings.enabled_modules.split(",") if item.strip()} or None
    app.state.installed_modules = ModuleRegistry(ALL_MODULES).install(app, container, enabled)
    return app
