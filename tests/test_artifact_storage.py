from __future__ import annotations

import hashlib
import io

import pytest

from tutor_assistant_web.providers.artifacts import (
    ArtifactChecksumMismatch,
    ArtifactMimeMismatch,
    ArtifactQuarantined,
    ArtifactTooLarge,
    LocalArtifactStorage,
    S3ArtifactStorage,
)


class FakeBody(io.BytesIO):
    def close(self):
        super().close()


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.public_block = None
        self.lifecycle = None

    def upload_fileobj(self, stream, bucket, key, ExtraArgs):
        self.objects[(bucket, key)] = (stream.read(), ExtraArgs)

    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[(Bucket, Key)][0])}

    def head_object(self, Bucket, Key):
        content, args = self.objects[(Bucket, Key)]
        return {
            "ContentLength": len(content),
            "ContentType": args["ContentType"],
            "Metadata": args["Metadata"],
        }

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def head_bucket(self, Bucket):
        return {}

    def put_public_access_block(self, **kwargs):
        self.public_block = kwargs

    def put_bucket_acl(self, **kwargs):
        self.private_acl = kwargs

    def put_bucket_lifecycle_configuration(self, **kwargs):
        self.lifecycle = kwargs


class BrokenStream(io.BytesIO):
    def __init__(self):
        super().__init__(b"%PDF-first")
        self.calls = 0

    def read(self, size=-1):
        self.calls += 1
        if self.calls > 1:
            raise OSError("connection lost")
        return super().read(5)


def test_s3_streaming_round_trip_checksum_and_private_policy():
    client = FakeS3()
    storage = S3ArtifactStorage("private", client=client)
    content = b"%PDF-1.4\nsecure"
    stored = storage.put_stream(
        "tenant/lesson/material.pdf",
        io.BytesIO(content),
        "application/pdf",
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    assert stored.sha256 == hashlib.sha256(content).hexdigest()
    assert b"".join(storage.iter_bytes(stored.key, 3)) == content
    storage.ensure_private_bucket()
    block = client.public_block["PublicAccessBlockConfiguration"]
    assert all(block.values())


def test_interrupted_upload_does_not_create_object():
    client = FakeS3()
    storage = S3ArtifactStorage("private", client=client)
    with pytest.raises(OSError, match="connection lost"):
        storage.put_stream("tenant/file.pdf", BrokenStream(), "application/pdf")
    assert client.objects == {}


def test_checksum_size_and_mime_are_enforced(tmp_path):
    storage = LocalArtifactStorage(tmp_path, max_bytes=12)
    with pytest.raises(ArtifactChecksumMismatch):
        storage.put_stream(
            "tenant/file.pdf",
            io.BytesIO(b"%PDF-valid"),
            "application/pdf",
            expected_sha256="0" * 64,
        )
    with pytest.raises(ArtifactTooLarge):
        storage.put("tenant/file.pdf", b"%PDF-" + b"x" * 20, "application/pdf")
    with pytest.raises(ArtifactMimeMismatch):
        storage.put("tenant/file.pdf", b"<html>", "application/pdf")


def test_antivirus_quarantine_blocks_upload(tmp_path):
    class Scanner:
        def scan(self, stream):
            raise ArtifactQuarantined("Eicar-Test-Signature FOUND")

    storage = LocalArtifactStorage(tmp_path, scanner=Scanner())
    with pytest.raises(ArtifactQuarantined):
        storage.put("tenant/file.pdf", b"%PDF-malicious", "application/pdf")
    assert not (tmp_path / "tenant/file.pdf").exists()


def test_tenant_prefix_is_mandatory(tmp_path):
    storage = LocalArtifactStorage(tmp_path)
    with pytest.raises(ValueError, match="tenant prefix"):
        storage.put("file.pdf", b"%PDF-valid", "application/pdf")
