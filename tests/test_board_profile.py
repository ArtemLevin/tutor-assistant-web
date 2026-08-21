from __future__ import annotations

import inspect

import pytest

from tutor_assistant_web.bootstrap.board_app_factory import (
    BOARD_PROFILE_MODULES,
    BOARD_PROFILE_PROVIDERS,
    create_board_app,
)
from tutor_assistant_web.bootstrap.profile import load_runtime_configuration, resolve_app_profile
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards import standalone_access


def test_app_profile_contract_defaults_and_rejects_unknown() -> None:
    assert resolve_app_profile("") == "full"
    assert resolve_app_profile("full") == "full"
    assert resolve_app_profile("board") == "board"
    with pytest.raises(ValueError, match="Unsupported APP_PROFILE"):
        resolve_app_profile("portal")


def test_board_profile_rejects_enabled_modules_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLED_MODULES", "boards,portal")
    with pytest.raises(ValueError, match="ENABLED_MODULES must be unset"):
        load_runtime_configuration("board")


def test_board_production_validation_does_not_require_full_product_providers() -> None:
    settings = Settings(
        enabled_modules="boards",
        app_env="production",
        app_secret_key="board-production-secret-key-material-123456",
        database_url="postgresql+psycopg://board:password@postgres/board",
        auto_migrate=False,
        public_base_url="https://board.example.test",
        artifact_storage_provider="s3",
        artifact_s3_bucket="board-artifacts",
        backup_s3_bucket="board-backups",
        session_cookie_secure=True,
        trusted_hosts="board.example.test",
        trusted_proxy_ips="10.0.0.0/8",
        seed_demo_data=False,
        bootstrap_admin_email="teacher@example.test",
        bootstrap_admin_password="strong-board-password",
        metrics_bearer_token="board-metrics-token-1234567890",
        bbb_demo_mode=True,
        transcription_provider="disabled",
        document_engine_provider="local",
        artifact_clamav_enabled=False,
        task_eager=True,
    )
    assert settings.bbb_demo_mode is True
    assert settings.transcription_provider == "disabled"
    assert settings.document_engine_provider == "local"
    assert settings.artifact_clamav_enabled is False


def test_board_profile_has_exact_route_and_provider_inventory(tmp_path) -> None:
    settings = Settings(
        enabled_modules="boards",
        artifact_storage_root=str(tmp_path / "artifacts"),
        redis_url="redis://localhost:6379/15",
    )
    database = Database("sqlite://")
    app = create_board_app(settings, database)
    paths = {route.path for route in app.routes}
    assert paths == {
        "/login",
        "/logout",
        "/j/{secret}",
        "/api/v1/boards/context",
        "/api/v1/boards",
        "/api/v1/boards/{document_id}",
        "/api/v1/boards/{document_id}/archive",
        "/api/v1/boards/{document_id}/unarchive",
        "/api/v1/boards/{document_id}/invitations",
        "/api/v1/boards/{document_id}/invitations/{invitation_id}",
        "/api/v1/boards/{document_id}/invitations/{invitation_id}/revoke",
        "/api/v1/boards/{document_id}/invitations/{invitation_id}/rotate",
        "/api/v1/boards/{document_id}/commands",
        "/api/v1/boards/{document_id}/snapshots",
        "/api/v1/boards/{document_id}/collaboration-ticket",
        "/api/v1/boards/{document_id}/collaboration",
        "/health/live",
        "/health/ready",
        "/metrics",
    }
    assert app.state.installed_modules == list(BOARD_PROFILE_MODULES)
    assert app.state.installed_providers == list(BOARD_PROFILE_PROVIDERS)
    assert not any(
        marker in paths
        for marker in {
            "/lessons",
            "/students",
            "/schedule",
            "/classroom",
            "/materials",
            "/portal",
        }
    )
    database.dispose()


def test_standalone_access_policy_has_no_student_domain_dependency() -> None:
    source = inspect.getsource(standalone_access)
    assert "StudentAccess" not in source
    assert "modules.students" not in source
    assert "modules.scheduling" not in source
    assert "modules.classroom" not in source
