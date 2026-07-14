from __future__ import annotations

import hashlib
import hmac
import secrets


def make_meeting_credentials() -> tuple[str, str, str]:
    return (
        f"lesson-{secrets.token_urlsafe(16)}",
        secrets.token_urlsafe(12),
        secrets.token_urlsafe(12),
    )


def join_token(lesson_id: str, student_id: str, secret: str) -> str:
    payload = f"{lesson_id}:{student_id}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def verify_join_token(lesson_id: str, student_id: str, token: str, secret: str) -> bool:
    return hmac.compare_digest(join_token(lesson_id, student_id, secret), token)
