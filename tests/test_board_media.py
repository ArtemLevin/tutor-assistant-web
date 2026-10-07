from __future__ import annotations

import base64
import hashlib
import io
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tutor_assistant_web.app import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.application import BoardPersistenceService
from tutor_assistant_web.modules.boards.media import (
    BoardMediaQuotaExceeded,
    BoardMediaService,
)
from tutor_assistant_web.modules.boards.models import BoardMediaAsset
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import DEFAULT_ORGANIZATION_ID
from tutor_assistant_web.providers.artifacts import LocalArtifactStorage
from tutor_assistant_web.shared.errors import ValidationError

PASSWORD = "test-password"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmc"
    "AAAAASUVORK5CYII="
)
GIF_1X1 = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "app_profile": "board",
        "app_secret_key": "test-secret-for-board-media",
        "database_url": f"sqlite:///{tmp_path / 'board-media.db'}",
        "artifact_storage_root": str(tmp_path / "artifacts"),
        "seed_demo_data": False,
        "bootstrap_admin_password": PASSWORD,
        "otel_exporter_otlp_endpoint": "",
        "board_media_uploads_enabled": True,
        "rate_limit_board_reads": 1000,
        "rate_limit_board_writes": 1000,
        "rate_limit_board_media_uploads": 1000,
    }
    values.update(overrides)
    return Settings(**values)


def _media_service(
    database: Database,
    storage: LocalArtifactStorage,
    *,
    max_assets_per_board: int = 10,
    gif_max_frames: int = 50,
) -> BoardMediaService:
    return BoardMediaService(
        database,
        storage,
        DEFAULT_ORGANIZATION_ID,
        uploads_enabled=True,
        max_asset_bytes=2 * 1024 * 1024,
        max_assets_per_board=max_assets_per_board,
        max_bytes_per_board=4 * 1024 * 1024,
        max_dimension=16_384,
        max_pixels=64_000_000,
        gif_max_frames=gif_max_frames,
        gif_max_decoded_pixels=256_000_000,
    )


def _standalone_board(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'service.db'}")
    database.migrate()
    identity = IdentityService(database)
    identity.bootstrap(Settings(seed_demo_data=False, bootstrap_admin_password=PASSWORD))
    principal = identity.authenticate("admin@localhost", PASSWORD)
    assert principal is not None
    storage = LocalArtifactStorage(tmp_path / "service-artifacts")
    board = BoardPersistenceService(
        database,
        storage,
        DEFAULT_ORGANIZATION_ID,
    ).create_standalone(principal.user_id, "Media board")
    return database, storage, principal, board


def _upload(
    service: BoardMediaService,
    board_id: str,
    content: bytes,
    *,
    mime_type: str,
    file_name: str,
    key: str,
    actor_id: str,
):
    return service.upload(
        board_id,
        io.BytesIO(content),
        declared_mime_type=mime_type,
        file_name=file_name,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        idempotency_key=key,
        created_by_user_id=actor_id,
        created_by_actor_id=actor_id,
    )


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def _login(client: TestClient) -> None:
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": _csrf_from(page.text),
            "email": "admin@localhost",
            "password": PASSWORD,
            "next": "/",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_board_media_service_round_trip_and_idempotency(tmp_path):
    database, storage, principal, board = _standalone_board(tmp_path)
    try:
        service = _media_service(database, storage)
        first = _upload(
            service,
            board.id,
            PNG_1X1,
            mime_type="image/png",
            file_name="../урок.png",
            key="media:test:png",
            actor_id=principal.user_id,
        )
        second = _upload(
            service,
            board.id,
            PNG_1X1,
            mime_type="image/png",
            file_name="../урок.png",
            key="media:test:png",
            actor_id=principal.user_id,
        )

        assert first.id == second.id
        assert first.file_name == "урок.png"
        assert first.intrinsic_width == first.intrinsic_height == 1
        assert first.storage_status == "available"
        assert storage.read(first.storage_key) == PNG_1X1
        assert first.storage_key.startswith(
            f"{DEFAULT_ORGANIZATION_ID}/boards/{board.id}/media/"
        )
    finally:
        database.dispose()


