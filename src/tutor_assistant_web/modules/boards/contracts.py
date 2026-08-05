from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, RootModel

from tutor_assistant_web.shared.board_contracts.board_command_envelope_schema import (
    BoardCommand,
    BoardCommandEnvelope13,
    Identifier,
)


class LegacyBoardCommandEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: Identifier = Field(alias="actorId")
    base_revision: int = Field(alias="baseRevision", ge=0)
    commands: list[BoardCommand] = Field(min_length=1, max_length=100)
    document_id: Identifier = Field(alias="documentId")
    expected_document_sha256: str = Field(
        alias="expectedDocumentSha256",
        pattern=r"^[a-f0-9]{64}$",
    )
    idempotency_key: str = Field(
        alias="idempotencyKey",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    schema_version: Literal["1.0", "1.2"] = Field(alias="schemaVersion")


BoardCommandEnvelope: TypeAlias = LegacyBoardCommandEnvelope | BoardCommandEnvelope13


class BoardCommandEnvelopeInput(RootModel[BoardCommandEnvelope]):
    pass


def envelope_commands(envelope: BoardCommandEnvelope) -> list[BoardCommand]:
    if isinstance(envelope, BoardCommandEnvelope13):
        return [item.command for item in envelope.commands]
    return list(envelope.commands)


def envelope_lamport_range(envelope: BoardCommandEnvelope) -> tuple[int, int]:
    if not isinstance(envelope, BoardCommandEnvelope13):
        return 0, 0
    orders = [item.order for item in envelope.commands]
    if any(item.base_revision_at_creation > envelope.base_revision for item in orders):
        raise ValueError("baseRevisionAtCreation превышает baseRevision пакета")
    lamports = [item.lamport for item in orders]
    if any(current <= previous for previous, current in zip(lamports, lamports[1:])):
        raise ValueError("Lamport должен строго возрастать внутри пакета")
    return lamports[0], lamports[-1]
