from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_local_compose_is_one_private_application_stack() -> None:
    document = yaml.safe_load((ROOT / "compose.local.yml").read_text(encoding="utf-8"))
    services = document["services"]
    assert {
        "gateway",
        "web",
        "tutorboard",
        "geometryos",
        "migration",
        "worker",
        "scheduler",
        "postgres",
        "redis",
        "minio",
        "minio-init",
        "artifact-init",
        "clamav",
        "smoke",
    } <= set(services)
    assert services["web"]["build"]["target"] == "web"
    assert services["web"]["command"][services["web"]["command"].index("--workers") + 1] == "1"
    assert services["worker"]["build"]["target"] == "worker"
    assert services["scheduler"]["build"]["target"] == "scheduler"
    assert services["migration"]["build"]["target"] == "migration"
    assert services["tutorboard"]["build"]["context"] == "${TUTORBOARD_CONTEXT:-../tutorboard}"
    assert services["geometryos"]["build"]["context"] == "${GEOMETRYOS_CONTEXT:-../geometryos}"
    assert services["migration"]["restart"] == "no"
    assert services["web"]["depends_on"]["migration"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["web"]["depends_on"]["geometryos"]["condition"] == "service_healthy"

    assert services["gateway"]["ports"] == ["${LOCAL_PORT:-8080}:8080"]
    for private_service in (
        "web",
        "tutorboard",
        "geometryos",
        "postgres",
        "redis",
        "minio",
        "clamav",
    ):
        assert "ports" not in services[private_service]
    assert document["networks"]["frontend"]["internal"] is True
    assert document["networks"]["backend"]["internal"] is True


def test_local_gateway_and_scripts_expose_one_origin() -> None:
    caddy = (ROOT / "deploy" / "local" / "Caddyfile").read_text(encoding="utf-8")
    assert "handle_path /board/*" in caddy
    assert "reverse_proxy tutorboard:8080" in caddy
    assert "reverse_proxy web:8000" in caddy
    assert "geometryos" not in caddy

    start = (ROOT / "deploy" / "local" / "start-local.ps1").read_text(encoding="utf-8")
    stop = (ROOT / "deploy" / "local" / "stop-local.ps1").read_text(encoding="utf-8")
    reset = (ROOT / "deploy" / "local" / "reset-local.ps1").read_text(encoding="utf-8")
    assert "--profile smoke" in start
    assert "run --rm smoke" in start
    assert "down" in stop
    assert "--volumes" in reset
    assert "Read-Host" in reset


def test_local_smoke_script_is_syntactically_valid_and_covers_vertical_slice() -> None:
    source = (ROOT / "deploy" / "local" / "smoke.py").read_text(encoding="utf-8")
    compile(source, "deploy/local/smoke.py", "exec")
    for marker in (
        "/login",
        "/board/",
        "/collaboration-ticket",
        "/api/v1/geometryos/api/v1/generate",
        "/snapshots",
        "/evidence",
        "/publish",
    ):
        assert marker in source


def test_local_credentials_file_is_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/local/.env.local" in ignore
    assert (ROOT / "deploy" / "local" / ".env.local.example").is_file()
