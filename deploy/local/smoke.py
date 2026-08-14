from __future__ import annotations

import asyncio
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import websockets

from tutor_assistant_web.modules.boards.application import canonical_json
from tutor_assistant_web.shared.board_contracts.board_document_schema import BoardDocument14

BASE_URL = os.getenv("LOCAL_BASE_URL", "http://gateway:8080").rstrip("/")
PUBLIC_ORIGIN = os.getenv("LOCAL_PUBLIC_ORIGIN", "http://localhost:8080").rstrip("/")
EMAIL = os.getenv("LOCAL_ADMIN_EMAIL", "admin@localhost")
PASSWORD = os.getenv("LOCAL_ADMIN_PASSWORD", "local-demo-password")
FIXTURES = Path("/smoke/board-fixtures")
DOCUMENT_ID = "document:local-distribution-smoke"


def require(response: httpx.Response, *statuses: int) -> httpx.Response:
    if response.status_code not in statuses:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {response.text[:500]}"
        )
    return response


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match is None:
        raise RuntimeError("Login page did not expose a CSRF token.")
    return match.group(1)


def lesson_id_from(html: str) -> str:
    match = re.search(r'href="/lessons/([^"/?#]+)"', html)
    if match is None:
        raise RuntimeError("Demo lesson was not found on the dashboard.")
    return match.group(1)


def cookie_header(client: httpx.Client) -> str:
    return "; ".join(f"{item.name}={item.value}" for item in client.cookies.jar)


async def verify_collaboration(
    client: httpx.Client,
    websocket_path: str,
    ticket: str,
) -> None:
    split = urlsplit(BASE_URL)
    websocket_url = urlunsplit(
        (
            "wss" if split.scheme == "https" else "ws",
            split.netloc,
            websocket_path,
            f"ticket={quote(ticket)}",
            "",
        )
    )
    async with websockets.connect(
        websocket_url,
        origin=PUBLIC_ORIGIN,
        additional_headers={"Cookie": cookie_header(client)},
        subprotocols=["tutorboard.v1"],
        open_timeout=10,
        close_timeout=5,
    ) as socket:
        ready = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        if ready.get("type") != "ready" or ready.get("documentId") != DOCUMENT_ID:
            raise RuntimeError(f"Unexpected collaboration handshake: {ready}")
        snapshot = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        if snapshot.get("type") != "presence.snapshot":
            raise RuntimeError(f"Unexpected collaboration presence snapshot: {snapshot}")
        await socket.send(
            json.dumps(
                {
                    "type": "presence",
                    "sequence": 1,
                    "cursor": {"x": 10, "y": 20},
                    "viewport": {"x": 0, "y": 0, "zoom": 1},
                    "selectedObjectIds": [],
                }
            )
        )
        await socket.send('{"type":"heartbeat"}')
        heartbeat = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        if heartbeat != {"type": "heartbeat.ack"}:
            raise RuntimeError(f"Unexpected collaboration heartbeat: {heartbeat}")


def fixture_document(revision: int) -> tuple[dict, str]:
    snapshot = json.loads((FIXTURES / "board-snapshot.json").read_text(encoding="utf-8"))
    snapshot["documentId"] = DOCUMENT_ID
    snapshot["revision"] = revision
    snapshot["document"]["id"] = DOCUMENT_ID
    snapshot["document"]["title"] = "Проверка единого локального приложения"
    document = BoardDocument14.model_validate(snapshot["document"])
    digest = canonical_json(document)[2]
    snapshot["documentSha256"] = digest
    return snapshot, digest


def ensure_revision_and_snapshot(
    client: httpx.Client,
    *,
    csrf: str,
    user_id: str,
) -> tuple[int, str]:
    recovered = require(client.get(f"/api/v1/boards/{DOCUMENT_ID}"), 200).json()
    board = recovered["board"]
    revision = int(board["currentRevision"])
    if revision > 1:
        raise RuntimeError(f"Smoke board has unexpected revision {revision}.")

    snapshot, digest = fixture_document(1)
    if revision == 0:
        command = json.loads((FIXTURES / "board-command-envelope.json").read_text(encoding="utf-8"))
        command.update(
            {
                "documentId": DOCUMENT_ID,
                "baseRevision": 0,
                "expectedDocumentSha256": digest,
                "idempotencyKey": "local-distribution-smoke:revision-1",
                "actorId": user_id,
            }
        )
        for index, item in enumerate(command["commands"]):
            payload = item["command"]
            payload["actorId"] = user_id
            if payload.get("kind") == "core.document.rename":
                payload["title"] = "Проверка единого локального приложения"
            item["order"]["baseRevisionAtCreation"] = 0
            item["order"]["lamport"] = index + 1
        appended = require(
            client.post(
                f"/api/v1/boards/{DOCUMENT_ID}/commands",
                json=command,
                headers={"x-csrf-token": csrf},
            ),
            200,
        ).json()
        revision = int(appended["revision"])

    recovered = require(client.get(f"/api/v1/boards/{DOCUMENT_ID}"), 200).json()
    board = recovered["board"]
    current_digest = str(board["currentDocumentSha256"])
    if current_digest != digest:
        raise RuntimeError(
            f"Smoke board digest mismatch: expected {digest}, received {current_digest}."
        )
    if recovered["snapshot"] is None:
        require(
            client.post(
                f"/api/v1/boards/{DOCUMENT_ID}/snapshots",
                json=snapshot,
                headers={"x-csrf-token": csrf},
            ),
            201,
        )
    return revision, digest


