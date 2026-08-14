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

import redis.asyncio as async_redis
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tutor_assistant_web.modules.identity.application import Principal
from tutor_assistant_web.observability import (
    BOARD_COLLABORATION_PUBLISH_DURATION,
    BOARD_SYNC_EVENTS,
    BOARD_WEBSOCKET_CONNECTIONS,
    BOARD_WEBSOCKET_MESSAGES,
)
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


class InkPreviewStyle(CollaborationModel):
    stroke: str = Field(min_length=1, max_length=32)
    stroke_width: float = Field(alias="strokeWidth", gt=0, le=128)
    opacity: float = Field(ge=0, le=1)


class InkPreviewUpdate(CollaborationModel):
    type: Literal["preview.ink"]
    sequence: int = Field(ge=0)
    preview_id: str = Field(alias="previewId", min_length=1, max_length=128)
    phase: Literal["start", "update", "end", "cancel"]
    points: list[Cursor] = Field(default_factory=list, max_length=64)
    style: InkPreviewStyle | None = None


class TransformScale(CollaborationModel):
    x: float = Field(gt=0, le=100)
    y: float = Field(gt=0, le=100)


class TransformSnapshot(CollaborationModel):
    object_id: str = Field(alias="objectId", min_length=1, max_length=128)
    position: Cursor
    rotation: float = Field(ge=-360_000, le=360_000)
    scale: TransformScale


class TransformPreviewUpdate(CollaborationModel):
    type: Literal["preview.transform"]
    sequence: int = Field(ge=0)
    preview_id: str = Field(alias="previewId", min_length=1, max_length=128)
    phase: Literal["update", "end", "cancel"]
    transforms: list[TransformSnapshot] = Field(default_factory=list, max_length=200)


@dataclass(frozen=True)
class CollaborationTicket:
    organization_id: str
    document_id: str
    user_id: str
    role: str
    client_id: str
    display_name: str


