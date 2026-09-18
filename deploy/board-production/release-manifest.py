#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
TARGETS = ("board-api", "tutorboard", "migration", "ops")
IMAGE_KEYS = {
    "board-api": "boardApi",
    "tutorboard": "tutorboard",
    "migration": "migration",
    "ops": "ops",
}
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ReleaseManifestError(ValueError):
    pass


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"cannot read JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseManifestError(f"{path} must contain a JSON object")
    return payload


def _validate_commit(value: object, name: str) -> str:
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        raise ReleaseManifestError(f"{name} must be a lowercase 40-character Git commit SHA")
    return value


def _expected_image_repositories(
    backend_repository: str,
    frontend_repository: str,
) -> dict[str, str]:
    backend = f"ghcr.io/{backend_repository.lower()}"
    frontend = f"ghcr.io/{frontend_repository.lower()}"
    return {
        "boardApi": f"{backend}-board-api",
        "tutorboard": frontend,
        "migration": f"{backend}-migration",
        "ops": f"{backend}-ops",
    }


def _validate_image_ref(value: object, expected_repository: str, name: str) -> str:
    if not isinstance(value, str):
        raise ReleaseManifestError(f"{name} image reference must be a string")
    prefix = f"{expected_repository}@sha256:"
    if not value.startswith(prefix):
        raise ReleaseManifestError(
            f"{name} must use immutable repository {expected_repository}@sha256:<digest>"
        )
    digest = value[len(prefix) :]
    if DIGEST_RE.fullmatch(digest) is None:
        raise ReleaseManifestError(f"{name} must contain a complete lowercase sha256 digest")
    return value


