from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tutor_assistant_web.shared.board_contracts.board_command_envelope_schema import (
    BoardCommandEnvelope13,
)
from tutor_assistant_web.shared.board_contracts.board_document_schema import BoardDocument11
from tutor_assistant_web.shared.board_contracts.board_geometry_import_schema import (
    BoardGeometryImport11,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_schema import BoardSnapshot11

ROOT = Path(__file__).parents[1]
CONTRACT_ROOT = ROOT / "schemas" / "board" / "v1"


def _json(relative_path: str) -> dict:
    return json.loads((CONTRACT_ROOT / relative_path).read_text(encoding="utf-8"))


def test_vendored_contract_manifest_is_complete_and_fresh() -> None:
    source = json.loads((ROOT / "schemas" / "board" / "source.json").read_text(encoding="utf-8"))
    manifest_path = CONTRACT_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert source["contract"] == manifest["contract"] == "board/v1"
    assert source["sourceRepository"] == "https://github.com/ArtemLevin/tutorboard"
    assert re.fullmatch(r"[a-f0-9]{40}", source["sourceCommit"])
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == source["manifestSha256"]

    for relative_path, metadata in manifest["artifacts"].items():
        artifact = CONTRACT_ROOT / relative_path
        assert artifact.is_file(), relative_path
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == metadata["sha256"]


def test_generated_dtos_accept_canonical_tutorboard_fixtures() -> None:
    pairs = (
        (BoardDocument11, "fixtures/board-document.json"),
        (BoardCommandEnvelope13, "fixtures/board-command-envelope.json"),
        (BoardSnapshot11, "fixtures/board-snapshot.json"),
        (BoardGeometryImport11, "fixtures/board-geometry-import.json"),
    )
    for model, fixture in pairs:
        parsed = model.model_validate(_json(fixture))
        serialized = parsed.model_dump(mode="json", by_alias=True)
        assert model.model_validate(serialized) == parsed


def test_generated_dtos_forbid_unknown_transport_fields() -> None:
    payload = _json("fixtures/board-command-envelope.json")
    payload["serverOwnedRevision"] = 8

    try:
        BoardCommandEnvelope13.model_validate(payload)
    except ValueError as error:
        assert "serverOwnedRevision" in str(error)
    else:
        raise AssertionError("board contract DTO must reject unknown fields")
