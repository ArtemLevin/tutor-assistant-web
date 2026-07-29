from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Literal
from urllib.parse import urlsplit

import redis
import redis.asyncio as async_redis
from fastapi import WebSocket
from pydantic import BaseModel, ConfigDict, Field

from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.observability import BOARD_SYNC_EVENTS, BOARD_WEBSOCKET_CONNECTIONS
from tutor_assistant_web.shared.errors import ForbiddenError

_PROTOCOL = "tutorboard.v1"


class CollaborationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cursor(CollaborationModel):
    x: float = Field(ge=-1_000_000, le=1_000_000)
    y: float = Field(ge=-1_000_000, le=1_000_000)


class ViewportHint(CollaborationModel):
    x: float = Field(ge=-1_000_000, le=1_000_000)
    y: float = Field(ge=-1_000_000, le=1_000_000)
    zoom: float = Field(gt=0, le=64)


class PresenceUpdate(CollaborationModel):
    type: Literal["presence"]
    sequence: int = Field(ge=0)
    cursor: Cursor | None = None
    viewport: ViewportHint | None = None
    selected_object_ids: list[str] = Field(
        default_factory=list,
        alias="selectedObjectIds",
        max_length=200,
    )


@dataclass(frozen=True)
class CollaborationTicket:
    organization_id: str
    document_id: str
    user_id: str
    role: str
    client_id: str


class CollaborationBroker:
    """Distribute redacted room events and consume short-lived one-time tickets."""

    def __init__(
        self,
        redis_url: str,
        *,
        distributed: bool,
        ticket_ttl_seconds: int = 30,
    ) -> None:
        self.redis_url = redis_url
        self.distributed = distributed
        self.ticket_ttl_seconds = ticket_ttl_seconds
        self._tickets: dict[str, tuple[float, CollaborationTicket]] = {}
        self._ticket_lock = threading.Lock()
        self._subscribers: dict[
            str,
            set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict]]],
        ] = defaultdict(set)
        self._subscriber_lock = threading.Lock()

    def issue_ticket(
        self,
        principal: Principal,
        document_id: str,
        client_id: str,
    ) -> str:
        token = secrets.token_urlsafe(32)
        key = self._ticket_key(token)
        ticket = CollaborationTicket(
            organization_id=principal.organization_id,
            document_id=document_id,
            user_id=principal.user_id,
            role=principal.role,
            client_id=client_id,
        )
        if self.distributed:
            client = redis.Redis.from_url(self.redis_url, decode_responses=True)
            try:
                created = client.set(
                    key,
                    json.dumps(asdict(ticket), separators=(",", ":")),
                    ex=self.ticket_ttl_seconds,
                    nx=True,
                )
            finally:
                client.close()
            if not created:
                raise RuntimeError("Could not allocate a collaboration ticket")
        else:
            with self._ticket_lock:
                self._tickets[key] = (time.monotonic() + self.ticket_ttl_seconds, ticket)
        return token

    def consume_ticket(self, token: str) -> CollaborationTicket | None:
        if len(token) > 256:
            return None
        key = self._ticket_key(token)
        if self.distributed:
            client = redis.Redis.from_url(self.redis_url, decode_responses=True)
            try:
                payload = client.getdel(key)
            finally:
                client.close()
            if not payload:
                return None
            return CollaborationTicket(**json.loads(payload))
        with self._ticket_lock:
            expires_at, ticket = self._tickets.pop(key, (0.0, None))
        if ticket is None or expires_at < time.monotonic():
            return None
        return ticket

    def publish(self, organization_id: str, document_id: str, event: dict) -> None:
        channel = self._channel(organization_id, document_id)
        if self.distributed:
            client = redis.Redis.from_url(self.redis_url, decode_responses=True)
            try:
                client.publish(channel, json.dumps(event, separators=(",", ":")))
            finally:
                client.close()
            return
        with self._subscriber_lock:
            subscribers = tuple(self._subscribers.get(channel, ()))
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(self._offer, queue, event)

    async def subscribe(
        self,
        organization_id: str,
        document_id: str,
    ) -> AsyncIterator[dict]:
        channel = self._channel(organization_id, document_id)
        if self.distributed:
            client = async_redis.Redis.from_url(self.redis_url, decode_responses=True)
            pubsub = client.pubsub()
            await pubsub.subscribe(channel)
            try:
                async for item in pubsub.listen():
                    if item["type"] == "message":
                        yield json.loads(item["data"])
            finally:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
                await client.aclose()
            return
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        subscriber = (asyncio.get_running_loop(), queue)
        with self._subscriber_lock:
            self._subscribers[channel].add(subscriber)
        try:
            while True:
                yield await queue.get()
        finally:
            with self._subscriber_lock:
                self._subscribers[channel].discard(subscriber)
                if not self._subscribers[channel]:
                    self._subscribers.pop(channel, None)

    @staticmethod
    def _offer(queue: asyncio.Queue[dict], event: dict) -> None:
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(event)

    @staticmethod
    def _ticket_key(token: str) -> str:
        return f"tutorboard:collaboration:ticket:{hashlib.sha256(token.encode()).hexdigest()}"

    @staticmethod
    def _channel(organization_id: str, document_id: str) -> str:
        digest = hashlib.sha256(f"{organization_id}\0{document_id}".encode()).hexdigest()
        return f"tutorboard:collaboration:room:{digest}"


