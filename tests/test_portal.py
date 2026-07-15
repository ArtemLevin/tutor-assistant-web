from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tutor_assistant_web.bootstrap.app_factory import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.audit.models import AuditEvent
from tutor_assistant_web.modules.automation.application import OutboxService
from tutor_assistant_web.modules.automation.models import OutboxEvent, OutboxStatus
from tutor_assistant_web.modules.identity.application import IdentityService, Principal
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    Organization,
    StudentAccess,
    User,
)
from tutor_assistant_web.modules.materials.models import (
    ArtifactStatus,
    ArtifactVersion,
    EvidenceBundle,
    GenerationRun,
    GenerationStatus,
    ProcessingJob,
)
from tutor_assistant_web.modules.portal.application import (
    PortalEventHandler,
    PortalService,
    PublicationService,
)
from tutor_assistant_web.modules.portal.models import MaterialDelivery, UserNotification
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.providers.documents import LocalArtifactStorage
from tutor_assistant_web.shared.errors import NotFoundError, ValidationError

ORG_ID = DEFAULT_ORGANIZATION_ID


class UnusedDispatcher:
    name = "unused"

    def enqueue_lesson_processing(self, job_id: str, queue: str = "materials") -> None:
        raise AssertionError(f"unexpected lesson job: {job_id}")

    def enqueue_outbox_delivery(self, event_id: str, lease_token: str) -> None:
        raise AssertionError(f"unexpected delivery: {event_id}")


class UnavailableDeliveryDispatcher:
    name = "celery"

    def enqueue_lesson_processing(self, job_id: str, queue: str = "materials") -> None:
        raise AssertionError(f"unexpected lesson job: {job_id}")

    def enqueue_outbox_delivery(self, event_id: str, lease_token: str) -> None:
        raise ConnectionError("delivery worker broker is unavailable")


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def setup_portal_data(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'portal.db'}")
    database.migrate()
    settings = Settings(
        seed_demo_data=False,
        bootstrap_admin_password="admin-password",
        database_url=f"sqlite:///{tmp_path / 'portal.db'}",
        artifact_storage_root=str(tmp_path / "artifacts"),
        app_secret_key="test-secret",
    )
    identity = IdentityService(database)
    identity.bootstrap(settings)
    admin = identity.authenticate("admin@localhost", "admin-password")
    assert admin is not None
    with database.sessions() as session:
        student = Student(
            organization_id=ORG_ID,
            full_name="Мария Иванова",
            grade="8 класс",
            subject="Математика",
        )
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=ORG_ID,
            student_id=student.id,
            title="Алгебра",
            topic="Квадратные уравнения",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id=f"meeting-{student.id}",
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.flush()
        job = ProcessingJob(organization_id=ORG_ID, lesson_id=lesson.id)
        session.add(job)
        evidence = EvidenceBundle(
            organization_id=ORG_ID,
            lesson_id=lesson.id,
            content_hash="a" * 64,
            payload={"schema_version": "1.0"},
        )
        session.add(evidence)
        session.flush()
        run = GenerationRun(
            organization_id=ORG_ID,
            lesson_id=lesson.id,
            job_id=job.id,
            evidence_bundle_id=evidence.id,
            idempotency_key="b" * 64,
            status=GenerationStatus.approved.value,
            approved_by=admin.user_id,
        )
        session.add(run)
        session.flush()
        storage = LocalArtifactStorage(settings.artifact_storage_root)
        pdf = storage.put(
            f"{ORG_ID}/{lesson.id}/{run.id}/v1/material.pdf", b"%PDF-test", "application/pdf"
        )
        web = storage.put(
            f"{ORG_ID}/{lesson.id}/{run.id}/v1/material.html",
            b"<!doctype html><title>Lesson</title><h1>Material</h1>",
            "text/html; charset=utf-8",
        )
        artifacts = [
            ArtifactVersion(
                organization_id=ORG_ID,
                lesson_id=lesson.id,
                generation_run_id=run.id,
                kind="pdf",
                filename="material.pdf",
                media_type=pdf.media_type,
                storage_key=pdf.key,
                sha256=pdf.sha256,
                size=pdf.size,
                status=ArtifactStatus.approved.value,
            ),
            ArtifactVersion(
                organization_id=ORG_ID,
                lesson_id=lesson.id,
                generation_run_id=run.id,
                kind="html",
                filename="material.html",
                media_type=web.media_type,
                storage_key=web.key,
                sha256=web.sha256,
                size=web.size,
                status=ArtifactStatus.approved.value,
            ),
        ]
        session.add_all(artifacts)
        session.commit()
        artifact_ids = {item.kind: item.id for item in artifacts}
    invited = identity.create_invitation(
        ORG_ID,
        admin.user_id,
        "parent@example.test",
        MembershipRole.parent.value,
        24,
        student_id=student.id,
    )
    parent = identity.accept_invitation(invited.token, "Родитель", "strong-password")
    return database, settings, identity, admin, parent, student, run, artifact_ids


