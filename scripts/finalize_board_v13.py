from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"marker missing in {path}: {old[:120]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_before(path: str, marker: str, addition: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(f"insertion marker missing in {path}: {marker[:120]!r}")
    file.write_text(text.replace(marker, addition + marker, 1), encoding="utf-8")


def patch_models() -> None:
    path = "src/tutor_assistant_web/modules/boards/models.py"
    replace_once(
        path,
        (
            '        CheckConstraint("lamport_min >= 0", '
            'name="ck_board_commands_lamport_min"),\n'
            "        CheckConstraint(\n"
            '            "lamport_max >= lamport_min",\n'
            '            name="ck_board_commands_lamport_range",\n'
            "        ),"
        ),
        (
            "        CheckConstraint(\n"
            '            "(lamport_min IS NULL AND lamport_max IS NULL) OR "\n'
            '            "(lamport_min IS NOT NULL AND lamport_max IS NOT NULL)",\n'
            '            name="ck_board_commands_lamport_pair",\n'
            "        ),\n"
            "        CheckConstraint(\n"
            '            "lamport_min IS NULL OR "\n'
            '            "(lamport_min > 0 AND lamport_max >= lamport_min)",\n'
            '            name="ck_board_commands_lamport_range",\n'
            "        ),"
        ),
    )
    replace_once(
        path,
        (
            "    lamport_min: Mapped[int] = mapped_column(Integer, default=0)\n"
            "    lamport_max: Mapped[int] = mapped_column(Integer, default=0)"
        ),
        (
            "    lamport_min: Mapped[int | None] = mapped_column(Integer, nullable=True)\n"
            "    lamport_max: Mapped[int | None] = mapped_column(Integer, nullable=True)"
        ),
    )


def patch_application() -> None:
    path = "src/tutor_assistant_web/modules/boards/application.py"
    replace_once(path, "import json\n", "import json\nimport logging\n")
    replace_once(
        path,
        (
            '_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")\n'
            '_UNSAFE_IDENTIFIERS = {"__proto__", "constructor", "prototype"}'
        ),
        (
            '_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")\n'
            '_UNSAFE_IDENTIFIERS = {"__proto__", "constructor", "prototype"}\n'
            "_LOGGER = logging.getLogger(__name__)"
        ),
    )
    replace_once(
        path,
        (
            "        self.expected_revision = expected_revision\n"
            "        self.current_revision = current_revision\n\n\n"
            "@dataclass(frozen=True)"
        ),
        (
            "        self.expected_revision = expected_revision\n"
            "        self.current_revision = current_revision\n\n\n"
            "class BoardLamportConflict(ConflictError):\n"
            "    def __init__(\n"
            "        self,\n"
            "        actor_id: str,\n"
            "        previous_lamport: int,\n"
            "        incoming_lamport: int,\n"
            "    ) -> None:\n"
            '        super().__init__("Lamport пакета должен возрастать для actor доски")\n'
            "        self.actor_id = actor_id\n"
            "        self.previous_lamport = previous_lamport\n"
            "        self.incoming_lamport = incoming_lamport\n\n\n"
            "@dataclass(frozen=True)"
        ),
    )
    replace_once(
        path,
        (
            "        try:\n"
            "            lamport_min, lamport_max = envelope_lamport_range(envelope)\n"
            "        except ValueError as exc:\n"
            "            raise ValidationError(str(exc)) from exc"
        ),
        (
            "        try:\n"
            "            lamport_range = envelope_lamport_range(envelope)\n"
            "        except ValueError as exc:\n"
            "            raise ValidationError(str(exc)) from exc\n"
            "        if lamport_range is None:\n"
            "            lamport_min = None\n"
            "            lamport_max = None\n"
            "        else:\n"
            "            lamport_min, lamport_max = lamport_range"
        ),
    )
    replace_once(
        path,
        "            if lamport_min > 0:\n",
        "            if lamport_min is not None:\n",
    )
    replace_once(
        path,
        (
            "                if lamport_min <= latest_lamport:\n"
            '                    raise ConflictError("Lamport пакета должен возрастать для actor доски")'
        ),
        (
            "                if lamport_min <= latest_lamport:\n"
            "                    raise BoardLamportConflict(\n"
            "                        contract_actor_id,\n"
            "                        latest_lamport,\n"
            "                        lamport_min,\n"
            "                    )"
        ),
    )
    replace_once(
        path,
        (
            "            session.add(batch)\n"
            "            session.commit()\n"
            "            return batch"
        ),
        (
            "            session.add(batch)\n"
            "            session.commit()\n"
            "            _LOGGER.info(\n"
            '                "Board command batch committed",\n'
            "                extra={\n"
            '                    "event": "board.command_batch.committed",\n'
            '                    "document_id": document.id,\n'
            '                    "actor_id": contract_actor_id,\n'
            '                    "schema_version": envelope.schema_version,\n'
            '                    "base_revision": envelope.base_revision,\n'
            '                    "revision": revision,\n'
            '                    "lamport_min": lamport_min,\n'
            '                    "lamport_max": lamport_max,\n'
            '                    "command_count": len(envelope.commands),\n'
            '                    "payload_sha256": payload_sha256,\n'
            "                },\n"
            "            )\n"
            "            return batch"
        ),
    )


