"""Compatibility facade for imports from the original pilot.

Application code now lives inside its owning module and external integrations are
selected by the composition root.
"""

from __future__ import annotations

from typing import Any

from tutor_assistant_web.bootstrap.container import build_material_generator
from tutor_assistant_web.bootstrap.seed import seed_data
from tutor_assistant_web.config import Settings
from tutor_assistant_web.modules.materials.application import evidence_payload
from tutor_assistant_web.shared.security import (
    join_token,
    make_meeting_credentials,
    verify_join_token,
)


def request_materials(payload: dict[str, Any], settings: Settings) -> list[dict[str, str]]:
    return [
        {
            "kind": item.kind,
            "title": item.title,
            "content": item.content,
            "source_url": item.source_url,
        }
        for item in build_material_generator(settings).generate(payload)
    ]


__all__ = [
    "evidence_payload",
    "join_token",
    "make_meeting_credentials",
    "request_materials",
    "seed_data",
    "verify_join_token",
]
