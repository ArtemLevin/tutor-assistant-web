from __future__ import annotations

import re
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tutor_assistant_web.bootstrap.app_factory import PACKAGE_DIR, create_app
from tutor_assistant_web.bootstrap.container import BoardAppContainer, build_container
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database

ROOT = PACKAGE_DIR.parents[1]

PRODUCTION_DATABASE_URL = "postgresql+psycopg://tutor:secret@db:5432/tutor"
PRODUCTION_SECRET = "production-secret-with-more-than-32-characters"


def board_settings(**overrides) -> Settings:
    values = {
        "app_profile": "board",
        "seed_demo_data": False,
        "bootstrap_admin_password": "admin-password",
    }
    values.update(overrides)
    return Settings(**values)


def test_profile_defaults_to_full_and_rejects_unknown_values():
    assert Settings().app_profile == "full"
    assert Settings(app_profile=" BOARD ").app_profile == "board"
    with pytest.raises(ValidationError, match="APP_PROFILE"):
        Settings(app_profile="boards")


def test_board_profile_rejects_enabled_modules():
    with pytest.raises(ValidationError, match="ENABLED_MODULES"):
        board_settings(enabled_modules="boards")


def test_production_board_profile_validates_only_its_runtime_dependencies():
    settings = board_settings(
        app_env="production",
        app_secret_key=PRODUCTION_SECRET,
        database_url=PRODUCTION_DATABASE_URL,
        auto_migrate=False,
        task_eager=False,
        public_base_url="https://board.example.test",
        artifact_storage_provider="s3",
        session_cookie_secure=True,
        bootstrap_admin_email="admin@example.test",
        metrics_bearer_token="metrics-token-with-24-characters",
    )

    assert settings.bbb_demo_mode is True
    assert settings.document_engine_provider == "local"
    assert settings.artifact_clamav_enabled is False


def test_production_board_profile_requires_distributed_collaboration():
    with pytest.raises(ValidationError, match="TASK_EAGER"):
        board_settings(
            app_env="production",
            app_secret_key=PRODUCTION_SECRET,
            database_url=PRODUCTION_DATABASE_URL,
            auto_migrate=False,
            task_eager=True,
            public_base_url="https://board.example.test",
            artifact_storage_provider="s3",
            session_cookie_secure=True,
            bootstrap_admin_email="admin@example.test",
            metrics_bearer_token="metrics-token-with-24-characters",
        )


def test_board_container_does_not_construct_full_stack_providers(tmp_path, monkeypatch):
    settings = board_settings(artifact_storage_root=str(tmp_path / "artifacts"))
    database = Database(f"sqlite:///{tmp_path / 'board.db'}")
    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

    def forbidden(_settings):
        raise AssertionError("full-stack provider constructed")

    monkeypatch.setattr(
        "tutor_assistant_web.bootstrap.container.build_conference_provider", forbidden
    )
    monkeypatch.setattr(
        "tutor_assistant_web.bootstrap.container.build_material_generator", forbidden
    )
    monkeypatch.setattr(
        "tutor_assistant_web.bootstrap.container.build_transcription_provider", forbidden
    )
    monkeypatch.setattr("tutor_assistant_web.bootstrap.container.build_document_engine", forbidden)

    container = build_container(settings, database, templates, ZoneInfo("UTC"))

    assert isinstance(container, BoardAppContainer)
    assert not hasattr(container, "conference")
    assert not hasattr(container, "materials")
    assert not hasattr(container, "jobs")
    assert not hasattr(container, "document_engine")


def test_board_profile_exposes_only_reviewed_routes(tmp_path):
    settings = board_settings(artifact_storage_root=str(tmp_path / "artifacts"))
    database = Database(f"sqlite:///{tmp_path / 'board.db'}")
    app = create_app(settings, database)

    def flatten(router):
        for route in router.routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from flatten(included)
            else:
                yield route

    inventory = {
        (method, route.path)
        for route in flatten(app.router)
        for method in (getattr(route, "methods", None) or {"WEBSOCKET"})
    }
    expected = {
        ("GET", "/login"),
        ("POST", "/login"),
        ("POST", "/logout"),
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("GET", "/metrics"),
        ("GET", "/j/{secret}"),
        ("GET", "/api/v1/boards/context"),
        ("POST", "/api/v1/boards"),
        ("GET", "/api/v1/boards"),
        ("GET", "/api/v1/boards/{document_id}"),
        ("PATCH", "/api/v1/boards/{document_id}"),
        ("DELETE", "/api/v1/boards/{document_id}"),
        ("POST", "/api/v1/boards/{document_id}/archive"),
        ("POST", "/api/v1/boards/{document_id}/unarchive"),
        ("POST", "/api/v1/boards/{document_id}/invitations"),
        ("GET", "/api/v1/boards/{document_id}/invitations"),
        ("PATCH", "/api/v1/boards/{document_id}/invitations/{invitation_id}"),
        ("POST", "/api/v1/boards/{document_id}/invitations/{invitation_id}/revoke"),
        ("POST", "/api/v1/boards/{document_id}/invitations/{invitation_id}/rotate"),
        ("GET", "/api/v1/boards/{document_id}/commands"),
        ("POST", "/api/v1/boards/{document_id}/commands"),
        ("POST", "/api/v1/boards/{document_id}/snapshots"),
        ("POST", "/api/v1/boards/{document_id}/collaboration-ticket"),
        ("WEBSOCKET", "/api/v1/boards/{document_id}/collaboration"),
    }

    assert inventory == expected
    assert app.state.installed_modules == ("identity", "audit", "boards", "health")