def dispatch_publications(database: Database) -> dict[str, int]:
    return OutboxService(
        database,
        UnusedDispatcher(),
        max_attempts=3,
        retry_base_seconds=1,
        event_handlers=(PortalEventHandler(database),),
    ).dispatch_pending()


def test_recipient_invitation_creates_scoped_student_access(tmp_path):
    database, _, _, _, parent, student, _, _ = setup_portal_data(tmp_path)

    with database.sessions() as session:
        access = session.scalar(select(StudentAccess))
        membership = session.scalar(select(Membership).where(Membership.user_id == parent.user_id))
        assert access is not None
        assert access.student_id == student.id and access.role == MembershipRole.parent.value
        assert membership is not None and membership.role == MembershipRole.parent.value


def test_parent_can_be_linked_to_multiple_students(tmp_path):
    database, _, identity, admin, parent, _, _, _ = setup_portal_data(tmp_path)
    with database.sessions() as session:
        second = Student(
            organization_id=ORG_ID,
            full_name="Пётр Иванов",
            subject="Математика",
        )
        session.add(second)
        session.commit()
    invitation = identity.create_invitation(
        ORG_ID,
        admin.user_id,
        parent.email,
        MembershipRole.parent.value,
        24,
        student_id=second.id,
    )
    identity.accept_invitation(invitation.token, parent.full_name, "strong-password")

    with database.sessions() as session:
        student_ids = set(
            session.scalars(
                select(StudentAccess.student_id).where(
                    StudentAccess.user_id == parent.user_id,
                    StudentAccess.active.is_(True),
                )
            )
        )
        assert len(student_ids) == 2 and second.id in student_ids


def test_invitation_rejects_student_from_another_tenant(tmp_path):
    database, _, identity, admin, _, _, _, _ = setup_portal_data(tmp_path)
    with database.sessions() as session:
        organization = Organization(name="Other", slug="other")
        session.add(organization)
        session.flush()
        other_student = Student(
            organization_id=organization.id,
            full_name="Чужой ученик",
            subject="Физика",
        )
        session.add(other_student)
        session.commit()

    with pytest.raises(NotFoundError):
        identity.create_invitation(
            ORG_ID,
            admin.user_id,
            "cross-tenant@example.test",
            MembershipRole.parent.value,
            24,
            student_id=other_student.id,
        )


def test_expired_recipient_invitation_does_not_create_access(tmp_path):
    database, _, identity, admin, _, student, _, _ = setup_portal_data(tmp_path)
    invitation = identity.create_invitation(
        ORG_ID,
        admin.user_id,
        "late@example.test",
        MembershipRole.student.value,
        24,
        student_id=student.id,
    )
    with database.sessions() as session:
        stored = session.get(type(invitation.invitation), invitation.invitation.id)
        assert stored is not None
        stored.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()

    with pytest.raises(ValidationError):
        identity.accept_invitation(invitation.token, "Опоздавший", "strong-password")
    with database.sessions() as session:
        assert (
            session.scalar(
                select(func.count(StudentAccess.id)).where(StudentAccess.user_id != admin.user_id)
            )
            == 1
        )


