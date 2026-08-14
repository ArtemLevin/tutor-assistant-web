from __future__ import annotations

import hashlib
import io
import subprocess
from pathlib import Path

import pytest
import yaml
from sqlalchemy import UniqueConstraint

from tutor_assistant_web import backup_operations
from tutor_assistant_web.config import Settings
from tutor_assistant_web.load_operations import _guard
from tutor_assistant_web.modules.classroom.models import RecordingAsset
from tutor_assistant_web.modules.identity.models import Organization, User
from tutor_assistant_web.modules.materials.models import GenerationRun
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.version import __version__

ROOT = Path(__file__).parents[1]


def test_release_version_and_images_are_immutable_non_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert __version__ == "1.0.0"
    assert "USER tutor:tutor" in dockerfile
    for target in ("web", "worker", "scheduler", "migration", "ops"):
        assert f"AS {target}" in dockerfile
    assert "tutor-assistant-web==1.0.0" in dockerfile
    assert "pip==26.1.2" in dockerfile


def test_production_compose_has_separate_processes_and_private_network() -> None:
    document = yaml.safe_load((ROOT / "compose.production.yml").read_text(encoding="utf-8"))
    services = document["services"]
    assert {
        "web-blue",
        "web-green",
        "worker-blue",
        "worker-green",
        "scheduler",
        "migration",
        "tutorboard-blue",
        "tutorboard-green",
        "geometryos",
    } <= set(services)
    assert "ports" not in services["web-blue"]
    assert services["tutorboard-blue"]["read_only"] is True
    assert services["tutorboard-blue"]["cap_drop"] == ["ALL"]
    assert services["geometryos"]["read_only"] is True
    assert services["geometryos"]["cap_drop"] == ["ALL"]
    assert services["geometryos"]["image"].startswith("${GEOMETRYOS_IMAGE:")
    assert services["web-blue"]["image"].startswith("${BLUE_WEB_IMAGE:")
    assert services["worker-green"]["image"].startswith("${GREEN_WORKER_IMAGE:")
    assert services["tutorboard-blue"]["image"].startswith("${TUTORBOARD_BLUE_IMAGE:")
    assert services["scheduler"]["image"].startswith("${SCHEDULER_IMAGE:")
    assert services["migration"]["image"].startswith("${MIGRATION_IMAGE:")
    assert services["ops"]["image"].startswith("${OPS_IMAGE:")
    assert "ports" not in services["geometryos"]
    assert services["web-blue"]["depends_on"]["geometryos"]["condition"] == "service_healthy"
    assert "ports" not in services["postgres"]
    assert document["networks"]["backend"]["internal"] is True
    assert services["migration"]["restart"] == "no"
    for service in services.values():
        assert service["cpus"]
        assert service["mem_limit"]
        assert service["pids_limit"]
        assert service["logging"]["driver"] == "json-file"
        assert service["logging"]["options"]["max-size"]
        assert service["logging"]["options"]["max-file"]


@pytest.mark.parametrize(
    ("model", "column", "unique_index"),
    [
        (Organization, "slug", True),
        (User, "email", True),
        (Lesson, "bbb_meeting_id", True),
        (RecordingAsset, "record_id", True),
        (GenerationRun, "job_id", False),
        (GenerationRun, "idempotency_key", False),
    ],
)
def test_postgresql_unique_constraints_match_historical_indexes(
    model, column: str, unique_index: bool
) -> None:
    table = model.__table__
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(item.name for item in constraint.columns) == (column,)
        for constraint in table.constraints
    )
    assert any(
        tuple(item.name for item in index.columns) == (column,)
        and bool(index.unique) is unique_index
        for index in table.indexes
    )


def test_release_shell_scripts_are_syntactically_valid() -> None:
    script_directories = (
        ROOT / "deploy" / "production",
        ROOT / "deploy" / "ubuntu",
        ROOT / "deploy" / "yandex-cloud" / "scripts",
    )
    for script_directory in script_directories:
        for script in script_directory.glob("*.sh"):
            subprocess.run(["sh", "-n", str(script)], check=True)
    deploy = (ROOT / "deploy" / "production" / "deploy.sh").read_text(encoding="utf-8")
    assert "GEOMETRYOS_IMAGE must be pinned with @sha256:" in deploy
    assert "compose pull geometryos" in deploy
    assert 'deploy/ubuntu/preflight.sh" "$RELEASE" "$TUTORBOARD_RELEASE"' in deploy
    assert "Resolving release tags to immutable repository digests" in deploy
    assert "docker image inspect --format" in deploy

    rollback = (ROOT / "deploy" / "production" / "rollback.sh").read_text(encoding="utf-8")
    assert "Exact rollback digests are unavailable" in rollback
    assert "PREVIOUS_SCHEDULER_IMAGE" in rollback


