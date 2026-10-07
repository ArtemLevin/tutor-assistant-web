from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tutor_assistant_web.app import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.application import BoardPersistenceService, canonical_json
from tutor_assistant_web.modules.boards.contracts import (
    BoardCommandEnvelopeInput,
    BoardSnapshotInput,
)
from tutor_assistant_web.modules.boards.media import (
    BoardMediaQuotaExceeded,
    BoardMediaService,
)
from tutor_assistant_web.modules.boards.models import BoardMediaAsset
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import DEFAULT_ORGANIZATION_ID
from tutor_assistant_web.providers.artifacts import LocalArtifactStorage
from tutor_assistant_web.shared.board_contracts import board_document_schema
from tutor_assistant_web.shared.errors import ValidationError

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "schemas" / "board" / "v1" / "fixtures"
PASSWORD = "test-password"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII="
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


def _media_object(asset: BoardMediaAsset, *, object_id: str = "object:media-01") -> dict:
    return {
        "groupId": None,
        "id": object_id,
        "locked": False,
        "position": {"x": 20, "y": 30},
        "rotation": 0,
        "scale": {"x": 1, "y": 1},
        "source": {"kind": "user"},
        "style": {
            "fill": None,
            "opacity": 1,
            "stroke": "#1a1a1a",
            "strokeWidth": 1,
        },
        "visible": True,
        "kind": "media.asset",
        "assetId": asset.asset_id,
        "byteSize": asset.byte_size,
        "contentSha256": asset.content_sha256,
        "fileName": asset.file_name,
        "intrinsicSize": {
            "width": asset.intrinsic_width,
            "height": asset.intrinsic_height,
        },
        "mimeType": asset.mime_type,
        "size": {"width": 100, "height": 100},
    }


def _media_envelope(
    board_id: str,
    actor_id: str,
    media_object: dict,
    *,
    base_revision: int = 0,
    idempotency_key: str = "media:command:1",
    expected_document_sha256: str = "0" * 64,
):
    return BoardCommandEnvelopeInput.model_validate(
        {
            "actorId": actor_id,
            "baseRevision": base_revision,
            "commands": [
                {
                    "command": {
                        "actorId": actor_id,
                        "id": f"command:media:{base_revision + 1}",
                        "kind": "core.objects.add",
                        "timestamp": "2026-10-07T18:00:00.000Z",
                        "atIndex": 0,
                        "objects": [media_object],
                    },
                    "order": {
                        "baseRevisionAtCreation": base_revision,
                        "lamport": base_revision + 1,
                    },
                }
            ],
            "documentId": board_id,
            "expectedDocumentSha256": expected_document_sha256,
            "idempotencyKey": idempotency_key,
            "originId": "origin:media-authority-test",
            "schemaVersion": "1.7",
        }
    ).root


def _media_snapshot(
    board_id: str,
    media_object: dict,
    *,
    revision: int = 0,
):
    payload = json.loads((FIXTURES / "board-snapshot.json").read_text())
    payload["documentId"] = board_id
    payload["revision"] = revision
    payload["document"]["id"] = board_id
    payload["document"]["objects"] = {media_object["id"]: media_object}
    payload["document"]["order"] = [media_object["id"]]
    payload["document"]["groups"] = {}
    document = board_document_schema.BoardDocument.model_validate(payload["document"])
    payload["documentSha256"] = canonical_json(document)[2]
    return BoardSnapshotInput.model_validate(payload).root


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
        assert first.storage_key.startswith(f"{DEFAULT_ORGANIZATION_ID}/boards/{board.id}/media/")
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


def test_board_media_service_cleans_up_when_final_reauthorization_fails(tmp_path):
    database, storage, principal, board = _standalone_board(tmp_path)
    try:
        service = _media_service(database, storage)

        def reject_finalization() -> None:
            raise ValidationError("access epoch changed")

        with pytest.raises(ValidationError, match="access epoch changed"):
            service.upload(
                board.id,
                io.BytesIO(PNG_1X1),
                declared_mime_type="image/png",
                file_name="revoked.png",
                expected_sha256=hashlib.sha256(PNG_1X1).hexdigest(),
                idempotency_key="media:test:reauthorize",
                created_by_user_id=principal.user_id,
                created_by_actor_id=principal.user_id,
                reauthorize=reject_finalization,
            )

        with database.sessions() as session:
            asset = session.scalar(
                select(BoardMediaAsset).where(
                    BoardMediaAsset.board_document_id == board.id,
                    BoardMediaAsset.upload_idempotency_key == "media:test:reauthorize",
                )
            )
            assert asset is not None
            assert asset.storage_status == "deleted"
            storage_key = asset.storage_key
        with pytest.raises(FileNotFoundError):
            storage.read(storage_key)
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
        cross_board = client.get(f"/api/v1/boards/{second_board}/media/{asset_id}/content")
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


