"""Destructive, explicitly gated staging fixtures for release load tests."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from tutor_assistant_web import models  # noqa: F401
from tutor_assistant_web.config import Settings, get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.models import OutboxEvent
from tutor_assistant_web.modules.classroom.models import RecordingAsset
from tutor_assistant_web.modules.identity.models import Organization
from tutor_assistant_web.modules.materials.models import JobStatus, ProcessingJob
from tutor_assistant_web.modules.scheduling.models import Lesson, LessonStatus
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.security import join_token, make_meeting_credentials


def _guard(settings: Settings) -> None:
    database_name = make_url(settings.database_url).database or ""
    if settings.app_env.lower() != "staging":
        raise RuntimeError("load fixtures require APP_ENV=staging")
    if os.getenv("CONFIRM_LOAD_DATABASE") != database_name:
        raise RuntimeError("CONFIRM_LOAD_DATABASE must exactly match the staging database name")


def seed(settings: Settings, *, count: int = 100, audio_url: str) -> dict[str, object]:
    _guard(settings)
    if not audio_url.startswith("https://"):
        raise RuntimeError("LOAD_AUDIO_URL must be a reachable HTTPS audio fixture")
    database = Database.from_settings(settings)
    batch_id = datetime.now(UTC).strftime("load-%Y%m%dT%H%M%SZ")
    lesson_ids: list[str] = []
    job_ids: list[str] = []
    with database.sessions() as session:
        organization = session.scalar(select(Organization).order_by(Organization.created_at))
        if organization is None:
            raise RuntimeError("bootstrap staging before creating a load fixture")
        student = session.scalar(
            select(Student).where(Student.organization_id == organization.id).limit(1)
        )
        if student is None:
            student = Student(
                organization_id=organization.id,
                full_name=f"Load fixture {batch_id}",
                subject="Load test",
                hourly_rate=Decimal("0"),
            )
            session.add(student)
            session.flush()
        started = datetime.now(UTC).replace(second=0, microsecond=0)
        for index in range(count):
            meeting_id, attendee, moderator = make_meeting_credentials()
            lesson = Lesson(
                organization_id=organization.id,
                student_id=student.id,
                title=f"Load transcription {index + 1}",
                starts_at=started + timedelta(minutes=index),
                ends_at=started + timedelta(minutes=index + 60),
                status=LessonStatus.completed.value,
                price_snapshot=Decimal("0"),
                bbb_meeting_id=meeting_id,
                attendee_password=attendee,
                moderator_password=moderator,
            )
            session.add(lesson)
            session.flush()
            record_id = f"{batch_id}-{index:03d}"
            session.add(
                RecordingAsset(
                    organization_id=organization.id,
                    lesson_id=lesson.id,
                    record_id=record_id,
                    state="published",
                    playback_url=audio_url,
                    raw_metadata={"formats": [{"type": "audio", "url": audio_url}]},
                )
            )
            job = ProcessingJob(
                organization_id=organization.id,
                lesson_id=lesson.id,
                kind="post_lesson",
                queue_name="transcription",
                trigger="staging_load_fixture",
                dedup_key=f"{batch_id}:{record_id}",
                record_id=record_id,
                message="Staging load fixture",
            )
            session.add(job)
            session.flush()
            session.add(
                OutboxEvent(
                    organization_id=organization.id,
                    topic="post_lesson.requested",
                    dedup_key=f"{batch_id}:outbox:{record_id}",
                    payload={"job_id": job.id},
                )
            )
            lesson_ids.append(lesson.id)
            job_ids.append(job.id)
        session.commit()
    join_urls = [
        f"{settings.public_base_url.rstrip('/')}/join/{lesson_id}/"
        f"{join_token(lesson_id, student.id, settings.app_secret_key)}"
        for lesson_id in lesson_ids[:20]
    ]
    return {
        "batch_id": batch_id,
        "lesson_ids": lesson_ids,
        "job_ids": job_ids,
        "parallel_lesson_ids": lesson_ids[:20],
        "student_join_urls": join_urls,
        "queued_transcriptions": len(job_ids),
    }


def wait_for_batch(settings: Settings, batch_id: str, timeout: int) -> dict[str, object]:
    _guard(settings)
    database = Database.from_settings(settings)
    deadline = time.monotonic() + timeout
    terminal = {
        JobStatus.completed.value,
        JobStatus.dead_letter.value,
        JobStatus.canceled.value,
        JobStatus.failed.value,
    }
    statuses: dict[str, int] = {}
    while time.monotonic() < deadline:
        with database.sessions() as session:
            rows = session.execute(
                select(ProcessingJob.status, func.count(ProcessingJob.id))
                .where(ProcessingJob.dedup_key.like(f"{batch_id}:%"))
                .group_by(ProcessingJob.status)
            ).all()
        statuses = {str(status): int(count) for status, count in rows}
        total = sum(statuses.values())
        if (
            total
            and sum(count for status, count in statuses.items() if status in terminal) == total
        ):
            result = {"batch_id": batch_id, "total": total, "statuses": statuses}
            if statuses.get(JobStatus.completed.value, 0) != total:
                raise RuntimeError(json.dumps(result, sort_keys=True))
            return result
        time.sleep(5)
    raise TimeoutError(json.dumps({"batch_id": batch_id, "statuses": statuses}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Gated staging load fixtures")
    commands = parser.add_subparsers(dest="command", required=True)
    seed_command = commands.add_parser("seed")
    seed_command.add_argument("--count", type=int, default=100, choices=range(1, 501))
    seed_command.add_argument("--audio-url", default=os.getenv("LOAD_AUDIO_URL", ""))
    wait_command = commands.add_parser("wait")
    wait_command.add_argument("batch_id")
    wait_command.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()
    settings = get_settings()
    result = (
        seed(settings, count=args.count, audio_url=args.audio_url)
        if args.command == "seed"
        else wait_for_batch(settings, args.batch_id, args.timeout)
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
