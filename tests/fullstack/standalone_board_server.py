from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from tutor_assistant_web.bootstrap.app_factory import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.identity.application import IdentityService

PASSWORD = "standalone-pilot-e2e-password"
TEACHER_EMAIL = "standalone-pilot-teacher@example.test"


def settings(database_path: Path, artifact_path: Path) -> Settings:
    return Settings(
        app_env="development",
        app_profile="board",
        app_secret_key="standalone-pilot-e2e-secret-key-32-characters",
        artifact_storage_provider="local",
        artifact_storage_root=str(artifact_path),
        auto_migrate=False,
        bootstrap_admin_email=TEACHER_EMAIL,
        bootstrap_admin_name="E2E Преподаватель",
        bootstrap_admin_password=PASSWORD,
        database_url=f"sqlite:///{database_path}",
        metrics_enabled=False,
        otel_exporter_otlp_endpoint="",
        public_base_url="http://127.0.0.1:4173",
        rate_limit_board_reads=10_000,
        rate_limit_board_writes=10_000,
        rate_limit_invitations=1_000,
        rate_limit_login=1_000,
        seed_demo_data=False,
        session_cookie_secure=False,
        task_eager=True,
        trusted_hosts="127.0.0.1,localhost",
    )


def main() -> None:
    database_path = Path(
        os.getenv(
            "STANDALONE_PILOT_E2E_DATABASE",
            "/tmp/tutorboard-standalone-pilot-e2e.db",
        )
    ).resolve()
    artifact_path = Path(
        os.getenv(
            "STANDALONE_PILOT_E2E_ARTIFACTS",
            "/tmp/tutorboard-standalone-pilot-e2e-artifacts",
        )
    ).resolve()
    database_path.unlink(missing_ok=True)
    artifact_path.mkdir(parents=True, exist_ok=True)

    configured = settings(database_path, artifact_path)
    database = Database.from_settings(configured)
    database.migrate()
    IdentityService(database).bootstrap(configured)

    uvicorn.run(
        create_app(configured, database),
        host="127.0.0.1",
        port=4181,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
