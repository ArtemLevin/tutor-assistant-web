from __future__ import annotations

import hashlib
import io
import subprocess
from pathlib import Path

import pytest
import yaml

from tutor_assistant_web import backup_operations
from tutor_assistant_web.config import Settings
from tutor_assistant_web.load_operations import _guard
from tutor_assistant_web.version import __version__

ROOT = Path(__file__).parents[1]


def test_release_version_and_images_are_immutable_non_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert __version__ == "1.0.0"
    assert "USER tutor:tutor" in dockerfile
    for target in ("web", "worker", "scheduler", "migration", "ops"):
        assert f"AS {target}" in dockerfile
    assert "tutor-assistant-web==1.0.0" in dockerfile


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
    } <= set(services)
    assert "ports" not in services["web-blue"]
    assert "ports" not in services["postgres"]
    assert document["networks"]["backend"]["internal"] is True
    assert services["migration"]["restart"] == "no"


def test_release_shell_scripts_are_syntactically_valid() -> None:
    for script in (ROOT / "deploy" / "production").glob("*.sh"):
        subprocess.run(["sh", "-n", str(script)], check=True)


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
