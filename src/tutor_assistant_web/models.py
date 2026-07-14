"""Compatibility exports for code written against the pilot layout.

New code imports models from their owning business module.
"""

from tutor_assistant_web.modules.audit.models import AuditEvent
from tutor_assistant_web.modules.automation.models import (
    LessonTranscript,
    OutboxEvent,
    WebhookReceipt,
)
from tutor_assistant_web.modules.classroom.models import RecordingAsset
from tutor_assistant_web.modules.identity.models import (
    Invitation,
    Membership,
    Organization,
    StudentAccess,
    User,
)
from tutor_assistant_web.modules.materials.models import (
    ArtifactStatus,
    ArtifactVersion,
    BuildLog,
    EvidenceBundle,
    GenerationRun,
    GenerationStatus,
    JobStatus,
    MaterialArtifact,
    ProcessingJob,
)
from tutor_assistant_web.modules.portal.models import MaterialDelivery, UserNotification
from tutor_assistant_web.modules.scheduling.models import Lesson, LessonStatus
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.shared.models import new_id, utcnow

__all__ = [
    "JobStatus",
    "ArtifactStatus",
    "ArtifactVersion",
    "BuildLog",
    "EvidenceBundle",
    "GenerationRun",
    "GenerationStatus",
    "Lesson",
    "LessonStatus",
    "MaterialArtifact",
    "Membership",
    "Organization",
    "ProcessingJob",
    "RecordingAsset",
    "Student",
    "StudentAccess",
    "MaterialDelivery",
    "UserNotification",
    "User",
    "Invitation",
    "AuditEvent",
    "LessonTranscript",
    "OutboxEvent",
    "WebhookReceipt",
    "new_id",
    "utcnow",
]
