from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from tutor_assistant_web.modules.boards.contracts import (
    BoardCommandEnvelopeInput,
    envelope_actor_ids,
    envelope_base_revisions,
    envelope_commands,
    envelope_lamport_range,
)

CONTRACT = Path(__file__).parents[1] / "schemas" / "board" / "v1" / "fixtures"


def payload() -> dict:
    return json.loads((CONTRACT / "board-command-envelope.json").read_text())


def test_ordered_envelope_exposes_commands_and_order_metadata() -> None:
    envelope = BoardCommandEnvelopeInput.model_validate(payload()).root
    assert len(envelope_commands(envelope)) == 2
    assert envelope_actor_ids(envelope) == ["actor:tutor-01", "actor:tutor-01"]
    assert envelope_base_revisions(envelope) == [7, 7]
    assert envelope_lamport_range(envelope) == (8, 9)


@pytest.mark.parametrize("lamports", ([8, 8], [9, 8], [0, 1]))
def test_ordered_envelope_rejects_invalid_lamport(lamports: list[int]) -> None:
    value = payload()
    for item, lamport in zip(value["commands"], lamports, strict=True):
        item["order"]["lamport"] = lamport
    with pytest.raises(PydanticValidationError):
        BoardCommandEnvelopeInput.model_validate(value)


def test_ordered_envelope_rejects_future_base_revision() -> None:
    value = payload()
    value["commands"][0]["order"]["baseRevisionAtCreation"] = 9
    with pytest.raises(PydanticValidationError):
        BoardCommandEnvelopeInput.model_validate(value)


@pytest.mark.parametrize("version", ["1.0", "1.2"])
def test_legacy_envelopes_remain_readable(version: str) -> None:
    value = payload()
    value["schemaVersion"] = version
    value["commands"] = [item["command"] for item in value["commands"]]
    envelope = BoardCommandEnvelopeInput.model_validate(value).root
    assert envelope.schema_version == version
    assert envelope_lamport_range(envelope) is None


def test_unknown_envelope_version_fails_closed() -> None:
    value = payload()
    value["schemaVersion"] = "2.0"
    with pytest.raises(PydanticValidationError):
        BoardCommandEnvelopeInput.model_validate(value)


def test_unknown_transport_field_fails_closed() -> None:
    value = payload()
    value["unexpected"] = True
    with pytest.raises(PydanticValidationError):
        BoardCommandEnvelopeInput.model_validate(value)