def test_publication_is_atomic_and_delivery_is_idempotent(tmp_path):
    database, _, _, admin, parent, student, run, _ = setup_portal_data(tmp_path)

    PublicationService(database, ORG_ID).publish(run.id, admin.user_id)
    with database.sessions() as session:
        published = session.get(GenerationRun, run.id)
        event = session.scalar(select(OutboxEvent))
        assert published is not None and published.status == GenerationStatus.published.value
        assert event is not None and event.status == OutboxStatus.pending.value
        assert session.scalar(select(func.count(AuditEvent.id))) == 1

    assert dispatch_publications(database)["dispatched"] == 1
    PortalEventHandler(database).handle(
        "material.published",
        ORG_ID,
        {
            "generation_run_id": run.id,
            "actor_user_id": admin.user_id,
            "publication_version": 1,
        },
    )
    with database.sessions() as session:
        delivery = session.scalar(select(MaterialDelivery))
        notification = session.scalar(select(UserNotification))
        assert delivery is not None and delivery.student_id == student.id
        assert notification is not None and notification.user_id == parent.user_id
        assert session.scalar(select(func.count(MaterialDelivery.id))) == 1
        assert session.scalar(select(func.count(UserNotification.id))) == 1


def test_publication_survives_temporary_delivery_unavailability(tmp_path):
    database, _, _, admin, _, _, run, _ = setup_portal_data(tmp_path)
    PublicationService(database, ORG_ID).publish(run.id, admin.user_id)

    result = OutboxService(
        database,
        UnavailableDeliveryDispatcher(),
        max_attempts=3,
        retry_base_seconds=1,
        event_handlers=(PortalEventHandler(database),),
        jitter=lambda low, high: high,
    ).dispatch_pending()

    assert result == {"dispatched": 0, "retried": 1, "dead": 0}
    with database.sessions() as session:
        published = session.get(GenerationRun, run.id)
        event = session.scalar(select(OutboxEvent))
        assert published.status == GenerationStatus.published.value
        assert event.status == OutboxStatus.pending.value
        assert "delivery worker broker is unavailable" in event.last_error


def test_recipient_can_read_only_assigned_published_artifacts(tmp_path):
    database, settings, identity, admin, parent, _, run, artifact_ids = setup_portal_data(tmp_path)
    storage = LocalArtifactStorage(settings.artifact_storage_root)
    portal = PortalService(database, storage, parent)

    with pytest.raises(NotFoundError):
        portal.artifact(artifact_ids["pdf"])
    PublicationService(database, ORG_ID).publish(run.id, admin.user_id)
    dispatch_publications(database)
    artifact, content = portal.artifact(artifact_ids["pdf"])
    assert artifact.kind == "pdf" and content.startswith(b"%PDF-")

    with database.sessions() as session:
        outsider_user = User(
            email="outsider@example.test",
            full_name="Другой родитель",
            password_hash=identity.passwords.hash("strong-password"),
        )
        session.add(outsider_user)
        session.flush()
        session.add(
            Membership(
                organization_id=ORG_ID,
                user_id=outsider_user.id,
                role=MembershipRole.parent.value,
            )
        )
        session.commit()
    outsider = Principal(
        outsider_user.id,
        ORG_ID,
        parent.organization_name,
        MembershipRole.parent.value,
        outsider_user.email,
        outsider_user.full_name,
    )
    with pytest.raises(NotFoundError):
        PortalService(database, storage, outsider).artifact(artifact_ids["pdf"])
    with database.sessions() as session:
        delivery_id = session.scalar(select(MaterialDelivery.id))
    assert delivery_id is not None
    with pytest.raises(NotFoundError):
        PortalService(database, storage, outsider).delivery(delivery_id)


