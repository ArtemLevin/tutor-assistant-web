from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tutor_assistant_web.bootstrap.app_factory import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.automation.application import (
    BigBlueButtonWebhookVerifier,
    InvalidWebhookSignature,
    OutboxService,
    PostLessonWorkflowService,
    RecordingReadyService,
)
from tutor_assistant_web.modules.automation.models import (
    LessonTranscript,
    OutboxEvent,
    OutboxStatus,
    TranscriptStatus,
    WebhookReceipt,
)
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.materials.application import MaterialsService
from tutor_assistant_web.modules.materials.models import (
    JobStatus,
    MaterialArtifact,
    ProcessingJob,
)
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.contracts import (
    ConferenceRecording,
    GeneratedArtifact,
    TranscriptionResult,
    TranscriptionSegment,
)

ORG_ID = "00000000-0000-0000-0000-000000000001"


def add_lesson(database: Database, meeting_id: str = "meeting-1") -> Lesson:
    with database.sessions() as session:
        student = Student(
            organization_id=ORG_ID,
            full_name="Иван Иванов",
            subject="Математика",
        )
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=ORG_ID,
            student_id=student.id,
            title="Алгебра",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id=meeting_id,
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.commit()
        return lesson


def test_bbb_recording_callback_is_signed_and_idempotent(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'webhook.db'}")
    settings = Settings(
        app_secret_key="app-secret",
        bootstrap_admin_password="test-password",
        database_url=f"sqlite:///{tmp_path / 'webhook.db'}",
        bbb_demo_mode=False,
        bbb_base_url="https://bbb.example.test",
        bbb_secret="bbb-secret",
        task_eager=False,
        seed_demo_data=False,
    )
    app = create_app(settings, database)
    with TestClient(app) as client:
        lesson = add_lesson(database)
        token = jwt.encode(
            {"meeting_id": lesson.bbb_meeting_id, "record_id": "record-1"},
            settings.bbb_secret,
            algorithm="HS256",
        )
        first = client.post(
            "/webhooks/bigbluebutton/recording-ready",
            data={"signed_parameters": token},
        )
        duplicate = client.post(
            "/webhooks/bigbluebutton/recording-ready",
            data={"signed_parameters": token},
        )
        invalid = client.post(
            "/webhooks/bigbluebutton/recording-ready",
            data={"signed_parameters": f"{token}broken"},
        )

    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert invalid.status_code == 401
    with database.sessions() as session:
        assert session.scalar(select(func.count(WebhookReceipt.id))) == 1
        assert session.scalar(select(func.count(ProcessingJob.id))) == 1
        assert session.scalar(select(func.count(OutboxEvent.id))) == 1


def test_webhook_verifier_rejects_wrong_secret():
    token = jwt.encode(
        {"meeting_id": "meeting", "record_id": "record"},
        "right-secret",
        algorithm="HS256",
    )
    with pytest.raises(InvalidWebhookSignature):
        BigBlueButtonWebhookVerifier("wrong-secret").decode(token)


class FailingDispatcher:
    name = "failing"

    def enqueue_lesson_processing(self, job_id: str, queue: str = "materials") -> None:
        raise RuntimeError(f"broker unavailable for {job_id}")

    def enqueue_outbox_delivery(self, event_id: str, lease_token: str) -> None:
        raise RuntimeError(f"broker unavailable for {event_id}")


def test_outbox_schedules_retry_when_dispatch_fails(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'outbox.db'}")
    database.migrate()
    lesson = add_lesson(database)
    RecordingReadyService(database).accept(lesson.bbb_meeting_id, "record-2")

    result = OutboxService(
        database,
        FailingDispatcher(),
        max_attempts=3,
        retry_base_seconds=10,
    ).dispatch_pending()

    assert result == {"dispatched": 0, "retried": 1, "dead": 0}
    with database.sessions() as session:
        event = session.scalar(select(OutboxEvent))
        assert event is not None
        assert event.status == OutboxStatus.pending.value
        assert event.attempts == 1
        assert "broker unavailable" in event.last_error


class RecordingConference:
    name = "fake-bbb"
    is_demo = False

    def create_room(self, command):
        return None

    def join_url(self, command):
        return "https://bbb.example.test/join"

    def end_room(self, meeting_id):
        return None

    def recordings(self, meeting_id):
        return [
            ConferenceRecording(
                record_id="record-3",
                state="published",
                playback_url="https://media.example.test/lesson.mp3",
                metadata={
                    "formats": [{"type": "podcast", "url": "https://media.example.test/lesson.mp3"}]
                },
            )
        ]


class FakeTranscriber:
    name = "fake-asr"

    def transcribe(self, source):
        return TranscriptionResult(
            text="Решили квадратное уравнение.",
            language="ru",
            segments=[
                TranscriptionSegment(
                    start=0.0,
                    end=3.5,
                    text="Решили квадратное уравнение.",
                )
            ],
            provider=self.name,
            model="test-model",
        )


class CapturingGenerator:
    name = "capture"

    def __init__(self):
        self.evidence = None

    def generate(self, evidence):
        self.evidence = evidence
        return [GeneratedArtifact(kind="summary", title="Итог", content="Готово")]


def test_post_lesson_workflow_transcribes_then_generates_materials(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'workflow.db'}")
    database.migrate()
    lesson = add_lesson(database)
    accepted = RecordingReadyService(database).accept(lesson.bbb_meeting_id, "record-3")
    conference = RecordingConference()
    classroom = ClassroomService(
        database,
        conference,
        "https://app.example.test",
        "secret",
        ORG_ID,
    )
    generator = CapturingGenerator()
    materials = MaterialsService(database, generator, classroom, organization_id=ORG_ID)

    PostLessonWorkflowService(
        database,
        classroom,
        materials,
        FakeTranscriber(),
        ORG_ID,
    ).process(accepted.job_id)

    with database.sessions() as session:
        job = session.get(ProcessingJob, accepted.job_id)
        transcript = session.scalar(select(LessonTranscript))
        artifact = session.scalar(select(MaterialArtifact))
        assert job is not None and job.status == JobStatus.completed.value
        assert transcript is not None and transcript.text.startswith("Решили")
        assert artifact is not None and artifact.job_id == job.id
    assert generator.evidence["transcript"]["text"].startswith("Решили")


def test_post_lesson_transcription_hands_off_to_materials_queue_transactionally(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'workflow-handoff.db'}")
    database.migrate()
    lesson = add_lesson(database)
    accepted = RecordingReadyService(database).accept(lesson.bbb_meeting_id, "record-3")
    classroom = ClassroomService(
        database,
        RecordingConference(),
        "https://app.example.test",
        "secret",
        ORG_ID,
    )
    generator = CapturingGenerator()
    workflow = PostLessonWorkflowService(
        database,
        classroom,
        MaterialsService(database, generator, classroom, organization_id=ORG_ID),
        FakeTranscriber(),
        ORG_ID,
    )

    workflow.transcribe(accepted.job_id)

    with database.sessions() as session:
        job = session.get(ProcessingJob, accepted.job_id)
        topics = set(session.scalars(select(OutboxEvent.topic)))
        transcript = session.scalar(select(LessonTranscript))
        assert job.status == JobStatus.queued.value
        assert job.queue_name == "materials"
        assert job.stage == "materials_queued"
        assert topics == {"post_lesson.requested", "materials.requested"}
        assert transcript.status == TranscriptStatus.completed.value
    assert generator.evidence is None
