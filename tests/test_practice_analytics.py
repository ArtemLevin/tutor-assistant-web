from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.application import IdentityService, Principal
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    StudentAccess,
    User,
)
from tutor_assistant_web.modules.practice.analytics import (
    PracticeAnalytics,
    ensure_practice_analytics_access,
)
from tutor_assistant_web.modules.practice.repository import PracticeRepository
from tutor_assistant_web.modules.practice.retention import calculate_retention_index
from tutor_assistant_web.modules.practice.schemas import PracticeAnalyticsMetadataDocument
from tutor_assistant_web.modules.students.application import StudentData, StudentService
from tutor_assistant_web.shared.errors import ForbiddenError

FIXTURE = Path("contracts/practice-analytics-v1/fixtures/metadata.json")
TODAY = date(2026, 9, 1)


def make_environment(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'practice-analytics.db'}")
    database.migrate()
    identity = IdentityService(database)
    identity.bootstrap(Settings(seed_demo_data=False, bootstrap_admin_password="admin-password"))
    student = StudentService(database, DEFAULT_ORGANIZATION_ID).create(
        StudentData(full_name="Analytics Student")
    )
    student_user = User(
        email="analytics-student@example.test",
        full_name="Analytics Student",
        password_hash=identity.passwords.hash("student-password"),
    )
    parent_user = User(
        email="analytics-parent@example.test",
        full_name="Analytics Parent",
        password_hash=identity.passwords.hash("parent-password"),
    )
    with database.sessions() as session:
        session.add_all([student_user, parent_user])
        session.flush()
        session.add_all(
            [
                Membership(
                    organization_id=DEFAULT_ORGANIZATION_ID,
                    user_id=student_user.id,
                    role=MembershipRole.student.value,
                ),
                Membership(
                    organization_id=DEFAULT_ORGANIZATION_ID,
                    user_id=parent_user.id,
                    role=MembershipRole.parent.value,
                ),
                StudentAccess(
                    organization_id=DEFAULT_ORGANIZATION_ID,
                    student_id=student.id,
                    user_id=student_user.id,
                    role=MembershipRole.student.value,
                ),
                StudentAccess(
                    organization_id=DEFAULT_ORGANIZATION_ID,
                    student_id=student.id,
                    user_id=parent_user.id,
                    role=MembershipRole.parent.value,
                ),
            ]
        )
        session.commit()
    student_principal = Principal(
        user_id=student_user.id,
        organization_id=DEFAULT_ORGANIZATION_ID,
        organization_name="Tutor Workspace",
        role=MembershipRole.student.value,
        email=student_user.email,
        full_name=student_user.full_name,
    )
    parent_principal = Principal(
        user_id=parent_user.id,
        organization_id=DEFAULT_ORGANIZATION_ID,
        organization_name="Tutor Workspace",
        role=MembershipRole.parent.value,
        email=parent_user.email,
        full_name=parent_user.full_name,
    )
    return database, student, student_principal, parent_principal


def practice_event(event_id: str, *, correct: bool, hints: int = 0, attempts: int = 1):
    return {
        "eventVersion": 2,
        "eventId": event_id,
        "timestamp": f"2026-08-{20 + int(event_id[-1])}T12:00:00+00:00",
        "sessionId": f"session-{event_id}",
        "exerciseId": f"demo:v1:{event_id}",
        "competencyId": "skill",
        "generatorKey": "demo.generator",
        "generatorVersion": 1,
        "seed": event_id,
        "difficulty": 1,
        "attemptCount": attempts,
        "hintsUsed": hints,
        "outcome": "correct" if correct else "incorrect",
        "rating": "good" if correct else "again",
        "durationMs": 60_000,
    }


def seed_analytics_data(database: Database, student_id: str) -> None:
    schedule = {
        "status": "active",
        "dueAt": "2026-08-23",
        "intervalStep": 2,
        "intervalDays": 7,
        "attempts": 4,
        "correct": 1,
        "streak": 0,
        "lapses": 3,
        "consecutiveLapses": 2,
        "repeatedLapse": True,
        "hintsUsedTotal": 5,
        "lastGeneratorKey": "demo.generator",
        "lastGeneratorVersion": 1,
    }
    state = {
        "schemaVersion": 2,
        "revision": 1,
        "clientInstanceId": "device",
        "updatedAt": "2026-09-01T08:00:00+00:00",
        "competencies": {"skill": schedule},
        "sessions": {
            "2026-08-31": {
                "sessionId": "session-2026-08-31",
                "date": "2026-08-31",
                "status": "completed",
                "correct": 1,
                "total": 3,
                "completedAt": "2026-08-31T12:30:00+00:00",
            }
        },
        "events": [],
    }
    metadata = PracticeAnalyticsMetadataDocument.model_validate(
        {
            "schemaVersion": 1,
            "sourceStudentKey": "fixture_student",
            "sourceRevision": "fixture-rev",
            "generatedAt": "2026-09-01T09:00:00Z",
            "competencies": [
                {
                    "competencyId": "skill",
                    "title": "Линейные уравнения",
                    "groupTitle": "Алгебра",
                    "masteryLevel": 4,
                    "sourceLessonDate": "2026-08-20",
                    "sourceLessonHref": "20.08.26.html",
                    "provider": "demo.generator",
                    "coverageStatus": "covered-generator",
                }
            ],
        }
    )
    with database.sessions() as session:
        repository = PracticeRepository(session, DEFAULT_ORGANIZATION_ID, student_id)
        profile = repository.create_profile(state)
        profile.revision = 1
        repository.upsert_analytics_metadata(
            metadata.model_dump(mode="json"),
            schema_version=1,
            source_revision="fixture-rev",
        )
        for index, payload in enumerate(
            [
                practice_event("event-1", correct=True, hints=0, attempts=1),
                practice_event("event-2", correct=False, hints=2, attempts=3),
                practice_event("event-3", correct=False, hints=2, attempts=3),
                practice_event("event-4", correct=False, hints=1, attempts=2),
            ],
            start=1,
        ):
            repository.add_event(
                event_id=payload["eventId"],
                event_version=2,
                client_instance_id="device",
                competency_id="skill",
                outcome=payload["outcome"],
                occurred_at=datetime(2026, 8, 20 + index, 12, tzinfo=UTC),
                event_jsonb=payload,
            )
        session.commit()


