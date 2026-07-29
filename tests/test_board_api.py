from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from tutor_assistant_web.app import create_app
from tutor_assistant_web.board_evidence_export import export_public_board_evidence
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.audit.models import AuditEvent
from tutor_assistant_web.modules.boards import geometry_gateway
from tutor_assistant_web.modules.boards.application import (
    BoardPersistenceService,
    canonical_json,
)
from tutor_assistant_web.modules.boards.evidence import _utc_milliseconds
from tutor_assistant_web.modules.identity.application import IdentityService
from tutor_assistant_web.modules.identity.models import (
    DEFAULT_ORGANIZATION_ID,
    Membership,
    MembershipRole,
    Organization,
    StudentAccess,
    User,
)
from tutor_assistant_web.modules.scheduling.models import Lesson
from tutor_assistant_web.modules.students.models import Student
from tutor_assistant_web.providers.artifacts import LocalArtifactStorage
from tutor_assistant_web.shared.board_contracts.board_document_schema import BoardDocument10

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "schemas" / "board" / "v1" / "fixtures"
PASSWORD = "test-password"
DOCUMENT_ID = "document:api-lesson"


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "app_secret_key": "test-secret-for-board-api",
        "database_url": f"sqlite:///{tmp_path / 'board-api.db'}",
        "artifact_storage_root": str(tmp_path / "artifacts"),
        "seed_demo_data": False,
        "bootstrap_admin_password": PASSWORD,
        "otel_exporter_otlp_endpoint": "",
        "rate_limit_board_reads": 1000,
        "rate_limit_board_writes": 1000,
    }
    values.update(overrides)
    return Settings(**values)


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def _login(client: TestClient, email: str = "admin@localhost") -> None:
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": _csrf_from(page.text),
            "email": email,
            "password": PASSWORD,
            "next": "/",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _context(client: TestClient) -> dict:
    response = client.get("/api/v1/boards/context")
    assert response.status_code == 200
    return response.json()


def _create_board(client: TestClient, lesson_id: str, csrf: str):
    return client.post(
        f"/api/v1/lessons/{lesson_id}/board",
        json={"documentId": DOCUMENT_ID},
        headers={"x-csrf-token": csrf},
    )


def _command_payload(user_id: str, *, base_revision: int = 0, key: str = "api:batch-1"):
    payload = json.loads((FIXTURES / "board-command-envelope.json").read_text())
    payload.update(
        {
            "documentId": DOCUMENT_ID,
            "baseRevision": base_revision,
            "idempotencyKey": key,
            "actorId": user_id,
        }
    )
    for command in payload["commands"]:
        command["actorId"] = user_id
    return payload


def _snapshot_payload(*, revision: int = 0):
    payload = json.loads((FIXTURES / "board-snapshot.json").read_text())
    payload["documentId"] = DOCUMENT_ID
    payload["revision"] = revision
    payload["document"]["id"] = DOCUMENT_ID
    document = BoardDocument10.model_validate(payload["document"])
    payload["documentSha256"] = canonical_json(document)[2]
    return payload


def _seed_lesson(database: Database, *, organization_id: str = DEFAULT_ORGANIZATION_ID):
    with database.sessions() as session:
        student = Student(organization_id=organization_id, full_name="Board API Student")
        session.add(student)
        session.flush()
        lesson = Lesson(
            organization_id=organization_id,
            student_id=student.id,
            title="Board API lesson",
            starts_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(hours=1),
            bbb_meeting_id=f"board-api-{student.id}",
            attendee_password="attendee",
            moderator_password="moderator",
        )
        session.add(lesson)
        session.commit()
        return student.id, lesson.id


def _add_recipient(
    database: Database,
    student_id: str,
    *,
    email: str,
    role: MembershipRole,
) -> str:
    identity = IdentityService(database)
    with database.sessions() as session:
        user = User(
            email=email,
            full_name=f"Board {role.value}",
            password_hash=identity.passwords.hash(PASSWORD),
        )
        session.add(user)
        session.flush()
        session.add(
            Membership(
                organization_id=DEFAULT_ORGANIZATION_ID,
                user_id=user.id,
                role=role.value,
            )
        )
        session.add(
            StudentAccess(
                organization_id=DEFAULT_ORGANIZATION_ID,
                student_id=student_id,
                user_id=user.id,
                role=role.value,
            )
        )
        session.commit()
        return user.id


