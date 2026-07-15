from __future__ import annotations

import hashlib
import io
import socket
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from tutor_assistant_web.shared.contracts import StoredArtifact


class ArtifactStorageError(RuntimeError):
    pass


class ArtifactTooLarge(ArtifactStorageError):
    pass


class ArtifactChecksumMismatch(ArtifactStorageError):
    pass


class ArtifactMimeMismatch(ArtifactStorageError):
    pass


class ArtifactQuarantined(ArtifactStorageError):
    pass


def validate_key(key: str) -> PurePosixPath:
    pure = PurePosixPath(key)
    if pure.is_absolute() or ".." in pure.parts or len(pure.parts) < 2:
        raise ValueError("artifact key must contain a tenant prefix and relative path")
    if any(not part or part in {".", ".."} for part in pure.parts):
        raise ValueError("invalid artifact storage key")
    return pure


def detected_mime(head: bytes, declared: str) -> str:
    base = declared.partition(";")[0].strip().lower()
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith((b"\x89PNG\r\n\x1a\n",)):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"RIFF",)) and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"ID3") or head.startswith((b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
        return "audio/mpeg"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return base if base in {"audio/mp4", "video/mp4"} else "video/mp4"
    stripped = head.lstrip().lower()
    if stripped.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if b"\\documentclass" in head[:4096]:
        return "application/x-tex"
    # Plain textual formats cannot be identified reliably from magic bytes.
    if base.startswith("text/") or base in {"application/json", "application/x-tex"}:
        try:
            head.decode("utf-8")
            return base
        except UnicodeDecodeError:
            return "application/octet-stream"
    return "application/octet-stream"


def validate_mime(head: bytes, declared: str, allowed: set[str]) -> str:
    base = declared.partition(";")[0].strip().lower()
    if base not in allowed:
        raise ArtifactMimeMismatch(f"MIME type is not allowed: {base}")
    actual = detected_mime(head, declared)
    if actual != base:
        raise ArtifactMimeMismatch(f"declared MIME {base} does not match content {actual}")
    return declared


class ClamAVScanner:
    """Minimal clamd INSTREAM client; no temporary shared volume is required."""

    def __init__(self, host: str, port: int = 3310, timeout: float = 60.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, stream: BinaryIO) -> None:
        stream.seek(0)
        with socket.create_connection((self.host, self.port), self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(b"zINSTREAM\0")
            while chunk := stream.read(1024 * 1024):
                sock.sendall(struct.pack(">I", len(chunk)))
                sock.sendall(chunk)
            sock.sendall(struct.pack(">I", 0))
            response = bytearray()
            while not response.endswith(b"\0"):
                part = sock.recv(4096)
                if not part:
                    break
                response.extend(part)
        stream.seek(0)
        result = response.rstrip(b"\0").decode("utf-8", "replace")
        if result.endswith(" FOUND"):
            raise ArtifactQuarantined(result)
        if not result.endswith(" OK"):
            raise ArtifactStorageError(f"ClamAV scan failed: {result or 'empty response'}")


@dataclass(frozen=True)
class ObjectInfo:
    sha256: str
    size: int
    media_type: str


class _ValidatedStorage:
    def __init__(
        self,
        *,
        max_bytes: int,
        allowed_mime_types: set[str],
        scanner: ClamAVScanner | None,
    ) -> None:
        self.max_bytes = max_bytes
        self.allowed_mime_types = allowed_mime_types
        self.scanner = scanner

    def _prepare(
        self,
        stream: BinaryIO,
        media_type: str,
        expected_sha256: str | None,
        max_bytes: int | None,
    ) -> tuple[SpooledTemporaryFile, ObjectInfo]:
        limit = min(max_bytes or self.max_bytes, self.max_bytes)
        staged = SpooledTemporaryFile(  # noqa: SIM115 - ownership passes to the caller
            max_size=min(limit, 16 * 1024 * 1024), mode="w+b"
        )
        digest, total, head = hashlib.sha256(), 0, bytearray()
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                staged.close()
                raise ArtifactTooLarge(f"artifact exceeds {limit} bytes")
            if len(head) < 8192:
                head.extend(chunk[: 8192 - len(head)])
            digest.update(chunk)
            staged.write(chunk)
        checksum = digest.hexdigest()
        if expected_sha256 and checksum.lower() != expected_sha256.lower():
            staged.close()
            raise ArtifactChecksumMismatch("SHA-256 checksum mismatch")
        validate_mime(bytes(head), media_type, self.allowed_mime_types)
        staged.seek(0)
        if self.scanner:
            self.scanner.scan(staged)
        staged.seek(0)
        return staged, ObjectInfo(checksum, total, media_type)


class LocalArtifactStorage(_ValidatedStorage):
    name = "local"

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = 500 * 1024 * 1024,
        allowed_mime_types: set[str] | None = None,
        scanner: ClamAVScanner | None = None,
    ) -> None:
        super().__init__(
            max_bytes=max_bytes,
            allowed_mime_types=allowed_mime_types or default_allowed_mimes(),
            scanner=scanner,
        )
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        pure = validate_key(key)
        candidate = self.root.joinpath(*pure.parts).resolve()
        if self.root not in candidate.parents:
            raise ValueError("artifact path escapes storage root")
        return candidate

    def put(self, key: str, content: bytes, media_type: str) -> StoredArtifact:
        return self.put_stream(key, io.BytesIO(content), media_type)

    def put_stream(self, key, stream, media_type, *, expected_sha256=None, max_bytes=None):
        path = self._path(key)
        staged, info = self._prepare(stream, media_type, expected_sha256, max_bytes)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".uploading")
            with temporary.open("wb") as output:
                while chunk := staged.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(path)
        finally:
            staged.close()
        return StoredArtifact(key, info.sha256, info.size, info.media_type)

    def iter_bytes(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with self._path(key).open("rb") as source:
            while chunk := source.read(chunk_size):
                yield chunk

    def read(self, key: str) -> bytes:
        return b"".join(self.iter_bytes(key))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def stat(self, key: str) -> StoredArtifact:
        path = self._path(key)
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        return StoredArtifact(key, digest.hexdigest(), size, "application/octet-stream")


class S3ArtifactStorage(_ValidatedStorage):
    name = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        max_bytes: int = 500 * 1024 * 1024,
        allowed_mime_types: set[str] | None = None,
        scanner: ClamAVScanner | None = None,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            max_bytes=max_bytes,
            allowed_mime_types=allowed_mime_types or default_allowed_mimes(),
            scanner=scanner,
        )
        self.bucket = bucket
        self.client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            config=Config(
                signature_version="s3v4", retries={"max_attempts": 4, "mode": "adaptive"}
            ),
        )

    def ensure_private_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket"}:
                raise
            args = {"Bucket": self.bucket}
            region = getattr(getattr(self.client, "meta", None), "region_name", None)
            if region and region != "us-east-1":
                args["CreateBucketConfiguration"] = {"LocationConstraint": region}
            self.client.create_bucket(**args)
        try:
            self.client.put_public_access_block(
                Bucket=self.bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
        except ClientError as exc:
            # MinIO releases without the AWS public-access-block extension still create
            # buckets as private. Explicitly reset the bucket ACL in that compatibility case.
            code = exc.response.get("Error", {}).get("Code")
            if code not in {"MalformedXML", "NotImplemented", "XNotImplemented"}:
                raise
            try:
                self.client.put_bucket_acl(Bucket=self.bucket, ACL="private")
            except ClientError as acl_exc:
                acl_code = acl_exc.response.get("Error", {}).get("Code")
                if acl_code not in {"MalformedXML", "NotImplemented", "XNotImplemented"}:
                    raise

    def put(self, key: str, content: bytes, media_type: str) -> StoredArtifact:
        return self.put_stream(key, io.BytesIO(content), media_type)

    def put_stream(self, key, stream, media_type, *, expected_sha256=None, max_bytes=None):
        validate_key(key)
        staged, info = self._prepare(stream, media_type, expected_sha256, max_bytes)
        try:
            self.client.upload_fileobj(
                staged,
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": media_type,
                    "Metadata": {"sha256": info.sha256},
                    "ServerSideEncryption": "AES256",
                },
            )
        finally:
            staged.close()
        return StoredArtifact(key, info.sha256, info.size, info.media_type)

    def iter_bytes(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        validate_key(key)
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            while chunk := body.read(chunk_size):
                yield chunk
        finally:
            body.close()

    def read(self, key: str) -> bytes:
        return b"".join(self.iter_bytes(key))

    def delete(self, key: str) -> None:
        validate_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def stat(self, key: str) -> StoredArtifact:
        validate_key(key)
        response = self.client.head_object(Bucket=self.bucket, Key=key)
        return StoredArtifact(
            key,
            response.get("Metadata", {}).get("sha256", ""),
            int(response["ContentLength"]),
            response.get("ContentType", "application/octet-stream"),
        )

    def configure_lifecycle(self, retention_days: int, abort_multipart_days: int) -> None:
        # Retention expiry is database-driven to preserve the soft-delete recovery window.
        # The bucket lifecycle owns cleanup that cannot be represented by an object row.
        _ = retention_days
        self.client.put_bucket_lifecycle_configuration(
            Bucket=self.bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "abort-incomplete-multipart",
                        "Status": "Enabled",
                        "Prefix": "",
                        "AbortIncompleteMultipartUpload": {
                            "DaysAfterInitiation": abort_multipart_days
                        },
                    }
                ]
            },
        )


def default_allowed_mimes() -> set[str]:
    return {
        "application/pdf",
        "application/json",
        "application/x-tex",
        "text/html",
        "text/plain",
        "image/png",
        "image/jpeg",
        "audio/wav",
        "audio/mpeg",
        "audio/mp4",
        "video/mp4",
        "application/octet-stream",
    }
