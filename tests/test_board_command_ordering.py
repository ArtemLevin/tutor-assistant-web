from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from tutor_assistant_web.modules.boards.contracts import (
    BoardCommandEnvelopeInput,
    envelope_commands,
    envelope_lamport_range,
)

CONTRACT = Path(__file__).parents[1] / "schemas" / "board" / "v1" / "fixtures"


def payload() -> dict:
    return json.loads((CONTRACT / "board-command-envelope.json").read_text())


def test_ordered_envelope_exposes_commands_and_lamport_range() -> None:
    envelope = BoardCommandEnvelopeInput.model_validate(payload()).root
    assert len(envelope_commands(envelope)) == 2
    assert envelope_lamport_range(envelope) == (8, 9)


def test_ordered_envelope_rejects_non_monotonic_lamport() -> None:
    value = payload()
    value["commands"][1]["order"]["lamport"] = 8
    envelope = BoardCommandEnvelopeInput.model_validate(value).root
    with pytest.raises(ValueError, match="строго возрастать"):
        envelope_lamport_range(envelope)


def test_ordered_envelope_rejects_future_base_revision() -> None:
    value = payload()
    value["commands"][0]["order"]["baseRevisionAtCreation"] = 9
    envelope = BoardCommandEnvelopeInput.model_validate(value).root
    with pytest.raises(ValueError, match="превышает"):
        envelope_lamport_range(envelope)


def test_legacy_envelope_remains_readable() -> None:
    value = payload()
    value["schemaVersion"] = "1.2"
    value["commands"] = [item["command"] for item in value["commands"]]
    envelope = BoardCommandEnvelopeInput.model_validate(value).root
    assert envelope.schema_version == "1.2"
    assert envelope_lamport_range(envelope) == (0, 0)


def test_unknown_envelope_version_fails_closed() -> None:
    value = payload()
    value["schemaVersion"] = "2.0"
    with pytest.raises(PydanticValidationError):
        BoardCommandEnvelopeInput.model_validate(value)