def test_cross_repo_analytics_fixture_validates_against_backend_model():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document = PracticeAnalyticsMetadataDocument.model_validate(payload)
    assert document.schemaVersion == 1
    assert document.competencies[0].masteryLevel == 3
    assert document.competencies[0].competencyId == "algebra.linear"


def test_retention_index_v1_is_versioned_deterministic_and_handles_insufficient_data():
    one = [practice_event("event-1", correct=True)]
    insufficient = calculate_retention_index(one, {"intervalDays": 3}, today=TODAY)
    assert insufficient.index is None
    assert insufficient.category == "insufficient-data"

    stable_events = [practice_event(f"event-{index}", correct=True) for index in range(1, 5)]
    stable = calculate_retention_index(
        stable_events,
        {"intervalDays": 60, "lapses": 0, "repeatedLapse": False, "dueAt": "2026-09-02"},
        today=TODAY,
    )
    assert stable == calculate_retention_index(
        stable_events,
        {"intervalDays": 60, "lapses": 0, "repeatedLapse": False, "dueAt": "2026-09-02"},
        today=TODAY,
    )
    assert stable.category == "stable"
    assert stable.index is not None and stable.index >= 75
    with pytest.raises(ValueError):
        calculate_retention_index(stable_events, {}, today=TODAY, version=2)


def test_report_separates_mastery_from_retention_and_explains_actions(tmp_path):
    database, student, _, _ = make_environment(tmp_path)
    seed_analytics_data(database, student.id)
    report = PracticeAnalytics(database, DEFAULT_ORGANIZATION_ID).student_report(
        student.id, today=TODAY
    )
    skill = report["competencies"][0]
    assert skill["masteryLevel"] == 4
    assert skill["retention"]["category"] == "rebuild"
    assert skill["retention"]["index"] is not None
    assert skill["overdueDays"] == 9
    codes = {action["code"] for action in skill["recommendedActions"]}
    assert {"repeated-lapse", "long-overdue", "mastery-retention-drop", "hint-dependence"} <= codes
    assert all(action["reason"] for action in skill["recommendedActions"])
    assert report["summary"]["generatorCoverageGaps"] == 0
    assert report["summary"]["eventCount"] == 4


def test_pre_lesson_brief_has_deterministic_priority_snapshot(tmp_path):
    database, student, _, _ = make_environment(tmp_path)
    seed_analytics_data(database, student.id)
    brief = PracticeAnalytics(database, DEFAULT_ORGANIZATION_ID).build_pre_lesson_practice_brief(
        student.id, today=TODAY
    )
    assert {
        "studentId": brief["studentId"],
        "overdueCount": brief["overdueCount"],
        "repeatedLapseCount": brief["repeatedLapseCount"],
        "masteryRetentionMismatchCount": brief["masteryRetentionMismatchCount"],
        "recommendedWarmupCompetencies": brief["recommendedWarmupCompetencies"],
    } == {
        "studentId": student.id,
        "overdueCount": 1,
        "repeatedLapseCount": 1,
        "masteryRetentionMismatchCount": 1,
        "recommendedWarmupCompetencies": ["skill"],
    }
    assert brief["skillsRequiringAttention"][0]["competencyId"] == "skill"
    assert brief["lastPracticeSession"]["date"] == "2026-08-31"


def test_student_parent_and_teacher_analytics_access_is_tenant_scoped(tmp_path):
    database, student, student_principal, parent_principal = make_environment(tmp_path)
    ensure_practice_analytics_access(database, student_principal, student.id)
    ensure_practice_analytics_access(database, parent_principal, student.id)
    tutor = Principal(
        user_id="teacher",
        organization_id=DEFAULT_ORGANIZATION_ID,
        organization_name="Tutor Workspace",
        role=MembershipRole.tutor.value,
        email="teacher@example.test",
        full_name="Teacher",
    )
    ensure_practice_analytics_access(database, tutor, student.id)
    wrong = Principal(
        user_id=student_principal.user_id,
        organization_id="00000000-0000-0000-0000-000000000999",
        organization_name="Other",
        role=MembershipRole.student.value,
        email=student_principal.email,
        full_name=student_principal.full_name,
    )
    with pytest.raises((ForbiddenError, Exception)) as error:
        ensure_practice_analytics_access(database, wrong, student.id)
    assert error.value is not None
