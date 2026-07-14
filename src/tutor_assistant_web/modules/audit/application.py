from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.audit.models import AuditEvent


class AuditService:
    def __init__(self, database: Database, organization_id: str) -> None:
        self.database = database
        self.organization_id = organization_id

    def record(
        self,
        actor_user_id: str | None,
        action: str,
        entity_type: str,
        entity_id: str = "",
        details: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            organization_id=self.organization_id,
            actor_user_id=actor_user_id,
            action=action[:100],
            entity_type=entity_type[:80],
            entity_id=entity_id[:120],
            details=details or {},
        )
        with self.database.sessions() as session:
            session.add(event)
            session.commit()
        return event

    def recent(self, limit: int = 100) -> list[AuditEvent]:
        with self.database.sessions() as session:
            return list(
                session.scalars(
                    select(AuditEvent)
                    .options(selectinload(AuditEvent.actor))
                    .where(AuditEvent.organization_id == self.organization_id)
                    .order_by(AuditEvent.created_at.desc())
                    .limit(min(max(limit, 1), 500))
                )
            )
