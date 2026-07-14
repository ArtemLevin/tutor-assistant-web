from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CreateConference:
    meeting_id: str
    name: str
    attendee_password: str
    moderator_password: str
    record: bool
    recording_ready_url: str = ""


@dataclass(frozen=True)
class JoinConference:
    meeting_id: str
    full_name: str
    password: str
    user_id: str
    role: str
    logout_url: str
    demo_url: str


@dataclass(frozen=True)
class ConferenceRecording:
    record_id: str
    state: str
    playback_url: str
    metadata: dict[str, Any]


class ConferenceProvider(Protocol):
    name: str
    is_demo: bool

    def create_room(self, command: CreateConference) -> None: ...

    def join_url(self, command: JoinConference) -> str: ...

    def end_room(self, meeting_id: str) -> None: ...

    def recordings(self, meeting_id: str) -> list[ConferenceRecording]: ...


@dataclass(frozen=True)
class GeneratedArtifact:
    kind: str
    title: str
    content: str
    source_url: str = ""


class MaterialGenerator(Protocol):
    name: str

    def generate(self, evidence: dict[str, Any]) -> list[GeneratedArtifact]: ...


@dataclass(frozen=True)
class TranscriptionSource:
    record_id: str
    media_url: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TranscriptionSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    segments: list[TranscriptionSegment]
    provider: str
    model: str


class TranscriptionProvider(Protocol):
    name: str

    def transcribe(self, source: TranscriptionSource) -> TranscriptionResult: ...


class JobDispatcher(Protocol):
    name: str

    def enqueue_lesson_processing(self, job_id: str) -> None: ...
