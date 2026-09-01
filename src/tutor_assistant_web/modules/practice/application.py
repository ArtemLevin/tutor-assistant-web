from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.modules.identity.models import MembershipRole, StudentAccess
from tutor_assistant_web.modules.practice.repository import PracticeRepository
from tutor_assistant_web.modules.practice.schemas import (
    BootstrapResponse,
    EventBatchRequest,
    EventBatchResponse,
    PracticeStateDocument,
    StateResponse,
    StateUpdateRequest,
)
from tutor_assistant_web.shared.errors import ConflictError, ForbiddenError, NotFoundError


class PracticeRevisionConflict(ConflictError):
    def __init__(self, response: StateResponse) -> None:
        super().__init__("PracticeState revision conflict")
        self.response = response


class PracticeSyncService:
    def __init__(self, database: Database, principal: Principal, audit_service=None) -> None:
        self.database = database
        self.principal = principal
        self.audit_service = audit_service

    def _student_id(self, session) -> str:
        if self.principal.role != MembershipRole.student.value:
            raise ForbiddenError("Practice sync is available only to an authenticated student")
        accesses = list(
            session.scalars(
                select(StudentAccess).where(
                    StudentAccess.organization_id == self.principal.organization_id,
                    StudentAccess.user_id == self.principal.user_id,
                    StudentAccess.role == MembershipRole.student.value,
                    StudentAccess.active.is_(True),
                    StudentAccess.revoked_at.is_(None),
                )
            )
        )
        if len(accesses) != 1:
            raise ForbiddenError("Student account must be bound to exactly one active student")
        return accesses[0].student_id

    def student_id(self) -> str:
        with self.database.sessions() as session:
            return self._student_id(session)

    @staticmethod
    def _server_time() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _state(profile) -> PracticeStateDocument:
        payload = dict(profile.state_jsonb or {})
        payload["revision"] = int(profile.revision)
        return PracticeStateDocument.model_validate(payload)

    @staticmethod
    def _merge_events(existing: list[dict], incoming: list[dict]) -> list[dict]:
        by_id = {item.get("eventId"): item for item in existing if item.get("eventId")}
        for item in incoming:
            event_id = item.get("eventId")
            if event_id:
                by_id[event_id] = item
        return list(by_id.values())[-200:]

    def bootstrap(self) -> BootstrapResponse:
        with self.database.sessions() as session:
            student_id = self._student_id(session)
            profile = PracticeRepository(
                session, self.principal.organization_id, student_id
            ).profile()
            if profile is None:
                return BootstrapResponse(
                    profileExists=False, revision=0, state=None, serverTime=self._server_time()
                )
            return BootstrapResponse(
                profileExists=True,
                revision=int(profile.revision),
                state=self._state(profile),
                serverTime=self._server_time(),
            )

    def state(self) -> StateResponse:
        with self.database.sessions() as session:
            student_id = self._student_id(session)
            profile = PracticeRepository(
                session, self.principal.organization_id, student_id
            ).profile()
            if profile is None:
                raise NotFoundError("Practice profile does not exist")
            return StateResponse(
                revision=int(profile.revision),
                state=self._state(profile),
                serverTime=self._server_time(),
            )

    def update_state(self, request: StateUpdateRequest) -> StateResponse:
        created = False
        with self.database.sessions() as session:
            student_id = self._student_id(session)
            repository = PracticeRepository(session, self.principal.organization_id, student_id)
            profile = repository.profile()
            if profile is None:
                if request.baseRevision != 0:
                    raise PracticeRevisionConflict(
                        StateResponse(
                            revision=0,
                            state=request.state.model_copy(update={"revision": 0}),
                            serverTime=self._server_time(),
                        )
                    )
                profile = repository.create_profile(request.state.model_dump(mode="json"))
                created = True
            elif int(profile.revision) != request.baseRevision:
                raise PracticeRevisionConflict(
                    StateResponse(
                        revision=int(profile.revision),
                        state=self._state(profile),
                        serverTime=self._server_time(),
                    )
                )

            existing = dict(profile.state_jsonb or {})
            incoming = request.state.model_dump(mode="json")
            incoming["events"] = self._merge_events(
                list(existing.get("events") or []), list(incoming.get("events") or [])
            )
            next_revision = int(profile.revision) + 1
            incoming["revision"] = next_revision
            profile.schema_version = 2
            profile.revision = next_revision
            profile.state_jsonb = incoming
            session.commit()
            response = StateResponse(
                revision=next_revision,
                state=PracticeStateDocument.model_validate(incoming),
                serverTime=self._server_time(),
            )
        self._audit(
            student_id,
            "practice.profile.created" if created else "practice.profile.updated",
            {"revision": response.revision},
        )
        return response

    def ingest_events(self, request: EventBatchRequest) -> EventBatchResponse:
        accepted: list[str] = []
        duplicates: list[str] = []
        with self.database.sessions() as session:
            student_id = self._student_id(session)
            repository = PracticeRepository(session, self.principal.organization_id, student_id)
            profile = repository.profile()
            if profile is None:
                raise NotFoundError("Create the practice profile before uploading events")
            incoming = [event.model_dump(mode="json") for event in request.events]
            existing = repository.events_by_ids([event["eventId"] for event in incoming])
            for event_model, payload in zip(request.events, incoming, strict=True):
                prior = existing.get(event_model.eventId)
                if prior is not None:
                    if (
                        prior.organization_id != self.principal.organization_id
                        or prior.student_id != student_id
                        or prior.event_jsonb != payload
                    ):
                        raise ConflictError(f"eventId conflict: {event_model.eventId}")
                    duplicates.append(event_model.eventId)
                    continue
                repository.add_event(
                    event_id=event_model.eventId,
                    event_version=event_model.eventVersion,
                    client_instance_id=request.clientInstanceId,
                    competency_id=event_model.competencyId,
                    outcome=event_model.outcome,
                    occurred_at=event_model.timestamp,
                    event_jsonb=payload,
                )
                accepted.append(event_model.eventId)
            if accepted:
                state = dict(profile.state_jsonb or {})
                state["events"] = self._merge_events(
                    list(state.get("events") or []),
                    [payload for payload in incoming if payload["eventId"] in accepted],
                )
                next_revision = int(profile.revision) + 1
                state["revision"] = next_revision
                profile.revision = next_revision
                profile.state_jsonb = state
            session.commit()
            revision = int(profile.revision)
        if accepted:
            self._audit(
                student_id,
                "practice.events.ingested",
                {"accepted": len(accepted), "duplicates": len(duplicates), "revision": revision},
            )
        return EventBatchResponse(
            acceptedEventIds=accepted,
            duplicateEventIds=duplicates,
            revision=revision,
            serverTime=self._server_time(),
        )

    def _audit(self, student_id: str, action: str, metadata: dict) -> None:
        if self.audit_service is not None:
            self.audit_service.record(
                self.principal.user_id,
                action,
                "student",
                student_id,
                metadata,
            )
