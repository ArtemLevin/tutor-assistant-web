"""Compatibility exports for code written against the pilot layout.

New code imports models from their owning business module.
"""

from tutor_assistant_web.modules.classroom.models import RecordingAsset
from tutor_assistant_web.modules.materials.models import (
    JobStatus,
    MaterialArtifact,
    ProcessingJob,
)
from tutor_assistant_web.modules.scheduling.models import Lesson, LessonStatus
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.models import new_id, utcnow

__all__ = [
    "JobStatus",
    "Lesson",
    "LessonStatus",
    "MaterialArtifact",
    "ProcessingJob",
    "RecordingAsset",
    "Student",
    "new_id",
    "utcnow",
]
