from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect, select

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.application import (
    BoardPersistenceService,
    BoardRevisionConflict,
)
from tutor_assistant_web.modules.boards.contracts import BoardCommandEnvelopeInput
from tutor_assistant_web.modules.boards.models import (
    BoardCommandBatch,
    BoardDocument,
    BoardGeometryImport,
    BoardSnapshot,
    BoardSnapshotStatus,
)
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    Organization,
    User,
)
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.providers.artifacts import LocalArtifactStorage
from tutor_assistant_web.shared.board_contracts.board_geometry_import_schema import (
    BoardGeometryImport11,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_schema import BoardSnapshot14
from tutor_assistant_web.shared.errors import (
    ConflictError,
    GoneError,
    NotFoundError,
    ValidationError,
)

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "schemas" / "board" / "v1" / "fixtures"


@pytest.fixture()
def board_context(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'boards.db'}")
    database.migrate()
    identity = IdentityService(database)
    identity.bootstrap(
        Settings(
            seed_demo_data=False,
            bootstrap_admin_password="admin-password",
        )
    )
    principal = identity.authenticate("admin@localhost", "admin-password")
    assert principal is not None
    with database.sessions() as session:
        student = Student(
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="Board Student",
        )
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=DEFAULT_ORGANIZATION_ID,
            student_id=student.id,
            title="Board lesson",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id="board-lesson",
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.commit()
    storage = LocalArtifactStorage(tmp_path / "artifacts")
    service = BoardPersistenceService(
        database,
        storage,
        DEFAULT_ORGANIZATION_ID,
        snapshot_interval_commands=1,
    )
    yield database, storage, service, principal.user_id, lesson
    database.dispose()


def load_command(**changes):
    payload = json.loads((FIXTURES / "board-command-envelope.json").read_text())
    payload.update(
        {
            "baseRevision": 0,
            "idempotencyKey": "client:test:batch-01",
            **changes,
        }
    )
    for index, item in enumerate(payload["commands"]):
        item["order"]["baseRevisionAtCreation"] = payload["baseRevision"]
        item["order"]["lamport"] = payload["baseRevision"] * len(payload["commands"]) + index + 1
    return BoardCommandEnvelopeInput.model_validate(payload).root


def load_snapshot(**changes) -> BoardSnapshot14:
    payload = json.loads((FIXTURES / "board-snapshot.json").read_text())
    payload.update(changes)
    return BoardSnapshot14.model_validate(payload)


def load_geometry_import(**changes) -> BoardGeometryImport11:
    payload = json.loads((FIXTURES / "board-geometry-import.json").read_text())
    payload.update({"baseRevision": 0, **changes})
    return BoardGeometryImport11.model_validate(payload)


def test_migration_exposes_board_tables_and_tenant_constraints(board_context):
    database, _, _, _, _ = board_context
    inspector = inspect(database.engine)

    assert {
        "board_documents",
        "board_command_batches",
        "board_snapshots",
        "board_geometry_imports",
    }.issubset(inspector.get_table_names())
    assert "fk_board_documents_org_student_lesson" in {
        item["name"] for item in inspector.get_foreign_keys("board_documents")
    }
    assert "uq_board_commands_org_document_idempotency" in {
        item["name"] for item in inspector.get_unique_constraints("board_command_batches")
    }


def test_create_board_is_tenant_scoped_and_idempotent(board_context):
    database, _, service, _, lesson = board_context

    created = service.create_for_lesson(lesson.id, "document:lesson-01")
    repeated = service.create_for_lesson(lesson.id, "document:lesson-01")

    assert repeated.id == created.id
    assert created.student_id == lesson.student_id
    with pytest.raises(ConflictError):
        service.create_for_lesson(lesson.id, "document:other")

    other = Organization(name="Other", slug="other-board")
    with database.sessions() as session:
        session.add(other)
        session.commit()
    with pytest.raises(NotFoundError):
        BoardPersistenceService(
            database,
            service.storage,
            other.id,
        ).create_for_lesson(lesson.id, "document:other-tenant")


def test_revision_and_idempotency_contract(board_context):
    database, _, service, actor_user_id, lesson = board_context
    service.create_for_lesson(lesson.id, "document:lesson-01")
    envelope = load_command()

    first = service.append_commands(envelope, actor_user_id)
    repeated = service.append_commands(envelope, actor_user_id)

    assert first.id == repeated.id
    assert first.revision == 1
    assert service.get("document:lesson-01").current_revision == 1
    assert service.snapshot_due("document:lesson-01")
    assert [item.revision for item in service.commands_after("document:lesson-01", 0)] == [1]
    with database.sessions() as session:
        assert len(list(session.scalars(select(BoardCommandBatch)))) == 1

    changed = load_command(expectedDocumentSha256="1" * 64)
    with pytest.raises(ConflictError, match="Idempotency key"):
        service.append_commands(changed, actor_user_id)
    with pytest.raises(BoardRevisionConflict) as conflict:
        service.append_commands(
            load_command(idempotencyKey="client:test:batch-02"),
            actor_user_id,
        )
    assert conflict.value.current_revision == 1
    assert conflict.value.expected_revision == 0


def test_actor_must_belong_to_board_organization(board_context):
    database, _, service, _, lesson = board_context
    service.create_for_lesson(lesson.id, "document:lesson-01")
    outsider = User(
        email="outsider@example.test",
        full_name="Outsider",
        password_hash="test-only-hash",
    )
    with database.sessions() as session:
        session.add(outsider)
        session.commit()

    with pytest.raises(NotFoundError, match="Участник"):
        service.append_commands(load_command(), outsider.id)


def test_snapshot_round_trip_and_recovery(board_context):
    _, storage, service, actor_user_id, lesson = board_context
    service.create_for_lesson(lesson.id, "document:lesson-01")
    initial = load_snapshot(revision=0)
    stored_initial = service.save_snapshot(initial)
    assert stored_initial.revision == 0
    assert storage.stat(stored_initial.storage_key).sha256 == stored_initial.sha256

    service.append_commands(load_command(), actor_user_id)
    current = load_snapshot(revision=1)
    stored_current = service.save_snapshot(current)
    repeated = service.save_snapshot(current)

    assert repeated.id == stored_current.id
    assert stored_current.storage_status == BoardSnapshotStatus.available.value
    loaded = service.load_latest_snapshot("document:lesson-01")
    assert loaded is not None
    assert loaded.model_dump(mode="json", by_alias=True) == current.model_dump(
        mode="json",
        by_alias=True,
    )
    recovery = service.recovery("document:lesson-01")
    assert recovery.snapshot is not None
    assert recovery.snapshot.revision == 1
    assert recovery.command_batches == []
    document = service.get("document:lesson-01")
    assert document.last_snapshot_revision == 1
    assert document.commands_since_snapshot == 0
    assert document.bytes_since_snapshot == 0


def test_snapshot_rejects_invalid_checksum_or_revision(board_context):
    _, _, service, actor_user_id, lesson = board_context
    service.create_for_lesson(lesson.id, "document:lesson-01")

    with pytest.raises(ValidationError, match="SHA-256"):
        service.save_snapshot(load_snapshot(revision=0, documentSha256="0" * 64))
    service.append_commands(load_command(), actor_user_id)
    with pytest.raises(BoardRevisionConflict):
        service.save_snapshot(load_snapshot(revision=2))


def test_geometry_import_keeps_prompt_out_of_provenance(board_context):
    database, _, service, _, lesson = board_context
    service.create_for_lesson(lesson.id, "document:lesson-01")
    contract = load_geometry_import()

    created = service.record_geometry_import(contract)
    repeated = service.record_geometry_import(contract)

    assert repeated.id == created.id
    assert "prompt" not in created.provenance
    assert created.prompt_sha256
    with database.sessions() as session:
        assert len(list(session.scalars(select(BoardGeometryImport)))) == 1

    changed_payload = json.loads((FIXTURES / "board-geometry-import.json").read_text())
    changed_payload["baseRevision"] = 0
    changed_payload["prompt"] = "Другое построение"
    with pytest.raises(ConflictError, match="Import ID"):
        service.record_geometry_import(BoardGeometryImport11.model_validate(changed_payload))


def test_soft_delete_and_purge_remove_snapshot_objects(board_context):
    database, storage, service, _, lesson = board_context
    service = BoardPersistenceService(
        database,
        storage,
        DEFAULT_ORGANIZATION_ID,
        delete_grace_days=0,
    )
    service.create_for_lesson(lesson.id, "document:lesson-01")
    stored = service.save_snapshot(load_snapshot(revision=0))

    removed = service.soft_delete("document:lesson-01")

    assert removed.deleted_at is not None
    with pytest.raises(GoneError):
        service.get("document:lesson-01")
    assert service.purge_due() == 1
    with pytest.raises(FileNotFoundError):
        storage.stat(stored.storage_key)
    with database.sessions() as session:
        assert session.scalar(select(BoardDocument.id)) is None
        assert session.scalar(select(BoardSnapshot.id)) is None


def test_snapshot_corruption_is_detected(board_context):
    database, storage, service, _, lesson = board_context
    service.create_for_lesson(lesson.id, "document:lesson-01")
    stored = service.save_snapshot(load_snapshot(revision=0))
    path = storage._path(stored.storage_key)
    path.write_bytes(b"{}")

    with pytest.raises(ConflictError, match="повреждён"):
        service.load_latest_snapshot("document:lesson-01")
    with database.sessions() as session:
        snapshot = session.get(BoardSnapshot, stored.id)
        assert snapshot.storage_status == BoardSnapshotStatus.quarantined.value


def test_snapshot_integrity_maintenance_quarantines_damage(board_context):
    database, storage, service, _, lesson = board_context
    service.create_for_lesson(lesson.id, "document:lesson-01")
    stored = service.save_snapshot(load_snapshot(revision=0))

    assert service.verify_integrity() == {"checked": 1, "quarantined": 0}
    storage._path(stored.storage_key).write_bytes(b"{}")
    assert service.verify_integrity() == {"checked": 1, "quarantined": 1}
    with database.sessions() as session:
        snapshot = session.get(BoardSnapshot, stored.id)
        assert snapshot.storage_status == BoardSnapshotStatus.quarantined.value


def test_failed_snapshot_upload_is_retryable(board_context, tmp_path):
    database, _, _, _, lesson = board_context

    class FlakyStorage(LocalArtifactStorage):
        def __init__(self, root):
            super().__init__(root)
            self.failures = 1

        def put(self, key, content, media_type):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("temporary storage failure")
            return super().put(key, content, media_type)

    storage = FlakyStorage(tmp_path / "flaky-artifacts")
    service = BoardPersistenceService(database, storage, DEFAULT_ORGANIZATION_ID)
    service.create_for_lesson(lesson.id, "document:lesson-01")
    snapshot = load_snapshot(revision=0)

    with pytest.raises(RuntimeError, match="temporary storage"):
        service.save_snapshot(snapshot)
    with database.sessions() as session:
        pending = session.scalar(select(BoardSnapshot))
        assert pending.storage_status == BoardSnapshotStatus.uploading.value
        assert "temporary storage failure" in pending.upload_error

    stored = service.save_snapshot(snapshot)

    assert stored.storage_status == BoardSnapshotStatus.available.value
    assert stored.upload_error == ""
    with database.sessions() as session:
        assert len(list(session.scalars(select(BoardSnapshot)))) == 1


def test_cross_tenant_board_rows_are_not_visible(board_context):
    database, storage, service, _, lesson = board_context
    service.create_for_lesson(lesson.id, "document:lesson-01")
    other = Organization(name="Other Workspace", slug="other-workspace")
    user = User(
        email="other@example.test",
        full_name="Other Tutor",
        password_hash="test-only-hash",
    )
    with database.sessions() as session:
        session.add_all([other, user])
        session.flush()
        session.add(
            Membership(
                organization_id=other.id,
                user_id=user.id,
                role=MembershipRole.tutor.value,
            )
        )
        session.commit()

    other_service = BoardPersistenceService(database, storage, other.id)
    with pytest.raises(NotFoundError):
        other_service.get("document:lesson-01")
    with pytest.raises(NotFoundError):
        other_service.append_commands(load_command(), user.id)
