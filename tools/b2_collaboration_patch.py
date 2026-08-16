from pathlib import Path

path = Path("src/tutor_assistant_web/modules/boards/collaboration.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    'from tutor_assistant_web.modules.identity.application import Principal\n',
    'from tutor_assistant_web.modules.boards.guest_access import GuestPrincipal\n'
    'from tutor_assistant_web.modules.identity.application import Principal\n',
)
text = text.replace(
    '''class CollaborationTicket:
    organization_id: str
    document_id: str
    user_id: str
    role: str
    client_id: str
    display_name: str
''',
    '''class CollaborationTicket:
    organization_id: str
    document_id: str
    user_id: str
    role: str
    client_id: str
    display_name: str
    principal_type: str = "teacher"
    invitation_id: str | None = None
    credential_version: int | None = None
    can_write: bool = True
    access_expires_at: float | None = None
''',
)
text = text.replace(
    '''    async def issue_ticket(
        self,
        principal: Principal,
        document_id: str,
        client_id: str,
    ) -> str:
''',
    '''    async def issue_ticket(
        self,
        principal: Principal | GuestPrincipal,
        document_id: str,
        client_id: str,
    ) -> str:
''',
)
text = text.replace(
    '''        ticket = CollaborationTicket(
            organization_id=principal.organization_id,
            document_id=document_id,
            user_id=principal.user_id,
            role=principal.role,
            client_id=client_id,
            display_name=principal.full_name or principal.user_id,
        )
''',
    '''        is_guest = isinstance(principal, GuestPrincipal)
        ticket = CollaborationTicket(
            organization_id=principal.organization_id,
            document_id=document_id,
            user_id=principal.user_id,
            role=principal.role,
            client_id=client_id,
            display_name=principal.full_name or principal.user_id,
            principal_type="guest" if is_guest else "teacher",
            invitation_id=principal.invitation_id if is_guest else None,
            credential_version=principal.credential_version if is_guest else None,
            can_write=principal.can_write if is_guest else principal.role != "parent",
            access_expires_at=(
                principal.access_expires_at.timestamp()
                if is_guest and principal.access_expires_at is not None
                else None
            ),
        )
''',
)
text = text.replace(
    '''    async def relay() -> None:
        async for event in broker.subscribe(ticket.organization_id, ticket.document_id):
            if event.get("clientId") != ticket.client_id:
                BOARD_WEBSOCKET_MESSAGES.labels(
                    direction="sent", type=str(event.get("type", "unknown"))
                ).inc()
                await websocket.send_json(event)

    async def receive() -> None:
''',
    '''    write_allowed = {"value": ticket.can_write}

    async def relay() -> None:
        async for event in broker.subscribe(ticket.organization_id, ticket.document_id):
            if event.get("clientId") == ticket.client_id:
                continue
            event_type = str(event.get("type", "unknown"))
            if event_type.startswith("access."):
                if ticket.principal_type != "guest":
                    continue
                target = event.get("_targetInvitationId")
                if target is not None and target != ticket.invitation_id:
                    continue
                if event_type == "access.capabilities.changed":
                    write_allowed["value"] = bool(event.get("_canWrite", False))
                public_event = {
                    key: value for key, value in event.items() if not key.startswith("_")
                }
                BOARD_WEBSOCKET_MESSAGES.labels(direction="sent", type=event_type).inc()
                await websocket.send_json(public_event)
                if event_type == "access.revoked":
                    await websocket.close(code=4403, reason="Guest access revoked")
                    return
                continue
            public_event = {
                key: value for key, value in event.items() if not key.startswith("_")
            }
            BOARD_WEBSOCKET_MESSAGES.labels(direction="sent", type=event_type).inc()
            await websocket.send_json(public_event)

    async def receive() -> None:
''',
)
text = text.replace(
    '''            while True:
                payload = await websocket.receive_text()
''',
    '''            while True:
                payload = await websocket.receive_text()
                if (
                    ticket.principal_type == "guest"
                    and ticket.access_expires_at is not None
                    and ticket.access_expires_at <= time.time()
                ):
                    await websocket.send_json(
                        {
                            "schemaVersion": "1.0",
                            "type": "access.revoked",
                            "boardId": ticket.document_id,
                            "terminal": True,
                        }
                    )
                    await websocket.close(code=4403, reason="Guest access expired")
                    return
''',
)
text = text.replace(
    '''                if isinstance(update, (InkPreviewUpdate, TransformPreviewUpdate)):
                    if ticket.role == "parent":
                        await websocket.close(code=1008, reason="Read-only collaboration role")
                        return
''',
    '''                if isinstance(update, (InkPreviewUpdate, TransformPreviewUpdate)):
                    if not write_allowed["value"]:
                        await websocket.close(code=1008, reason="Read-only collaboration role")
                        return
''',
)
path.write_text(text, encoding="utf-8")