def _read_frontend_release(path: Path) -> dict:
    payload = _load_json(path)
    required = {
        "repository",
        "sourceCommit",
        "boardContractVersion",
        "standaloneContractVersion",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ReleaseManifestError(
            f"frontend release file is missing fields: {', '.join(missing)}"
        )
    repository = payload["repository"]
    if not isinstance(repository, str) or "/" not in repository:
        raise ReleaseManifestError("frontend release repository must be owner/name")
    _validate_commit(payload["sourceCommit"], "frontend sourceCommit")
    for key in ("boardContractVersion", "standaloneContractVersion"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ReleaseManifestError(f"frontend release {key} must be a non-empty string")
    return payload


def _parse_digest_inputs(values: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for raw in values:
        target, separator, path = raw.partition("=")
        if not separator or not target or not path:
            raise ReleaseManifestError("--digest must use target=/path/to/file syntax")
        if target not in TARGETS:
            raise ReleaseManifestError(f"unknown digest target: {target}")
        if target in parsed:
            raise ReleaseManifestError(f"duplicate digest target: {target}")
        parsed[target] = Path(path)

    missing = sorted(set(TARGETS) - parsed.keys())
    if missing:
        raise ReleaseManifestError(f"missing digest targets: {', '.join(missing)}")
    return parsed


def _read_digest_refs(
    digest_files: dict[str, Path],
    backend_repository: str,
    frontend_repository: str,
) -> dict[str, str]:
    expected = _expected_image_repositories(backend_repository, frontend_repository)
    images: dict[str, str] = {}
    for target in TARGETS:
        path = digest_files[target]
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ReleaseManifestError(f"cannot read digest file {path}: {exc}") from exc
        key = IMAGE_KEYS[target]
        images[key] = _validate_image_ref(value, expected[key], key)
    return images


def validate_manifest(payload: dict) -> dict:
    expected_fields = {
        "schemaVersion",
        "backendRepository",
        "backendCommit",
        "frontendRepository",
        "frontendCommit",
        "boardContractVersion",
        "standaloneContractVersion",
        "workflowRunId",
        "workflowRunAttempt",
        "images",
    }
    missing = sorted(expected_fields - payload.keys())
    extra = sorted(payload.keys() - expected_fields)
    if missing:
        raise ReleaseManifestError(f"release manifest is missing fields: {', '.join(missing)}")
    if extra:
        raise ReleaseManifestError(f"release manifest has unknown fields: {', '.join(extra)}")
    if payload["schemaVersion"] != SCHEMA_VERSION:
        raise ReleaseManifestError(f"schemaVersion must be {SCHEMA_VERSION}")

    backend_repository = payload["backendRepository"]
    frontend_repository = payload["frontendRepository"]
    if not isinstance(backend_repository, str) or "/" not in backend_repository:
        raise ReleaseManifestError("backendRepository must be owner/name")
    if not isinstance(frontend_repository, str) or "/" not in frontend_repository:
        raise ReleaseManifestError("frontendRepository must be owner/name")

    _validate_commit(payload["backendCommit"], "backendCommit")
    _validate_commit(payload["frontendCommit"], "frontendCommit")

    for key in ("boardContractVersion", "standaloneContractVersion"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ReleaseManifestError(f"{key} must be a non-empty string")

    for key in ("workflowRunId", "workflowRunAttempt"):
        value = payload[key]
        if not isinstance(value, str) or not value.isdigit() or int(value) < 1:
            raise ReleaseManifestError(f"{key} must be a positive integer encoded as a string")

    images = payload["images"]
    if not isinstance(images, dict):
        raise ReleaseManifestError("images must be an object")
    expected_image_keys = set(IMAGE_KEYS.values())
    missing_images = sorted(expected_image_keys - images.keys())
    extra_images = sorted(images.keys() - expected_image_keys)
    if missing_images:
        raise ReleaseManifestError(
            f"release manifest is missing images: {', '.join(missing_images)}"
        )
    if extra_images:
        raise ReleaseManifestError(
            f"release manifest has unknown images: {', '.join(extra_images)}"
        )

    expected_repositories = _expected_image_repositories(backend_repository, frontend_repository)
    for key, repository in expected_repositories.items():
        _validate_image_ref(images[key], repository, key)

    return payload


def _assemble(args: argparse.Namespace) -> int:
    frontend_release = _read_frontend_release(Path(args.frontend_release))
    backend_commit = _validate_commit(args.backend_commit, "backend commit")
    digest_files = _parse_digest_inputs(args.digest)
    images = _read_digest_refs(
        digest_files,
        args.backend_repository,
        frontend_release["repository"],
    )
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "backendRepository": args.backend_repository,
        "backendCommit": backend_commit,
        "frontendRepository": frontend_release["repository"],
        "frontendCommit": frontend_release["sourceCommit"],
        "boardContractVersion": frontend_release["boardContractVersion"],
        "standaloneContractVersion": frontend_release["standaloneContractVersion"],
        "workflowRunId": args.workflow_run_id,
        "workflowRunAttempt": args.workflow_run_attempt,
        "images": images,
    }
    validate_manifest(payload)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _verify_against_expected(args: argparse.Namespace, payload: dict) -> None:
    if args.backend_repository and payload["backendRepository"] != args.backend_repository:
        raise ReleaseManifestError("backendRepository does not match the expected repository")
    if args.backend_commit and payload["backendCommit"] != args.backend_commit:
        raise ReleaseManifestError("backendCommit does not match the expected commit")
    if args.frontend_release:
        frontend_release = _read_frontend_release(Path(args.frontend_release))
        comparisons = {
            "frontendRepository": frontend_release["repository"],
            "frontendCommit": frontend_release["sourceCommit"],
            "boardContractVersion": frontend_release["boardContractVersion"],
            "standaloneContractVersion": frontend_release["standaloneContractVersion"],
        }
        for key, expected in comparisons.items():
            if payload[key] != expected:
                raise ReleaseManifestError(f"{key} does not match the pinned frontend release")


def _verify(args: argparse.Namespace) -> int:
    payload = validate_manifest(_load_json(Path(args.manifest)))
    _verify_against_expected(args, payload)
    print("release manifest verified")
    return 0


def _env(args: argparse.Namespace) -> int:
    payload = validate_manifest(_load_json(Path(args.manifest)))
    images = payload["images"]
    assignments = {
        "BOARD_API_DIGEST": images["boardApi"],
        "TUTORBOARD_DIGEST": images["tutorboard"],
        "MIGRATION_DIGEST": images["migration"],
        "OPS_DIGEST": images["ops"],
    }
    for key, value in assignments.items():
        print(f"{key}='{value}'")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble and verify immutable TutorBoard release manifests."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--output", required=True)
    assemble.add_argument("--backend-repository", required=True)
    assemble.add_argument("--backend-commit", required=True)
    assemble.add_argument("--frontend-release", required=True)
    assemble.add_argument("--workflow-run-id", required=True)
    assemble.add_argument("--workflow-run-attempt", required=True)
    assemble.add_argument("--digest", action="append", default=[], required=True)
    assemble.set_defaults(handler=_assemble)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--backend-repository")
    verify.add_argument("--backend-commit")
    verify.add_argument("--frontend-release")
    verify.set_defaults(handler=_verify)

    env = subparsers.add_parser("env")
    env.add_argument("--manifest", required=True)
    env.set_defaults(handler=_env)

    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except ReleaseManifestError as exc:
        print(f"release manifest error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
