from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from statistics import median
from typing import Any

from sqlalchemy import select

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.modules.identity.models import MembershipRole, StudentAccess
from tutor_assistant_web.modules.practice.repository import PracticeRepository
from tutor_assistant_web.modules.practice.retention import (
    RETENTION_INDEX_VERSION,
    calculate_retention_index,
)
from tutor_assistant_web.modules.practice.schemas import PracticeAnalyticsMetadataDocument
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.errors import ForbiddenError, NotFoundError

CATEGORY_PRIORITY = {
    "rebuild": 0,
    "fragile": 1,
    "watch": 2,
    "insufficient-data": 3,
    "stable": 4,
}
GAP_PREFIXES = ("missing-", "ambiguous")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 1)


def _p90(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.9))))
    return ordered[index]


def ensure_practice_analytics_access(
    database: Database,
    principal: Principal,
    student_id: str,
) -> None:
    with database.sessions() as session:
        student = session.scalar(
            select(Student.id).where(
                Student.id == student_id,
                Student.organization_id == principal.organization_id,
                Student.active.is_(True),
            )
        )
        if student is None:
            raise NotFoundError("Ученик не найден")
        if principal.role in {MembershipRole.admin.value, MembershipRole.tutor.value}:
            return
        if principal.role not in {MembershipRole.student.value, MembershipRole.parent.value}:
            raise ForbiddenError("Practice analytics is unavailable for this role")
        access = session.scalar(
            select(StudentAccess.id).where(
                StudentAccess.organization_id == principal.organization_id,
                StudentAccess.student_id == student_id,
                StudentAccess.user_id == principal.user_id,
                StudentAccess.role == principal.role,
                StudentAccess.active.is_(True),
                StudentAccess.revoked_at.is_(None),
            )
        )
        if access is None:
            raise ForbiddenError("Practice analytics access is not granted for this student")