class CollaborationBroker:
    """Distribute redacted room events and consume short-lived one-time tickets."""

    def __init__(
        self,
        redis_url: str,
        *,
        distributed: bool,
        presence_ttl_seconds: int = 60,
        ticket_ttl_seconds: int = 30,
    ) -> None:
        self.redis_url = redis_url
        self.distributed = distributed
        self.presence_ttl_seconds = presence_ttl_seconds
        self.ticket_ttl_seconds = ticket_ttl_seconds
        self._redis = (
            async_redis.Redis.from_url(redis_url, decode_responses=True) if distributed else None
        )
        self._tickets: dict[str, tuple[float, CollaborationTicket]] = {}
        self._ticket_lock = threading.Lock()
        self._subscribers: dict[
            str,
            set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict]]],
        ] = defaultdict(set)
        self._subscriber_lock = threading.Lock()
        self._presence: dict[str, dict[str, tuple[float, dict]]] = defaultdict(dict)
        self._presence_lock = threading.Lock()

    async def issue_ticket(
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
            display_name=principal.full_name or principal.user_id,
        )
        if self.distributed:
            assert self._redis is not None
            created = await self._redis.set(
                key,
                json.dumps(asdict(ticket), separators=(",", ":")),
                ex=self.ticket_ttl_seconds,
                nx=True,
            )
            if not created:
                raise RuntimeError("Could not allocate a collaboration ticket")
        else:
            with self._ticket_lock:
                self._tickets[key] = (time.monotonic() + self.ticket_ttl_seconds, ticket)
        return token

    async def consume_ticket(self, token: str) -> CollaborationTicket | None:
        if len(token) > 256:
            return None
        key = self._ticket_key(token)
        if self.distributed:
            assert self._redis is not None
            payload = await self._redis.getdel(key)
            if not payload:
                return None
            return CollaborationTicket(**json.loads(payload))
        with self._ticket_lock:
            expires_at, ticket = self._tickets.pop(key, (0.0, None))
        if ticket is None or expires_at < time.monotonic():
            return None
        return ticket

    async def publish(self, organization_id: str, document_id: str, event: dict) -> None:
        channel = self._channel(organization_id, document_id)
        event_type = str(event.get("type", "unknown"))
        BOARD_WEBSOCKET_MESSAGES.labels(direction="published", type=event_type).inc()
        if self.distributed:
            assert self._redis is not None
            with BOARD_COLLABORATION_PUBLISH_DURATION.time():
                await self._redis.publish(channel, json.dumps(event, separators=(",", ":")))
            return
        with self._subscriber_lock:
            subscribers = tuple(self._subscribers.get(channel, ()))
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(self._offer, queue, event)

    async def set_presence(
        self,
        organization_id: str,
        document_id: str,
        event: dict,
    ) -> None:
        channel = self._channel(organization_id, document_id)
        client_id = str(event["clientId"])
        expires_at = time.time() + self.presence_ttl_seconds
        if self.distributed:
            assert self._redis is not None
            roster_key, expiry_key = self._presence_keys(channel)
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.hset(roster_key, client_id, json.dumps(event, separators=(",", ":")))
                pipe.zadd(expiry_key, {client_id: expires_at})
                pipe.expire(roster_key, self.presence_ttl_seconds * 2)
                pipe.expire(expiry_key, self.presence_ttl_seconds * 2)
                await pipe.execute()
            return
        with self._presence_lock:
            self._presence[channel][client_id] = (expires_at, event)

    async def touch_presence(
        self,
        organization_id: str,
        document_id: str,
        client_id: str,
    ) -> None:
        channel = self._channel(organization_id, document_id)
        expires_at = time.time() + self.presence_ttl_seconds
        if self.distributed:
            assert self._redis is not None
            roster_key, expiry_key = self._presence_keys(channel)
            if await self._redis.hexists(roster_key, client_id):
                async with self._redis.pipeline(transaction=True) as pipe:
                    pipe.zadd(expiry_key, {client_id: expires_at})
                    pipe.expire(roster_key, self.presence_ttl_seconds * 2)
                    pipe.expire(expiry_key, self.presence_ttl_seconds * 2)
                    await pipe.execute()
            return
        with self._presence_lock:
            current = self._presence.get(channel, {}).get(client_id)
            if current is not None:
                self._presence[channel][client_id] = (expires_at, current[1])

    async def remove_presence(
        self,
        organization_id: str,
        document_id: str,
        client_id: str,
    ) -> None:
        channel = self._channel(organization_id, document_id)
        if self.distributed:
            assert self._redis is not None
            roster_key, expiry_key = self._presence_keys(channel)
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.hdel(roster_key, client_id)
                pipe.zrem(expiry_key, client_id)
                await pipe.execute()
            return
        with self._presence_lock:
            room = self._presence.get(channel)
            if room is None:
                return
            room.pop(client_id, None)
            if not room:
                self._presence.pop(channel, None)

    async def list_presence(
        self,
        organization_id: str,
        document_id: str,
    ) -> list[dict]:
        channel = self._channel(organization_id, document_id)
        now = time.time()
        if self.distributed:
            assert self._redis is not None
            roster_key, expiry_key = self._presence_keys(channel)
            stale = await self._redis.zrangebyscore(expiry_key, 0, now)
            if stale:
                async with self._redis.pipeline(transaction=True) as pipe:
                    pipe.hdel(roster_key, *stale)
                    pipe.zrem(expiry_key, *stale)
                    await pipe.execute()
            payloads = await self._redis.hgetall(roster_key)
            return [json.loads(payload) for payload in payloads.values()]
        with self._presence_lock:
            room = self._presence.get(channel, {})
            stale = [client_id for client_id, (expires_at, _) in room.items() if expires_at <= now]
            for client_id in stale:
                room.pop(client_id, None)
            if not room:
                self._presence.pop(channel, None)
            return [event for _, event in room.values()]

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    async def subscribe(
        self,
        organization_id: str,
        document_id: str,
    ) -> AsyncIterator[dict]:
        channel = self._channel(organization_id, document_id)
        if self.distributed:
            assert self._redis is not None
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(channel)
            try:
                async for item in pubsub.listen():
                    if item["type"] == "message":
                        yield json.loads(item["data"])
            finally:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
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

    @staticmethod
    def _presence_keys(channel: str) -> tuple[str, str]:
        return f"{channel}:presence", f"{channel}:presence-expiry"


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
        "protocolVersion": "1.1",
        "actorId": ticket.user_id,
        "clientId": ticket.client_id,
        "displayName": ticket.display_name,
        "role": ticket.role,
    }
    await broker.set_presence(ticket.organization_id, ticket.document_id, joined)
    await broker.publish(ticket.organization_id, ticket.document_id, joined)
    await websocket.send_json(
        {
            "type": "ready",
            "protocolVersion": "1.1",
            "documentId": ticket.document_id,
            "clientId": ticket.client_id,
            "currentRevision": current_revision,
            "heartbeatSeconds": 20,
        }
    )
    participants = await broker.list_presence(ticket.organization_id, ticket.document_id)
    await websocket.send_json(
        {
            "type": "presence.snapshot",
            "protocolVersion": "1.1",
            "participants": [
                participant
                for participant in participants
                if participant.get("clientId") != ticket.client_id
            ],
        }
    )

    async def relay() -> None:
        async for event in broker.subscribe(ticket.organization_id, ticket.document_id):
            if event.get("clientId") != ticket.client_id:
                BOARD_WEBSOCKET_MESSAGES.labels(
                    direction="sent", type=str(event.get("type", "unknown"))
                ).inc()
                await websocket.send_json(event)

    async def receive() -> None:
        recent: deque[float] = deque()
        last_sequence = -1
        try:
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
                    BOARD_WEBSOCKET_MESSAGES.labels(direction="received", type="heartbeat").inc()
                    await broker.touch_presence(
                        ticket.organization_id,
                        ticket.document_id,
                        ticket.client_id,
                    )
                    await websocket.send_json({"type": "heartbeat.ack"})
                    continue
                try:
                    decoded = json.loads(payload)
                    if not isinstance(decoded, dict):
                        raise ValueError("Collaboration message must be an object")
                    message_type = decoded.get("type")
                    if message_type == "presence":
                        update: PresenceUpdate | InkPreviewUpdate | TransformPreviewUpdate = (
                            PresenceUpdate.model_validate(decoded)
                        )
                    elif message_type == "preview.ink":
                        update = InkPreviewUpdate.model_validate(decoded)
                        if (
                            update.phase == "start" and (update.style is None or not update.points)
                        ) or (update.phase == "update" and not update.points):
                            raise ValueError("Ink preview phase is incomplete")
                    elif message_type == "preview.transform":
                        update = TransformPreviewUpdate.model_validate(decoded)
                        if update.phase == "update" and not update.transforms:
                            raise ValueError("Transform preview update is empty")
                    else:
                        raise ValueError("Unsupported collaboration message")
                except (json.JSONDecodeError, ValidationError, ValueError):
                    await websocket.close(code=1003, reason="Invalid collaboration message")
                    return
                if update.sequence <= last_sequence:
                    continue
                last_sequence = update.sequence
                if isinstance(update, (InkPreviewUpdate, TransformPreviewUpdate)):
                    if ticket.role == "parent":
                        await websocket.close(code=1008, reason="Read-only collaboration role")
                        return
                    event = {
                        "type": update.type,
                        "protocolVersion": "1.1",
                        "actorId": ticket.user_id,
                        "clientId": ticket.client_id,
                        "displayName": ticket.display_name,
                        **update.model_dump(
                            mode="json",
                            by_alias=True,
                            exclude={"type"},
                            exclude_none=True,
                        ),
                    }
                    BOARD_WEBSOCKET_MESSAGES.labels(direction="received", type=update.type).inc()
                    await broker.touch_presence(
                        ticket.organization_id,
                        ticket.document_id,
                        ticket.client_id,
                    )
                    await broker.publish(
                        ticket.organization_id,
                        ticket.document_id,
                        event,
                    )
                    continue
                event = {
                    "type": "presence.updated",
                    "protocolVersion": "1.1",
                    "actorId": ticket.user_id,
                    "clientId": ticket.client_id,
                    "displayName": ticket.display_name,
                    "role": ticket.role,
                    **update.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude={"type"},
                    ),
                }
                BOARD_WEBSOCKET_MESSAGES.labels(direction="received", type="presence").inc()
                await broker.set_presence(
                    ticket.organization_id,
                    ticket.document_id,
                    event,
                )
                await broker.publish(ticket.organization_id, ticket.document_id, event)
        except WebSocketDisconnect:
            return

    tasks: set[asyncio.Task[None]] = set()
    try:
        tasks = {asyncio.create_task(relay()), asyncio.create_task(receive())}
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.cancelled():
                continue
            error = task.exception()
            if error is not None:
                raise error
    except asyncio.CancelledError:
        # ASGI servers and Starlette's TestClient cancel connection scopes during
        # a normal client close. Treat that as a graceful socket disconnect.
        pass
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        BOARD_WEBSOCKET_CONNECTIONS.labels(role=ticket.role).dec()
        BOARD_SYNC_EVENTS.labels(event="websocket_disconnected").inc()
        await broker.remove_presence(
            ticket.organization_id,
            ticket.document_id,
            ticket.client_id,
        )
        await broker.publish(
            ticket.organization_id,
            ticket.document_id,
            {
                "type": "presence.left",
                "protocolVersion": "1.1",
                "actorId": ticket.user_id,
                "clientId": ticket.client_id,
                "displayName": ticket.display_name,
                "role": ticket.role,
            },
        )
