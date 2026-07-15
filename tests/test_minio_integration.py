from __future__ import annotations

import hashlib
import io
import os
import time
from uuid import uuid4

import boto3
import pytest

from tutor_assistant_web.providers.artifacts import S3ArtifactStorage

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
