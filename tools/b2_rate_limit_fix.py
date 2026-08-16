from pathlib import Path

path = Path("src/tutor_assistant_web/shared/middleware.py")
text = path.read_text(encoding="utf-8")
old = '''            if count > limit:
                logger.warning("Rate limit exceeded", extra={"category": category})
                headers = {"Retry-After": str(self.settings.rate_limit_window_seconds)}
                if path.startswith("/j/") or (
                    path.startswith("/api/v1/boards/") and "/invitations" in path
                ):
'''
new = '''            if count > limit:
                logger.warning("Rate limit exceeded", extra={"category": category})
                headers = {"Retry-After": str(self.settings.rate_limit_window_seconds)}
                path = request.url.path
                if path.startswith("/j/") or (
                    path.startswith("/api/v1/boards/") and "/invitations" in path
                ):
'''
if old not in text:
    raise SystemExit("rate-limit dispatch anchor missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
