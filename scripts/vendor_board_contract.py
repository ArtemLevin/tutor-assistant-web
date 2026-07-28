from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "schemas" / "board" / "v1"
SOURCE_METADATA = ROOT / "schemas" / "board" / "source.json"


def _git_head(repository: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT.parent / "tutorboard",
    )
    arguments = parser.parse_args()
    source_repository = arguments.source_root.resolve()
    source_contract = source_repository / "contracts" / "board" / "v1"
    manifest = source_contract / "manifest.json"
    if not manifest.is_file():
        raise SystemExit(f"TutorBoard board/v1 manifest is missing: {manifest}")
    parsed = json.loads(manifest.read_text(encoding="utf-8"))
    if parsed.get("contract") != "board/v1":
        raise SystemExit("TutorBoard source does not publish contract board/v1.")

    if DESTINATION.exists():
        shutil.rmtree(DESTINATION)
    shutil.copytree(source_contract, DESTINATION)
    SOURCE_METADATA.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_METADATA.write_text(
        json.dumps(
            {
                "contract": "board/v1",
                "manifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "sourceCommit": _git_head(source_repository),
                "sourceRepository": "https://github.com/ArtemLevin/tutorboard",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
