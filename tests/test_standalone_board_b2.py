from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

import tutor_assistant_web.db as db_module
from tutor_assistant_web.app import create_app
from tutor_assistant_web.config import Settings
from tutor_assistant_web.db import Database
from tutor_assistant_web.modules.boards.application import canonical_json
from tutor_assistant_web.modules.boards.models import BoardDocument, BoardInvitation
from tutor_assistant_web.modules.boards.standalone_contracts import (
    GuestBoardAccessContext,
    TeacherBoardAccessContext,
)
from tutor_assistant_web.observability import redact
from tutor_assistant_web.shared.board_contracts.board_document_schema import BoardDocument14

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "schemas" / "board" / "v1" / "fixtures"
PASSWORD = "test-password"


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "app_secret_key": "test-secret-for-standalone-board-b2",
        "database_url": f"sqlite:///{tmp_path / 'standalone-b2.db'}",
        "artifact_storage_root": str(tmp_path / "artifacts"),
        "seed_demo_data": False,
        "bootstrap_admin_password": PASSWORD,
        "otel_exporter_otlp_endpoint": "",
        "session_cookie_secure": False,
        "public_base_url": "http://testserver",
        "rate_limit_invitations": 1000,
        "rate_limit_board_reads": 1000,
        "rate_limit_board_writes": 1000,
    }
    values.update(overrides)
    return Settings(**values)


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


def _legacy_context(client: TestClient) -> dict:
    response = client.get("/api/v1/boards/context")
    assert response.status_code == 200
    return response.json()