def validate_websocket_origin(
    websocket: WebSocket, public_base_url: str, *, production: bool
) -> None:
    origin = websocket.headers.get("origin", "")
    if not origin:
        if production:
            raise ForbiddenError("WebSocket Origin is required")
        return
    expected = urlsplit(public_base_url)
    supplied = urlsplit(origin)
    if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
        raise ForbiddenError("WebSocket Origin is not allowed")


async def run_collaboration_socket(
    websocket: WebSocket,
    broker: CollaborationBroker,
    ticket: CollaborationTicket,
    *,
    current_revision: int,
) -> None:
    await websocket.accept(subprotocol=_PROTOCOL)
    BOARD_WEBSOCKET_CONNECTIONS.labels(role=ticket.role).inc()
    BOARD_SYNC_EVENTS.labels(event="websocket_connected").inc()
    joined = {
        "type": "presence.joined",
        "protocolVersion": "1.0",
        "actorId": ticket.user_id,
        "clientId": ticket.client_id,
        "role": ticket.role,
    }
    broker.publish(ticket.organization_id, ticket.document_id, joined)
    await websocket.send_json(
        {
            "type": "ready",
            "protocolVersion": "1.0",
            "documentId": ticket.document_id,
            "clientId": ticket.client_id,
            "currentRevision": current_revision,
            "heartbeatSeconds": 20,
        }
    )

    async def relay() -> None:
        async for event in broker.subscribe(ticket.organization_id, ticket.document_id):
            if event.get("clientId") != ticket.client_id:
                await websocket.send_json(event)

    async def receive() -> None:
        recent: deque[float] = deque()
        last_sequence = -1
        while True:
            payload = await websocket.receive_text()
            if len(payload.encode()) > 32 * 1024:
                await websocket.close(code=1009, reason="Message too large")
                return
            now = time.monotonic()
            recent.append(now)
            while recent and recent[0] < now - 1:
                recent.popleft()
            if len(recent) > 30:
                await websocket.close(code=1008, reason="Message rate exceeded")
                return
            if payload == '{"type":"heartbeat"}':
                await websocket.send_json({"type": "heartbeat.ack"})
                continue
            update = PresenceUpdate.model_validate_json(payload)
            if update.sequence <= last_sequence:
                continue
            last_sequence = update.sequence
            broker.publish(
                ticket.organization_id,
                ticket.document_id,
                {
                    "type": "presence.updated",
                    "protocolVersion": "1.0",
                    "actorId": ticket.user_id,
                    "clientId": ticket.client_id,
                    "role": ticket.role,
                    **update.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude={"type"},
                    ),
                },
            )

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(relay())
            group.create_task(receive())
    finally:
        BOARD_WEBSOCKET_CONNECTIONS.labels(role=ticket.role).dec()
        BOARD_SYNC_EVENTS.labels(event="websocket_disconnected").inc()
        broker.publish(
            ticket.organization_id,
            ticket.document_id,
            {
                "type": "presence.left",
                "protocolVersion": "1.0",
                "actorId": ticket.user_id,
                "clientId": ticket.client_id,
                "role": ticket.role,
            },
        )
