from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "deploy" / "board-production" / "release-manifest.py"
BACKEND_REPOSITORY = "ArtemLevin/tutor-assistant-web"
FRONTEND_REPOSITORY = "ArtemLevin/tutorboard"
BACKEND_COMMIT = "b" * 40
FRONTEND_COMMIT = "f" * 40


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _frontend_release(tmp_path: Path, *, commit: str = FRONTEND_COMMIT) -> Path:
    path = tmp_path / "tutorboard-release.json"
    path.write_text(
        json.dumps(
            {
                "repository": FRONTEND_REPOSITORY,
                "sourceCommit": commit,
                "boardContractVersion": "1.5",
                "standaloneContractVersion": "1.0",
            }
        ),
        encoding="utf-8",
    )
    return path


def _digest_files(tmp_path: Path) -> dict[str, Path]:
    repositories = {
        "board-api": "ghcr.io/artemlevin/tutor-assistant-web-board-api",
        "tutorboard": "ghcr.io/artemlevin/tutorboard",
        "migration": "ghcr.io/artemlevin/tutor-assistant-web-migration",
        "ops": "ghcr.io/artemlevin/tutor-assistant-web-ops",
    }
    result = {}
    for index, (target, repository) in enumerate(repositories.items(), start=1):
        path = tmp_path / f"{target}.digest"
        path.write_text(f"{repository}@sha256:{str(index) * 64}\n", encoding="utf-8")
        result[target] = path
    return result


def _assemble_args(tmp_path: Path) -> tuple[list[str], Path, Path, dict[str, Path]]:
    frontend_release = _frontend_release(tmp_path)
    digest_files = _digest_files(tmp_path)
    output = tmp_path / "release-manifest.json"
    args = [
        "assemble",
        "--output",
        str(output),
        "--backend-repository",
        BACKEND_REPOSITORY,
        "--backend-commit",
        BACKEND_COMMIT,
        "--frontend-release",
        str(frontend_release),
        "--workflow-run-id",
        "12345",
        "--workflow-run-attempt",
        "1",
    ]
    for target, path in digest_files.items():
        args.extend(["--digest", f"{target}={path}"])
    return args, output, frontend_release, digest_files


def test_assemble_verify_and_emit_environment(tmp_path: Path):
    args, output, frontend_release, _ = _assemble_args(tmp_path)

    assembled = _run(*args)
    assert assembled.returncode == 0, assembled.stderr

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["backendCommit"] == BACKEND_COMMIT
    assert payload["frontendCommit"] == FRONTEND_COMMIT
    assert payload["images"]["boardApi"].startswith(
        "ghcr.io/artemlevin/tutor-assistant-web-board-api@sha256:"
    )

    verified = _run(
        "verify",
        "--manifest",
        str(output),
        "--backend-repository",
        BACKEND_REPOSITORY,
        "--backend-commit",
        BACKEND_COMMIT,
        "--frontend-release",
        str(frontend_release),
    )
    assert verified.returncode == 0, verified.stderr
    assert "release manifest verified" in verified.stdout

    emitted = _run("env", "--manifest", str(output))
    assert emitted.returncode == 0, emitted.stderr
    assert "BOARD_API_DIGEST='ghcr.io/artemlevin/tutor-assistant-web-board-api@sha256:" in (
        emitted.stdout
    )
    assert "TUTORBOARD_DIGEST='ghcr.io/artemlevin/tutorboard@sha256:" in emitted.stdout


def test_assemble_rejects_missing_target(tmp_path: Path):
    args, _, _, _ = _assemble_args(tmp_path)
    index = args.index("--digest")
    del args[index : index + 2]

    result = _run(*args)

    assert result.returncode == 2
    assert "missing digest targets" in result.stderr


def test_assemble_rejects_duplicate_target(tmp_path: Path):
    args, _, _, digest_files = _assemble_args(tmp_path)
    args.extend(["--digest", f"board-api={digest_files['board-api']}"])

    result = _run(*args)

    assert result.returncode == 2
    assert "duplicate digest target: board-api" in result.stderr


def test_assemble_rejects_tag_reference(tmp_path: Path):
    args, _, _, digest_files = _assemble_args(tmp_path)
    digest_files["ops"].write_text(
        "ghcr.io/artemlevin/tutor-assistant-web-ops:latest\n",
        encoding="utf-8",
    )

    result = _run(*args)

    assert result.returncode == 2
    assert "immutable repository" in result.stderr


def test_assemble_rejects_short_digest(tmp_path: Path):
    args, _, _, digest_files = _assemble_args(tmp_path)
    digest_files["migration"].write_text(
        "ghcr.io/artemlevin/tutor-assistant-web-migration@sha256:1234\n",
        encoding="utf-8",
    )

    result = _run(*args)

    assert result.returncode == 2
    assert "complete lowercase sha256 digest" in result.stderr


def test_assemble_rejects_wrong_repository(tmp_path: Path):
    args, _, _, digest_files = _assemble_args(tmp_path)
    digest_files["board-api"].write_text(
        f"ghcr.io/example/board-api@sha256:{'a' * 64}\n",
        encoding="utf-8",
    )

    result = _run(*args)

    assert result.returncode == 2
    assert "tutor-assistant-web-board-api" in result.stderr


def test_verify_rejects_backend_commit_mismatch(tmp_path: Path):
    args, output, _, _ = _assemble_args(tmp_path)
    assert _run(*args).returncode == 0

    result = _run(
        "verify",
        "--manifest",
        str(output),
        "--backend-commit",
        "c" * 40,
    )

    assert result.returncode == 2
    assert "backendCommit does not match" in result.stderr


def test_verify_rejects_frontend_pin_mismatch(tmp_path: Path):
    args, output, _, _ = _assemble_args(tmp_path)
    assert _run(*args).returncode == 0
    changed_release = _frontend_release(tmp_path, commit="e" * 40)

    result = _run(
        "verify",
        "--manifest",
        str(output),
        "--frontend-release",
        str(changed_release),
    )

    assert result.returncode == 2
    assert "frontendCommit does not match" in result.stderr
