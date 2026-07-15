from __future__ import annotations

import uvicorn

from tutor_assistant_web.bootstrap.app_factory import create_app
from tutor_assistant_web.config import get_settings

app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "tutor_assistant_web.app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
    )
