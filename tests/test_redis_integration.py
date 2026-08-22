from __future__ import annotations

import asyncio
import os

import pytest
from redis import Redis

from tutor_assistant_web.modules.boards.collaboration import (
    CollaborationBroker,
    CollaborationTicket,
    run_collaboration_socket,
)

TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_REDIS_URL,
    reason="TEST_REDIS_URL is required for Redis integration tests",
)


def test_redis_broker_is_reachable_and_durable_queues_are_declared(monkeypatch):
    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
    monkeypatch.setenv("TASK_EAGER", "false")
    from tutor_assistant_web.worker import celery_app

    client = Redis.from_url(TEST_REDIS_URL)
    try:
        assert client.ping() is True
        queues = {queue.name for queue in celery_app.conf.task_queues}
        assert queues == {"transcription", "materials", "delivery", "maintenance"}
        assert celery_app.conf.task_acks_late is True
        assert celery_app.conf.worker_prefetch_multiplier == 1
    finally:
        client.flushdb()
        client.close()


def test_collaboration_ticket_and_room_events_cross_broker_instances():
    async def scenario() -> None:
        first = CollaborationBroker(TEST_REDIS_URL, distributed=True)
        second = CollaborationBroker(TEST_REDIS_URL, distributed=True)
        try:
            token = await first.issue_ticket(
                _principal(),
                "board:shared",
                "client:first",
            )
            ticket = await second.consume_ticket(token)
            assert ticket is not None
            assert ticket.document_id == "board:shared"
            assert await first.consume_ticket(token) is None

            subscription = second.subscribe("organization:a", "board:shared")
            pending = asyncio.create_task(anext(subscription))
            await asyncio.sleep(0.1)
            expected = {"type": "presence.updated", "clientId": "client:first"}
            await first.publish("organization:a", "board:shared", expected)
            assert await asyncio.wait_for(pending, timeout=2) == expected
            await subscription.aclose()
        finally:
            await first.close()
            await second.close()

    asyncio.run(scenario())


def test_guest_capability_change_and_revocation_cross_broker_instances():
    class Socket:
        def __init__(self) -> None:
            self.messages: asyncio.Queue[dict] = asyncio.Queue()
            self.incoming: asyncio.Queue[str] = asyncio.Queue()
            self.closed: asyncio.Future[tuple[int, str]] = asyncio.Future()

        async def accept(self, *, subprotocol: str) -> None:
            assert subprotocol == "tutorboard.v1"

        async def send_json(self, message: dict) -> None:
            await self.messages.put(message)

        async def receive_text(self) -> str:
            return await self.incoming.get()

        async def close(self, *, code: int, reason: str) -> None:
            if not self.closed.done():
                self.closed.set_result((code, reason))

    async def scenario() -> None:
        publisher = CollaborationBroker(TEST_REDIS_URL, distributed=True)
        socket_broker = CollaborationBroker(TEST_REDIS_URL, distributed=True)
        socket = Socket()
        ticket = CollaborationTicket(
            organization_id="organization:a",
            document_id="board:shared",
            user_id="guest:a",
            role="guest",
            client_id="guest-client",
            display_name="Guest",
            principal_type="guest",
            invitation_id="invitation:a",
            credential_version=1,
            can_write=True,
        )
        task = asyncio.create_task(
            run_collaboration_socket(socket, socket_broker, ticket, current_revision=0)  # type: ignore[arg-type]
        )
        try:
            assert (await asyncio.wait_for(socket.messages.get(), timeout=2))["type"] == "ready"
            assert (await asyncio.wait_for(socket.messages.get(), timeout=2))["type"] == (
                "presence.snapshot"
            )
            await asyncio.sleep(0.1)
            await publisher.publish(
                "organization:a",
                "board:shared",
                {
                    "type": "access.capabilities.changed",
                    "schemaVersion": "1.0",
                    "boardId": "board:shared",
                    "capabilities": ["board.read"],
                    "_canWrite": False,
                    "_targetInvitationId": "invitation:a",
                },
            )
            changed = await asyncio.wait_for(socket.messages.get(), timeout=2)
            assert changed == {
                "type": "access.capabilities.changed",
                "schemaVersion": "1.0",
                "boardId": "board:shared",
                "capabilities": ["board.read"],
            }
            await publisher.publish(
                "organization:a",
                "board:shared",
                {
                    "type": "access.revoked",
                    "schemaVersion": "1.0",
                    "boardId": "board:shared",
                    "terminal": True,
                    "_targetInvitationId": "invitation:a",
                },
            )
            revoked = await asyncio.wait_for(socket.messages.get(), timeout=2)
            assert revoked["type"] == "access.revoked"
            assert await asyncio.wait_for(socket.closed, timeout=2) == (
                4403,
                "Guest access revoked",
            )
            await asyncio.wait_for(task, timeout=2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await publisher.close()
            await socket_broker.close()

    asyncio.run(scenario())


def _principal():
    from tutor_assistant_web.modules.identity.application import Principal

    return Principal(
        user_id="user:a",
        organization_id="organization:a",
        organization_name="Organization A",
        role="tutor",
        email="tutor@example.test",
        full_name="Tutor",
    )