def _create_board(client: TestClient, csrf: str, title: str = "B2 board") -> str:
    response = client.post(
        "/api/v1/boards",
        json={"title": title},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 201
    return response.json()["boardId"]


def _create_invitation(
    client: TestClient,
    board_id: str,
    csrf: str,
    *,
    display_name: str = "Guest Student",
    write_enabled: bool = True,
    expires_at: datetime | None = None,
) -> dict:
    body: dict[str, object] = {
        "displayName": display_name,
        "writeEnabled": write_enabled,
    }
    if expires_at is not None:
        body["expiresAt"] = expires_at.isoformat()
    response = client.post(
        f"/api/v1/boards/{board_id}/invitations",
        json=body,
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _join_path(invitation_result: dict) -> tuple[str, str]:
    parsed = urlsplit(invitation_result["joinUrl"])
    secret = parsed.path.rsplit("/", 1)[1]
    assert secret
    return parsed.path, secret


def _become_guest(
    client: TestClient,
    settings: Settings,
    invitation_result: dict,
) -> tuple[dict, str]:
    client.cookies.delete(settings.session_cookie_name)
    join_path, secret = _join_path(invitation_result)
    joined = client.get(join_path, follow_redirects=False)
    assert joined.status_code == 303, joined.text
    assert joined.headers["location"].endswith("#/board")
    assert joined.headers["cache-control"] == "no-store"
    assert joined.headers["referrer-policy"] == "no-referrer"
    assert joined.headers["x-robots-tag"] == "noindex, nofollow"
    set_cookie = joined.headers["set-cookie"]
    assert settings.board_guest_cookie_name in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert secret not in set_cookie
    context_response = client.get("/api/v1/boards/context")
    assert context_response.status_code == 200, context_response.text
    context = context_response.json()
    GuestBoardAccessContext.model_validate(context)
    return context, secret


def _command_payload(
    board_id: str,
    actor_id: str,
    *,
    base_revision: int = 0,
    key: str = "b2:guest-batch-1",
    lamport_start: int = 1,
) -> dict:
    payload = json.loads((FIXTURES / "board-command-envelope.json").read_text())
    payload.update(
        {
            "documentId": board_id,
            "baseRevision": base_revision,
            "idempotencyKey": key,
            "actorId": actor_id,
            "originId": "origin:b2-guest",
        }
    )
    for index, item in enumerate(payload["commands"]):
        command = item.get("command", item)
        command["actorId"] = actor_id
        order = item.get("order")
        if order is not None:
            order["baseRevisionAtCreation"] = base_revision
            order["lamport"] = lamport_start + index
    return payload


def _snapshot_payload(board_id: str, *, revision: int = 0) -> dict:
    payload = json.loads((FIXTURES / "board-snapshot.json").read_text())
    payload["documentId"] = board_id
    payload["revision"] = revision
    payload["document"]["id"] = board_id
    document = BoardDocument14.model_validate(payload["document"])
    payload["documentSha256"] = canonical_json(document)[2]
    return payload


@pytest.fixture()
def b2_api(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings.database_url)
    app = create_app(settings, database)
    with TestClient(app) as client:
        _login(client)
        teacher_context = _legacy_context(client)
        board_id = _create_board(client, teacher_context["csrfToken"])
        yield client, database, settings, board_id, teacher_context
    database.dispose()


def test_invitation_secret_is_transient_and_guest_context_is_least_privilege(b2_api):
    client, database, settings, board_id, teacher = b2_api
    result = _create_invitation(client, board_id, teacher["csrfToken"])
    join_path, raw_secret = _join_path(result)
    summary = result["invitation"]
    assert set(summary) == {
        "schemaVersion",
        "invitationId",
        "boardId",
        "displayName",
        "writeEnabled",
        "expiresAt",
        "revokedAt",
        "createdAt",
        "lastUsedAt",
        "useCount",
    }
    assert raw_secret not in json.dumps(summary)

    with database.sessions() as session:
        invitation = session.get(BoardInvitation, summary["invitationId"])
        assert invitation is not None
        assert len(invitation.secret_digest) == 64
        assert raw_secret != invitation.secret_digest
        assert raw_secret not in invitation.secret_digest

    listed = client.get(f"/api/v1/boards/{board_id}/invitations")
    assert listed.status_code == 200
    assert "joinUrl" not in json.dumps(listed.json())
    assert raw_secret not in listed.text

    guest, _ = _become_guest(client, settings, result)
    assert guest["principalType"] == "guest"
    assert guest["boardId"] == board_id
    assert guest["role"] == "student"
    assert "organizationId" not in guest
    assert "userId" not in guest
    assert set(guest["capabilities"]) == {
        "board.read",
        "board.write",
        "board.snapshot.write",
        "collaboration.connect",
    }
    assert len(guest["cacheScopeId"]) >= 8
    assert len(guest["accessEpoch"]) >= 8

    recovered = client.get(f"/api/v1/boards/{board_id}")
    assert recovered.status_code == 200
    assert recovered.json()["board"]["documentId"] == board_id
    assert recovered.headers["x-csrf-token"] == guest["csrfToken"]
    assert join_path.startswith("/j/")


def test_guest_can_persist_snapshot_commands_and_get_bound_collaboration_ticket(b2_api):
    client, _, settings, board_id, teacher = b2_api
    result = _create_invitation(client, board_id, teacher["csrfToken"])
    guest, _ = _become_guest(client, settings, result)
    headers = {
        "x-csrf-token": guest["csrfToken"],
        "x-board-access-epoch": guest["accessEpoch"],
    }

    snapshot = client.post(
        f"/api/v1/boards/{board_id}/snapshots",
        json=_snapshot_payload(board_id),
        headers=headers,
    )
    assert snapshot.status_code == 201, snapshot.text

    appended = client.post(
        f"/api/v1/boards/{board_id}/commands",
        json=_command_payload(board_id, guest["actorId"]),
        headers=headers,
    )
    assert appended.status_code == 200, appended.text
    assert appended.json()["revision"] == 1

    commands = client.get(f"/api/v1/boards/{board_id}/commands?afterRevision=0")
    assert commands.status_code == 200
    assert commands.json()["items"][0]["actorUserId"] is None
    assert commands.json()["items"][0]["envelope"]["actorId"] == guest["actorId"]

    ticket = client.post(
        f"/api/v1/boards/{board_id}/collaboration-ticket",
        json={"clientId": "guest-client-b2"},
        headers={"x-csrf-token": guest["csrfToken"]},
    )
    assert ticket.status_code == 200, ticket.text
    assert ticket.json()["ticket"]
    assert ticket.json()["websocketPath"].endswith(f"/{board_id}/collaboration")


def test_guest_csrf_and_access_epoch_are_required_for_durable_writes(b2_api):
    client, _, settings, board_id, teacher = b2_api
    result = _create_invitation(client, board_id, teacher["csrfToken"])
    guest, _ = _become_guest(client, settings, result)
    payload = _command_payload(board_id, guest["actorId"])

    no_epoch = client.post(
        f"/api/v1/boards/{board_id}/commands",
        json=payload,
        headers={"x-csrf-token": guest["csrfToken"]},
    )
    assert no_epoch.status_code == 409
    assert no_epoch.json()["code"] == "access_epoch_changed"

    bad_csrf = client.post(
        f"/api/v1/boards/{board_id}/commands",
        json=payload,
        headers={
            "x-csrf-token": "wrong-guest-csrf",
            "x-board-access-epoch": guest["accessEpoch"],
        },
    )
    assert bad_csrf.status_code == 403
    assert bad_csrf.json()["code"] == "guest_session_invalid"


def test_teacher_principal_wins_when_teacher_and_guest_cookies_coexist(b2_api):
    client, _, settings, board_id, teacher = b2_api
    result = _create_invitation(client, board_id, teacher["csrfToken"])
    guest, _ = _become_guest(client, settings, result)
    guest_cookie = client.cookies.get(settings.board_guest_cookie_name)
    assert guest_cookie

    _login(client)
    assert client.cookies.get(settings.board_guest_cookie_name) == guest_cookie
    legacy = _legacy_context(client)
    assert set(legacy) == {"userId", "organizationId", "role", "csrfToken"}

    strict = client.get(f"/api/v1/boards/context?boardId={board_id}")
    assert strict.status_code == 200
    teacher_strict = strict.json()
    TeacherBoardAccessContext.model_validate(teacher_strict)
    assert teacher_strict["principalType"] == "teacher"
    assert teacher_strict["actorId"] == legacy["userId"]
    assert teacher_strict["cacheScopeId"] != guest["cacheScopeId"]


def test_two_invitations_have_distinct_cache_scopes_and_actor_ids(b2_api):
    client, _, settings, board_id, teacher = b2_api
    first = _create_invitation(
        client,
        board_id,
        teacher["csrfToken"],
        display_name="Guest A",
    )
    second = _create_invitation(
        client,
        board_id,
        teacher["csrfToken"],
        display_name="Guest B",
    )

    guest_a, _ = _become_guest(client, settings, first)
    client.cookies.delete(settings.board_guest_cookie_name)
    guest_b, _ = _become_guest(client, settings, second)
    assert guest_a["cacheScopeId"] != guest_b["cacheScopeId"]
    assert guest_a["actorId"] != guest_b["actorId"]
    assert guest_a["accessEpoch"] != guest_b["accessEpoch"]


def test_guest_write_switch_changes_epoch_and_stale_epoch_never_writes(b2_api):
    client, _, settings, board_id, teacher = b2_api
    result = _create_invitation(client, board_id, teacher["csrfToken"])
    guest_initial, _ = _become_guest(client, settings, result)
    old_epoch = guest_initial["accessEpoch"]

    _login(client)
    teacher_after_login = _legacy_context(client)
    disabled = client.patch(
        f"/api/v1/boards/{board_id}",
        json={"guestWritesEnabled": False},
        headers={"x-csrf-token": teacher_after_login["csrfToken"]},
    )
    assert disabled.status_code == 200
    client.cookies.delete(settings.session_cookie_name)
    read_only = client.get("/api/v1/boards/context").json()
    assert "board.write" not in read_only["capabilities"]
    assert read_only["accessEpoch"] != old_epoch

    read_only_write = client.post(
        f"/api/v1/boards/{board_id}/commands",
        json=_command_payload(board_id, read_only["actorId"]),
        headers={
            "x-csrf-token": read_only["csrfToken"],
            "x-board-access-epoch": read_only["accessEpoch"],
        },
    )
    assert read_only_write.status_code == 403
    assert read_only_write.json()["code"] == "board_read_only"

    _login(client)
    teacher_after_login = _legacy_context(client)
    enabled = client.patch(
        f"/api/v1/boards/{board_id}",
        json={"guestWritesEnabled": True},
        headers={"x-csrf-token": teacher_after_login["csrfToken"]},
    )
    assert enabled.status_code == 200
    client.cookies.delete(settings.session_cookie_name)
    guest_new = client.get("/api/v1/boards/context").json()
    assert "board.write" in guest_new["capabilities"]
    assert guest_new["accessEpoch"] not in {old_epoch, read_only["accessEpoch"]}

    stale = client.post(
        f"/api/v1/boards/{board_id}/commands",
        json=_command_payload(board_id, guest_new["actorId"], key="b2:stale-epoch"),
        headers={
            "x-csrf-token": guest_new["csrfToken"],
            "x-board-access-epoch": old_epoch,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "access_epoch_changed"
    board = client.get(f"/api/v1/boards/{board_id}").json()["board"]
    assert board["currentRevision"] == 0


def test_invitation_write_switch_changes_only_target_guest_scope(b2_api):
    client, _, settings, board_id, teacher = b2_api
    first = _create_invitation(client, board_id, teacher["csrfToken"], display_name="A")
    second = _create_invitation(client, board_id, teacher["csrfToken"], display_name="B")
    first_id = first["invitation"]["invitationId"]

    guest_a, _ = _become_guest(client, settings, first)
    _login(client)
    teacher_now = _legacy_context(client)
    changed = client.patch(
        f"/api/v1/boards/{board_id}/invitations/{first_id}",
        json={"writeEnabled": False},
        headers={"x-csrf-token": teacher_now["csrfToken"]},
    )
    assert changed.status_code == 200
    client.cookies.delete(settings.session_cookie_name)
    guest_a_new = client.get("/api/v1/boards/context").json()
    assert guest_a_new["accessEpoch"] != guest_a["accessEpoch"]
    assert "board.write" not in guest_a_new["capabilities"]

    client.cookies.clear()
    guest_b, _ = _become_guest(client, settings, second)
    assert "board.write" in guest_b["capabilities"]


def test_revoke_and_rotate_invalidate_old_credentials_and_public_join_is_non_enumerating(b2_api):
    client, _, settings, board_id, teacher = b2_api
    result = _create_invitation(client, board_id, teacher["csrfToken"])
    join_path, old_secret = _join_path(result)
    invitation_id = result["invitation"]["invitationId"]
    guest, _ = _become_guest(client, settings, result)
    assert guest["principalType"] == "guest"

    _login(client)
    teacher_now = _legacy_context(client)
    revoked = client.post(
        f"/api/v1/boards/{board_id}/invitations/{invitation_id}/revoke",
        headers={"x-csrf-token": teacher_now["csrfToken"]},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revokedAt"] is not None
    client.cookies.delete(settings.session_cookie_name)

    stale_session = client.get("/api/v1/boards/context")
    assert stale_session.status_code == 401
    assert stale_session.json()["code"] == "guest_session_version_mismatch"
    assert settings.board_guest_cookie_name in stale_session.headers.get("set-cookie", "")

    revoked_join = client.get(join_path, follow_redirects=False)
    random_join = client.get("/j/not-a-real-invitation-secret-00000000", follow_redirects=False)
    assert revoked_join.status_code == random_join.status_code == 404
    assert (
        revoked_join.json()
        == random_join.json()
        == {
            "code": "invitation_invalid",
            "detail": "This invitation link is unavailable.",
        }
    )
    assert old_secret not in revoked_join.text

    _login(client)
    teacher_now = _legacy_context(client)
    rotated = client.post(
        f"/api/v1/boards/{board_id}/invitations/{invitation_id}/rotate",
        headers={"x-csrf-token": teacher_now["csrfToken"]},
    )
    assert rotated.status_code == 200
    new_path, new_secret = _join_path(rotated.json())
    assert new_secret != old_secret
    assert client.get(join_path, follow_redirects=False).status_code == 404
    client.cookies.delete(settings.session_cookie_name)
    new_join = client.get(new_path, follow_redirects=False)
    assert new_join.status_code == 303


def test_expired_invitation_and_past_expiry_do_not_leak_state(b2_api):
    client, database, settings, board_id, teacher = b2_api
    past = client.post(
        f"/api/v1/boards/{board_id}/invitations",
        json={
            "displayName": "Past",
            "writeEnabled": True,
            "expiresAt": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
        headers={"x-csrf-token": teacher["csrfToken"]},
    )
    assert past.status_code == 422

    result = _create_invitation(
        client,
        board_id,
        teacher["csrfToken"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    join_path, _ = _join_path(result)
    invitation_id = result["invitation"]["invitationId"]
    guest, _ = _become_guest(client, settings, result)
    with database.sessions() as session:
        invitation = session.get(BoardInvitation, invitation_id)
        assert invitation is not None
        invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    expired_session = client.get("/api/v1/boards/context")
    assert expired_session.status_code == 401
    assert expired_session.json()["code"] == "guest_session_invalid"
    expired_join = client.get(join_path, follow_redirects=False)
    assert expired_join.status_code == 404
    assert expired_join.json()["code"] == "invitation_invalid"
    assert guest["actorId"].startswith("guest:")


def test_join_and_ticket_values_are_redacted_before_persistence():
    raw = "GET /j/super-secret-value?ticket=one-time-ticket&token=other"
    safe = redact(raw)
    assert isinstance(safe, str)
    assert "super-secret-value" not in safe
    assert "one-time-ticket" not in safe
    assert "token=other" not in safe
    assert "/j/[REDACTED]" in safe
    assert "ticket=[REDACTED]" in safe


def test_invitation_rate_limit_uses_standalone_problem_shape(tmp_path):
    settings = _settings(tmp_path, rate_limit_invitations=1)
    database = Database(settings.database_url)
    with TestClient(create_app(settings, database)) as client:
        first = client.get("/j/invalid-secret-number-one-000000000", follow_redirects=False)
        second = client.get("/j/invalid-secret-number-two-000000000", follow_redirects=False)
        assert first.status_code == 404
        assert second.status_code == 429
        assert second.json()["code"] == "rate_limit_exceeded"
        assert second.headers["cache-control"] == "no-store"
    database.dispose()


def _alembic_config(database: Database) -> Config:
    config = Config()
    migrations = Path(db_module.__file__).with_name("migrations")
    config.set_main_option("script_location", str(migrations))
    url = database.engine.url.render_as_string(False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migration_0016_can_downgrade_when_unused(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'migration-b2-empty.db'}")
    config = _alembic_config(database)
    command.upgrade(config, "0016_board_guest_invites")
    tables = set(inspect(database.engine).get_table_names())
    assert "board_invitations" in tables
    command.downgrade(config, "0015_standalone_boards")
    assert "board_invitations" not in set(inspect(database.engine).get_table_names())
    database.dispose()


def test_migration_0016_refuses_physical_downgrade_with_invitation_data(b2_api):
    client, database, _, board_id, teacher = b2_api
    _create_invitation(client, board_id, teacher["csrfToken"])
    with pytest.raises(RuntimeError, match="Cannot downgrade standalone guest invitations"):
        command.downgrade(_alembic_config(database), "0015_standalone_boards")


def test_invitation_orm_never_persists_join_secret(b2_api):
    client, database, _, board_id, teacher = b2_api
    result = _create_invitation(client, board_id, teacher["csrfToken"])
    _, secret = _join_path(result)
    with database.sessions() as session:
        document = session.scalar(select(BoardDocument).where(BoardDocument.id == board_id))
        invitation = session.scalar(
            select(BoardInvitation).where(BoardInvitation.board_document_id == board_id)
        )
        assert document is not None and invitation is not None
        persisted = "|".join(
            str(value)
            for value in (
                invitation.id,
                invitation.secret_digest,
                invitation.display_name,
                invitation.board_document_id,
            )
        )
        assert secret not in persisted
