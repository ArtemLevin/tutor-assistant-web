from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.audit.models import AuditEvent
from tutor_assistant_web.modules.automation.models import OutboxEvent
from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.modules.identity.models import StudentAccess
from tutor_assistant_web.modules.materials.models import (
    ArtifactStatus,
    ArtifactVersion,
    BuildLog,
    GenerationRun,
    GenerationStatus,
)
from tutor_assistant_web.modules.portal.models import (
    DeliveryStatus,
    MaterialDelivery,
    NotificationKind,
    UserNotification,
)
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.shared.contracts import ArtifactStorage
from tutor_assistant_web.shared.errors import ConflictError, NotFoundError


@dataclass(frozen=True)
class PortalHome:
    accesses: list[StudentAccess]
    deliveries: list[MaterialDelivery]
    notifications: list[UserNotification]
    unread_count: int


class PublicationService:
    def __init__(self, database: Database, organization_id: str) -> None:
        self.database = database
        self.organization_id = organization_id

    def publish(self, run_id: str, actor_user_id: str) -> GenerationRun:
        with self.database.sessions() as session:
            run = self._run(session, run_id)
            if run.status == GenerationStatus.published.value:
                return run
            if run.status != GenerationStatus.approved.value:
                raise ConflictError("Сначала согласуйте комплект")
            now = datetime.now(UTC)
            version = max((item.version for item in run.versions), default=1)
            run.status = GenerationStatus.published.value
            run.published_at = now
            for artifact in run.versions:
                artifact.status = ArtifactStatus.published.value
                artifact.published_at = now
            session.add_all(
                [
                    BuildLog(
                        organization_id=self.organization_id,
                        generation_run_id=run.id,
                        stage="publish",
                        status="published",
                        message="Комплект опубликован и поставлен в очередь доставки",
                    ),
                    OutboxEvent(
                        organization_id=self.organization_id,
                        topic="material.published",
                        dedup_key=f"material.published:{run.id}:v{version}",
                        payload={
                            "generation_run_id": run.id,
                            "actor_user_id": actor_user_id,
                            "publication_version": version,
                        },
                    ),
                    AuditEvent(
                        organization_id=self.organization_id,
                        actor_user_id=actor_user_id,
                        action="materials.published",
                        entity_type="generation_run",
                        entity_id=run.id,
                        details={"lesson_id": run.lesson_id, "version": version},
                    ),
                ]
            )
            session.commit()
            return run

    def revoke(self, run_id: str, actor_user_id: str) -> GenerationRun:
        with self.database.sessions() as session:
            run = self._run(session, run_id)
            if run.status == GenerationStatus.revoked.value:
                return run
            if run.status != GenerationStatus.published.value:
                raise ConflictError("Отозвать можно только опубликованный комплект")
            version = max((item.version for item in run.versions), default=1)
            run.status = GenerationStatus.revoked.value
            for artifact in run.versions:
                artifact.status = ArtifactStatus.revoked.value
            session.add_all(
                [
                    BuildLog(
                        organization_id=self.organization_id,
                        generation_run_id=run.id,
                        stage="publish",
                        status="revoked",
                        message="Публикация отозвана преподавателем",
                    ),
                    OutboxEvent(
                        organization_id=self.organization_id,
                        topic="material.revoked",
                        dedup_key=f"material.revoked:{run.id}:v{version}",
                        payload={
                            "generation_run_id": run.id,
                            "actor_user_id": actor_user_id,
                            "publication_version": version,
                        },
                    ),
                    AuditEvent(
                        organization_id=self.organization_id,
                        actor_user_id=actor_user_id,
                        action="materials.revoked",
                        entity_type="generation_run",
                        entity_id=run.id,
                        details={"lesson_id": run.lesson_id, "version": version},
                    ),
                ]
            )
            session.commit()
            return run

    def _run(self, session, run_id: str) -> GenerationRun:
        run = session.scalar(
            select(GenerationRun)
            .options(selectinload(GenerationRun.versions))
            .where(
                GenerationRun.id == run_id,
                GenerationRun.organization_id == self.organization_id,
            )
            .with_for_update()
        )
        if run is None:
            raise NotFoundError("Сборка не найдена")
        return run


