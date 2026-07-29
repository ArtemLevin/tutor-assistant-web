from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from sqlalchemy import select

from tutor_assistant_web.bootstrap.container import build_artifact_storage
from tutor_assistant_web.config import get_settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.evidence import BoardEvidenceService
from tutor_assistant_web.modules.boards.models import BoardEvidence, BoardEvidenceStatus
from tutor_assistant_web.shared.contracts import ArtifactStorage
from tutor_assistant_web.shared.errors import NotFoundError


def export_public_board_evidence(
    database: Database,
    storage: ArtifactStorage,
    organization_id: str,
    evidence_id: str,
    output_root: Path,
) -> Path:
    """Atomically export one published SVG with a metadata-minimized public manifest."""
    with database.sessions() as session:
        evidence = session.scalar(
            select(BoardEvidence).where(
                BoardEvidence.organization_id == organization_id,
                BoardEvidence.id == evidence_id,
                BoardEvidence.storage_status == BoardEvidenceStatus.available.value,
                BoardEvidence.published_at.is_not(None),
                BoardEvidence.revoked_at.is_(None),
            )
        )
        if evidence is None:
            raise NotFoundError("Published board evidence not found")
    service = BoardEvidenceService(database, storage, organization_id)
    svg, _, _ = service.read_artifact(evidence, "svg")
    destination = output_root.resolve() / "board-evidence" / evidence.id
    if destination.exists():
        raise FileExistsError(f"Export destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{evidence.id}-",
            dir=destination.parent,
        )
    )
    try:
        (temporary / "preview.svg").write_bytes(svg)
        public_manifest = {
            "schemaVersion": "1.0",
            "board": {
                "revision": evidence.revision,
                "title": "Итоговая доска занятия",
            },
            "assets": {"preview": "preview.svg"},
        }
        (temporary / "manifest.json").write_text(
            json.dumps(
                public_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except Exception:
        for item in temporary.iterdir():
            item.unlink()
        temporary.rmdir()
        raise
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export one published board evidence artifact for a static student site"
    )
    parser.add_argument("--organization", required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        exported = export_public_board_evidence(
            database,
            build_artifact_storage(settings),
            args.organization,
            args.evidence_id,
            args.output,
        )
    finally:
        database.dispose()
    print(exported)
