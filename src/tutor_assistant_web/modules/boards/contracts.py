from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from tutor_assistant_web.shared.board_contracts.board_command_envelope_schema import (
    BoardCommand,
    BoardCommandEnvelope13,
    Identifier,
)


class LegacyBoardCommandEnvelope(BaseModel):
    """Strict reader for historical Board envelope versions."""

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


type BoardCommandEnvelope = Annotated[
    LegacyBoardCommandEnvelope | BoardCommandEnvelope13,
    Field(discriminator="schema_version"),
]


class BoardCommandEnvelopeInput(RootModel[BoardCommandEnvelope]):
    """Runtime boundary shared by routes and persistence."""

    @model_validator(mode="after")
    def validate_ordering(self) -> BoardCommandEnvelopeInput:
        envelope_lamport_range(self.root)
        return self


def envelope_commands(envelope: BoardCommandEnvelope) -> list[BoardCommand]:
    if isinstance(envelope, BoardCommandEnvelope13):
        return [item.command for item in envelope.commands]
    return list(envelope.commands)


def envelope_actor_ids(envelope: BoardCommandEnvelope) -> list[str]:
    return [command.root.actor_id.root for command in envelope_commands(envelope)]


def envelope_base_revisions(envelope: BoardCommandEnvelope) -> list[int]:
    if isinstance(envelope, BoardCommandEnvelope13):
        return [item.order.base_revision_at_creation for item in envelope.commands]
    return [envelope.base_revision for _ in envelope.commands]


def envelope_lamport_range(
    envelope: BoardCommandEnvelope,
) -> tuple[int, int] | None:
    """Return the actor-local Lamport range carried by an ordered envelope."""

    if not isinstance(envelope, BoardCommandEnvelope13):
        return None

    orders = [item.order for item in envelope.commands]
    if any(item.base_revision_at_creation > envelope.base_revision for item in orders):
        raise ValueError("baseRevisionAtCreation превышает baseRevision пакета")

    lamports = [item.lamport for item in orders]
    if any(current <= previous for previous, current in zip(lamports, lamports[1:], strict=False)):
        raise ValueError("Lamport должен строго возрастать внутри пакета")
    return lamports[0], lamports[-1]