class PortalEventHandler:
    topics = {"material.published", "material.revoked"}

    def __init__(self, database: Database) -> None:
        self.database = database

    def handles(self, topic: str) -> bool:
        return topic in self.topics

    def handle(self, topic: str, organization_id: str, payload: dict) -> None:
        run_id = payload.get("generation_run_id")
        actor_user_id = payload.get("actor_user_id")
        version = payload.get("publication_version", 1)
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("publication event has no generation_run_id")
        if not isinstance(version, int) or version < 1:
            raise ValueError("publication event has invalid version")
        if topic == "material.published":
            self._deliver(organization_id, run_id, actor_user_id, version)
        elif topic == "material.revoked":
            self._revoke(organization_id, run_id, version)
        else:
            raise ValueError(f"unsupported portal topic: {topic}")

    def _deliver(
        self, organization_id: str, run_id: str, actor_user_id: str | None, version: int
    ) -> None:
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            run = session.scalar(
                select(GenerationRun)
                .options(selectinload(GenerationRun.lesson).selectinload(Lesson.student))
                .where(
                    GenerationRun.id == run_id,
                    GenerationRun.organization_id == organization_id,
                    GenerationRun.status == GenerationStatus.published.value,
                )
            )
            if run is None:
                raise NotFoundError("Опубликованная сборка не найдена")
            student_id = run.lesson.student_id
            delivery = session.scalar(
                select(MaterialDelivery).where(
                    MaterialDelivery.organization_id == organization_id,
                    MaterialDelivery.generation_run_id == run.id,
                    MaterialDelivery.student_id == student_id,
                )
            )
            if delivery is None:
                delivery = MaterialDelivery(
                    organization_id=organization_id,
                    generation_run_id=run.id,
                    student_id=student_id,
                    created_by_user_id=actor_user_id,
                    publication_version=version,
                )
                session.add(delivery)
                session.flush()
            delivery.status = DeliveryStatus.available.value
            delivery.published_at = now
            delivery.revoked_at = None
            previous_exists = bool(
                session.scalar(
                    select(func.count(MaterialDelivery.id)).where(
                        MaterialDelivery.organization_id == organization_id,
                        MaterialDelivery.student_id == student_id,
                        MaterialDelivery.id != delivery.id,
                        MaterialDelivery.status == DeliveryStatus.available.value,
                    )
                )
            )
            kind = (
                NotificationKind.material_replaced.value
                if previous_exists
                else NotificationKind.material_available.value
            )
            accesses = list(
                session.scalars(
                    select(StudentAccess).where(
                        StudentAccess.organization_id == organization_id,
                        StudentAccess.student_id == student_id,
                        StudentAccess.active.is_(True),
                    )
                )
            )
            for access in accesses:
                dedup_key = f"{kind}:{delivery.id}:{access.user_id}:v{version}"
                if session.scalar(
                    select(UserNotification.id).where(UserNotification.dedup_key == dedup_key)
                ):
                    continue
                session.add(
                    UserNotification(
                        organization_id=organization_id,
                        user_id=access.user_id,
                        student_id=student_id,
                        delivery_id=delivery.id,
                        kind=kind,
                        dedup_key=dedup_key,
                        title=f"Материалы: {run.lesson.topic or run.lesson.title}",
                        body=f"Доступен комплект версии {version}.",
                    )
                )
            session.commit()

    def _revoke(self, organization_id: str, run_id: str, version: int) -> None:
        now = datetime.now(UTC)
        with self.database.sessions() as session:
            delivery = session.scalar(
                select(MaterialDelivery)
                .options(selectinload(MaterialDelivery.student))
                .where(
                    MaterialDelivery.organization_id == organization_id,
                    MaterialDelivery.generation_run_id == run_id,
                )
                .with_for_update()
            )
            if delivery is None:
                return
            delivery.status = DeliveryStatus.revoked.value
            delivery.revoked_at = now
            accesses = list(
                session.scalars(
                    select(StudentAccess).where(
                        StudentAccess.organization_id == organization_id,
                        StudentAccess.student_id == delivery.student_id,
                        StudentAccess.active.is_(True),
                    )
                )
            )
            for access in accesses:
                dedup_key = f"material_revoked:{delivery.id}:{access.user_id}:v{version}"
                if session.scalar(
                    select(UserNotification.id).where(UserNotification.dedup_key == dedup_key)
                ):
                    continue
                session.add(
                    UserNotification(
                        organization_id=organization_id,
                        user_id=access.user_id,
                        student_id=delivery.student_id,
                        delivery_id=delivery.id,
                        kind=NotificationKind.material_revoked.value,
                        dedup_key=dedup_key,
                        title=f"Комплект для {delivery.student.full_name} отозван",
                        body="Преподаватель готовит исправленную версию материалов.",
                    )
                )
            session.commit()