def test_board_profile_returns_404_for_full_application_routes(tmp_path):
    settings = board_settings(artifact_storage_root=str(tmp_path / "artifacts"))
    database = Database(f"sqlite:///{tmp_path / 'board.db'}")

    with TestClient(create_app(settings, database)) as client:
        assert client.get("/health/live").json()["profile"] == "board"
        ready = client.get("/health/ready")
        assert set(ready.json()["checks"]) == {"postgresql", "redis", "s3"}
        for path in (
            "/students",
            "/schedule",
            "/portal",
            "/api/v1/students",
            "/api/v1/lessons/lesson:1/boards",
            "/api/v1/geometry/generate",
            "/static/styles.css",
            "/docs",
            "/openapi.json",
        ):
            assert client.get(path).status_code == 404, path


def test_board_profile_runs_standalone_teacher_and_guest_management(tmp_path):
    settings = board_settings(artifact_storage_root=str(tmp_path / "artifacts"))
    database = Database(f"sqlite:///{tmp_path / 'board.db'}")

    with TestClient(create_app(settings, database), follow_redirects=False) as client:
        login_page = client.get("/login")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
        assert csrf is not None
        login = client.post(
            "/login",
            data={
                "csrf_token": csrf.group(1),
                "email": settings.bootstrap_admin_email,
                "password": settings.effective_bootstrap_password,
                "next": "/boards",
            },
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/boards"

        context = client.get("/api/v1/boards/context").json()
        created = client.post(
            "/api/v1/boards",
            json={"title": "Алгебра"},
            headers={"x-csrf-token": context["csrfToken"]},
        )
        assert created.status_code == 201
        board_id = created.json()["boardId"]
        invitation = client.post(
            f"/api/v1/boards/{board_id}/invitations",
            json={"displayName": "Ученик", "writeEnabled": True},
            headers={"x-csrf-token": context["csrfToken"]},
        )
        assert invitation.status_code == 201
        assert invitation.json()["joinUrl"].startswith(f"{settings.public_base_url.rstrip('/')}/j/")


def test_board_production_compose_is_minimal_hardened_and_state_isolated():
    document = yaml.safe_load((ROOT / "compose.board.production.yml").read_text())
    services = document["services"]
    assert {
        "caddy",
        "board-api-blue",
        "board-api-green",
        "tutorboard-blue",
        "tutorboard-green",
        "migration",
        "ops",
        "backup",
        "postgres",
        "redis",
        "minio",
        "minio-init",
    } == set(services)
    assert not {
        "worker",
        "scheduler",
        "bigbluebutton",
        "transcription",
        "clamav",
        "geometryos",
        "document-engine",
        "portal",
    } & set(services)
    assert document["name"] == "${BOARD_COMPOSE_PROJECT_NAME:-tutorboard-production}"
    assert document["networks"]["data"]["internal"] is True
    for name in ("board-api-blue", "board-api-green", "tutorboard-blue", "tutorboard-green"):
        assert services[name]["read_only"] is True
        assert services[name]["cap_drop"] == ["ALL"]
        assert services[name]["security_opt"] == ["no-new-privileges:true"]
    assert services["board-api-blue"]["environment"]["APP_PROFILE"] == "board"
    assert services["board-api-blue"]["environment"]["ARTIFACT_CLAMAV_ENABLED"] == ("false")
    assert "ports" not in services["postgres"]
    assert "ports" not in services["redis"]
    assert "ports" not in services["minio"]
    assert set(document["volumes"]) == {
        "board-postgres",
        "board-redis",
        "board-minio",
        "board-caddy-data",
        "board-caddy-config",
    }


def test_board_caddy_has_explicit_allowlist_and_secret_redaction():
    caddy = (ROOT / "deploy/board-production/Caddyfile.template").read_text()
    for route in (
        "/login",
        "/logout",
        "/j/*",
        "/api/v1/boards/*",
        "/health/*",
        "/metrics",
        "/boards/*",
        "/b/*",
        "/board/*",
    ):
        assert route in caddy
    assert 'respond "Not Found" 404' in caddy
    assert "request>headers>Authorization delete" in caddy
    assert "request>headers>Cookie delete" in caddy
    assert '"(/j/[^/?]+)|(ticket=[^&]+)" "[REDACTED]"' in caddy


def test_board_deployment_scripts_use_digest_pinning_and_slot_rollback():
    deployment = ROOT / "deploy/board-production"
    deploy = (deployment / "deploy.sh").read_text()
    rollback = (deployment / "rollback.sh").read_text()
    smoke = (deployment / "smoke.sh").read_text()
    assert "@sha256:" in deploy
    assert "new_slot=green" in deploy and "new_slot=blue" in deploy
    assert "release-manifest.json" in deploy
    assert "caddy validate" in deploy
    assert "PREVIOUS_RELEASE" in rollback
    assert "caddy reload" in rollback
    assert "CHECK_LOG_REDACTION" in smoke
    assert "join-sentinel" in smoke and "ticket-sentinel" in smoke
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "FROM web AS board-api" in dockerfile
    assert "ENV APP_PROFILE=board" in dockerfile
