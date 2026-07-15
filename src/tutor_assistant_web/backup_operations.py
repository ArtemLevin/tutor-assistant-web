from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy.engine import make_url

from tutor_assistant_web.config import Settings, get_settings

_BACKUP_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z")


def _postgres_environment(database_url: str) -> dict[str, str]:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("backup and restore require PostgreSQL")
    environment = os.environ.copy()
    environment.update(
        {
            "PGHOST": url.host or "localhost",
            "PGPORT": str(url.port or 5432),
            "PGUSER": url.username or "postgres",
            "PGDATABASE": url.database or "postgres",
        }
    )
    if url.password:
        environment["PGPASSWORD"] = url.password
    if url.query.get("sslmode"):
        environment["PGSSLMODE"] = str(url.query["sslmode"])
    return environment


def _s3(settings: Settings, *, backup: bool = False):
    endpoint = (
        settings.backup_s3_endpoint_url or settings.artifact_s3_endpoint_url
        if backup
        else settings.artifact_s3_endpoint_url
    )
    region = (
        settings.backup_s3_region or settings.artifact_s3_region
        if backup
        else settings.artifact_s3_region
    )
    access_key = (
        settings.backup_s3_access_key or settings.artifact_s3_access_key
        if backup
        else settings.artifact_s3_access_key
    )
    secret_key = (
        settings.backup_s3_secret_key or settings.artifact_s3_secret_key
        if backup
        else settings.artifact_s3_secret_key
    )
    kwargs = {
        "service_name": "s3",
        "region_name": region,
        "config": Config(signature_version="s3v4", retries={"max_attempts": 5}),
    }
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if access_key:
        kwargs["aws_access_key_id"] = access_key
    if secret_key:
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client(**kwargs)


def _ensure_private_bucket(client, bucket: str, *, versioning: bool = True) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        region = client.meta.region_name or "us-east-1"
        request = {"Bucket": bucket}
        if region != "us-east-1":
            request["CreateBucketConfiguration"] = {"LocationConstraint": region}
        client.create_bucket(**request)
    try:
        client.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
    except Exception:
        client.put_bucket_acl(Bucket=bucket, ACL="private")
    if versioning:
        with suppress(Exception):
            client.put_bucket_versioning(
                Bucket=bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )


def _configure_retention(client, settings: Settings) -> None:
    prefix = settings.backup_s3_prefix.strip("/") + "/"
    client.put_bucket_lifecycle_configuration(
        Bucket=settings.backup_s3_bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "tutor-backup-retention",
                    "Status": "Enabled",
                    "Filter": {"Prefix": prefix},
                    "Expiration": {"Days": settings.backup_retention_days},
                    "NoncurrentVersionExpiration": {
                        "NoncurrentDays": settings.backup_retention_days
                    },
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                }
            ]
        },
    )


def _objects(client, bucket: str, prefix: str = ""):
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            yield str(item["Key"])


def _push_metrics(settings: Settings, job: str, metrics: dict[str, float]) -> None:
    if not settings.pushgateway_url:
        return
    body = "".join(
        f"# TYPE {metric} gauge\n{metric} {value}\n" for metric, value in metrics.items()
    )
    httpx.put(
        f"{settings.pushgateway_url.rstrip('/')}/metrics/job/{job}",
        content=body,
        headers={"Content-Type": "text/plain; version=0.0.4"},
        timeout=5,
    ).raise_for_status()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _delete_prefix(client, bucket: str, prefix: str) -> int:
    keys = list(_objects(client, bucket, prefix))
    for start in range(0, len(keys), 1000):
        client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in keys[start : start + 1000]]},
        )
    return len(keys)


def _stream_copy(
    source_client,
    source_bucket: str,
    source_key: str,
    target_client,
    target_bucket: str,
    target_key: str,
) -> None:
    response = source_client.get_object(Bucket=source_bucket, Key=source_key)
    body = response["Body"]
    extra = {"Metadata": response.get("Metadata", {})}
    if response.get("ContentType"):
        extra["ContentType"] = response["ContentType"]
    try:
        target_client.upload_fileobj(body, target_bucket, target_key, ExtraArgs=extra)
    finally:
        body.close()


