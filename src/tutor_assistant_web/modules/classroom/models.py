from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tutor_assistant_web.db import Base
from tutor_assistant_web.shared.models import new_id, utcnow

if TYPE_CHECKING:
    from tutor_assistant_web.modules.scheduling.models import Lesson


class RecordingAsset(Base):
    __tablename__ = "recording_assets"
    __table_args__ = (UniqueConstraint("record_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[str] = mapped_column(ForeignKey("lessons.id"), index=True)
    record_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(32), default="processing")
    playback_url: Mapped[str] = mapped_column(Text, default="")
    raw_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lesson: Mapped[Lesson] = relationship("Lesson", back_populates="recordings")
