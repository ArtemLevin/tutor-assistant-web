from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tutor_assistant_web.modules.practice.models import (
    PracticeAnalyticsMetadata,
    PracticeEvent,
    PracticeProfile,
)


class PracticeRepository:
    def __init__(self, session: Session, organization_id: str, student_id: str) -> None:
        self.session = session
        self.organization_id = organization_id
        self.student_id = student_id

    def profile(self) -> PracticeProfile | None:
        return self.session.scalar(
            select(PracticeProfile).where(
                PracticeProfile.organization_id == self.organization_id,
                PracticeProfile.student_id == self.student_id,
            )
        )

    def create_profile(self, state: dict, schema_version: int = 2) -> PracticeProfile:
        profile = PracticeProfile(
            organization_id=self.organization_id,
            student_id=self.student_id,
            schema_version=schema_version,
            revision=0,
            state_jsonb=state,
        )
        self.session.add(profile)
        self.session.flush()
        return profile

    def analytics_metadata(self) -> PracticeAnalyticsMetadata | None:
        return self.session.scalar(
            select(PracticeAnalyticsMetadata).where(
                PracticeAnalyticsMetadata.organization_id == self.organization_id,
                PracticeAnalyticsMetadata.student_id == self.student_id,
            )
        )

    def upsert_analytics_metadata(
        self,
        payload: dict,
        *,
        schema_version: int = 1,
        source_revision: str = "",
    ) -> PracticeAnalyticsMetadata:
        row = self.analytics_metadata()
        if row is None:
            row = PracticeAnalyticsMetadata(
                organization_id=self.organization_id,
                student_id=self.student_id,
            )
            self.session.add(row)
        row.schema_version = schema_version
        row.source_revision = source_revision
        row.metadata_jsonb = payload
        self.session.flush()
        return row

    def all_events(self) -> list[PracticeEvent]:
        return list(
            self.session.scalars(
                select(PracticeEvent)
                .where(
                    PracticeEvent.organization_id == self.organization_id,
                    PracticeEvent.student_id == self.student_id,
                )
                .order_by(PracticeEvent.occurred_at, PracticeEvent.id)
            )
        )

    def events_by_ids(self, event_ids: list[str]) -> dict[str, PracticeEvent]:
        if not event_ids:
            return {}
        rows = self.session.scalars(
            select(PracticeEvent).where(PracticeEvent.event_id.in_(event_ids))
        )
        return {row.event_id: row for row in rows}

    def add_event(
        self,
        *,
        event_id: str,
        event_version: int,
        client_instance_id: str,
        competency_id: str,
        outcome: str,
        occurred_at,
        event_jsonb: dict,
    ) -> PracticeEvent:
        row = PracticeEvent(
            organization_id=self.organization_id,
            student_id=self.student_id,
            event_id=event_id,
            event_version=event_version,
            client_instance_id=client_instance_id,
            competency_id=competency_id,
            outcome=outcome,
            occurred_at=occurred_at,
            event_jsonb=event_jsonb,
        )
        self.session.add(row)
        return row
