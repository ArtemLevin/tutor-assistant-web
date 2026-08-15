from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

BoardCapability = Literal[
    "board.read",
    "board.write",
    "board.snapshot.write",
    "collaboration.connect",
    "board.export",
    "board.history.read",
    "board.invites.manage",
    "board.archive",
    "board.delete",
]


class _StandaloneContextBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["1.0"] = Field(alias="schemaVersion")
    actor_id: str = Field(alias="actorId", min_length=1, max_length=128)
    board_id: str = Field(alias="boardId", min_length=1, max_length=128)
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    capabilities: list[BoardCapability] = Field(min_length=1, max_length=9)
    csrf_token: str = Field(alias="csrfToken", min_length=8, max_length=512)
    cache_scope_id: str = Field(alias="cacheScopeId", min_length=8, max_length=512)
    access_epoch: str = Field(alias="accessEpoch", min_length=8, max_length=512)

    @model_validator(mode="after")
    def validate_capability_invariants(self) -> _StandaloneContextBase:
        capabilities = self.capabilities
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("Board capabilities must not contain duplicates")
        if "board.read" not in capabilities:
            raise ValueError("Every standalone board context requires board.read")
        if "board.snapshot.write" in capabilities and "board.write" not in capabilities:
            raise ValueError("board.snapshot.write requires board.write")
        if "collaboration.connect" in capabilities and "board.read" not in capabilities:
            raise ValueError("collaboration.connect requires board.read")
        return self


class TeacherBoardAccessContext(_StandaloneContextBase):
    principal_type: Literal["teacher"] = Field(alias="principalType")
    role: Literal["admin", "tutor"]
    organization_id: str = Field(alias="organizationId", min_length=1, max_length=128)
    user_id: str = Field(alias="userId", min_length=1, max_length=128)


class GuestBoardAccessContext(_StandaloneContextBase):
    principal_type: Literal["guest"] = Field(alias="principalType")
    role: Literal["student"]

    @model_validator(mode="after")
    def reject_management_capabilities(self) -> GuestBoardAccessContext:
        forbidden: set[str] = {
            "board.export",
            "board.history.read",
            "board.invites.manage",
            "board.archive",
            "board.delete",
        }
        leaked = forbidden.intersection(self.capabilities)
        if leaked:
            raise ValueError(f"Guest context cannot grant {sorted(leaked)[0]}")
        return self


class BoardAccessContextInput(
    RootModel[TeacherBoardAccessContext | GuestBoardAccessContext]
):
    """Strict B0 contract reader; not wired into runtime routes until B1/B2."""
