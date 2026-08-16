from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing anchor in {path}: {old[:100]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


# Guest CSRF failures are contract problems, never unhandled RuntimeErrors.
path = "src/tutor_assistant_web/modules/boards/guest_access.py"
replace(
    path,
    '''    def validate_csrf_header(self, request: Request, principal: GuestPrincipal) -> None:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not secrets.compare_digest(supplied, principal.csrf_token):
            raise GuestSessionInvalid("Guest CSRF token is missing or stale")
''',
    '''    def validate_csrf_header(self, request: Request, principal: GuestPrincipal) -> None:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not secrets.compare_digest(supplied, principal.csrf_token):
            from tutor_assistant_web.modules.boards.standalone_contracts import (
                StandaloneBoardProblem,
            )

            raise StandaloneBoardProblem(
                "guest_session_invalid",
                "Guest CSRF token is missing or stale.",
                403,
            )
''',
)

# Standalone Problem responses preserve the frozen B0 public error shape.
path = "src/tutor_assistant_web/bootstrap/app_factory.py"
replace(
    path,
    'from tutor_assistant_web.modules.boards.module import MODULE as BOARDS_MODULE\n',
    'from tutor_assistant_web.modules.boards.module import MODULE as BOARDS_MODULE\n'
    'from tutor_assistant_web.modules.boards.standalone_contracts import StandaloneBoardProblem\n',
)
replace(
    path,
    '''    @app.exception_handler(ApplicationError)
    async def handle_application_error(request: Request, exc: ApplicationError):
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {
                    "error": {
                        "code": exc.__class__.__name__,
                        "message": str(exc),
                    }
                },
                status_code=exc.status_code,
            )
''',
    '''    @app.exception_handler(ApplicationError)
    async def handle_application_error(request: Request, exc: ApplicationError):
        if isinstance(exc, StandaloneBoardProblem):
            response = JSONResponse(
                {"code": exc.code, "detail": str(exc)},
                status_code=exc.status_code,
                headers={"Cache-Control": "private, no-store"},
            )
            if exc.code in {"guest_session_invalid", "guest_session_version_mismatch"}:
                container.board_guest_access_service().clear_guest_cookie(response)
            return response
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {
                    "error": {
                        "code": exc.__class__.__name__,
                        "message": str(exc),
                    }
                },
                status_code=exc.status_code,
            )
''',
)

# Rate-limit invite management and public secret exchange. Return standalone Problem shape.
path = "src/tutor_assistant_web/shared/middleware.py"
replace(
    path,
    '''        if "invitation" in path:
            return "invitations", self.settings.rate_limit_invitations
''',
    '''        if "invitation" in path or path.startswith("/j/"):
            return "invitations", self.settings.rate_limit_invitations
''',
)
replace(
    path,
    '''            if count > limit:
                logger.warning("Rate limit exceeded", extra={"category": category})
                return JSONResponse(
                    {"detail": "Слишком много запросов. Повторите попытку позже."},
                    status_code=429,
                    headers={"Retry-After": str(self.settings.rate_limit_window_seconds)},
                )
''',
    '''            if count > limit:
                logger.warning("Rate limit exceeded", extra={"category": category})
                headers = {"Retry-After": str(self.settings.rate_limit_window_seconds)}
                if path.startswith("/j/") or (
                    path.startswith("/api/v1/boards/") and "/invitations" in path
                ):
                    headers["Cache-Control"] = "no-store"
                    return JSONResponse(
                        {
                            "code": "rate_limit_exceeded",
                            "detail": "Too many invitation requests. Retry later.",
                        },
                        status_code=429,
                        headers=headers,
                    )
                return JSONResponse(
                    {"detail": "Слишком много запросов. Повторите попытку позже."},
                    status_code=429,
                    headers=headers,
                )
''',
)

# Persistent logging / Sentry / tracing redaction for secret-bearing URL surfaces.
path = "src/tutor_assistant_web/observability.py"
replace(
    path,
    'r"(?i)(token|secret|password|checksum)=([^&\\s]+)"\n',
    'r"(?i)(token|secret|ticket|password|checksum)=([^&\\s]+)"\n'
    '_JOIN_PATH = re.compile(r"(?i)(/j/)[^/?#\\s]+")\n',
)
replace(
    path,
    '''        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _EMAIL.sub("[REDACTED_EMAIL]", value)
        value = _PHONE.sub("[REDACTED_PHONE]", value)
        return _SECRET_QUERY.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
''',
    '''        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _EMAIL.sub("[REDACTED_EMAIL]", value)
        value = _PHONE.sub("[REDACTED_PHONE]", value)
        value = _JOIN_PATH.sub(r"\\1[REDACTED]", value)
        return _SECRET_QUERY.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
''',
)
replace(
    path,
    '''def configure_telemetry(app, settings: Settings, engine) -> None:
    _configure_tracer(settings)
    if not getattr(app.state, "otel_instrumented", False):
        FastAPIInstrumentor.instrument_app(app)
        app.state.otel_instrumented = True
''',
    '''def _server_request_hook(span, scope: dict[str, Any]) -> None:
    if span is None or not getattr(span, "is_recording", lambda: False)():
        return
    path = str(scope.get("path", ""))
    query = scope.get("query_string", b"")
    if isinstance(query, bytes):
        query_text = query.decode("latin-1", errors="replace")
    else:
        query_text = str(query or "")
    safe_path = str(redact(path))
    safe_query = str(redact(query_text))
    target = safe_path + (f"?{safe_query}" if safe_query else "")
    span.set_attribute("url.path", safe_path)
    span.set_attribute("url.query", safe_query)
    span.set_attribute("http.target", target)


def configure_telemetry(app, settings: Settings, engine) -> None:
    _configure_tracer(settings)
    if not getattr(app.state, "otel_instrumented", False):
        FastAPIInstrumentor.instrument_app(app, server_request_hook=_server_request_hook)
        app.state.otel_instrumented = True
''',
)
