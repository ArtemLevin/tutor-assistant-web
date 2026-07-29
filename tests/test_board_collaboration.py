from __future__ import annotations

import asyncio

from tutor_assistant_web.modules.boards.collaboration import CollaborationBroker
from tutor_assistant_web.modules.identity.application import Principal


def _principal(organization_id: str) -> Principal:
    return Principal(
        user_id=f"user:{organization_id}",
        organization_id=organization_id,
        organization_name=organization_id,
        role="tutor",
        email="",
        full_name="",
    )


def test_collaboration_tickets_are_one_time_and_tenant_bound():
    broker = CollaborationBroker(
        "redis://unused",
        distributed=False,
        ticket_ttl_seconds=30,
    )
    token = broker.issue_ticket(_principal("organization:a"), "document:1", "browser:a")
    ticket = broker.consume_ticket(token)
    assert ticket is not None
    assert ticket.organization_id == "organization:a"
    assert ticket.document_id == "document:1"
    assert broker.consume_ticket(token) is None


def test_collaboration_rooms_do_not_cross_tenant_boundaries():
    async def scenario() -> None:
        broker = CollaborationBroker("redis://unused", distributed=False)
        subscription = broker.subscribe("organization:a", "document:shared")
        pending = asyncio.create_task(anext(subscription))
        await asyncio.sleep(0)
        broker.publish(
            "organization:b",
            "document:shared",
            {"type": "board.revision", "revision": 1},
        )
        await asyncio.sleep(0)
        assert not pending.done()
        expected = {"type": "board.revision", "revision": 2}
        broker.publish("organization:a", "document:shared", expected)
        assert await asyncio.wait_for(pending, timeout=1) == expected
        await subscription.aclose()

    asyncio.run(scenario())
