"""Tenant-scoped immutable audit log."""

from tutor_assistant_web.modules.audit.application import AuditService
from tutor_assistant_web.modules.audit.models import AuditEvent

__all__ = ["AuditEvent", "AuditService"]
