from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tutor_assistant_web.modules.boards.standalone_contracts import (
    BoardAccessContextInput,
    GuestBoardAccessContext,
    TeacherBoardAccessContext,
)

FIXTURES = Path(__file__).parents[1] / "contracts" / "standalone-board" / "fixtures"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_teacher_fixture_matches_strict_contract() -> None:
    context = BoardAccessContextInput.model_validate(load_fixture("teacher-context.json")).root

    assert isinstance(context, TeacherBoardAccessContext)
    assert context.principal_type == "teacher"
    assert "board.invites.manage" in context.capabilities


def test_guest_fixture_matches_strict_contract() -> None:
    context = BoardAccessContextInput.model_validate(load_fixture("guest-context.json")).root

    assert isinstance(context, GuestBoardAccessContext)
    assert context.principal_type == "guest"
    assert context.capabilities == [
        "board.read",
        "board.write",
        "board.snapshot.write",
        "collaboration.connect",
    ]


def test_unknown_context_fields_are_rejected() -> None:
    payload = load_fixture("guest-context.json")
    payload["invitationSecret"] = "must-not-be-accepted"

    with pytest.raises(ValidationError):
        BoardAccessContextInput.model_validate(payload)


def test_guest_management_capabilities_are_rejected() -> None:
    payload = load_fixture("guest-context.json")
    payload["capabilities"] = ["board.read", "board.invites.manage"]

    with pytest.raises(ValidationError):
        BoardAccessContextInput.model_validate(payload)


def test_snapshot_write_requires_board_write() -> None:
    payload = load_fixture("guest-context.json")
    payload["capabilities"] = [
        "board.read",
        "board.snapshot.write",
        "collaboration.connect",
    ]

    with pytest.raises(ValidationError):
        BoardAccessContextInput.model_validate(payload)