def test_board_media_reference_authority_accepts_command_and_marks_revision(tmp_path):
    database, storage, principal, board = _standalone_board(tmp_path)
    try:
        media = _upload(
            _media_service(database, storage),
            board.id,
            PNG_1X1,
            mime_type="image/png",
            file_name="lesson.png",
            key="media:authority:command",
            actor_id=principal.user_id,
        )
        boards = BoardPersistenceService(database, storage, DEFAULT_ORGANIZATION_ID)
        envelope = _media_envelope(board.id, principal.user_id, _media_object(media))

        batch = boards.append_commands(envelope, principal.user_id)

        assert batch.revision == 1
        with database.sessions() as session:
            stored = session.scalar(select(BoardMediaAsset).where(BoardMediaAsset.id == media.id))
            assert stored is not None
            assert stored.first_referenced_revision == 1
    finally:
        database.dispose()


def test_board_media_reference_authority_rejects_forged_cross_board_and_unavailable(
    tmp_path,
):
    database, storage, principal, board = _standalone_board(tmp_path)
    try:
        media = _upload(
            _media_service(database, storage),
            board.id,
            PNG_1X1,
            mime_type="image/png",
            file_name="lesson.png",
            key="media:authority:reject",
            actor_id=principal.user_id,
        )
        boards = BoardPersistenceService(database, storage, DEFAULT_ORGANIZATION_ID)

        forged = _media_object(media)
        forged["contentSha256"] = "0" * 64
        with pytest.raises(ValidationError, match="invalid for this board"):
            boards.append_commands(
                _media_envelope(
                    board.id,
                    principal.user_id,
                    forged,
                    idempotency_key="media:command:forged",
                ),
                principal.user_id,
            )

        other_board = boards.create_standalone(principal.user_id, "Other media board")
        with pytest.raises(ValidationError, match="invalid for this board"):
            boards.append_commands(
                _media_envelope(
                    other_board.id,
                    principal.user_id,
                    _media_object(media),
                    idempotency_key="media:command:cross-board",
                ),
                principal.user_id,
            )

        with database.sessions() as session:
            stored = session.scalar(select(BoardMediaAsset).where(BoardMediaAsset.id == media.id))
            assert stored is not None
            stored.storage_status = "quarantined"
            session.commit()

        with pytest.raises(ValidationError, match="invalid for this board"):
            boards.append_commands(
                _media_envelope(
                    board.id,
                    principal.user_id,
                    _media_object(media),
                    idempotency_key="media:command:quarantined",
                ),
                principal.user_id,
            )

        assert boards.get(board.id).current_revision == 0
        with database.sessions() as session:
            stored = session.scalar(select(BoardMediaAsset).where(BoardMediaAsset.id == media.id))
            assert stored is not None
            assert stored.first_referenced_revision is None
    finally:
        database.dispose()


def test_board_media_reference_authority_validates_snapshot_and_marks_revision(tmp_path):
    database, storage, principal, board = _standalone_board(tmp_path)
    try:
        media = _upload(
            _media_service(database, storage),
            board.id,
            PNG_1X1,
            mime_type="image/png",
            file_name="snapshot.png",
            key="media:authority:snapshot",
            actor_id=principal.user_id,
        )
        boards = BoardPersistenceService(database, storage, DEFAULT_ORGANIZATION_ID)
        snapshot = _media_snapshot(board.id, _media_object(media))

        stored_snapshot = boards.save_snapshot(snapshot)

        assert stored_snapshot.storage_status == "available"
        with database.sessions() as session:
            stored = session.scalar(select(BoardMediaAsset).where(BoardMediaAsset.id == media.id))
            assert stored is not None
            assert stored.first_referenced_revision == 0

        forged = _media_object(media, object_id="object:media-forged")
        forged["byteSize"] += 1
        with pytest.raises(ValidationError, match="invalid for this board"):
            boards.save_snapshot(_media_snapshot(board.id, forged))
    finally:
        database.dispose()