class PortalService:
    def __init__(
        self,
        database: Database,
        storage: ArtifactStorage,
        principal: Principal,
    ) -> None:
        self.database = database
        self.storage = storage
        self.principal = principal

    def home(self) -> PortalHome:
        with self.database.sessions() as session:
            accesses = list(
                session.scalars(
                    select(StudentAccess)
                    .options(selectinload(StudentAccess.student))
                    .where(
                        StudentAccess.organization_id == self.principal.organization_id,
                        StudentAccess.user_id == self.principal.user_id,
                        StudentAccess.active.is_(True),
                    )
                    .order_by(StudentAccess.created_at)
                )
            )
            student_ids = [item.student_id for item in accesses]
            deliveries = (
                list(
                    session.scalars(
                        select(MaterialDelivery)
                        .options(
                            selectinload(MaterialDelivery.student),
                            selectinload(MaterialDelivery.generation_run).selectinload(
                                GenerationRun.lesson
                            ),
                            selectinload(MaterialDelivery.generation_run).selectinload(
                                GenerationRun.versions
                            ),
                        )
                        .where(
                            MaterialDelivery.organization_id == self.principal.organization_id,
                            MaterialDelivery.student_id.in_(student_ids),
                            MaterialDelivery.status == DeliveryStatus.available.value,
                        )
                        .order_by(MaterialDelivery.published_at.desc())
                    )
                )
                if student_ids
                else []
            )
            notifications = (
                list(
                    session.scalars(
                        select(UserNotification)
                        .where(
                            UserNotification.organization_id == self.principal.organization_id,
                            UserNotification.user_id == self.principal.user_id,
                            UserNotification.student_id.in_(student_ids),
                        )
                        .order_by(UserNotification.created_at.desc())
                        .limit(30)
                    )
                )
                if student_ids
                else []
            )
            unread_count = (
                session.scalar(
                    select(func.count(UserNotification.id)).where(
                        UserNotification.organization_id == self.principal.organization_id,
                        UserNotification.user_id == self.principal.user_id,
                        UserNotification.student_id.in_(student_ids),
                        UserNotification.read_at.is_(None),
                    )
                )
                if student_ids
                else 0
            )
            return PortalHome(accesses, deliveries, notifications, unread_count or 0)

    def delivery(self, delivery_id: str) -> MaterialDelivery:
        with self.database.sessions() as session:
            delivery = session.scalar(
                select(MaterialDelivery)
                .join(
                    StudentAccess,
                    StudentAccess.student_id == MaterialDelivery.student_id,
                )
                .options(
                    selectinload(MaterialDelivery.student),
                    selectinload(MaterialDelivery.generation_run).selectinload(
                        GenerationRun.lesson
                    ),
                    selectinload(MaterialDelivery.generation_run).selectinload(
                        GenerationRun.versions
                    ),
                )
                .where(
                    MaterialDelivery.id == delivery_id,
                    MaterialDelivery.organization_id == self.principal.organization_id,
                    MaterialDelivery.status == DeliveryStatus.available.value,
                    StudentAccess.organization_id == self.principal.organization_id,
                    StudentAccess.user_id == self.principal.user_id,
                    StudentAccess.active.is_(True),
                )
            )
            if delivery is None:
                raise NotFoundError("Материалы не найдены")
            return delivery

    def artifact(self, artifact_id: str) -> tuple[ArtifactVersion, bytes]:
        with self.database.sessions() as session:
            artifact = session.scalar(
                select(ArtifactVersion)
                .join(GenerationRun, GenerationRun.id == ArtifactVersion.generation_run_id)
                .join(
                    MaterialDelivery,
                    MaterialDelivery.generation_run_id == GenerationRun.id,
                )
                .join(
                    StudentAccess,
                    StudentAccess.student_id == MaterialDelivery.student_id,
                )
                .where(
                    ArtifactVersion.id == artifact_id,
                    ArtifactVersion.organization_id == self.principal.organization_id,
                    ArtifactVersion.status == ArtifactStatus.published.value,
                    GenerationRun.status == GenerationStatus.published.value,
                    MaterialDelivery.organization_id == self.principal.organization_id,
                    MaterialDelivery.status == DeliveryStatus.available.value,
                    StudentAccess.organization_id == self.principal.organization_id,
                    StudentAccess.user_id == self.principal.user_id,
                    StudentAccess.active.is_(True),
                )
            )
            if artifact is None:
                raise NotFoundError("Материал не найден")
            return artifact, self.storage.read(artifact.storage_key)

    def mark_notification_read(self, notification_id: str) -> UserNotification:
        with self.database.sessions() as session:
            notification = session.scalar(
                select(UserNotification)
                .join(
                    StudentAccess,
                    StudentAccess.student_id == UserNotification.student_id,
                )
                .where(
                    UserNotification.id == notification_id,
                    UserNotification.organization_id == self.principal.organization_id,
                    UserNotification.user_id == self.principal.user_id,
                    StudentAccess.organization_id == self.principal.organization_id,
                    StudentAccess.user_id == self.principal.user_id,
                    StudentAccess.active.is_(True),
                )
            )
            if notification is None:
                raise NotFoundError("Уведомление не найдено")
            notification.read_at = notification.read_at or datetime.now(UTC)
            session.commit()
            return notification