def patch_routes() -> None:
    path = "src/tutor_assistant_web/modules/boards/routes.py"
    replace_once(
        path,
        (
            "from tutor_assistant_web.modules.boards.application import (\n"
            "    BoardPersistenceService,\n"
            "    BoardRevisionConflict,\n"
            "    canonical_json,\n"
            ")"
        ),
        (
            "from tutor_assistant_web.modules.boards.application import (\n"
            "    BoardLamportConflict,\n"
            "    BoardPersistenceService,\n"
            "    BoardRevisionConflict,\n"
            "    canonical_json,\n"
            ")"
        ),
    )
    replace_once(
        path,
        (
            "        envelope = envelope_input.root\n"
            "        if envelope.document_id.root != document_id:"
        ),
        (
            "        envelope = envelope_input.root\n"
            "        BOARD_SYNC_EVENTS.labels(\n"
            '            event=f"envelope_{envelope.schema_version}_received"\n'
            "        ).inc()\n"
            "        if envelope.document_id.root != document_id:"
        ),
    )
    replace_once(
        path,
        (
            "        try:\n"
            "            batch = boards.append_commands(envelope, actor.user_id)\n"
            "        except BoardRevisionConflict as exc:"
        ),
        (
            "        try:\n"
            "            batch = boards.append_commands(envelope, actor.user_id)\n"
            "        except BoardLamportConflict as exc:\n"
            '            BOARD_SYNC_EVENTS.labels(event="lamport_conflict").inc()\n'
            "            return JSONResponse(\n"
            "                {\n"
            '                    "error": {\n'
            '                        "code": "board_lamport_conflict",\n'
            '                        "message": str(exc),\n'
            '                        "actorId": exc.actor_id,\n'
            '                        "previousLamport": exc.previous_lamport,\n'
            '                        "incomingLamport": exc.incoming_lamport,\n'
            "                    }\n"
            "                },\n"
            "                status_code=409,\n"
            '                headers={"ETag": _etag(previous_revision)},\n'
            "            )\n"
            "        except BoardRevisionConflict as exc:"
        ),
    )
    replace_once(
        path,
        '                    "command_count": len(envelope.commands),',
        '                    "command_count": len(envelope_commands(envelope)),',
    )
    replace_once(
        path,
        (
            "            container.collaboration.publish(\n"
            "                actor.organization_id,\n"
            "                document.id,\n"
            "                {\n"
            '                    "type": "board.revision",\n'
            '                    "protocolVersion": "1.0",\n'
            '                    "documentId": document.id,\n'
            '                    "revision": batch.revision,\n'
            '                    "baseRevision": batch.base_revision,\n'
            '                    "idempotencyKey": batch.idempotency_key,\n'
            '                    "actorId": actor.user_id,\n'
            "                },\n"
            "            )\n"
            "        return JSONResponse("
        ),
        (
            "            container.collaboration.publish(\n"
            "                actor.organization_id,\n"
            "                document.id,\n"
            "                {\n"
            '                    "type": "board.revision",\n'
            '                    "protocolVersion": "1.0",\n'
            '                    "documentId": document.id,\n'
            '                    "revision": batch.revision,\n'
            '                    "baseRevision": batch.base_revision,\n'
            '                    "idempotencyKey": batch.idempotency_key,\n'
            '                    "actorId": actor.user_id,\n'
            "                },\n"
            "            )\n"
            "        else:\n"
            '            BOARD_SYNC_EVENTS.labels(event="idempotent_retry").inc()\n'
            "        return JSONResponse("
        ),
    )
    replace_once(
        path,
        (
            '        "actorUserId": batch.actor_user_id,\n'
            '        "payloadSha256": batch.payload_sha256,'
        ),
        (
            '        "actorUserId": batch.actor_user_id,\n'
            '        "schemaVersion": batch.schema_version,\n'
            '        "lamportMin": batch.lamport_min,\n'
            '        "lamportMax": batch.lamport_max,\n'
            '        "payloadSha256": batch.payload_sha256,'
        ),
    )