def prune(settings: Settings, *, now: datetime | None = None) -> dict[str, int]:
    """Delete expired backup sets; bucket lifecycle is a second safety net."""
    client = _s3(settings, backup=True)
    prefix = settings.backup_s3_prefix.strip("/")
    threshold = (now or datetime.now(UTC)) - timedelta(days=settings.backup_retention_days)
    removed_sets = 0
    removed_objects = 0
    for key in list(_objects(client, settings.backup_s3_bucket, f"{prefix}/manifests/")):
        manifest = json.loads(
            client.get_object(Bucket=settings.backup_s3_bucket, Key=key)["Body"].read()
        )
        created_at = datetime.fromisoformat(str(manifest["created_at"]))
        if created_at >= threshold:
            continue
        removed_objects += _delete_prefix(
            client,
            settings.backup_s3_bucket,
            str(manifest["artifact_prefix"]),
        )
        client.delete_object(Bucket=settings.backup_s3_bucket, Key=manifest["database_key"])
        client.delete_object(Bucket=settings.backup_s3_bucket, Key=key)
        removed_objects += 2
        removed_sets += 1
    return {"removed_sets": removed_sets, "removed_objects": removed_objects}


def delete_drill_bucket(settings: Settings, bucket: str) -> dict[str, object]:
    if not bucket.startswith("tutor-restore-") or bucket == settings.artifact_s3_bucket:
        raise ValueError("only an isolated tutor-restore-* bucket can be deleted")
    client = _s3(settings)
    removed = _delete_prefix(client, bucket, "")
    client.delete_bucket(Bucket=bucket)
    return {"deleted_bucket": bucket, "removed_objects": removed}


def backup(settings: Settings, backup_id: str | None = None) -> dict[str, object]:
    backup_id = backup_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if not _BACKUP_ID.fullmatch(backup_id):
        raise ValueError("backup id must use YYYYMMDDTHHMMSSZ")
    started = perf_counter()
    artifact_client = _s3(settings)
    backup_client = _s3(settings, backup=True)
    _ensure_private_bucket(backup_client, settings.backup_s3_bucket)
    _configure_retention(backup_client, settings)
    prefix = settings.backup_s3_prefix.strip("/")
    with tempfile.TemporaryDirectory(prefix="tutor-backup-") as temporary:
        dump = Path(temporary) / f"{backup_id}.dump"
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--file", str(dump)],
            env=_postgres_environment(settings.database_url),
            check=True,
        )
        checksum = _sha256(dump)
        database_key = f"{prefix}/postgres/{backup_id}.dump"
        backup_client.upload_file(
            str(dump),
            settings.backup_s3_bucket,
            database_key,
            ExtraArgs={"Metadata": {"sha256": checksum}},
        )
    artifact_prefix = f"{prefix}/artifacts/{backup_id}/"
    artifact_count = 0
    for key in _objects(artifact_client, settings.artifact_s3_bucket):
        _stream_copy(
            artifact_client,
            settings.artifact_s3_bucket,
            key,
            backup_client,
            settings.backup_s3_bucket,
            f"{artifact_prefix}{key}",
        )
        artifact_count += 1
    manifest = {
        "schema": "tutor-assistant-backup/v1",
        "backup_id": backup_id,
        "created_at": datetime.now(UTC).isoformat(),
        "database_key": database_key,
        "database_sha256": checksum,
        "artifact_prefix": artifact_prefix,
        "artifact_count": artifact_count,
    }
    backup_client.put_object(
        Bucket=settings.backup_s3_bucket,
        Key=f"{prefix}/manifests/{backup_id}.json",
        Body=json.dumps(manifest, sort_keys=True).encode(),
        ContentType="application/json",
    )
    prune(settings)
    _push_metrics(
        settings,
        "tutor_assistant_backup",
        {
            "tutor_backup_last_success_timestamp_seconds": datetime.now(UTC).timestamp(),
            "tutor_backup_duration_seconds": perf_counter() - started,
        },
    )
    return manifest