def test_board_media_service_rejects_checksum_quota_and_gif_complexity(tmp_path):
    database, storage, principal, board = _standalone_board(tmp_path)
    try:
        service = _media_service(database, storage, max_assets_per_board=1)
        with pytest.raises(ValidationError, match="SHA256|SHA-256"):
            service.upload(
                board.id,
                io.BytesIO(PNG_1X1),
                declared_mime_type="image/png",
                file_name="bad.png",
                expected_sha256="0" * 64,
                idempotency_key="media:test:bad-sha",
                created_by_user_id=principal.user_id,
                created_by_actor_id=principal.user_id,
            )

        _upload(
            service,
            board.id,
            PNG_1X1,
            mime_type="image/png",
            file_name="first.png",
            key="media:test:first",
            actor_id=principal.user_id,
        )
        with pytest.raises(BoardMediaQuotaExceeded):
            _upload(
                service,
                board.id,
                PNG_1X1,
                mime_type="image/png",
                file_name="second.png",
                key="media:test:second",
                actor_id=principal.user_id,
            )

        two_frame_gif = GIF_1X1[:-1] + GIF_1X1[19:-1] + b";"
        gif_service = _media_service(database, storage, max_assets_per_board=10, gif_max_frames=1)
        with pytest.raises(ValidationError, match="frame count"):
            _upload(
                gif_service,
                board.id,
                two_frame_gif,
                mime_type="image/gif",
                file_name="animated.gif",
                key="media:test:gif",
                actor_id=principal.user_id,
            )

        with database.sessions() as session:
            assert session.scalar(select(func.count(BoardMediaAsset.id))) == 1
    finally:
        database.dispose()


def test_board_media_api_streams_authorized_content_without_storage_key(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database), follow_redirects=False) as client:
        _login(client)
        context = client.get("/api/v1/boards/context").json()
        created = client.post(
            "/api/v1/boards",
            json={"title": "Media board"},
            headers={"x-csrf-token": context["csrfToken"]},
        )
        assert created.status_code == 201
        board_id = created.json()["boardId"]

        missing_csrf = client.post(
            f"/api/v1/boards/{board_id}/media",
            params={"fileName": "lesson.png"},
            content=PNG_1X1,
            headers={
                "content-type": "image/png",
                "x-content-sha256": hashlib.sha256(PNG_1X1).hexdigest(),
                "x-idempotency-key": "media:api:no-csrf",
            },
        )
        assert missing_csrf.status_code == 403

        uploaded = client.post(
            f"/api/v1/boards/{board_id}/media",
            params={"fileName": "lesson.png"},
            content=PNG_1X1,
            headers={
                "content-type": "image/png",
                "x-content-sha256": hashlib.sha256(PNG_1X1).hexdigest(),
                "x-idempotency-key": "media:api:png",
                "x-csrf-token": context["csrfToken"],
            },
        )
        assert uploaded.status_code == 201
        payload = uploaded.json()
        assert payload["mimeType"] == "image/png"
        assert payload["byteSize"] == len(PNG_1X1)
        assert payload["intrinsicSize"] == {"width": 1, "height": 1}
        assert payload["status"] == "available"
        assert "storageKey" not in payload
        asset_id = payload["assetId"]

        metadata = client.get(f"/api/v1/boards/{board_id}/media/{asset_id}")
        assert metadata.status_code == 200
        assert metadata.json() == payload

        content = client.get(f"/api/v1/boards/{board_id}/media/{asset_id}/content")
        assert content.status_code == 200
        assert content.content == PNG_1X1
        assert content.headers["content-type"].startswith("image/png")
        assert content.headers["x-content-type-options"] == "nosniff"
        assert content.headers["x-content-sha256"] == hashlib.sha256(PNG_1X1).hexdigest()
        assert content.headers["etag"] == f'"sha256-{hashlib.sha256(PNG_1X1).hexdigest()}"'

        cached = client.get(
            f"/api/v1/boards/{board_id}/media/{asset_id}/content",
            headers={"if-none-match": content.headers["etag"]},
        )
        assert cached.status_code == 304

        second_board = client.post(
            "/api/v1/boards",
            json={"title": "Other board"},
            headers={"x-csrf-token": context["csrfToken"]},
        ).json()["boardId"]
        cross_board = client.get(
            f"/api/v1/boards/{second_board}/media/{asset_id}/content"
        )
        assert cross_board.status_code == 404
    database.dispose()


def test_board_media_upload_feature_gate_prevents_body_persistence(tmp_path):
    settings = _settings(tmp_path, board_media_uploads_enabled=False)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database), follow_redirects=False) as client:
        _login(client)
        context = client.get("/api/v1/boards/context").json()
        board_id = client.post(
            "/api/v1/boards",
            json={"title": "Disabled media"},
            headers={"x-csrf-token": context["csrfToken"]},
        ).json()["boardId"]
        response = client.post(
            f"/api/v1/boards/{board_id}/media",
            params={"fileName": "lesson.png"},
            content=PNG_1X1,
            headers={
                "content-type": "image/png",
                "x-content-sha256": hashlib.sha256(PNG_1X1).hexdigest(),
                "x-idempotency-key": "media:api:disabled",
                "x-csrf-token": context["csrfToken"],
            },
        )
        assert response.status_code == 503
        with database.sessions() as session:
            assert session.scalar(select(func.count(BoardMediaAsset.id))) == 0
    database.dispose()