def patch_api_tests() -> None:
    path = "tests/test_board_api.py"
    replace_once(
        path,
        "from tutor_assistant_web.modules.boards.evidence import _utc_milliseconds",
        (
            "from tutor_assistant_web.modules.boards.evidence import _utc_milliseconds\n"
            "from tutor_assistant_web.modules.boards.models import BoardCommandBatch"
        ),
    )
    replace_once(
        path,
        (
            "def _command_payload(user_id: str, *, base_revision: int = 0, "
            'key: str = "api:batch-1"):\n'
            "    payload = json.loads((FIXTURES / \"board-command-envelope.json\").read_text())\n"
            "    payload.update(\n"
            "        {\n"
            '            "documentId": DOCUMENT_ID,\n'
            '            "baseRevision": base_revision,\n'
            '            "idempotencyKey": key,\n'
            '            "actorId": user_id,\n'
            "        }\n"
            "    )\n"
            '    for command in payload["commands"]:\n'
            '        command["actorId"] = user_id\n'
            "    return payload"
        ),
        (
            "def _command_payload(\n"
            "    user_id: str,\n"
            "    *,\n"
            "    base_revision: int = 0,\n"
            '    key: str = "api:batch-1",\n'
            "    lamport_start: int = 1,\n"
            "):\n"
            "    payload = json.loads((FIXTURES / \"board-command-envelope.json\").read_text())\n"
            "    payload.update(\n"
            "        {\n"
            '            "documentId": DOCUMENT_ID,\n'
            '            "baseRevision": base_revision,\n'
            '            "idempotencyKey": key,\n'
            '            "actorId": user_id,\n'
            "        }\n"
            "    )\n"
            '    for index, item in enumerate(payload["commands"]):\n'
            '        command = item.get("command", item)\n'
            '        command["actorId"] = user_id\n'
            '        order = item.get("order")\n'
            "        if order is not None:\n"
            '            order["baseRevisionAtCreation"] = base_revision\n'
            '            order["lamport"] = lamport_start + index\n'
            "    return payload\n\n\n"
            "def _legacy_command_payload(\n"
            "    user_id: str,\n"
            "    *,\n"
            "    base_revision: int = 0,\n"
            '    key: str = "api:legacy-batch",\n'
            "):\n"
            "    payload = _command_payload(\n"
            "        user_id,\n"
            "        base_revision=base_revision,\n"
            "        key=key,\n"
            "    )\n"
            '    payload["schemaVersion"] = "1.2"\n'
            '    payload["commands"] = [item["command"] for item in payload["commands"]]\n'
            "    return payload"
        ),
    )
    append_before(
        path,
        "\ndef test_actor_id_is_bound_to_authenticated_user(board_api):\n",
        '''

def test_ordered_lamport_range_is_persisted_and_replay_is_rejected(board_api):
    client, database, _, _, _, context = board_api
    csrf = context["csrfToken"]
    user_id = context["userId"]
    expected_sha = _snapshot_payload()["documentSha256"]

    first = _command_payload(user_id, lamport_start=1)
    first["expectedDocumentSha256"] = expected_sha
    response = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=first,
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200

    with database.sessions() as session:
        stored = session.scalar(
            select(BoardCommandBatch).where(
                BoardCommandBatch.board_document_id == DOCUMENT_ID,
                BoardCommandBatch.revision == 1,
            )
        )
        assert stored is not None
        assert (stored.lamport_min, stored.lamport_max) == (1, 2)

    replay = _command_payload(
        user_id,
        base_revision=1,
        key="api:batch-lamport-replay",
        lamport_start=2,
    )
    replay["expectedDocumentSha256"] = expected_sha
    rejected = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=replay,
        headers={"x-csrf-token": csrf},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"] == {
        "code": "board_lamport_conflict",
        "message": "Lamport пакета должен возрастать для actor доски",
        "actorId": user_id,
        "previousLamport": 2,
        "incomingLamport": 2,
    }


def test_mixed_legacy_and_ordered_journal_remains_recoverable(board_api):
    client, database, _, _, _, context = board_api
    csrf = context["csrfToken"]
    user_id = context["userId"]
    expected_sha = _snapshot_payload()["documentSha256"]

    legacy = _legacy_command_payload(user_id)
    legacy["expectedDocumentSha256"] = expected_sha
    assert client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=legacy,
        headers={"x-csrf-token": csrf},
    ).status_code == 200

    ordered = _command_payload(
        user_id,
        base_revision=1,
        key="api:ordered-after-legacy",
        lamport_start=1,
    )
    ordered["expectedDocumentSha256"] = expected_sha
    assert client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=ordered,
        headers={"x-csrf-token": csrf},
    ).status_code == 200

    recovered = client.get(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        params={"afterRevision": 0},
    )
    assert recovered.status_code == 200
    items = recovered.json()["items"]
    assert [item["revision"] for item in items] == [1, 2]
    assert [item["schemaVersion"] for item in items] == ["1.2", "1.3"]
    assert items[0]["lamportMin"] is None
    assert items[1]["lamportMin"] == 1

    with database.sessions() as session:
        batches = list(
            session.scalars(
                select(BoardCommandBatch)
                .where(BoardCommandBatch.board_document_id == DOCUMENT_ID)
                .order_by(BoardCommandBatch.revision)
            )
        )
    assert batches[0].lamport_min is None
    assert batches[0].lamport_max is None
    assert (batches[1].lamport_min, batches[1].lamport_max) == (1, 2)


def test_client_clock_skew_does_not_control_command_acceptance(board_api):
    client, _, _, _, _, context = board_api
    csrf = context["csrfToken"]
    user_id = context["userId"]
    expected_sha = _snapshot_payload()["documentSha256"]

    past = _command_payload(user_id, lamport_start=1)
    for item in past["commands"]:
        item["command"]["timestamp"] = "2000-01-01T00:00:00.000Z"
    past["expectedDocumentSha256"] = expected_sha
    assert client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=past,
        headers={"x-csrf-token": csrf},
    ).status_code == 200

    future = _command_payload(
        user_id,
        base_revision=1,
        key="api:future-clock",
        lamport_start=3,
    )
    for item in future["commands"]:
        item["command"]["timestamp"] = "2099-01-01T00:00:00.000Z"
    future["expectedDocumentSha256"] = expected_sha
    response = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=future,
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["revision"] == 2


def test_idempotency_key_rejects_a_different_payload(board_api):
    client, _, _, _, _, context = board_api
    csrf = context["csrfToken"]
    user_id = context["userId"]
    payload = _command_payload(user_id)
    payload["expectedDocumentSha256"] = _snapshot_payload()["documentSha256"]
    assert client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=payload,
        headers={"x-csrf-token": csrf},
    ).status_code == 200

    changed = json.loads(json.dumps(payload))
    changed["commands"][0]["command"]["title"] = "Different payload"
    rejected = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=changed,
        headers={"x-csrf-token": csrf},
    )
    assert rejected.status_code == 409
''',
    )


def write_contract_tests() -> None:
    Path("tests/test_board_command_ordering.py").write_text(
        '''from __future__ import annotations

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
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_models()
    patch_application()
    patch_routes()
    patch_api_tests()
    write_contract_tests()


if __name__ == "__main__":
    main()
