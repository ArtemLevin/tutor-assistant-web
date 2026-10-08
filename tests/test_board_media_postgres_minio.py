"""F3.2.2: real PostgreSQL row locks and MinIO media durability/failure gates."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from tutor_assistant_web.backup_operations import verify_restored_media_assets
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.application import BoardPersistenceService
from tutor_assistant_web.modules.boards.media import BoardMediaQuotaExceeded, BoardMediaService
from tutor_assistant_web.modules.boards.models import BoardMediaAsset
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import DEFAULT_ORGANIZATION_ID
from tutor_assistant_web.providers.artifacts import S3ArtifactStorage
from tutor_assistant_web.shared.errors import ValidationError

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL") or not os.getenv("TEST_S3_ENDPOINT_URL"),
    reason="Requires real TEST_DATABASE_URL (PostgreSQL) and TEST_S3_ENDPOINT_URL (MinIO)",
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII="
)


@pytest.fixture()
def stack():
    """Unique DB schema/bucket avoid interference with other integration cases."""
    schema = f"test_media_{uuid4().hex}"
    base_url = make_url(os.environ["TEST_DATABASE_URL"])
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    database = Database(url.render_as_string(hide_password=False))
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["TEST_S3_ENDPOINT_URL"],
        aws_access_key_id=os.getenv("TEST_S3_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.getenv("TEST_S3_SECRET_KEY", "minioadmin"),
        region_name="us-east-1",
    )
    bucket = f"board-media-{uuid4().hex}"
    try:
        database.migrate()
        identity = IdentityService(database)
        identity.bootstrap(
            Settings(seed_demo_data=False, bootstrap_admin_password="admin-password")
        )
        principal = identity.authenticate("admin@localhost", "admin-password")
        assert principal is not None
        client.create_bucket(Bucket=bucket)
        storage = S3ArtifactStorage(bucket, client=client)
        storage.ensure_private_bucket()
        board = BoardPersistenceService(
            database, storage, DEFAULT_ORGANIZATION_ID
        ).create_standalone(principal.user_id, "Concurrent Media")
        yield database, storage, principal.user_id, board.id
    finally:
        database.dispose()
        # No test data leaks into persistent MinIO buckets.
        try:
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for item in page.get("Contents", []):
                    client.delete_object(Bucket=bucket, Key=item["Key"])
            client.delete_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "NoSuchBucket":
                raise
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def service(database, storage, *, limit: int = 20) -> BoardMediaService:
    return BoardMediaService(
        database,
        storage,
        DEFAULT_ORGANIZATION_ID,
        uploads_enabled=True,
        max_asset_bytes=32 * 1024 * 1024,
        max_assets_per_board=limit,
        max_bytes_per_board=1024 * 1024 * 1024,
        max_dimension=16_384,
        max_pixels=64_000_000,
        gif_max_frames=500,
        gif_max_decoded_pixels=256_000_000,
    )


def upload(media: BoardMediaService, board_id: str, actor_id: str, key: str, **kwargs):
    return media.upload(
        board_id,
        io.BytesIO(PNG),
        declared_mime_type="image/png",
        file_name="lesson.png",
        expected_sha256=hashlib.sha256(PNG).hexdigest(),
        idempotency_key=key,
        created_by_user_id=actor_id,
        created_by_actor_id=actor_id,
        **kwargs,
    )


def test_postgres_minio_serializes_quota_and_preserves_idempotency(stack):
    database, storage, actor, board_id = stack
    media = service(database, storage, limit=1)
    start = threading.Barrier(2)

    def concurrent_upload(index: int):
        start.wait()
        try:
            return upload(media, board_id, actor, f"media:concurrent:{index}")
        except BoardMediaQuotaExceeded:
            return "quota_exceeded"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(concurrent_upload, 1)
        second = pool.submit(concurrent_upload, 2)
        results = [first.result(), second.result()]

    successes = [item for item in results if isinstance(item, BoardMediaAsset)]
    assert len(successes) == 1
    assert results.count("quota_exceeded") == 1
    asset = successes[0]
    assert asset.storage_status == "available"
    assert storage.read(asset.storage_key) == PNG
    assert storage.stat(asset.storage_key).sha256 == asset.content_sha256

    repeated = upload(media, board_id, actor, asset.upload_idempotency_key)
    assert repeated.asset_id == asset.asset_id
    assert repeated.storage_key == asset.storage_key
    with database.sessions() as session:
        assert session.scalar(select(func.count(BoardMediaAsset.id))) == 1

    inventory = media.unreferenced_report(board_id)
    assert (inventory.available_count, inventory.available_bytes) == (1, len(PNG))
    assert inventory.uploading_count == 0


def test_postgres_minio_revocation_rolls_back_bytes_and_allows_key_retry(stack):
    database, storage, actor, board_id = stack
    media = service(database, storage)

    def reject_reauthorization() -> None:
        raise ValidationError("rights revoked")

    with pytest.raises(ValidationError, match="rights revoked"):
        upload(
            media,
            board_id,
            actor,
            "media:revoked:before-finalize",
            reauthorize=reject_reauthorization,
        )
    with database.sessions() as session:
        row = session.scalar(
            select(BoardMediaAsset).where(
                BoardMediaAsset.upload_idempotency_key == "media:revoked:before-finalize"
            )
        )
        assert row is not None
        assert row.storage_status == "deleted"
        key, asset_id = row.storage_key, row.asset_id
    with pytest.raises(ClientError) as missing:
        storage.stat(key)
    assert missing.value.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}

    recovered = upload(media, board_id, actor, "media:revoked:before-finalize")
    assert recovered.asset_id == asset_id
    assert storage.read(recovered.storage_key) == PNG
    assert media.unreferenced_report(board_id).available_count == 1


def test_postgres_minio_storage_failure_after_write_is_compensated(stack):
    database, storage, actor, board_id = stack

    class LostAcknowledgementStorage(S3ArtifactStorage):
        def put_stream(self, key, stream, media_type, *, expected_sha256=None, max_bytes=None):
            super().put_stream(
                key,
                stream,
                media_type,
                expected_sha256=expected_sha256,
                max_bytes=max_bytes,
            )
            raise OSError("acknowledgement lost")

    failing_storage = LostAcknowledgementStorage(storage.bucket, client=storage.client)
    with pytest.raises(OSError, match="acknowledgement lost"):
        upload(service(database, failing_storage), board_id, actor, "media:s3:ack-lost")

    with database.sessions() as session:
        row = session.scalar(
            select(BoardMediaAsset).where(
                BoardMediaAsset.upload_idempotency_key == "media:s3:ack-lost"
            )
        )
        assert row is not None
        assert row.storage_status == "deleted"
        key = row.storage_key
    with pytest.raises(ClientError):
        storage.stat(key)
    recovered = upload(service(database, storage), board_id, actor, "media:s3:ack-lost")
    assert recovered.storage_key == key
    assert storage.read(key) == PNG


def test_postgres_minio_restore_gate_detects_corruption_despite_matching_metadata(stack):
    database, storage, actor, board_id = stack
    media = service(database, storage)
    asset = upload(media, board_id, actor, "media:restore:integrity")
    url = database.engine.url.render_as_string(hide_password=False)
    assert verify_restored_media_assets(url, storage.client, storage.bucket) == 1

    # A copied object may preserve the original sha256 metadata despite damaged
    # bytes. The restore gate must hash the actual stream independently.
    storage.client.put_object(
        Bucket=storage.bucket,
        Key=asset.storage_key,
        Body=b"damaged-bytes",
        Metadata={"sha256": asset.content_sha256},
        ContentType="image/png",
    )
    with pytest.raises(RuntimeError, match="checksum or size"):
        verify_restored_media_assets(url, storage.client, storage.bucket)
