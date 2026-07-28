from __future__ import annotations

import hashlib
import io
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import boto3
import pytest

from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.application import BoardPersistenceService
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import DEFAULT_ORGANIZATION_ID
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.providers.artifacts import S3ArtifactStorage
from tutor_assistant_web.shared.board_contracts.board_snapshot_schema import BoardSnapshot10

BOARD_SNAPSHOT_FIXTURE = (
    Path(__file__).parents[1] / "schemas" / "board" / "v1" / "fixtures" / "board-snapshot.json"
)

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_S3_ENDPOINT_URL"), reason="TEST_S3_ENDPOINT_URL is not configured"
)


def storage() -> S3ArtifactStorage:
    endpoint = os.environ["TEST_S3_ENDPOINT_URL"]
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("TEST_S3_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.getenv("TEST_S3_SECRET_KEY", "minioadmin"),
        region_name="us-east-1",
    )
    bucket = f"artifacts-{uuid4().hex}"
    for attempt in range(20):
        try:
            client.create_bucket(Bucket=bucket)
            break
        except Exception:
            if attempt == 19:
                raise
            time.sleep(1)
    return S3ArtifactStorage(bucket, client=client)


def test_minio_private_streaming_lifecycle_and_backup_restore():
    source = storage()
    source.ensure_private_bucket()
    source.configure_lifecycle(retention_days=365, abort_multipart_days=1)
    content = b"%PDF-1.4\nminio integration"
    checksum = hashlib.sha256(content).hexdigest()
    stored = source.put_stream(
        "tenant-a/lesson/material.pdf",
        io.BytesIO(content),
        "application/pdf",
        expected_sha256=checksum,
    )
    assert source.read(stored.key) == content
    assert source.stat(stored.key).sha256 == checksum

    # Simulates restoring an object from an independent backup bucket.
    restored = storage()
    restored.ensure_private_bucket()
    restored.put_stream(
        stored.key,
        io.BytesIO(source.read(stored.key)),
        stored.media_type,
        expected_sha256=stored.sha256,
    )
    assert restored.stat(stored.key).sha256 == checksum


def test_board_snapshot_round_trip_through_minio(tmp_path):
    target = storage()
    target.ensure_private_bucket()
    database = Database(f"sqlite:///{tmp_path / 'minio-board.db'}")
    database.migrate()
    IdentityService(database).bootstrap(
        Settings(seed_demo_data=False, bootstrap_admin_password="admin-password")
    )
    with database.sessions() as session:
        student = Student(
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="MinIO Board Student",
        )
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=DEFAULT_ORGANIZATION_ID,
            student_id=student.id,
            title="MinIO board",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id=f"minio-board-{uuid4().hex}",
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.commit()
    service = BoardPersistenceService(
        database,
        target,
        DEFAULT_ORGANIZATION_ID,
    )
    service.create_for_lesson(lesson.id, "document:lesson-01")
    snapshot = BoardSnapshot10.model_validate(
        json.loads(BOARD_SNAPSHOT_FIXTURE.read_text()) | {"revision": 0}
    )

    stored = service.save_snapshot(snapshot)
    restored = service.load_latest_snapshot("document:lesson-01")

    assert target.stat(stored.storage_key).sha256 == stored.sha256
    assert restored is not None
    assert restored.document_sha256 == snapshot.document_sha256
    database.dispose()