@pytest.fixture()
def board_api(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database)) as client:
        student_id, lesson_id = _seed_lesson(database)
        _login(client)
        context = _context(client)
        response = _create_board(client, lesson_id, context["csrfToken"])
        assert response.status_code == 201
        yield client, database, settings, student_id, lesson_id, context
    database.dispose()


def test_board_api_requires_authentication_and_csrf(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database)) as client:
        _, lesson_id = _seed_lesson(database)
        assert client.get("/api/v1/boards/context").status_code == 401
        _login(client)
        rejected = client.post(
            f"/api/v1/lessons/{lesson_id}/board",
            json={"documentId": DOCUMENT_ID},
        )
        assert rejected.status_code == 403
        assert rejected.json()["detail"] == "CSRF-токен отсутствует или устарел"
    database.dispose()


def test_teacher_board_flow_revision_conflict_snapshot_and_audit(board_api):
    client, database, _, _, lesson_id, context = board_api
    csrf = context["csrfToken"]
    user_id = context["userId"]

    repeated_create = _create_board(client, lesson_id, csrf)
    assert repeated_create.status_code == 200

    created = client.get(f"/api/v1/boards/{DOCUMENT_ID}")
    assert created.status_code == 200
    assert created.headers["etag"] == '"board-revision-0"'
    assert created.json()["board"]["currentRevision"] == 0
    assert created.json()["snapshot"] is None

    first_payload = _command_payload(user_id)
    first_payload["expectedDocumentSha256"] = _snapshot_payload()["documentSha256"]
    appended = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=first_payload,
        headers={"x-csrf-token": csrf},
    )
    assert appended.status_code == 200
    assert appended.json()["revision"] == 1
    assert appended.headers["etag"] == '"board-revision-1"'
    repeated_append = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=first_payload,
        headers={"x-csrf-token": csrf},
    )
    assert repeated_append.status_code == 200
    assert repeated_append.json()["revision"] == 1

    conflict_payload = _command_payload(user_id, key="api:batch-2")
    conflict_payload["expectedDocumentSha256"] = first_payload["expectedDocumentSha256"]
    conflict = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=conflict_payload,
        headers={"x-csrf-token": csrf},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["currentRevision"] == 1
    assert conflict.json()["missingCommandBatches"][0]["revision"] == 1

    commands = client.get(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        params={"afterRevision": 0},
    )
    assert commands.status_code == 200
    assert commands.json()["items"][0]["envelope"]["actorId"] == user_id

    snapshot_payload = _snapshot_payload(revision=1)
    saved = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/snapshots",
        json=snapshot_payload,
        headers={"x-csrf-token": csrf},
    )
    assert saved.status_code == 201
    assert saved.json()["status"] == "available"

    recovered = client.get(f"/api/v1/boards/{DOCUMENT_ID}")
    assert recovered.json()["snapshot"]["revision"] == 1
    assert recovered.json()["commandBatches"] == []

    with database.sessions() as session:
        actions = list(session.scalars(select(AuditEvent.action)))
    assert {
        "board.created",
        "board.commands.appended",
        "board.snapshot.saved",
    }.issubset(set(actions))
    assert actions.count("board.created") == 1
    assert actions.count("board.commands.appended") == 1


def test_actor_id_is_bound_to_authenticated_user(board_api):
    client, _, _, _, _, context = board_api
    response = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=_command_payload("spoofed-user"),
        headers={"x-csrf-token": context["csrfToken"]},
    )
    assert response.status_code == 403
    assert "actorId" in response.json()["detail"]


def test_teacher_soft_delete_is_audited_and_returns_gone(board_api):
    client, database, _, _, _, context = board_api
    deleted = client.delete(
        f"/api/v1/boards/{DOCUMENT_ID}",
        headers={"x-csrf-token": context["csrfToken"]},
    )
    assert deleted.status_code == 204
    gone = client.get(f"/api/v1/boards/{DOCUMENT_ID}")
    assert gone.status_code == 410
    assert gone.json()["error"]["code"] == "GoneError"
    with database.sessions() as session:
        assert "board.deleted" in set(session.scalars(select(AuditEvent.action)))


def test_validation_error_does_not_echo_board_content(board_api):
    client, _, _, _, _, context = board_api
    marker = "PRIVATE-BOARD-CONTENT-MUST-NOT-BE-ECHOED"
    response = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json={
            "schemaVersion": "1.0",
            "documentId": DOCUMENT_ID,
            "private": marker,
        },
        headers={"x-csrf-token": context["csrfToken"]},
    )
    assert response.status_code == 422
    assert marker not in response.text