def restore(
    settings: Settings,
    backup_id: str,
    database_url: str,
    artifact_bucket: str,
) -> dict[str, object]:
    if os.getenv("ALLOW_RESTORE", "").lower() != "true":
        raise RuntimeError("set ALLOW_RESTORE=true for an isolated restore target")
    if not _BACKUP_ID.fullmatch(backup_id):
        raise ValueError("invalid backup id")
    started = perf_counter()
    backup_client = _s3(settings, backup=True)
    artifact_client = _s3(settings)
    prefix = settings.backup_s3_prefix.strip("/")
    manifest = json.loads(
        backup_client.get_object(
            Bucket=settings.backup_s3_bucket,
            Key=f"{prefix}/manifests/{backup_id}.json",
        )["Body"].read()
    )
    _ensure_private_bucket(artifact_client, artifact_bucket, versioning=False)
    with tempfile.TemporaryDirectory(prefix="tutor-restore-") as temporary:
        dump = Path(temporary) / f"{backup_id}.dump"
        backup_client.download_file(settings.backup_s3_bucket, manifest["database_key"], str(dump))
        checksum = _sha256(dump)
        if checksum != manifest["database_sha256"]:
            raise RuntimeError("database backup checksum mismatch")
        subprocess.run(["pg_restore", "--list", str(dump)], check=True, capture_output=True)
        subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                make_url(database_url).database or "postgres",
                str(dump),
            ],
            env=_postgres_environment(database_url),
            check=True,
        )
    restored = 0
    source_prefix = str(manifest["artifact_prefix"])
    verified_artifacts = 0
    for key in _objects(backup_client, settings.backup_s3_bucket, source_prefix):
        target_key = key.removeprefix(source_prefix)
        _stream_copy(
            backup_client,
            settings.backup_s3_bucket,
            key,
            artifact_client,
            artifact_bucket,
            target_key,
        )
        source = backup_client.head_object(Bucket=settings.backup_s3_bucket, Key=key)
        target = artifact_client.head_object(Bucket=artifact_bucket, Key=target_key)
        if source.get("ContentLength") != target.get("ContentLength"):
            raise RuntimeError(f"restored artifact size mismatch: {target_key}")
        source_hash = source.get("Metadata", {}).get("sha256")
        target_hash = target.get("Metadata", {}).get("sha256")
        if source_hash and source_hash != target_hash:
            raise RuntimeError(f"restored artifact checksum metadata mismatch: {target_key}")
        verified_artifacts += 1
        restored += 1
    result = {
        "backup_id": backup_id,
        "database_sha256": manifest["database_sha256"],
        "expected_artifacts": manifest["artifact_count"],
        "restored_artifacts": restored,
        "verified_artifacts": verified_artifacts,
        "duration_seconds": round(perf_counter() - started, 3),
    }
    if restored != int(manifest["artifact_count"]):
        raise RuntimeError("artifact restore count mismatch")
    _push_metrics(
        settings,
        "tutor_assistant_restore",
        {
            "tutor_restore_drill_last_success_timestamp_seconds": datetime.now(UTC).timestamp(),
            "tutor_restore_drill_duration_seconds": float(result["duration_seconds"]),
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL and S3 backup/restore operations")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--backup-id")
    restore_command = commands.add_parser("restore")
    restore_command.add_argument("backup_id")
    restore_command.add_argument("--database-url", required=True)
    restore_command.add_argument("--artifact-bucket", required=True)
    commands.add_parser("prune")
    delete_drill = commands.add_parser("delete-drill")
    delete_drill.add_argument("bucket")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "create":
        result = backup(settings, args.backup_id)
    elif args.command == "restore":
        result = restore(settings, args.backup_id, args.database_url, args.artifact_bucket)
    elif args.command == "prune":
        result = prune(settings)
    else:
        result = delete_drill_bucket(settings, args.bucket)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
