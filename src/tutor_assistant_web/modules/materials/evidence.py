from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tutor_assistant_web.modules.scheduling.models import Lesson


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LessonEvidence(EvidenceModel):
    id: str
    title: str
    topic: str
    started_at: str
    ended_at: str
    tutor_notes: str


class StudentEvidence(EvidenceModel):
    id: str
    full_name: str
    grade: str
    subject: str
    goal: str


class RecordingEvidence(EvidenceModel):
    record_id: str
    state: str
    playback_url: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class TranscriptEvidence(EvidenceModel):
    status: str
    language: str
    text: str
    segments: list[dict[str, Any]] = Field(default_factory=list)


class LessonEvidenceBundleV1(EvidenceModel):
    schema_version: Literal["1.0"] = "1.0"
    organization_id: str
    lesson: LessonEvidence
    student: StudentEvidence
    recordings: list[RecordingEvidence]
    transcript: TranscriptEvidence | None
    requested_artifacts: list[str]

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def build_evidence_bundle(lesson: Lesson) -> LessonEvidenceBundleV1:
    return LessonEvidenceBundleV1(
        organization_id=lesson.organization_id or "",
        lesson=LessonEvidence(
            id=lesson.id,
            title=lesson.title,
            topic=lesson.topic or "",
            started_at=lesson.starts_at.isoformat(),
            ended_at=lesson.ends_at.isoformat(),
            tutor_notes=lesson.tutor_notes or "",
        ),
        student=StudentEvidence(
            id=lesson.student.id,
            full_name=lesson.student.full_name,
            grade=lesson.student.grade or "",
            subject=lesson.student.subject or "",
            goal=lesson.student.goal or "",
        ),
        recordings=[
            RecordingEvidence(
                record_id=item.record_id,
                state=item.state,
                playback_url=item.playback_url,
                metadata=item.raw_metadata or {},
            )
            for item in sorted(lesson.recordings, key=lambda recording: recording.record_id)
        ],
        transcript=(
            TranscriptEvidence(
                status=lesson.transcript.status,
                language=lesson.transcript.language,
                text=lesson.transcript.text,
                segments=lesson.transcript.segments or [],
            )
            if lesson.transcript is not None
            else None
        ),
        requested_artifacts=["lesson_summary", "homework", "parent_report"],
    )