def test_ubuntu_host_contract_is_hardened_and_rebootable() -> None:
    ubuntu = ROOT / "deploy" / "ubuntu"
    bootstrap = (ubuntu / "bootstrap.sh").read_text(encoding="utf-8")
    preflight = (ubuntu / "preflight.sh").read_text(encoding="utf-8")
    stack_unit = (ubuntu / "tutorboard-stack.service").read_text(encoding="utf-8")
    firewall_unit = (ubuntu / "tutorboard-firewall.service").read_text(encoding="utf-8")
    host_smoke = (ubuntu / "host-smoke.sh").read_text(encoding="utf-8")

    assert "22.04|24.04" in bootstrap
    assert "docker-ce" in bootstrap
    assert "unattended-upgrades" in bootstrap
    assert "timedatectl set-ntp true" in bootstrap
    assert "PasswordAuthentication no" in bootstrap
    assert "authorized_keys" in bootstrap
    assert "ufw allow 443/udp" in bootstrap
    assert "tutorboard-stack.service" in bootstrap
    assert "tutorboard-firewall.service" in bootstrap

    for marker in (
        "Production host must run Ubuntu",
        "x86_64/amd64",
        "MINIMUM_HOST_MEMORY_MB",
        "MINIMUM_HOST_DISK_GB",
        "NTPSynchronized",
        "BACKUP_S3_ENDPOINT_URL",
        "must use off-host HTTPS S3",
        "docker manifest inspect --verbose",
        "compose config --quiet",
    ):
        assert marker in preflight

    assert "After=docker.service network-online.target tutorboard-firewall.service" in stack_unit
    assert "User=@DEPLOY_USER@" in stack_unit
    assert "NoNewPrivileges=true" in stack_unit
    assert "ProtectSystem=strict" in stack_unit
    assert "WantedBy=multi-user.target" in stack_unit
    assert "Before=docker.service tutorboard-stack.service" in firewall_unit
    assert "--verify-backup" in host_smoke
    assert "systemctl is-active --quiet tutorboard-stack.service" in host_smoke


def test_yandex_cloud_provisioning_keeps_secrets_out_of_terraform_state() -> None:
    yandex = ROOT / "deploy" / "yandex-cloud"
    terraform = "\n".join(
        path.read_text(encoding="utf-8") for path in (yandex / "terraform").glob("*.tf")
    )
    cloud_init = (yandex / "terraform" / "cloud-init.tftpl").read_text(encoding="utf-8")
    playbook = (yandex / "ansible" / "playbook.yml").read_text(encoding="utf-8")
    materializer = (yandex / "scripts" / "materialize-lockbox.sh").read_text(encoding="utf-8")

    assert 'version = "0.220.0"' in terraform
    assert "yandex_lockbox_secret_iam_binding" in terraform
    assert 'role      = "lockbox.payloadViewer"' in terraform
    assert 'data "yandex_lockbox_secret_version"' not in terraform
    assert "ghcr_token" not in terraform
    assert "lockbox_secret_id" in cloud_init
    assert "ghcr_token" not in cloud_init
    assert "no_log: true" in playbook
    assert "backend_commit" in playbook
    assert "169.254.169.254/computeMetadata" in materializer
    assert "payload.lockbox.api.cloud.yandex.net" in materializer


def test_production_backup_is_off_host_and_line_endings_are_pinned() -> None:
    example = (ROOT / "deploy" / "production" / ".env.production.example").read_text(
        encoding="utf-8"
    )
    init = (ROOT / "deploy" / "production" / "init.sh").read_text(encoding="utf-8")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "BACKUP_S3_ENDPOINT_URL=https://" in example
    assert "BACKUP_S3_ENDPOINT_URL=http://minio:9000" not in example
    assert 'cp "$SECRETS/artifact_s3_secret_key" "$SECRETS/backup_s3_secret_key"' not in init
    for rule in (
        "*.sh text eol=lf",
        "*.service text eol=lf",
        "*.yml text eol=lf",
        "*.yaml text eol=lf",
        "*.ps1 text eol=crlf",
    ):
        assert rule in attributes


def test_migration_image_contains_alembic_scripts() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    migration_stage = dockerfile.split("FROM runtime-base AS migration", maxsplit=1)[1]
    migration_stage = migration_stage.split("FROM runtime-base AS ops", maxsplit=1)[0]
    assert "/build/src/tutor_assistant_web/migrations" in migration_stage
    assert "./src/tutor_assistant_web/migrations" in migration_stage


def test_backup_sha256_is_streamed(tmp_path: Path) -> None:
    content = b"production-backup" * 100_000
    source = tmp_path / "database.dump"
    source.write_bytes(content)
    assert backup_operations._sha256(source) == hashlib.sha256(content).hexdigest()


def test_artifact_backup_copy_streams_between_s3_clients() -> None:
    body = io.BytesIO(b"artifact")

    class Source:
        @staticmethod
        def get_object(**_kwargs):
            return {"Body": body, "Metadata": {"sha256": "abc"}, "ContentType": "text/plain"}

    class Target:
        captured = b""
        extra = {}

        def upload_fileobj(self, stream, _bucket, _key, *, ExtraArgs):
            self.captured = stream.read()
            self.extra = ExtraArgs

    target = Target()
    backup_operations._stream_copy(Source(), "source", "item", target, "target", "copy")
    assert target.captured == b"artifact"
    assert target.extra == {"Metadata": {"sha256": "abc"}, "ContentType": "text/plain"}
    assert body.closed


def test_pushgateway_metrics_are_sent_as_one_group(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

    def put(url: str, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(backup_operations.httpx, "put", put)
    settings = Settings(pushgateway_url="http://pushgateway:9091")
    backup_operations._push_metrics(settings, "backup", {"first": 1.0, "second": 2.0})
    assert captured["url"] == "http://pushgateway:9091/metrics/job/backup"
    assert "first 1.0" in str(captured["content"])
    assert "second 2.0" in str(captured["content"])


def test_restore_and_load_fixtures_are_explicitly_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_RESTORE", raising=False)
    with pytest.raises(RuntimeError, match="ALLOW_RESTORE"):
        backup_operations.restore(
            Settings(),
            "20260715T120000Z",
            "postgresql+psycopg://tutor:test@localhost/drill",
            "drill-artifacts",
        )
    with pytest.raises(RuntimeError, match="APP_ENV=staging"):
        _guard(Settings(app_env="development"))
