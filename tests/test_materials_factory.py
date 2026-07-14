from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.classroom.application import ClassroomService
from tutor_assistant_web.modules.materials.application import MaterialsService
from tutor_assistant_web.modules.materials.evidence import LessonEvidenceBundleV1
from tutor_assistant_web.modules.materials.models import (
    ArtifactStatus,
    ArtifactVersion,
    BuildLog,
    EvidenceBundle,
    GenerationRun,
    GenerationStatus,
    ProcessingJob,
)
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.providers.documents import (
    LatexedDocumentEngine,
    LocalArtifactStorage,
    LocalDocumentEngine,
)
from tutor_assistant_web.providers.materials import LocalTemplateMaterialGenerator
from tutor_assistant_web.shared.contracts import DocumentBuildRequest, GeneratedArtifact

ORG_ID = "00000000-0000-0000-0000-000000000001"


class DemoConference:
    name = "demo"
    is_demo = True

    def recordings(self, meeting_id):
        return []


def add_lesson_and_job(database: Database) -> tuple[Lesson, ProcessingJob]:
    with database.sessions() as session:
        student = Student(
            organization_id=ORG_ID,
            full_name="Анна Петрова",
            grade="9 класс",
            subject="Математика",
            goal="ОГЭ",
        )
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=ORG_ID,
            student_id=student.id,
            title="Геометрия",
            topic="Подобие треугольников",
            tutor_notes="Закрепить признаки подобия",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id=f"meeting-{student.id}",
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.flush()
        job = ProcessingJob(organization_id=ORG_ID, lesson_id=lesson.id)
        session.add(job)
        session.commit()
        return lesson, job


def service(database: Database, storage_root: Path) -> MaterialsService:
    classroom = ClassroomService(
        database,
        DemoConference(),
        "http://localhost:8000",
        "secret",
        ORG_ID,
    )
    return MaterialsService(
        database,
        LocalTemplateMaterialGenerator(),
        classroom,
        organization_id=ORG_ID,
        document_engine=LocalDocumentEngine(),
        artifact_storage=LocalArtifactStorage(storage_root),
    )


def test_factory_builds_versions_and_is_idempotent(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'factory.db'}")
    database.migrate()
    _, job = add_lesson_and_job(database)
    materials = service(database, tmp_path / "artifacts")

    materials.process(job.id, sync_recordings=False)
    materials.process(job.id, start=False, sync_recordings=False)

    with database.sessions() as session:
        assert session.scalar(select(func.count(EvidenceBundle.id))) == 1
        assert session.scalar(select(func.count(GenerationRun.id))) == 1
        assert session.scalar(select(func.count(ArtifactVersion.id))) == 3
        assert session.scalar(select(func.count(BuildLog.id))) == 2
        run = session.scalar(select(GenerationRun))
        versions = list(session.scalars(select(ArtifactVersion).order_by(ArtifactVersion.kind)))
        assert run is not None and run.status == GenerationStatus.review_required.value
        assert {item.kind for item in versions} == {"html", "pdf", "tex"}
        assert all((tmp_path / "artifacts" / item.storage_key).is_file() for item in versions)

    materials.approve(run.id, "tutor-user")
    materials.publish(run.id)
    with database.sessions() as session:
        published = session.get(GenerationRun, run.id)
        statuses = set(session.scalars(select(ArtifactVersion.status)))
        assert published is not None and published.status == GenerationStatus.published.value
        assert statuses == {ArtifactStatus.published.value}


def test_rebuild_creates_new_generation_and_version_number(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'rebuild.db'}")
    database.migrate()
    lesson, first_job = add_lesson_and_job(database)
    materials = service(database, tmp_path / "artifacts")
    materials.process(first_job.id, sync_recordings=False)
    with database.sessions() as session:
        second_job = ProcessingJob(organization_id=ORG_ID, lesson_id=lesson.id)
        session.add(second_job)
        session.commit()

    materials.process(second_job.id, sync_recordings=False)

    with database.sessions() as session:
        assert session.scalar(select(func.count(EvidenceBundle.id))) == 1
        assert session.scalar(select(func.count(GenerationRun.id))) == 2
        assert set(session.scalars(select(ArtifactVersion.version))) == {1, 2}


def test_latexed_adapter_uses_real_compile_raw_contract():
    pdf = b"%PDF-1.4\ncompiled"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        if request.url.path == "/api/compile/raw":
            payload = json.loads(request.content)
            assert payload["content"].startswith("\\documentclass")
            return httpx.Response(
                200,
                json={"status": "success", "pdf_url": "/api/artifacts/pdf"},
            )
        assert request.url.path == "/api/artifacts/pdf"
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})

    engine = LatexedDocumentEngine(
        "https://latex.example.test",
        "token",
        transport=httpx.MockTransport(handler),
    )
    result = engine.build(
        DocumentBuildRequest(
            title="Тест",
            evidence={"schema_version": "1.0"},
            materials=[GeneratedArtifact("summary", "Итог", "Решили задачу")],
        )
    )

    assert result.engine == "latex-for-everyone"
    assert {item.kind for item in result.outputs} == {"tex", "html", "pdf"}
    assert next(item.content for item in result.outputs if item.kind == "pdf") == pdf


def test_committed_evidence_schema_matches_model_contract():
    schema_path = Path(__file__).parents[1] / "schemas" / "lesson-evidence-bundle-v1.schema.json"
    committed = json.loads(schema_path.read_text(encoding="utf-8"))
    generated = LessonEvidenceBundleV1.model_json_schema()

    assert committed["properties"]["schema_version"]["const"] == "1.0"
    assert set(generated["required"]).issubset(committed["required"])
