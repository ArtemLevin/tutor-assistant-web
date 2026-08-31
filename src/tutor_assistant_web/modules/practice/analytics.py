from __future__ import annotations

from sqlalchemy import func, select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.practice.models import PracticeEvent


class PracticeAnalytics:
    """Query-only foundation for Track E teacher analytics."""

    def __init__(self, database: Database, organization_id: str) -> None:
        self.database = database
        self.organization_id = organization_id

    def student_summary(self, student_id: str) -> dict[str, int]:
        with self.database.sessions() as session:
            rows = session.execute(
                select(PracticeEvent.outcome, func.count(PracticeEvent.id))
                .where(
                    PracticeEvent.organization_id == self.organization_id,
                    PracticeEvent.student_id == student_id,
                )
                .group_by(PracticeEvent.outcome)
            ).all()
        counts = {str(outcome): int(count) for outcome, count in rows}
        return {
            "events": sum(counts.values()),
            "correct": counts.get("correct", 0),
            "incorrect": counts.get("incorrect", 0),
        }
