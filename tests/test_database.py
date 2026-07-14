from __future__ import annotations

import pytest
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from tutor_assistant_web.db import Database, _alembic_config_value
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Organization,
    StudentAccess,
    User,
)
from tutor_assistant_web.modules.students.models import Student


def test_sqlite_enforces_cross_tenant_student_access(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'tenant.db'}")
    database.migrate()
    with database.sessions() as session:
        other = Organization(name="Other", slug="other")
        user = User(
            email="parent@example.test",
            full_name="Parent",
            password_hash="test-only-hash",
        )
        student = Student(
            organization_id=DEFAULT_ORGANIZATION_ID,
            full_name="Student",
        )
        session.add_all([other, user, student])
        session.commit()
        session.add(
            StudentAccess(
                organization_id=other.id,
                student_id=student.id,
                user_id=user.id,
                role="parent",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_database_keeps_sqlite_lightweight(tmp_path):
    database = Database(
        f"sqlite:///{tmp_path / 'pool.db'}",
        pool_size=99,
        max_overflow=99,
    )

    assert database.dialect_name == "sqlite"
    database.healthcheck()
    database.dispose()


def test_alembic_database_url_escapes_encoded_query_parameters():
    url = "postgresql+psycopg://user:secret@db/app?options=-csearch_path%3Dtenant"
    config = Config()

    config.set_main_option("sqlalchemy.url", _alembic_config_value(url))

    assert config.get_main_option("sqlalchemy.url") == url


def test_model_metadata_contains_production_constraints(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'metadata.db'}")
    database.create_schema()
    inspector = inspect(database.engine)

    assert "fk_student_access_org_student" in {
        item["name"] for item in inspector.get_foreign_keys("student_access")
    }
    assert "ix_outbox_claim" in {item["name"] for item in inspector.get_indexes("outbox_events")}
    database.dispose()
