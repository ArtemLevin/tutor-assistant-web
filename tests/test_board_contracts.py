from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tutor_assistant_web.modules.boards.contracts import (
    BoardCommandEnvelopeInput,
    BoardSnapshotInput,
)
from tutor_assistant_web.shared.board_contracts.board_command_envelope_schema import (
    BoardCommandEnvelope16,
)
from tutor_assistant_web.shared.board_contracts.board_document_schema import BoardDocument
from tutor_assistant_web.shared.board_contracts.board_geometry_import_schema import (
    BoardGeometryImport11,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_1_4_schema import (
    BoardSnapshot14,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_schema import BoardSnapshot15

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
        (BoardDocument, "fixtures/board-document.json"),
        (BoardCommandEnvelope16, "fixtures/board-command-envelope.json"),
        (BoardSnapshot15, "fixtures/board-snapshot.json"),
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
        BoardCommandEnvelope16.model_validate(payload)
    except ValueError as error:
        assert "serverOwnedRevision" in str(error)
    else:
        raise AssertionError("board contract DTO must reject unknown fields")


def test_legacy_snapshot_14_reader_survives_contract_upgrade() -> None:
    payload = _json("fixtures/board-snapshot.json")
    payload["schemaVersion"] = "1.4"
    payload["document"]["schemaVersion"] = "1.4"

    parsed = BoardSnapshot14.model_validate(payload)
    assert parsed.schema_version == "1.4"
    rolling = BoardSnapshotInput.model_validate(payload)
    assert rolling.root.schema_version == "1.4"


def _media_asset() -> dict:
    return {
        "groupId": None,
        "id": "object:media-contract-test",
        "assetId": "asset:media-contract-test",
        "byteSize": 12345,
        "contentSha256": "a" * 64,
        "fileName": "lesson.gif",
        "intrinsicSize": {"height": 100, "width": 160},
        "kind": "media.asset",
        "locked": False,
        "mimeType": "image/gif",
        "position": {"x": 0, "y": 0},
        "rotation": 0,
        "scale": {"x": 1, "y": 1},
        "size": {"height": 100, "width": 160},
        "source": {"kind": "user"},
        "style": {
            "fill": None,
            "opacity": 1,
            "stroke": None,
            "strokeWidth": 0,
        },
        "visible": True,
    }


def test_media_asset_is_contract_readable_but_runtime_persistence_is_gated() -> None:
    envelope = _json("fixtures/board-command-envelope.json")
    envelope["commands"] = [
        {
            "command": {
                "actorId": envelope["actorId"],
                "atIndex": 0,
                "id": "command:media-contract-test",
                "kind": "core.objects.add",
                "objects": [_media_asset()],
                "timestamp": "2026-09-30T00:00:00.000Z",
            },
            "order": {"baseRevisionAtCreation": envelope["baseRevision"], "lamport": 10},
        }
    ]

    assert BoardCommandEnvelope16.model_validate(envelope).schema_version == "1.6"
    try:
        BoardCommandEnvelopeInput.model_validate(envelope)
    except ValueError as error:
        assert "media.asset requires board media authority" in str(error)
    else:
        raise AssertionError("runtime command boundary must gate media.asset")

    snapshot = _json("fixtures/board-snapshot.json")
    asset = _media_asset()
    snapshot["document"]["objects"][asset["id"]] = asset
    snapshot["document"]["order"].append(asset["id"])
    assert BoardSnapshot15.model_validate(snapshot).schema_version == "1.5"
    try:
        BoardSnapshotInput.model_validate(snapshot)
    except ValueError as error:
        assert "media.asset requires board media authority" in str(error)
    else:
        raise AssertionError("runtime snapshot boundary must gate media.asset")


def test_previous_origin_aware_envelope_15_remains_readable() -> None:
    payload = _json("fixtures/board-command-envelope.json")
    payload["schemaVersion"] = "1.5"

    parsed = BoardCommandEnvelopeInput.model_validate(payload)
    assert parsed.root.schema_version == "1.5"
