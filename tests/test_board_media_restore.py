from __future__ import annotations

import pytest

from tutor_assistant_web.backup_operations import _validate_isolated_restore_target
from tutor_assistant_web.config import Settings


@pytest.fixture()
def settings():
    return Settings(
        database_url="postgresql+psycopg://tutor:pass@localhost:5432/tutorboard",
        artifact_s3_bucket="tutorboard-assets",
        backup_s3_bucket="tutorboard-backups",
        seed_demo_data=False,
    )


def test_restore_rejects_live_database_even_with_restore_bucket(settings):
    with pytest.raises(ValueError, match="isolated tutor_restore"):
        _validate_isolated_restore_target(
            settings,
            settings.database_url,
            "tutor-restore-20261008",
        )


def test_restore_rejects_live_or_malformed_artifact_buckets(settings):
    restored_db = "postgresql+psycopg://tutor:pass@localhost:5432/tutor_restore_20261008"
    for bucket in ["tutorboard-assets", "tutorboard-backups", "other-bucket"]:
        with pytest.raises(ValueError, match="isolated tutor-restore"):
            _validate_isolated_restore_target(settings, restored_db, bucket)


def test_restore_accepts_isolated_database_and_bucket(settings):
    _validate_isolated_restore_target(
        settings,
        "postgresql+psycopg://tutor:pass@localhost:5432/tutor_restore_20261008",
        "tutor-restore-20261008",
    )