def ensure_evidence(
    client: httpx.Client,
    *,
    lesson_id: str,
    csrf: str,
    revision: int,
    digest: str,
) -> dict:
    listing = require(
        client.get(f"/api/v1/lessons/{lesson_id}/board-evidence"),
        200,
    ).json()
    existing = next(
        (item for item in listing["items"] if item["documentId"] == DOCUMENT_ID),
        None,
    )
    if existing is None:
        preview = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120">'
            '<rect width="320" height="120" fill="#f7f1e8"/>'
            '<text x="20" y="68" font-size="20">TutorBoard local smoke</text>'
            "</svg>"
        )
        existing = require(
            client.post(
                f"/api/v1/boards/{DOCUMENT_ID}/evidence",
                json={
                    "schemaVersion": "1.0",
                    "revision": revision,
                    "documentSha256": digest,
                    "previewSvg": preview,
                    "previewPngBase64": "",
                    "transcriptLinks": [],
                },
                headers={"x-csrf-token": csrf},
            ),
            201,
        ).json()
    published = require(
        client.post(
            f"/api/v1/board-evidence/{existing['evidenceId']}/publish",
            headers={"x-csrf-token": csrf},
        ),
        200,
    ).json()
    require(client.get(published["artifacts"]["manifest"]), 200)
    require(client.get(published["artifacts"]["svg"]), 200)
    if published["publishedAt"] is None or published["revokedAt"] is not None:
        raise RuntimeError("Evidence was not published successfully.")
    return deepcopy(published)


def main() -> None:
    timeout = httpx.Timeout(20)
    with httpx.Client(base_url=BASE_URL, timeout=timeout, follow_redirects=False) as client:
        require(client.get("/health/live"), 200)
        login_page = require(client.get("/login"), 200)
        require(
            client.post(
                "/login",
                data={
                    "csrf_token": csrf_from(login_page.text),
                    "email": EMAIL,
                    "password": PASSWORD,
                    "next": "/",
                },
            ),
            303,
        )
        dashboard = require(client.get("/"), 200)
        lesson_id = lesson_id_from(dashboard.text)
        context = require(client.get("/api/v1/boards/context"), 200).json()

        require(client.get("/board/"), 200)
        require(
            client.post(
                f"/api/v1/lessons/{lesson_id}/board",
                json={"documentId": DOCUMENT_ID},
                headers={"x-csrf-token": context["csrfToken"]},
            ),
            200,
            201,
        )

        ready = require(client.get("/api/v1/geometryos/ready"), 200).json()
        if ready.get("status") != "ready":
            raise RuntimeError(f"GeometryOS is not ready: {ready}")
        generated = require(
            client.post(
                "/api/v1/geometryos/api/v1/generate",
                json={
                    "input": (
                        "Постройте треугольник ABC. Проведите высоту из вершины A к стороне BC."
                    ),
                    "input_type": "text",
                    "mode": "strict",
                    "output": ["svg"],
                },
            ),
            200,
        ).json()
        if generated.get("status") != "success" or generated.get("svg") is None:
            raise RuntimeError(f"GeometryOS generate failed: {generated}")

        ticket = require(
            client.post(
                f"/api/v1/boards/{DOCUMENT_ID}/collaboration-ticket",
                json={"clientId": "local-smoke"},
                headers={"x-csrf-token": context["csrfToken"]},
            ),
            200,
        ).json()
        asyncio.run(
            verify_collaboration(
                client,
                str(ticket["websocketPath"]),
                str(ticket["ticket"]),
            )
        )

        revision, digest = ensure_revision_and_snapshot(
            client,
            csrf=context["csrfToken"],
            user_id=context["userId"],
        )
        evidence = ensure_evidence(
            client,
            lesson_id=lesson_id,
            csrf=context["csrfToken"],
            revision=revision,
            digest=digest,
        )

    print("Unified local smoke passed:")
    print("  login -> lesson -> TutorBoard -> collaboration")
    print("  GeometryOS -> snapshot -> immutable evidence -> publish")
    print(f"  evidence={evidence['evidenceId']} revision={evidence['revision']}")


if __name__ == "__main__":
    main()
