from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from tutor_assistant_web.bbb import BigBlueButtonClient, BigBlueButtonError
from tutor_assistant_web.config import Settings, get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.models import (
    JobStatus,
    Lesson,
    LessonStatus,
    MaterialArtifact,
    ProcessingJob,
    Student,
)
from tutor_assistant_web.services import (
    join_token,
    make_meeting_credentials,
    seed_data,
    verify_join_token,
)
from tutor_assistant_web.worker import enqueue_processing

PACKAGE_DIR = Path(__file__).parent


def _localize(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone)


def _parse_local(value: str, timezone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)


def _safe_rate(value: str) -> Decimal:
    try:
        return max(Decimal(value.replace(",", ".") or "0"), Decimal("0"))
    except InvalidOperation as exc:
        raise HTTPException(422, "Некорректная ставка") from exc


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = database or Database(settings.database_url)
    timezone = ZoneInfo(settings.app_timezone)
    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    templates.env.filters["local_dt"] = lambda value, fmt="%d.%m %H:%M": _localize(
        value, timezone
    ).strftime(fmt)
    templates.env.globals["app_name"] = settings.app_name

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.create_schema()
        if settings.seed_demo_data:
            with database.sessions() as session:
                seed_data(session)
        yield

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = database
    app.state.timezone = timezone
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )

    def csrf_token(request: Request) -> str:
        token = request.session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(24)
            request.session["csrf"] = token
        return token

    def context(request: Request, **values: object) -> dict[str, object]:
        return {
            "request": request,
            "csrf_token": csrf_token(request),
            "demo_mode": settings.bbb_demo_mode,
            "timezone_name": settings.app_timezone,
            **values,
        }

    def is_authorized(request: Request) -> bool:
        return not settings.app_access_token or bool(request.session.get("authorized"))

    def require_tutor(request: Request) -> RedirectResponse | None:
        if is_authorized(request):
            return None
        target = quote(request.url.path, safe="/")
        return RedirectResponse(f"/login?next={target}", status_code=303)

    async def validated_form(request: Request):
        form = await request.form()
        supplied = str(form.get("csrf_token", ""))
        expected = str(request.session.get("csrf", ""))
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(403, "Форма устарела. Обновите страницу и повторите действие.")
        return form

    def bbb_client() -> BigBlueButtonClient:
        return BigBlueButtonClient(
            settings.bbb_base_url, settings.bbb_secret, settings.bbb_request_timeout
        )

    @app.exception_handler(BigBlueButtonError)
    async def handle_bbb_error(request: Request, exc: BigBlueButtonError):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context=context(
                request,
                title="BigBlueButton недоступен",
                message=str(exc),
                hint="Проверьте BBB_BASE_URL, BBB_SECRET и доступность сервера.",
            ),
            status_code=502,
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        if is_authorized(request):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"request": request, "next": next, "error": ""},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        form = await request.form()
        token = str(form.get("token", ""))
        target = str(form.get("next", "/"))
        if not secrets.compare_digest(token, settings.app_access_token):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"request": request, "next": target, "error": "Неверный код доступа"},
                status_code=401,
            )
        request.session.clear()
        request.session["authorized"] = True
        csrf_token(request)
        if not target.startswith("/") or target.startswith("//"):
            target = "/"
        return RedirectResponse(target, status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        await validated_form(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        now = datetime.now(UTC)
        with database.sessions() as session:
            students_count = session.scalar(
                select(func.count()).select_from(Student).where(Student.active.is_(True))
            )
            upcoming = list(
                session.scalars(
                    select(Lesson)
                    .options(selectinload(Lesson.student))
                    .where(
                        Lesson.ends_at >= now,
                        Lesson.status.in_([LessonStatus.scheduled.value, LessonStatus.live.value]),
                    )
                    .order_by(Lesson.starts_at)
                    .limit(6)
                )
            )
            pending_jobs = session.scalar(
                select(func.count())
                .select_from(ProcessingJob)
                .where(ProcessingJob.status.in_([JobStatus.queued.value, JobStatus.running.value]))
            )
            artifacts_count = session.scalar(select(func.count()).select_from(MaterialArtifact))
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=context(
                request,
                upcoming=upcoming,
                students_count=students_count or 0,
                pending_jobs=pending_jobs or 0,
                artifacts_count=artifacts_count or 0,
                now=now,
            ),
        )

    @app.get("/students", response_class=HTMLResponse)
    def students_page(request: Request, q: str = ""):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        statement = select(Student).where(Student.active.is_(True)).order_by(Student.full_name)
        if q.strip():
            statement = statement.where(Student.full_name.ilike(f"%{q.strip()}%"))
        with database.sessions() as session:
            students = list(session.scalars(statement))
        return templates.TemplateResponse(
            request=request,
            name="students.html",
            context=context(request, students=students, query=q),
        )

    @app.post("/students")
    async def create_student(request: Request):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        form = await validated_form(request)
        full_name = str(form.get("full_name", "")).strip()
        if len(full_name) < 2:
            raise HTTPException(422, "Укажите имя ученика")
        student = Student(
            full_name=full_name[:160],
            grade=str(form.get("grade", ""))[:32],
            subject=str(form.get("subject", "Математика"))[:120],
            goal=str(form.get("goal", ""))[:4000],
            guardian_name=str(form.get("guardian_name", ""))[:160],
            guardian_phone=str(form.get("guardian_phone", ""))[:80],
            email=str(form.get("email", ""))[:254],
            social_links=str(form.get("social_links", ""))[:2000],
            hourly_rate=_safe_rate(str(form.get("hourly_rate", "0"))),
            notes=str(form.get("notes", ""))[:8000],
        )
        with database.sessions() as session:
            session.add(student)
            session.commit()
        return RedirectResponse(f"/students/{student.id}", status_code=303)

    @app.get("/students/{student_id}", response_class=HTMLResponse)
    def student_detail(request: Request, student_id: str):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        with database.sessions() as session:
            student = session.scalar(
                select(Student)
                .options(selectinload(Student.lessons))
                .where(Student.id == student_id)
            )
            if student is None:
                raise HTTPException(404, "Ученик не найден")
            student.lessons.sort(key=lambda item: item.starts_at, reverse=True)
        return templates.TemplateResponse(
            request=request,
            name="student_detail.html",
            context=context(request, student=student),
        )

    @app.post("/students/{student_id}")
    async def update_student(request: Request, student_id: str):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        form = await validated_form(request)
        with database.sessions() as session:
            student = session.get(Student, student_id)
            if student is None:
                raise HTTPException(404, "Ученик не найден")
            student.full_name = str(form.get("full_name", student.full_name)).strip()[:160]
            student.grade = str(form.get("grade", student.grade))[:32]
            student.subject = str(form.get("subject", student.subject))[:120]
            student.goal = str(form.get("goal", student.goal))[:4000]
            student.guardian_name = str(form.get("guardian_name", student.guardian_name))[:160]
            student.guardian_phone = str(form.get("guardian_phone", student.guardian_phone))[:80]
            student.email = str(form.get("email", student.email))[:254]
            student.social_links = str(form.get("social_links", student.social_links))[:2000]
            student.hourly_rate = _safe_rate(str(form.get("hourly_rate", student.hourly_rate)))
            student.notes = str(form.get("notes", student.notes))[:8000]
            session.commit()
        return RedirectResponse(f"/students/{student_id}", status_code=303)

    @app.get("/schedule", response_class=HTMLResponse)
    def schedule(request: Request, week: str = ""):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        try:
            selected = date.fromisoformat(week) if week else datetime.now(timezone).date()
        except ValueError as exc:
            raise HTTPException(422, "Некорректная дата недели") from exc
        monday = selected - timedelta(days=selected.weekday())
        next_monday = monday + timedelta(days=7)
        start_utc = datetime.combine(monday, time.min, timezone).astimezone(UTC)
        end_utc = datetime.combine(next_monday, time.min, timezone).astimezone(UTC)
        with database.sessions() as session:
            students = list(
                session.scalars(
                    select(Student).where(Student.active.is_(True)).order_by(Student.full_name)
                )
            )
            lessons = list(
                session.scalars(
                    select(Lesson)
                    .options(selectinload(Lesson.student))
                    .where(Lesson.starts_at >= start_utc, Lesson.starts_at < end_utc)
                    .order_by(Lesson.starts_at)
                )
            )
        days = [monday + timedelta(days=index) for index in range(7)]
        lessons_by_day = {
            day: [item for item in lessons if _localize(item.starts_at, timezone).date() == day]
            for day in days
        }
        return templates.TemplateResponse(
            request=request,
            name="schedule.html",
            context=context(
                request,
                monday=monday,
                previous_week=monday - timedelta(days=7),
                next_week=monday + timedelta(days=7),
                week_end=monday + timedelta(days=6),
                days=days,
                lessons_by_day=lessons_by_day,
                students=students,
                default_start=datetime.now(timezone).replace(minute=0, second=0, microsecond=0)
                + timedelta(hours=1),
                default_end=datetime.now(timezone).replace(minute=0, second=0, microsecond=0)
                + timedelta(hours=2),
            ),
        )

    @app.post("/lessons")
    async def create_lesson(request: Request):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        form = await validated_form(request)
        starts_at = _parse_local(str(form.get("starts_at", "")), timezone)
        ends_at = _parse_local(str(form.get("ends_at", "")), timezone)
        if ends_at <= starts_at:
            raise HTTPException(422, "Окончание должно быть позже начала")
        student_id = str(form.get("student_id", ""))
        with database.sessions() as session:
            student = session.get(Student, student_id)
            if student is None or not student.active:
                raise HTTPException(404, "Ученик не найден")
            overlap = session.scalar(
                select(Lesson.id).where(
                    Lesson.status != LessonStatus.cancelled.value,
                    Lesson.starts_at < ends_at,
                    Lesson.ends_at > starts_at,
                )
            )
            if overlap:
                raise HTTPException(409, "В это время уже запланировано занятие")
            meeting_id, attendee, moderator = make_meeting_credentials()
            lesson = Lesson(
                student_id=student.id,
                title=str(form.get("title", "Занятие"))[:200] or "Занятие",
                topic=str(form.get("topic", ""))[:300],
                starts_at=starts_at,
                ends_at=ends_at,
                price_snapshot=student.hourly_rate,
                bbb_meeting_id=meeting_id,
                attendee_password=attendee,
                moderator_password=moderator,
                record_enabled=str(form.get("record_enabled", "")) == "on",
            )
            session.add(lesson)
            session.commit()
        return RedirectResponse(f"/lessons/{lesson.id}", status_code=303)

    @app.get("/lessons/{lesson_id}", response_class=HTMLResponse)
    def lesson_detail(request: Request, lesson_id: str):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        with database.sessions() as session:
            lesson = session.scalar(
                select(Lesson)
                .options(
                    selectinload(Lesson.student),
                    selectinload(Lesson.recordings),
                    selectinload(Lesson.jobs),
                    selectinload(Lesson.artifacts),
                )
                .where(Lesson.id == lesson_id)
            )
            if lesson is None:
                raise HTTPException(404, "Занятие не найдено")
            lesson.jobs.sort(key=lambda item: item.created_at, reverse=True)
            lesson.artifacts.sort(key=lambda item: item.created_at, reverse=True)
            token = join_token(lesson.id, lesson.student_id, settings.app_secret_key)
            student_url = f"{settings.public_base_url.rstrip('/')}/join/{lesson.id}/{token}"
        return templates.TemplateResponse(
            request=request,
            name="lesson_detail.html",
            context=context(request, lesson=lesson, student_url=student_url),
        )

    @app.post("/lessons/{lesson_id}/notes")
    async def update_notes(request: Request, lesson_id: str):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        form = await validated_form(request)
        with database.sessions() as session:
            lesson = session.get(Lesson, lesson_id)
            if lesson is None:
                raise HTTPException(404, "Занятие не найдено")
            lesson.topic = str(form.get("topic", lesson.topic))[:300]
            lesson.tutor_notes = str(form.get("tutor_notes", ""))[:20000]
            session.commit()
        return RedirectResponse(f"/lessons/{lesson_id}", status_code=303)

    def _prepare_meeting(lesson: Lesson) -> None:
        if settings.bbb_demo_mode:
            return
        bbb_client().create_meeting(
            meeting_id=lesson.bbb_meeting_id,
            name=lesson.title,
            attendee_password=lesson.attendee_password,
            moderator_password=lesson.moderator_password,
            record=lesson.record_enabled,
        )

    @app.get("/lessons/{lesson_id}/join/tutor")
    def join_as_tutor(request: Request, lesson_id: str):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        with database.sessions() as session:
            lesson = session.scalar(
                select(Lesson).options(selectinload(Lesson.student)).where(Lesson.id == lesson_id)
            )
            if lesson is None:
                raise HTTPException(404, "Занятие не найдено")
            _prepare_meeting(lesson)
            lesson.status = LessonStatus.live.value
            session.commit()
            if settings.bbb_demo_mode:
                return RedirectResponse(f"/demo-room/{lesson.id}?role=tutor", status_code=303)
            url = bbb_client().join_url(
                meeting_id=lesson.bbb_meeting_id,
                full_name="Преподаватель",
                password=lesson.moderator_password,
                user_id="tutor",
                role="MODERATOR",
                logout_url=f"{settings.public_base_url.rstrip('/')}/lessons/{lesson.id}",
            )
        return RedirectResponse(url, status_code=303)

    @app.get("/join/{lesson_id}/{token}")
    def join_as_student(lesson_id: str, token: str):
        with database.sessions() as session:
            lesson = session.scalar(
                select(Lesson).options(selectinload(Lesson.student)).where(Lesson.id == lesson_id)
            )
            if lesson is None or not verify_join_token(
                lesson.id, lesson.student_id, token, settings.app_secret_key
            ):
                raise HTTPException(404, "Ссылка недействительна")
            if lesson.status == LessonStatus.cancelled.value:
                raise HTTPException(410, "Занятие отменено")
            _prepare_meeting(lesson)
            if settings.bbb_demo_mode:
                return RedirectResponse(f"/demo-room/{lesson.id}?role=student", status_code=303)
            url = bbb_client().join_url(
                meeting_id=lesson.bbb_meeting_id,
                full_name=lesson.student.full_name,
                password=lesson.attendee_password,
                user_id=f"student-{lesson.student_id}",
                role="VIEWER",
                logout_url=f"{settings.public_base_url.rstrip('/')}/lesson-finished",
            )
        return RedirectResponse(url, status_code=303)

    @app.post("/lessons/{lesson_id}/end")
    async def end_lesson(request: Request, lesson_id: str):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        await validated_form(request)
        with database.sessions() as session:
            lesson = session.get(Lesson, lesson_id)
            if lesson is None:
                raise HTTPException(404, "Занятие не найдено")
            if not settings.bbb_demo_mode:
                bbb_client().end_meeting(lesson.bbb_meeting_id)
            lesson.status = LessonStatus.completed.value
            session.commit()
        return RedirectResponse(f"/lessons/{lesson_id}", status_code=303)

    @app.post("/lessons/{lesson_id}/process")
    async def process_lesson(request: Request, lesson_id: str):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        await validated_form(request)
        with database.sessions() as session:
            lesson = session.get(Lesson, lesson_id)
            if lesson is None:
                raise HTTPException(404, "Занятие не найдено")
            running = session.scalar(
                select(ProcessingJob.id).where(
                    ProcessingJob.lesson_id == lesson_id,
                    ProcessingJob.status.in_([JobStatus.queued.value, JobStatus.running.value]),
                )
            )
            if running:
                return RedirectResponse(f"/lessons/{lesson_id}", status_code=303)
            job = ProcessingJob(lesson_id=lesson_id)
            session.add(job)
            session.commit()
        enqueue_processing(job.id)
        return RedirectResponse(f"/lessons/{lesson_id}", status_code=303)

    @app.get("/api/jobs/{job_id}")
    def job_status(request: Request, job_id: str):
        if not is_authorized(request):
            raise HTTPException(401, "Требуется авторизация")
        with database.sessions() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None:
                raise HTTPException(404, "Задание не найдено")
            return {
                "id": job.id,
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "error": job.error,
            }

    @app.get("/artifacts/{artifact_id}.md", response_class=PlainTextResponse)
    def artifact_markdown(request: Request, artifact_id: str):
        blocked = require_tutor(request)
        if blocked:
            return blocked
        with database.sessions() as session:
            artifact = session.get(MaterialArtifact, artifact_id)
            if artifact is None:
                raise HTTPException(404, "Материал не найден")
            return PlainTextResponse(
                artifact.content,
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{artifact.kind}-{artifact.id}.md"'
                    )
                },
            )

    @app.get("/demo-room/{lesson_id}", response_class=HTMLResponse)
    def demo_room(request: Request, lesson_id: str, role: str = "student"):
        if not settings.bbb_demo_mode:
            raise HTTPException(404)
        with database.sessions() as session:
            lesson = session.scalar(
                select(Lesson).options(selectinload(Lesson.student)).where(Lesson.id == lesson_id)
            )
            if lesson is None:
                raise HTTPException(404, "Занятие не найдено")
        return templates.TemplateResponse(
            request=request,
            name="demo_room.html",
            context={"request": request, "lesson": lesson, "role": role},
        )

    @app.get("/lesson-finished", response_class=HTMLResponse)
    def lesson_finished(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="finished.html",
            context={"request": request},
        )

    @app.get("/health/live")
    def health_live():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/health/ready")
    def health_ready():
        checks: dict[str, str] = {"database": "ok"}
        try:
            database.healthcheck()
        except Exception as exc:
            checks["database"] = f"error: {exc}"
            return JSONResponse({"status": "error", "checks": checks}, status_code=503)
        checks["bigbluebutton"] = "demo" if settings.bbb_demo_mode else "configured"
        checks["queue"] = "eager" if settings.task_eager else "redis"
        return {"status": "ok", "checks": checks}

    return app


app = create_app()


def run() -> None:
    uvicorn.run("tutor_assistant_web.app:app", host="0.0.0.0", port=8000, reload=True)
