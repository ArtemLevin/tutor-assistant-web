from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from tutor_assistant_web.shared.board_contracts.board_command_envelope_1_6_schema import (
    BoardCommand as BoardCommand16,
)
from tutor_assistant_web.shared.board_contracts.board_command_envelope_1_6_schema import (
    BoardCommandEnvelope16,
)
from tutor_assistant_web.shared.board_contracts.board_command_envelope_1_6_schema import (
    OrderedBoardCommand as OrderedBoardCommand16,
)
from tutor_assistant_web.shared.board_contracts.board_command_envelope_schema import (
    BoardCommand as BoardCommand17,
)
from tutor_assistant_web.shared.board_contracts.board_command_envelope_schema import (
    BoardCommandEnvelope17,
    Identifier,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_1_4_schema import (
    BoardSnapshot14,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_1_5_schema import (
    BoardSnapshot15,
)
from tutor_assistant_web.shared.board_contracts.board_snapshot_schema import BoardSnapshot16


class LegacyBoardCommandEnvelope(BaseModel):
    """Strict reader for historical Board envelope versions."""

    model_config = ConfigDict(extra="forbid")

    actor_id: Identifier = Field(alias="actorId")
    base_revision: int = Field(alias="baseRevision", ge=0)
    commands: list[BoardCommand16] = Field(min_length=1, max_length=100)
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


class LegacyOrderedBoardCommandEnvelope(BaseModel):
    """Reader for the ordered Board envelope introduced in version 1.3."""

    model_config = ConfigDict(extra="forbid")

    actor_id: Identifier = Field(alias="actorId")
    base_revision: int = Field(alias="baseRevision", ge=0)
    commands: list[OrderedBoardCommand16] = Field(min_length=1, max_length=100)
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
    schema_version: Literal["1.3", "1.4"] = Field(alias="schemaVersion")

    @model_validator(mode="after")
    def reject_version_14_commands(self) -> LegacyOrderedBoardCommandEnvelope:
        unsupported = {
            "core.solid-3d-learning.act",
            "core.solid-3d-learning.complete",
            "core.solid-3d-learning.remove",
            "core.solid-3d-learning.reset",
            "core.solid-3d-learning.start",
        }
        if self.schema_version == "1.3" and any(
            item.command.root.kind in unsupported for item in self.commands
        ):
            raise ValueError("Команды обучения 3D требуют schemaVersion 1.4")
        return self


class PreviousOrderedBoardCommandEnvelope(BaseModel):
    """Strict reader for the origin-aware Board envelope version 1.5."""

    model_config = ConfigDict(extra="forbid")

    actor_id: Identifier = Field(alias="actorId")
    base_revision: int = Field(alias="baseRevision", ge=0)
    commands: list[OrderedBoardCommand16] = Field(min_length=1, max_length=100)
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
    origin_id: Identifier = Field(alias="originId")
    schema_version: Literal["1.5"] = Field(alias="schemaVersion")


type BoardCommandEnvelope = Annotated[
    LegacyBoardCommandEnvelope
    | LegacyOrderedBoardCommandEnvelope
    | PreviousOrderedBoardCommandEnvelope
    | BoardCommandEnvelope16
    | BoardCommandEnvelope17,
    Field(discriminator="schema_version"),
]


class BoardCommandEnvelopeInput(RootModel[BoardCommandEnvelope]):
    """Runtime boundary shared by routes and persistence."""

    @model_validator(mode="after")
    def validate_ordering(self) -> BoardCommandEnvelopeInput:
        envelope_lamport_range(self.root)
        return self


type CompatibleBoardCommand = BoardCommand16 | BoardCommand17


def envelope_commands(envelope: BoardCommandEnvelope) -> list[CompatibleBoardCommand]:
    if isinstance(
        envelope,
        (
            LegacyOrderedBoardCommandEnvelope,
            PreviousOrderedBoardCommandEnvelope,
            BoardCommandEnvelope16,
            BoardCommandEnvelope17,
        ),
    ):
        return [item.command for item in envelope.commands]
    return list(envelope.commands)


def envelope_actor_ids(envelope: BoardCommandEnvelope) -> list[str]:
    return [command.root.actor_id.root for command in envelope_commands(envelope)]


def envelope_base_revisions(envelope: BoardCommandEnvelope) -> list[int]:
    if isinstance(
        envelope,
        (
            LegacyOrderedBoardCommandEnvelope,
            PreviousOrderedBoardCommandEnvelope,
            BoardCommandEnvelope16,
            BoardCommandEnvelope17,
        ),
    ):
        return [item.order.base_revision_at_creation for item in envelope.commands]
    return [envelope.base_revision for _ in envelope.commands]


def envelope_lamport_range(
    envelope: BoardCommandEnvelope,
) -> tuple[int, int] | None:
    """Return the actor-local Lamport range carried by an ordered envelope."""

    if not isinstance(
        envelope,
        (
            LegacyOrderedBoardCommandEnvelope,
            PreviousOrderedBoardCommandEnvelope,
            BoardCommandEnvelope16,
            BoardCommandEnvelope17,
        ),
    ):
        return None

    orders = [item.order for item in envelope.commands]
    if any(item.base_revision_at_creation > envelope.base_revision for item in orders):
        raise ValueError("baseRevisionAtCreation превышает baseRevision пакета")

    lamports = [item.lamport for item in orders]
    if any(current <= previous for previous, current in zip(lamports, lamports[1:], strict=False)):
        raise ValueError("Lamport должен строго возрастать внутри пакета")
    return lamports[0], lamports[-1]


def envelope_origin_id(envelope: BoardCommandEnvelope) -> str | None:
    if isinstance(
        envelope,
        (
            PreviousOrderedBoardCommandEnvelope,
            BoardCommandEnvelope16,
            BoardCommandEnvelope17,
        ),
    ):
        return envelope.origin_id.root
    return None


@dataclass(frozen=True)
class MediaAssetReference:
    asset_id: str
    byte_size: int
    content_sha256: str
    file_name: str
    intrinsic_height: int
    intrinsic_width: int
    mime_type: str


def media_asset_references(value: object) -> list[MediaAssetReference]:
    if isinstance(value, BaseModel):
        return media_asset_references(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, dict):
        if value.get("kind") == "media.asset":
            intrinsic = value["intrinsicSize"]
            return [
                MediaAssetReference(
                    asset_id=str(value["assetId"]),
                    byte_size=int(value["byteSize"]),
                    content_sha256=str(value["contentSha256"]),
                    file_name=str(value["fileName"]),
                    intrinsic_height=int(intrinsic["height"]),
                    intrinsic_width=int(intrinsic["width"]),
                    mime_type=str(value["mimeType"]),
                )
            ]
        references: list[MediaAssetReference] = []
        for item in value.values():
            references.extend(media_asset_references(item))
        return references
    if isinstance(value, (list, tuple)):
        references = []
        for item in value:
            references.extend(media_asset_references(item))
        return references
    return []


def envelope_media_asset_references(
    envelope: BoardCommandEnvelope,
) -> list[MediaAssetReference]:
    return media_asset_references(envelope)


def snapshot_media_asset_references(
    snapshot: BoardSnapshotContract,
) -> list[MediaAssetReference]:
    return media_asset_references(snapshot.document)


type BoardSnapshotContract = Annotated[
    BoardSnapshot14 | BoardSnapshot15 | BoardSnapshot16,
    Field(discriminator="schema_version"),
]


class BoardSnapshotInput(RootModel[BoardSnapshotContract]):
    """Strict rolling-upgrade reader for BoardSnapshot 1.4, 1.5 and 1.6."""