def test_student_can_edit_assigned_board_parent_is_read_only(board_api):
    client, database, _, student_id, _, _ = board_api
    student_user_id = _add_recipient(
        database,
        student_id,
        email="student-board@example.test",
        role=MembershipRole.student,
    )
    _add_recipient(
        database,
        student_id,
        email="parent-board@example.test",
        role=MembershipRole.parent,
    )

    client.cookies.clear()
    _login(client, "student-board@example.test")
    student_context = _context(client)
    assert client.get(f"/api/v1/boards/{DOCUMENT_ID}").status_code == 200
    edited = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=_command_payload(student_user_id),
        headers={"x-csrf-token": student_context["csrfToken"]},
    )
    assert edited.status_code == 200
    deleted = client.delete(
        f"/api/v1/boards/{DOCUMENT_ID}",
        headers={"x-csrf-token": student_context["csrfToken"]},
    )
    assert deleted.status_code == 403

    client.cookies.clear()
    _login(client, "parent-board@example.test")
    parent_context = _context(client)
    assert client.get(f"/api/v1/boards/{DOCUMENT_ID}").status_code == 200
    rejected = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=_command_payload(parent_context["userId"], base_revision=1, key="parent:batch"),
        headers={"x-csrf-token": parent_context["csrfToken"]},
    )
    assert rejected.status_code == 403
    assert "только для чтения" in rejected.json()["error"]["message"]


def test_cross_student_and_cross_tenant_boards_are_not_disclosed(board_api, tmp_path):
    client, database, settings, assigned_student_id, _, _ = board_api
    _add_recipient(
        database,
        assigned_student_id,
        email="limited-student@example.test",
        role=MembershipRole.student,
    )
    other_student_id, other_lesson_id = _seed_lesson(database)
    BoardPersistenceService(
        database,
        LocalArtifactStorage(settings.artifact_storage_root),
        DEFAULT_ORGANIZATION_ID,
    ).create_for_lesson(other_lesson_id, "document:other-student")

    with database.sessions() as session:
        other_org = Organization(name="Other Board Org", slug="other-board-api")
        session.add(other_org)
        session.commit()
        other_org_id = other_org.id
    _, other_tenant_lesson_id = _seed_lesson(database, organization_id=other_org_id)
    BoardPersistenceService(
        database,
        LocalArtifactStorage(tmp_path / "other-artifacts"),
        other_org_id,
    ).create_for_lesson(other_tenant_lesson_id, "document:other-tenant")

    client.cookies.clear()
    _login(client, "limited-student@example.test")
    assert client.get("/api/v1/boards/document:other-student").status_code == 404
    assert client.get("/api/v1/boards/document:other-tenant").status_code == 404


def test_board_api_rejects_oversized_body_and_rate_limits_writes(tmp_path):
    settings = _settings(
        tmp_path,
        board_command_max_size_mb=1,
        rate_limit_board_writes=2,
    )
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database)) as client:
        _, lesson_id = _seed_lesson(database)
        _login(client)
        context = _context(client)
        first = _create_board(client, lesson_id, context["csrfToken"])
        assert first.status_code == 201

        oversized = client.post(
            f"/api/v1/boards/{DOCUMENT_ID}/commands",
            content=b"{" + b" " * (1024 * 1024),
            headers={
                "content-type": "application/json",
                "x-csrf-token": context["csrfToken"],
            },
        )
        assert oversized.status_code == 413

        limited = client.delete(
            f"/api/v1/boards/{DOCUMENT_ID}",
            headers={"x-csrf-token": context["csrfToken"]},
        )
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == str(settings.rate_limit_window_seconds)
    database.dispose()