def test_notifications_can_only_be_marked_by_their_owner(tmp_path):
    database, settings, identity, admin, parent, _, run, _ = setup_portal_data(tmp_path)
    PublicationService(database, ORG_ID).publish(run.id, admin.user_id)
    dispatch_publications(database)
    with database.sessions() as session:
        notification = session.scalar(select(UserNotification))
        assert notification is not None
    portal = PortalService(database, LocalArtifactStorage(settings.artifact_storage_root), parent)
    portal.mark_notification_read(notification.id)
    with database.sessions() as session:
        assert session.get(UserNotification, notification.id).read_at is not None

    outsider = Principal(
        "missing-user",
        ORG_ID,
        parent.organization_name,
        MembershipRole.parent.value,
        "missing@example.test",
        "Missing",
    )
    with pytest.raises(NotFoundError):
        PortalService(
            database, LocalArtifactStorage(settings.artifact_storage_root), outsider
        ).mark_notification_read(notification.id)


def test_revocation_closes_download_and_notifies_recipient(tmp_path):
    database, settings, _, admin, parent, _, run, artifact_ids = setup_portal_data(tmp_path)
    portal = PortalService(database, LocalArtifactStorage(settings.artifact_storage_root), parent)
    publication = PublicationService(database, ORG_ID)
    publication.publish(run.id, admin.user_id)
    dispatch_publications(database)
    assert portal.artifact(artifact_ids["pdf"])[1].startswith(b"%PDF-")

    publication.revoke(run.id, admin.user_id)
    dispatch_publications(database)

    with pytest.raises(NotFoundError):
        portal.artifact(artifact_ids["pdf"])
    with database.sessions() as session:
        delivery = session.scalar(select(MaterialDelivery))
        kinds = set(session.scalars(select(UserNotification.kind)))
        assert delivery is not None and delivery.status == "revoked"
        assert "material_revoked" in kinds


def test_student_access_revocation_hides_delivery_and_notifications(tmp_path):
    database, settings, identity, admin, parent, student, run, artifact_ids = setup_portal_data(
        tmp_path
    )
    publication = PublicationService(database, ORG_ID)
    publication.publish(run.id, admin.user_id)
    dispatch_publications(database)
    portal = PortalService(database, LocalArtifactStorage(settings.artifact_storage_root), parent)
    home = portal.home()
    assert len(home.deliveries) == 1 and len(home.notifications) == 1

    with database.sessions() as session:
        access_id = session.scalar(
            select(StudentAccess.id).where(
                StudentAccess.student_id == student.id,
                StudentAccess.user_id == parent.user_id,
            )
        )
        notification_id = session.scalar(
            select(UserNotification.id).where(UserNotification.user_id == parent.user_id)
        )
    assert access_id is not None and notification_id is not None
    identity.revoke_student_access(ORG_ID, student.id, access_id)

    home = portal.home()
    assert home.accesses == []
    assert home.deliveries == []
    assert home.notifications == [] and home.unread_count == 0
    with pytest.raises(NotFoundError):
        portal.artifact(artifact_ids["pdf"])
    with pytest.raises(NotFoundError):
        portal.mark_notification_read(notification_id)


def test_parent_portal_web_flow_and_sandboxed_preview(tmp_path):
    database, settings, _, admin, _, _, run, artifact_ids = setup_portal_data(tmp_path)
    PublicationService(database, ORG_ID).publish(run.id, admin.user_id)
    dispatch_publications(database)
    app = create_app(settings, database)

    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/login")
        authenticated = client.post(
            "/login",
            data={
                "csrf_token": csrf_from(login.text),
                "email": "parent@example.test",
                "password": "strong-password",
                "next": "/",
            },
        )
        assert authenticated.headers["location"] == "/portal"
        portal = client.get("/portal")
        assert portal.status_code == 200 and "Квадратные уравнения" in portal.text
        pdf = client.get(f"/portal/artifacts/{artifact_ids['pdf']}/download")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
        preview = client.get(f"/portal/artifacts/{artifact_ids['html']}/preview")
        assert preview.status_code == 200
        assert preview.headers["content-security-policy"].startswith("sandbox;")
        assert client.get("/students").status_code == 403
