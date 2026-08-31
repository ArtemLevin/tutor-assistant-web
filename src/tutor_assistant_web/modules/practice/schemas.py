from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompetencyPracticeState(StrictModel):
    status: Literal["active", "inactive"] = "active"
    activatedAt: datetime | None = None
    dueAt: str | None = None
    intervalStep: int = -1
    intervalDays: int = 0
    attempts: int = 0
    correct: int = 0
    streak: int = 0
    lapses: int = 0
    consecutiveLapses: int = 0
    repeatedLapse: bool = False
    hintsUsedTotal: int = 0
    lastAttemptAt: datetime | None = None
    lastRating: Literal["again", "hard", "good", "easy"] | None = None
    lastOutcome: Literal["correct", "incorrect"] | None = None
    lastExerciseSeed: str | None = None
    lastGeneratorKey: str | None = None
    lastGeneratorVersion: int = 0


class PracticeSessionItem(StrictModel):
    competencyId: str = Field(min_length=1, max_length=160)
    seed: str = Field(min_length=1, max_length=512)
    generatorKey: str = Field(min_length=1, max_length=160)
    generatorVersion: int = Field(default=1, ge=1)
    difficulty: int = Field(default=1, ge=1, le=3)
    status: Literal["pending", "answering", "awaiting-rating", "completed"] = "pending"
    attemptCount: int = Field(default=0, ge=0)
    hintsUsed: int = Field(default=0, ge=0)
    outcome: Literal["correct", "incorrect"] | None = None
    rating: Literal["again", "hard", "good", "easy"] | None = None
    startedAt: datetime | None = None
    checkedAt: datetime | None = None
    durationMs: int = Field(default=0, ge=0)
    remediation: bool = False


class PracticeSessionState(StrictModel):
    sessionId: str = Field(min_length=1, max_length=160)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    startedAt: datetime | None = None
    completedAt: datetime | None = None
    status: Literal["planned", "active", "completed"] = "planned"
    currentIndex: int = Field(default=0, ge=0)
    items: list[PracticeSessionItem] = Field(default_factory=list, max_length=100)
    exerciseIds: list[str] = Field(default_factory=list, max_length=100)
    correct: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)


class PracticeEventIn(StrictModel):
    eventVersion: Literal[2]
    eventId: str = Field(min_length=1, max_length=160)
    timestamp: datetime
    sessionId: str = Field(min_length=1, max_length=160)
    exerciseId: str = Field(min_length=1, max_length=512)
    competencyId: str = Field(min_length=1, max_length=160)
    generatorKey: str = Field(default="", max_length=160)
    generatorVersion: int = Field(default=1, ge=1)
    seed: str = Field(default="", max_length=512)
    difficulty: int = Field(default=1, ge=1, le=3)
    attemptCount: int = Field(default=0, ge=0)
    hintsUsed: int = Field(default=0, ge=0)
    outcome: Literal["correct", "incorrect"]
    rating: Literal["again", "hard", "good", "easy"]
    durationMs: int = Field(default=0, ge=0)


class PracticeStateDocument(StrictModel):
    schemaVersion: Literal[2]
    revision: int = Field(default=0, ge=0)
    clientInstanceId: str = Field(min_length=1, max_length=160)
    updatedAt: datetime
    competencies: dict[str, CompetencyPracticeState] = Field(default_factory=dict)
    sessions: dict[str, PracticeSessionState] = Field(default_factory=dict)
    events: list[PracticeEventIn] = Field(default_factory=list, max_length=200)


class EventBatchRequest(StrictModel):
    schemaVersion: Literal[1]
    clientInstanceId: str = Field(min_length=1, max_length=160)
    events: list[PracticeEventIn] = Field(min_length=1, max_length=100)


class EventBatchResponse(StrictModel):
    schemaVersion: Literal[1] = 1
    acceptedEventIds: list[str]
    duplicateEventIds: list[str]
    revision: int = Field(ge=0)
    serverTime: datetime


class StateUpdateRequest(StrictModel):
    schemaVersion: Literal[1]
    baseRevision: int = Field(ge=0)
    state: PracticeStateDocument


class StateResponse(StrictModel):
    schemaVersion: Literal[1] = 1
    revision: int = Field(ge=0)
    state: PracticeStateDocument
    serverTime: datetime


class BootstrapResponse(StrictModel):
    schemaVersion: Literal[1] = 1
    profileExists: bool
    revision: int = Field(ge=0)
    state: PracticeStateDocument | None
    serverTime: datetime