def teacher_actions(
    *,
    mastery_level: int | None,
    retention: dict[str, Any],
    schedule: dict[str, Any],
    overdue_days: int,
    hint_dependence: float | None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    lapses = int(_number(schedule.get("lapses")))
    repeated = bool(schedule.get("repeatedLapse")) or lapses >= 2
    category = retention["category"]

    if repeated:
        actions.append(
            {
                "code": "repeated-lapse",
                "label": "Разобрать навык на ближайшем занятии",
                "reason": f"Зафиксировано lapses: {lapses}; есть повторный провал.",
            }
        )
    if overdue_days >= 7:
        actions.append(
            {
                "code": "long-overdue",
                "label": "Вернуть в обязательный короткий блок",
                "reason": f"Повторение просрочено на {overdue_days} дн.",
            }
        )
    if mastery_level is not None and mastery_level >= 3 and category in {"fragile", "rebuild"}:
        actions.append(
            {
                "code": "mastery-retention-drop",
                "label": "Проверить сохранность навыка",
                "reason": (
                    f"Mastery {mastery_level}/4 остаётся высоким, retention category = {category}."
                ),
            }
        )
    if mastery_level is not None and mastery_level <= 2 and category == "stable":
        actions.append(
            {
                "code": "positive-reassessment",
                "label": "Рассмотреть педагогическую переоценку mastery",
                "reason": (
                    f"Mastery {mastery_level}/4, при этом retrieval устойчив: retention stable."
                ),
            }
        )
    if hint_dependence is not None and hint_dependence >= 50:
        actions.append(
            {
                "code": "hint-dependence",
                "label": "Проверить самостоятельность решения",
                "reason": f"Подсказки использованы в {round(hint_dependence)}% последних попыток.",
            }
        )
    return actions


class PracticeAnalytics:
    def __init__(
        self,
        database: Database,
        organization_id: str,
        *,
        retention_index_version: int = RETENTION_INDEX_VERSION,
    ) -> None:
        self.database = database
        self.organization_id = organization_id
        self.retention_index_version = retention_index_version

    def save_metadata(
        self,
        student_id: str,
        document: PracticeAnalyticsMetadataDocument,
    ) -> dict[str, Any]:
        payload = document.model_dump(mode="json")
        with self.database.sessions() as session:
            repository = PracticeRepository(session, self.organization_id, student_id)
            row = repository.upsert_analytics_metadata(
                payload,
                schema_version=document.schemaVersion,
                source_revision=document.sourceRevision,
            )
            session.commit()
            return {
                "schemaVersion": 1,
                "accepted": True,
                "sourceRevision": row.source_revision,
                "updatedAt": _iso(row.updated_at) or datetime.now(UTC).isoformat(),
            }

    def student_report(self, student_id: str, *, today: date | None = None) -> dict[str, Any]:
        today = today or datetime.now(UTC).date()
        with self.database.sessions() as session:
            repository = PracticeRepository(session, self.organization_id, student_id)
            profile = repository.profile()
            metadata_row = repository.analytics_metadata()
            events = repository.all_events()
            student = session.scalar(
                select(Student).where(
                    Student.organization_id == self.organization_id,
                    Student.id == student_id,
                )
            )
            if student is None:
                raise NotFoundError("Ученик не найден")
            state = dict(profile.state_jsonb or {}) if profile else {}
            metadata = dict(metadata_row.metadata_jsonb or {}) if metadata_row else {}
            profile_updated_at = profile.updated_at if profile else None

        metadata_by_id = {
            item["competencyId"]: item
            for item in metadata.get("competencies", [])
            if isinstance(item, dict) and item.get("competencyId")
        }
        schedules = state.get("competencies") or {}
        event_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in events:
            event_groups[row.competency_id].append(dict(row.event_jsonb or {}))

        competency_ids = sorted(set(metadata_by_id) | set(schedules) | set(event_groups))
        competencies = [
            self._competency_report(
                competency_id,
                metadata_by_id.get(competency_id, {}),
                schedules.get(competency_id, {}),
                event_groups.get(competency_id, []),
                today=today,
            )
            for competency_id in competency_ids
        ]
        attention = sorted(
            [item for item in competencies if item["recommendedActions"]],
            key=lambda item: (
                CATEGORY_PRIORITY[item["retention"]["category"]],
                -item["overdueDays"],
                item["retention"]["index"] if item["retention"]["index"] is not None else 101,
                item["title"],
            ),
        )
        stable = sorted(
            [item for item in competencies if item["retention"]["category"] == "stable"],
            key=lambda item: (-(item["intervalDays"] or 0), item["title"]),
        )
        recent_sessions = self._recent_sessions(state)
        aggregate = self._student_aggregate(
            competencies,
            events,
            state,
            today=today,
            profile_updated_at=profile_updated_at,
        )
        return {
            "schemaVersion": 1,
            "retentionIndexVersion": self.retention_index_version,
            "student": {
                "id": student.id,
                "fullName": student.full_name,
                "grade": student.grade,
                "subject": student.subject,
            },
            "metadata": {
                "available": bool(metadata_row),
                "sourceStudentKey": metadata.get("sourceStudentKey"),
                "sourceRevision": metadata.get("sourceRevision", ""),
                "generatedAt": metadata.get("generatedAt"),
                "updatedAt": _iso(metadata_row.updated_at) if metadata_row else None,
            },
            "summary": aggregate,
            "attention": attention,
            "stable": stable,
            "competencies": competencies,
            "recentSessions": recent_sessions,
            "generatedAt": datetime.now(UTC).isoformat(),
        }

    def build_pre_lesson_practice_brief(
        self,
        student_id: str,
        *,
        today: date | None = None,
    ) -> dict[str, Any]:
        report = self.student_report(student_id, today=today)
        attention = report["attention"][:3]
        summary = report["summary"]
        mismatch = [
            item
            for item in report["competencies"]
            if any(
                action["code"] in {"mastery-retention-drop", "positive-reassessment"}
                for action in item["recommendedActions"]
            )
        ]
        warmup = [item["competencyId"] for item in attention]
        if len(warmup) < 3:
            for item in report["competencies"]:
                if (
                    item["dueStatus"] in {"overdue", "due-today"}
                    and item["competencyId"] not in warmup
                ):
                    warmup.append(item["competencyId"])
                if len(warmup) >= 3:
                    break
        return {
            "schemaVersion": 1,
            "retentionIndexVersion": self.retention_index_version,
            "studentId": student_id,
            "skillsRequiringAttention": [
                {
                    "competencyId": item["competencyId"],
                    "title": item["title"],
                    "retention": item["retention"],
                    "actions": item["recommendedActions"],
                }
                for item in attention
            ],
            "overdueCount": summary["overdueCount"],
            "repeatedLapseCount": summary["repeatedLapseCount"],
            "masteryRetentionMismatchCount": len(mismatch),
            "lastPracticeSession": report["recentSessions"][0]
            if report["recentSessions"]
            else None,
            "recommendedWarmupCompetencies": warmup[:3],
            "generatedAt": report["generatedAt"],
        }

    def _competency_report(
        self,
        competency_id: str,
        metadata: dict[str, Any],
        schedule: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        today: date,
    ) -> dict[str, Any]:
        due_at = _date(schedule.get("dueAt"))
        overdue_days = max(0, (today - due_at).days) if due_at and due_at < today else 0
        due_status = (
            "overdue"
            if overdue_days
            else "due-today"
            if due_at == today
            else "scheduled"
            if due_at
            else "unscheduled"
        )
        retention = calculate_retention_index(
            events,
            schedule,
            today=today,
            version=self.retention_index_version,
        ).as_dict()
        hints = sum(1 for event in events if int(_number(event.get("hintsUsed"))) > 0)
        hint_dependence = _percent(hints, len(events))
        first_attempt_correct = sum(
            1
            for event in events
            if event.get("outcome") == "correct" and int(_number(event.get("attemptCount"), 1)) <= 1
        )
        durations = [
            int(_number(event.get("durationMs"))) for event in events if event.get("durationMs")
        ]
        mastery = metadata.get("masteryLevel")
        actions = teacher_actions(
            mastery_level=mastery if isinstance(mastery, int) else None,
            retention=retention,
            schedule=schedule,
            overdue_days=overdue_days,
            hint_dependence=hint_dependence,
        )
        trend = []
        for end in range(3, len(events) + 1):
            result = calculate_retention_index(
                events[:end],
                schedule,
                today=today,
                version=self.retention_index_version,
            )
            trend.append(
                {
                    "eventId": events[end - 1].get("eventId"),
                    "timestamp": events[end - 1].get("timestamp"),
                    "index": result.index,
                    "category": result.category,
                }
            )
        return {
            "competencyId": competency_id,
            "title": metadata.get("title") or competency_id,
            "groupTitle": metadata.get("groupTitle") or "",
            "masteryLevel": mastery if isinstance(mastery, int) else None,
            "retention": retention,
            "dueAt": schedule.get("dueAt"),
            "dueStatus": due_status,
            "overdueDays": overdue_days,
            "intervalDays": int(_number(schedule.get("intervalDays"))),
            "attempts": int(_number(schedule.get("attempts"), len(events))),
            "correct": int(_number(schedule.get("correct"))),
            "lapses": int(_number(schedule.get("lapses"))),
            "streak": int(_number(schedule.get("streak"))),
            "repeatedLapse": bool(schedule.get("repeatedLapse")),
            "firstAttemptAccuracy": _percent(first_attempt_correct, len(events)),
            "hintDependence": hint_dependence,
            "averageDurationMs": round(sum(durations) / len(durations)) if durations else None,
            "lastOutcomes": [event.get("outcome") for event in events[-5:]],
            "trend": trend[-12:],
            "sourceLessonDate": metadata.get("sourceLessonDate"),
            "sourceLessonHref": metadata.get("sourceLessonHref"),
            "provider": metadata.get("provider") or schedule.get("lastGeneratorKey"),
            "coverageStatus": metadata.get("coverageStatus"),
            "recommendedActions": actions,
        }

    def _student_aggregate(
        self,
        competencies: list[dict[str, Any]],
        events,
        state: dict[str, Any],
        *,
        today: date,
        profile_updated_at: datetime | None,
    ) -> dict[str, Any]:
        due_today = sum(1 for item in competencies if item["dueStatus"] == "due-today")
        overdue = [item for item in competencies if item["overdueDays"] > 0]
        repeated = [item for item in competencies if item["repeatedLapse"] or item["lapses"] >= 2]
        fragile = [
            item for item in competencies if item["retention"]["category"] in {"fragile", "rebuild"}
        ]
        covered_events = [dict(row.event_jsonb or {}) for row in events]
        hint_events = sum(1 for event in covered_events if int(_number(event.get("hintsUsed"))) > 0)
        first_correct = sum(
            1
            for event in covered_events
            if event.get("outcome") == "correct" and int(_number(event.get("attemptCount"), 1)) <= 1
        )
        durations = [
            int(_number(event.get("durationMs")))
            for event in covered_events
            if event.get("durationMs")
        ]
        sessions = list((state.get("sessions") or {}).values())
        completed_sessions = sum(1 for session in sessions if session.get("status") == "completed")
        event_days = {
            row.occurred_at.date()
            for row in events
            if row.occurred_at.date() >= today.fromordinal(max(1, today.toordinal() - 29))
        }
        coverage_gaps = sum(
            1
            for item in competencies
            if isinstance(item.get("coverageStatus"), str)
            and (
                item["coverageStatus"].startswith(GAP_PREFIXES)
                or item["coverageStatus"] == "ambiguous"
            )
        )
        return {
            "dueToday": due_today,
            "overdueCount": len(overdue),
            "overdueDays": sorted(item["overdueDays"] for item in overdue),
            "repeatedLapseCount": len(repeated),
            "fragileCount": len(fragile),
            "hintDependence": _percent(hint_events, len(covered_events)),
            "firstAttemptAccuracy": _percent(first_correct, len(covered_events)),
            "completionRate": _percent(completed_sessions, len(sessions)),
            "medianDurationMs": round(median(durations)) if durations else None,
            "p90DurationMs": _p90(durations),
            "practiceDaysLast30": len(event_days),
            "generatorCoverageGaps": coverage_gaps,
            "lastSuccessfulSync": _iso(profile_updated_at),
            "eventCount": len(covered_events),
        }

    @staticmethod
    def _recent_sessions(state: dict[str, Any]) -> list[dict[str, Any]]:
        sessions = list((state.get("sessions") or {}).values())
        sessions.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
        return [
            {
                "sessionId": session.get("sessionId"),
                "date": session.get("date"),
                "status": session.get("status"),
                "correct": session.get("correct", 0),
                "total": session.get("total", 0),
                "completedAt": session.get("completedAt"),
            }
            for session in sessions[:10]
        ]