def test_board_archive_history_and_lesson_listing(board_api):
    client, _, _, _, lesson_id, context = board_api
    csrf = context["csrfToken"]
    user_id = context["userId"]
    command = _command_payload(user_id)
    command["expectedDocumentSha256"] = _snapshot_payload()["documentSha256"]
    assert (
        client.post(
            f"/api/v1/boards/{DOCUMENT_ID}/commands",
            json=command,
            headers={"x-csrf-token": csrf},
        ).status_code
        == 200
    )

    history = client.get(f"/api/v1/boards/{DOCUMENT_ID}/revisions")
    assert history.status_code == 200
    assert [item["revision"] for item in history.json()["items"]] == [0, 1]
    assert history.json()["items"][1] == {
        "revision": 1,
        "documentSha256": command["expectedDocumentSha256"],
        "actorUserId": user_id,
        "createdAt": history.json()["items"][1]["createdAt"],
        "snapshotAvailable": False,
    }
    historical = client.get(f"/api/v1/boards/{DOCUMENT_ID}/revisions/1")
    assert historical.status_code == 200
    assert historical.json()["board"]["requestedRevision"] == 1
    assert historical.json()["commandBatches"][0]["revision"] == 1

    archived = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/archive",
        headers={"x-csrf-token": csrf},
    )
    assert archived.status_code == 200
    assert archived.json()["archivedAt"]
    active = client.get(
        f"/api/v1/lessons/{lesson_id}/boards",
        params={"includeArchived": "false"},
    )
    assert active.status_code == 200
    assert active.json()["items"] == []
    rejected = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/commands",
        json=_command_payload(user_id, base_revision=1, key="archived:write"),
        headers={"x-csrf-token": csrf},
    )
    assert rejected.status_code == 410

    restored = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/unarchive",
        headers={"x-csrf-token": csrf},
    )
    assert restored.status_code == 200
    assert restored.json()["archivedAt"] is None


def test_board_evidence_is_immutable_and_published_to_student(board_api):
    client, database, settings, student_id, lesson_id, context = board_api
    csrf = context["csrfToken"]
    user_id = context["userId"]
    command = _command_payload(user_id)
    snapshot = _snapshot_payload(revision=1)
    command["expectedDocumentSha256"] = snapshot["documentSha256"]
    assert (
        client.post(
            f"/api/v1/boards/{DOCUMENT_ID}/commands",
            json=command,
            headers={"x-csrf-token": csrf},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/boards/{DOCUMENT_ID}/snapshots",
            json=snapshot,
            headers={"x-csrf-token": csrf},
        ).status_code
        == 201
    )
    preview_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<rect width="100" height="100" fill="#fff"/></svg>'
    )
    payload = {
        "schemaVersion": "1.0",
        "revision": 1,
        "documentSha256": snapshot["documentSha256"],
        "previewSvg": preview_svg,
        "previewPngBase64": "",
        "transcriptLinks": [{"label": "Разбор задачи", "startMs": 1200, "endMs": 4800}],
    }
    finalized = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/evidence",
        json=payload,
        headers={"x-csrf-token": csrf},
    )
    assert finalized.status_code == 201
    evidence = finalized.json()
    assert evidence["revision"] == 1
    assert evidence["publishedAt"] is None
    artifact = client.get(evidence["artifacts"]["svg"])
    assert artifact.status_code == 200
    assert artifact.content == preview_svg.encode()
    assert artifact.headers["etag"].startswith('"sha256-')
    manifest = client.get(evidence["artifacts"]["manifest"]).json()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", manifest["finalizedAt"])

    repeated = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/evidence",
        json=payload,
        headers={"x-csrf-token": csrf},
    )
    assert repeated.status_code == 201
    assert repeated.json()["evidenceId"] == evidence["evidenceId"]
    changed = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/evidence",
        json={**payload, "previewSvg": preview_svg.replace("#fff", "#000")},
        headers={"x-csrf-token": csrf},
    )
    assert changed.status_code == 409

    _add_recipient(
        database,
        student_id,
        email="evidence-student@example.test",
        role=MembershipRole.student,
    )
    client.cookies.clear()
    _login(client, "evidence-student@example.test")
    assert client.get(f"/api/v1/lessons/{lesson_id}/board-evidence").json()["items"] == []
    assert client.get(evidence["artifacts"]["svg"]).status_code == 404

    client.cookies.clear()
    _login(client)
    admin_context = _context(client)
    published = client.post(
        f"/api/v1/board-evidence/{evidence['evidenceId']}/publish",
        headers={"x-csrf-token": admin_context["csrfToken"]},
    )
    assert published.status_code == 200
    assert published.json()["publishedAt"]
    repeated_publish = client.post(
        f"/api/v1/board-evidence/{evidence['evidenceId']}/publish",
        headers={"x-csrf-token": admin_context["csrfToken"]},
    )
    assert repeated_publish.status_code == 200
    exported = export_public_board_evidence(
        database,
        LocalArtifactStorage(settings.artifact_storage_root),
        DEFAULT_ORGANIZATION_ID,
        evidence["evidenceId"],
        Path(settings.artifact_storage_root).parent / "student-export",
    )
    public_manifest = json.loads((exported / "manifest.json").read_text())
    assert public_manifest == {
        "assets": {"preview": "preview.svg"},
        "board": {"revision": 1, "title": "Итоговая доска занятия"},
        "schemaVersion": "1.0",
    }
    assert (exported / "preview.svg").read_text() == preview_svg

    client.cookies.clear()
    _login(client, "evidence-student@example.test")
    visible = client.get(f"/api/v1/lessons/{lesson_id}/board-evidence")
    assert visible.status_code == 200
    assert [item["evidenceId"] for item in visible.json()["items"]] == [evidence["evidenceId"]]
    assert client.get(evidence["artifacts"]["svg"]).status_code == 200
    with database.sessions() as session:
        actions = list(session.scalars(select(AuditEvent.action)))
    assert actions.count("board.evidence.finalized") == 1
    assert actions.count("board.evidence.published") == 1


