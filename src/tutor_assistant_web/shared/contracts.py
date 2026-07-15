from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol


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

    def healthcheck(self) -> None: ...


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
class DocumentBuildRequest:
    title: str
    evidence: dict[str, Any]
    materials: list[GeneratedArtifact]


@dataclass(frozen=True)
class DocumentOutput:
    kind: str
    filename: str
    media_type: str
    content: bytes


@dataclass(frozen=True)
class DocumentBuildResult:
    outputs: list[DocumentOutput]
    engine: str
    log: str = ""


class DocumentEngine(Protocol):
    name: str

    def build(self, request: DocumentBuildRequest) -> DocumentBuildResult: ...


@dataclass(frozen=True)
class StoredArtifact:
    key: str
    sha256: str
    size: int
    media_type: str


class ArtifactStorage(Protocol):
    name: str

    def put(self, key: str, content: bytes, media_type: str) -> StoredArtifact: ...

    def put_stream(
        self,
        key: str,
        stream: BinaryIO,
        media_type: str,
        *,
        expected_sha256: str | None = None,
        max_bytes: int | None = None,
    ) -> StoredArtifact: ...

    def read(self, key: str) -> bytes: ...

    def iter_bytes(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]: ...

    def delete(self, key: str) -> None: ...

    def stat(self, key: str) -> StoredArtifact: ...

    def healthcheck(self) -> None: ...


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

    def enqueue_lesson_processing(self, job_id: str, queue: str = "materials") -> None: ...

    def enqueue_outbox_delivery(self, event_id: str, lease_token: str) -> None: ...


class OutboxEventHandler(Protocol):
    def handles(self, topic: str) -> bool: ...

    def handle(self, topic: str, organization_id: str, payload: dict[str, Any]) -> None: ...
