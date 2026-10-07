from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


from tutor_assistant_web.modules.boards.contracts import (
    BoardCommandEnvelopeInput,
    BoardSnapshotInput,
    envelope_media_asset_references,
    snapshot_media_asset_references,
)
from tutor_assistant_web.shared.board_contracts.board_command_envelope_1_6_schema import (
    BoardCommandEnvelope16,
)
from tutor_assistant_web.shared.board_contracts.board_command_envelope_schema import (
    BoardCommandEnvelope17,
)
from tutor_assistant_web.shared.board_contracts.board_document_schema import BoardDocument
from tutor_assistant_web.shared.board_contracts.board_geometry_import_schema import (
    BoardGeometryImport11,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_1_4_schema import (
    BoardSnapshot14,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_1_5_schema import (
    BoardSnapshot15,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_schema import BoardSnapshot16

ROOT = Path(__file__).parents[1]
CONTRACT_ROOT = ROOT / "schemas" / "board" / "v1"


def _json(relative_path: str) -> dict:
    return json.loads((CONTRACT_ROOT / relative_path).read_text(encoding="utf-8"))


def test_vendored_contract_manifest_is_complete_and_fresh() -> None:
    source = json.loads((ROOT / "schemas" / "board" / "source.json").read_text(encoding="utf-8"))
    manifest_path = CONTRACT_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert source["contract"] == manifest["contract"] == "board/v1"
    assert manifest["schemas"] == {
        "boardCommandEnvelope": "1.7",
        "boardDocument": "1.6",
        "boardGeometryImport": "1.0",
        "boardSnapshot": "1.6",
    }
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
        (BoardCommandEnvelope17, "fixtures/board-command-envelope.json"),
        (BoardSnapshot16, "fixtures/board-snapshot.json"),
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
        BoardCommandEnvelope17.model_validate(payload)
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


def test_media_asset_is_contract_readable_and_extracted_for_authority() -> None:
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

    assert BoardCommandEnvelope17.model_validate(envelope).schema_version == "1.7"
    runtime_envelope = BoardCommandEnvelopeInput.model_validate(envelope).root
    references = envelope_media_asset_references(runtime_envelope)
    assert len(references) == 1
    assert references[0].asset_id == "asset:media-contract-test"
    assert references[0].content_sha256 == "a" * 64

    snapshot = _json("fixtures/board-snapshot.json")
    asset = _media_asset()
    snapshot["document"]["objects"][asset["id"]] = asset
    snapshot["document"]["order"].append(asset["id"])
    assert BoardSnapshot16.model_validate(snapshot).schema_version == "1.6"
    runtime_snapshot = BoardSnapshotInput.model_validate(snapshot).root
    snapshot_references = snapshot_media_asset_references(runtime_snapshot)
    assert len(snapshot_references) == 1
    assert snapshot_references[0].asset_id == "asset:media-contract-test"


def _historical_envelope_payload(version: str) -> dict:
    payload = _json("fixtures/board-command-envelope.json")
    payload["schemaVersion"] = version
    payload["commands"] = payload["commands"][:2]
    return payload


def test_atomic_batch_replace_exposes_media_asset_to_authority() -> None:
    envelope = _json("fixtures/board-command-envelope.json")
    asset = _media_asset()
    envelope["commands"] = [
        {
            "command": {
                "actorId": envelope["actorId"],
                "changes": [
                    {
                        "atIndex": 0,
                        "originals": [],
                        "replacements": [asset],
                    }
                ],
                "id": "command:media-batch-contract-test",
                "kind": "core.objects.batch-replace",
                "timestamp": "2026-09-30T00:00:00.000Z",
            },
            "order": {"baseRevisionAtCreation": envelope["baseRevision"], "lamport": 20},
        }
    ]

    assert BoardCommandEnvelope17.model_validate(envelope).schema_version == "1.7"
    runtime_envelope = BoardCommandEnvelopeInput.model_validate(envelope).root
    references = envelope_media_asset_references(runtime_envelope)
    assert len(references) == 1
    assert references[0].asset_id == asset["assetId"]


def test_previous_origin_aware_envelope_15_remains_readable() -> None:
    payload = _historical_envelope_payload("1.5")

    parsed = BoardCommandEnvelopeInput.model_validate(payload)
    assert parsed.root.schema_version == "1.5"


def test_previous_media_envelope_16_remains_readable() -> None:
    payload = _historical_envelope_payload("1.6")

    legacy = BoardCommandEnvelope16.model_validate(payload)
    assert legacy.schema_version == "1.6"
    parsed = BoardCommandEnvelopeInput.model_validate(payload)
    assert parsed.root.schema_version == "1.6"


def test_previous_snapshot_15_remains_readable() -> None:
    payload = _json("fixtures/board-snapshot.json")
    payload["schemaVersion"] = "1.5"
    payload["document"]["schemaVersion"] = "1.5"

    legacy = BoardSnapshot15.model_validate(payload)
    assert legacy.schema_version == "1.5"
    parsed = BoardSnapshotInput.model_validate(payload)
    assert parsed.root.schema_version == "1.5"


def test_current_contract_accepts_single_sample_dot_and_atomic_batch_replace() -> None:
    document = _json("fixtures/board-document.json")
    dot = {
        "groupId": None,
        "id": "object:contract-dot",
        "ink": {
            "centerline": [],
            "closed": False,
            "samples": [
                {
                    "point": {"x": 42, "y": 24},
                    "pressure": 0.75,
                    "timestampMs": 0,
                }
            ],
            "version": "1.0",
        },
        "kind": "drawing.pen-stroke",
        "locked": False,
        "points": [{"x": 42, "y": 24}],
        "position": {"x": 0, "y": 0},
        "rotation": 0,
        "scale": {"x": 1, "y": 1},
        "source": {"kind": "user"},
        "style": {
            "fill": None,
            "opacity": 1,
            "stroke": "#111827",
            "strokeWidth": 4,
        },
        "visible": True,
    }
    document["objects"][dot["id"]] = dot
    document["order"].append(dot["id"])
    parsed_document = BoardDocument.model_validate(document)
    assert parsed_document.schema_version == "1.6"

    envelope = _json("fixtures/board-command-envelope.json")
    batch_commands = [
        item
        for item in envelope["commands"]
        if item["command"]["kind"] == "core.objects.batch-replace"
    ]
    assert len(batch_commands) == 1
    parsed_envelope = BoardCommandEnvelope17.model_validate(
        {**envelope, "commands": batch_commands}
    )
    assert parsed_envelope.schema_version == "1.7"