def test_board_evidence_timestamp_is_stable_after_sqlite_timezone_round_trip():
    instant = datetime(2026, 7, 28, 20, 0, 0, 123456, tzinfo=UTC)
    assert _utc_milliseconds(instant) == "2026-07-28T20:00:00.123Z"
    assert _utc_milliseconds(instant.replace(tzinfo=None)) == "2026-07-28T20:00:00.123Z"


def test_collaboration_ticket_is_one_time_and_room_is_revision_only(board_api):
    client, _, _, _, _, context = board_api
    response = client.post(
        f"/api/v1/boards/{DOCUMENT_ID}/collaboration-ticket",
        json={"clientId": "browser-a"},
        headers={"x-csrf-token": context["csrfToken"]},
    )
    assert response.status_code == 200
    ticket = response.json()
    assert ticket["protocolVersion"] == "1.0"
    websocket_url = f"{ticket['websocketPath']}?ticket={ticket['ticket']}"

    with client.websocket_connect(
        websocket_url,
        subprotocols=["tutorboard.v1"],
    ) as websocket:
        ready = websocket.receive_json()
        assert ready == {
            "type": "ready",
            "protocolVersion": "1.0",
            "documentId": DOCUMENT_ID,
            "clientId": "browser-a",
            "currentRevision": 0,
            "heartbeatSeconds": 20,
        }
        websocket.send_json(
            {
                "type": "presence",
                "sequence": 1,
                "cursor": {"x": 10, "y": 20},
                "viewport": {"x": 0, "y": 0, "zoom": 1},
                "selectedObjectIds": [],
            }
        )
        websocket.send_text('{"type":"heartbeat"}')
        assert websocket.receive_json() == {"type": "heartbeat.ack"}

    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            websocket_url,
            subprotocols=["tutorboard.v1"],
        ) as websocket,
    ):
        websocket.receive_json()


def test_geometryos_gateway_is_authenticated_bounded_and_correlated(
    board_api,
    monkeypatch,
):
    client, _, _, _, _, _ = board_api
    captured: list[tuple[str, str, bytes, dict]] = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "http://geometryos:8000"
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, path, *, content, headers):
            captured.append((method, path, content, headers))
            return httpx.Response(
                200,
                json={"status": "ready" if method == "GET" else "ok"},
                headers={
                    "content-type": "application/json",
                    "x-request-id": headers["X-Request-ID"],
                },
            )

    monkeypatch.setattr(geometry_gateway.httpx, "AsyncClient", FakeAsyncClient)
    ready = client.get("/api/v1/geometryos/ready")
    assert ready.status_code == 200
    generated = client.post(
        "/api/v1/geometryos/api/v1/generate",
        json={"schema_version": "1.0", "prompt": "triangle"},
    )
    assert generated.status_code == 200
    assert captured[0][0:3] == ("GET", "/ready", b"")
    assert captured[1][0:2] == ("POST", "/api/v1/generate")
    assert captured[1][3]["X-Request-ID"] == generated.headers["x-request-id"]


def test_board_client_telemetry_is_allowlisted_and_content_free(board_api):
    client, _, _, _, _, context = board_api
    accepted = client.post(
        "/api/v1/boards/client-events",
        json={
            "name": "collaboration.connection",
            "outcome": "recovered",
            "durationMs": 1200,
        },
        headers={"x-csrf-token": context["csrfToken"]},
    )
    assert accepted.status_code == 204
    marker = "PRIVATE-BOARD-CONTENT"
    rejected = client.post(
        "/api/v1/boards/client-events",
        json={"name": "custom", "outcome": "success", "content": marker},
        headers={"x-csrf-token": context["csrfToken"]},
    )
    assert rejected.status_code == 422
    assert marker not in rejected.text
