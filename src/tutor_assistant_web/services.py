from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tutor_assistant_web.bbb import BigBlueButtonClient
from tutor_assistant_web.config import Settings
from tutor_assistant_web.models import (
    JobStatus,
    Lesson,
    MaterialArtifact,
    ProcessingJob,
    RecordingAsset,
    Student,
)


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


def sync_recordings(session: Session, lesson: Lesson, client: BigBlueButtonClient) -> int:
    found = client.get_recordings(lesson.bbb_meeting_id)
    count = 0
    for item in found:
        current = session.scalar(
            select(RecordingAsset).where(RecordingAsset.record_id == item.record_id)
        )
        if current is None:
            current = RecordingAsset(lesson_id=lesson.id, record_id=item.record_id)
            session.add(current)
        current.state = item.state
        current.playback_url = item.playback_url
        current.raw_metadata = item.metadata
        current.synced_at = datetime.now(UTC)
        count += 1
    session.commit()
    return count


def evidence_payload(lesson: Lesson) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "lesson": {
            "id": lesson.id,
            "title": lesson.title,
            "topic": lesson.topic,
            "started_at": lesson.starts_at.isoformat(),
            "ended_at": lesson.ends_at.isoformat(),
            "tutor_notes": lesson.tutor_notes,
        },
        "student": {
            "id": lesson.student.id,
            "full_name": lesson.student.full_name,
            "grade": lesson.student.grade,
            "subject": lesson.student.subject,
            "goal": lesson.student.goal,
        },
        "recordings": [
            {
                "record_id": recording.record_id,
                "state": recording.state,
                "playback_url": recording.playback_url,
                "metadata": recording.raw_metadata,
            }
            for recording in lesson.recordings
        ],
        "requested_artifacts": ["lesson_summary", "homework", "parent_report"],
    }


def _fallback_materials(payload: dict[str, Any]) -> list[dict[str, str]]:
    lesson = payload["lesson"]
    student = payload["student"]
    topic = lesson["topic"] or lesson["title"]
    notes = lesson["tutor_notes"] or "Преподаватель пока не добавил заметки."
    return [
        {
            "kind": "summary",
            "title": f"Итоги занятия: {topic}",
            "content": (
                f"# {topic}\n\n"
                f"Ученик: **{student['full_name']}** ({student['grade'] or 'класс не указан'}).\n\n"
                f"## Заметки преподавателя\n\n{notes}\n\n"
                "## Следующий шаг\n\nПроверить транскрипт и дополнить итоговый материал."
            ),
        },
        {
            "kind": "homework",
            "title": f"Домашнее задание: {topic}",
            "content": (
                f"# Домашнее задание\n\nТема: **{topic}**.\n\n"
                "1. Повторить основные определения занятия.\n"
                "2. Решить 3 задания по теме.\n"
                "3. Отметить шаги, которые вызвали затруднение.\n\n"
                "> Это черновик пилота. Преподавателю следует проверить задания перед публикацией."
            ),
        },
    ]


def request_materials(payload: dict[str, Any], settings: Settings) -> list[dict[str, str]]:
    if not settings.materials_webhook_url:
        return _fallback_materials(payload)
    headers = {"Content-Type": "application/json"}
    if settings.materials_webhook_token:
        headers["Authorization"] = f"Bearer {settings.materials_webhook_token}"
    response = httpx.post(
        settings.materials_webhook_url,
        json=payload,
        headers=headers,
        timeout=settings.materials_request_timeout,
    )
    response.raise_for_status()
    body = response.json()
    artifacts = body.get("artifacts") if isinstance(body, dict) else None
    if not isinstance(artifacts, list):
        raise ValueError("materials webhook must return an artifacts list")
    return [item for item in artifacts if isinstance(item, dict)]


def process_job(session: Session, job_id: str, settings: Settings) -> None:
    job = session.get(ProcessingJob, job_id)
    if job is None:
        return
    job.status = JobStatus.running.value
    job.started_at = datetime.now(UTC)
    job.progress = 10
    job.message = "Собираем данные занятия"
    session.commit()
    try:
        lesson = session.scalar(
            select(Lesson)
            .options(
                selectinload(Lesson.student),
                selectinload(Lesson.recordings),
                selectinload(Lesson.artifacts),
            )
            .where(Lesson.id == job.lesson_id)
        )
        if lesson is None:
            raise ValueError("lesson not found")

        if not settings.bbb_demo_mode:
            client = BigBlueButtonClient(
                settings.bbb_base_url, settings.bbb_secret, settings.bbb_request_timeout
            )
            sync_recordings(session, lesson, client)
            session.refresh(lesson)
        job.progress = 45
        job.message = "Формируем пакет доказательств"
        session.commit()

        payload = evidence_payload(lesson)
        materials = request_materials(payload, settings)
        job.progress = 80
        job.message = "Сохраняем материалы"
        session.commit()

        for item in materials:
            title = str(item.get("title", "Материал"))[:200]
            kind = str(item.get("kind", "summary"))[:32]
            content = item.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            session.add(
                MaterialArtifact(
                    lesson_id=lesson.id,
                    title=title,
                    kind=kind,
                    content=content,
                    source_url=str(item.get("source_url", "")),
                )
            )
        job.status = JobStatus.completed.value
        job.progress = 100
        job.message = "Материалы готовы к проверке"
        job.completed_at = datetime.now(UTC)
        session.commit()
    except Exception as exc:
        session.rollback()
        job = session.get(ProcessingJob, job_id)
        if job is not None:
            job.status = JobStatus.failed.value
            job.error = str(exc)[:4000]
            job.message = "Обработка завершилась ошибкой"
            job.completed_at = datetime.now(UTC)
            session.commit()
        raise


def seed_data(session: Session) -> None:
    if session.scalar(select(Student.id).limit(1)):
        return
    student = Student(
        full_name="Анна Смирнова",
        grade="9 класс",
        subject="Математика",
        goal="Подготовка к ОГЭ, уверенная работа с геометрией",
        guardian_name="Елена Смирнова",
        guardian_phone="+7 900 000-00-00",
        hourly_rate=1800,
        notes="Демонстрационная запись — её можно удалить после знакомства с пилотом.",
    )
    session.add(student)
    session.flush()
    meeting_id, attendee, moderator = make_meeting_credentials()
    starts = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    from datetime import timedelta

    session.add(
        Lesson(
            student_id=student.id,
            title="Геометрия: подобие треугольников",
            topic="Признаки подобия треугольников",
            starts_at=starts + timedelta(hours=2),
            ends_at=starts + timedelta(hours=3),
            price_snapshot=student.hourly_rate,
            bbb_meeting_id=meeting_id,
            attendee_password=attendee,
            moderator_password=moderator,
        )
    )
    session.commit()
