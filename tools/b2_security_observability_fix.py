from pathlib import Path

path = Path("src/tutor_assistant_web/observability.py")
text = path.read_text(encoding="utf-8")
old = '_SECRET_QUERY = re.compile(r"(?i)(token|secret|password|checksum)=([^&\\s]+)")\n'
new = (
    '_SECRET_QUERY = re.compile(r"(?i)(token|secret|ticket|password|checksum)=([^&\\s]+)")\n'
    '_JOIN_PATH = re.compile(r"(?i)(/j/)[^/?#\\s]+")\n'
)
if old not in text:
    raise SystemExit("secret-query anchor missing")
text = text.replace(old, new, 1)
old = '''        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _EMAIL.sub("[REDACTED_EMAIL]", value)
        value = _PHONE.sub("[REDACTED_PHONE]", value)
        return _SECRET_QUERY.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
'''
new = '''        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _EMAIL.sub("[REDACTED_EMAIL]", value)
        value = _PHONE.sub("[REDACTED_PHONE]", value)
        value = _JOIN_PATH.sub(r"\\1[REDACTED]", value)
        return _SECRET_QUERY.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
'''
if old not in text:
    raise SystemExit("redact body anchor missing")
text = text.replace(old, new, 1)
old = '''def configure_telemetry(app, settings: Settings, engine) -> None:
    _configure_tracer(settings)
    if not getattr(app.state, "otel_instrumented", False):
        FastAPIInstrumentor.instrument_app(app)
        app.state.otel_instrumented = True
'''
new = '''def _server_request_hook(span, scope: dict[str, Any]) -> None:
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
'''
if old not in text:
    raise SystemExit("telemetry anchor missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
