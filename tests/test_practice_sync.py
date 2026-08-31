from __future__ import annotations

import json
from pathlib import Path

import pytest

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.application import IdentityService, Principal
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    Organization,
    StudentAccess,
    User,
)
from tutor_assistant_web.modules.practice.application import (
    PracticeRevisionConflict,
    PracticeSyncService,
)
from tutor_assistant_web.modules.practice.models import PracticeEvent
from tutor_assistant_web.modules.practice.schemas import (
    BootstrapResponse,
    EventBatchRequest,
    PracticeStateDocument,
    StateUpdateRequest,
)
from tutor_assistant_web.modules.students.application import StudentData, StudentService
from tutor_assistant_web.shared.errors import ConflictError, ForbiddenError

FIXTURE = Path("contracts/practice-sync-v1/fixtures/sync-cycle.json")


def make_student_service(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'practice.db'}")
    database.migrate()
    identity = IdentityService(database)
    identity.bootstrap(
        Settings(seed_demo_data=False, bootstrap_admin_password="admin-password")
    )
    student = StudentService(database, DEFAULT_ORGANIZATION_ID).create(
        StudentData(full_name="Practice Student")
    )
    user = User(
        email="practice-student@example.test",
        full_name="Practice Student",
        password_hash=identity.passwords.hash("student-password"),
    )
    with database.sessions() as session:
        session.add(user)
        session.flush()
        session.add(
            Membership(
                organization_id=DEFAULT_ORGANIZATION_ID,
                user_id=user.id,
                role=MembershipRole.student.value,
            )
        )
        session.add(
            StudentAccess(
                organization_id=DEFAULT_ORGANIZATION_ID,
                student_id=student.id,
                user_id=user.id,
                role=MembershipRole.student.value,
            )
        )
        session.commit()
    principal = Principal(
        user_id=user.id,
        organization_id=DEFAULT_ORGANIZATION_ID,
        organization_name="Tutor Workspace",
        role=MembershipRole.student.value,
        email=user.email,
        full_name=user.full_name,
    )
    return database, student, PracticeSyncService(database, principal)


def fixture_payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_cross_repo_fixture_validates_against_backend_models():
    payload = fixture_payload()
    bootstrap = BootstrapResponse.model_validate(payload["bootstrap"])
    batch = EventBatchRequest.model_validate(payload["eventBatch"])
    update = StateUpdateRequest.model_validate(payload["stateUpdate"])
    assert bootstrap.state is not None and bootstrap.state.schemaVersion == 2
    assert batch.events[0].eventId == "fixture-event-001"
    assert update.baseRevision == 7


def test_first_binding_and_optimistic_conflict_return_canonical_state(tmp_path):
    _, _, service = make_student_service(tmp_path)
    empty = service.bootstrap()
    assert empty.profileExists is False
    state = PracticeStateDocument.model_validate(fixture_payload()["bootstrap"]["state"])
    state = state.model_copy(update={"revision": 0})
    created = service.update_state(
        StateUpdateRequest(schemaVersion=1, baseRevision=0, state=state)
    )
    assert created.revision == 1
    stale = state.model_copy(update={"revision": 0})
    with pytest.raises(PracticeRevisionConflict) as error:
        service.update_state(
            StateUpdateRequest(schemaVersion=1, baseRevision=0, state=stale)
        )
    assert error.value.response.revision == 1
    assert error.value.response.state.revision == 1


def test_event_batch_is_idempotent_and_conflicting_event_id_is_rejected(tmp_path):
    database, _, service = make_student_service(tmp_path)
    state = PracticeStateDocument.model_validate(fixture_payload()["bootstrap"]["state"])
    state = state.model_copy(update={"revision": 0})
    service.update_state(
        StateUpdateRequest(schemaVersion=1, baseRevision=0, state=state)
    )
    batch = EventBatchRequest.model_validate(fixture_payload()["eventBatch"])
    first = service.ingest_events(batch)
    assert first.acceptedEventIds == ["fixture-event-001"]
    assert first.duplicateEventIds == []
    second = service.ingest_events(batch)
    assert second.acceptedEventIds == []
    assert second.duplicateEventIds == ["fixture-event-001"]
    assert second.revision == first.revision
    with database.sessions() as session:
        assert session.query(PracticeEvent).count() == 1

    changed = batch.model_copy(deep=True)
    changed.events[0].durationMs += 1
    with pytest.raises(ConflictError):
        service.ingest_events(changed)


def test_tenant_and_role_scope_are_enforced_before_profile_lookup(tmp_path):
    database, _, service = make_student_service(tmp_path)
    tutor = Principal(
        user_id=service.principal.user_id,
        organization_id=DEFAULT_ORGANIZATION_ID,
        organization_name="Tutor Workspace",
        role=MembershipRole.tutor.value,
        email=service.principal.email,
        full_name=service.principal.full_name,
    )
    with pytest.raises(ForbiddenError):
        PracticeSyncService(database, tutor).bootstrap()

    second = Organization(name="Other", slug="practice-other")
    with database.sessions() as session:
        session.add(second)
        session.commit()
        second_id = second.id
    wrong_workspace = Principal(
        user_id=service.principal.user_id,
        organization_id=second_id,
        organization_name="Other",
        role=MembershipRole.student.value,
        email=service.principal.email,
        full_name=service.principal.full_name,
    )
    with pytest.raises(ForbiddenError):
        PracticeSyncService(database, wrong_workspace).bootstrap()
