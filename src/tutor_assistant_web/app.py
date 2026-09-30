from __future__ import annotations

import uvicorn

from tutor_assistant_web.bootstrap.app_factory import create_app
from tutor_assistant_web.bootstrap.board_app_factory import create_board_app
from tutor_assistant_web.bootstrap.profile import load_runtime_configuration

runtime = load_runtime_configuration()
app = (
    create_board_app(runtime.settings)
    if runtime.profile == "board"
    else create_app(runtime.settings)
)


def run() -> None:
    settings = runtime.settings
    uvicorn.run(
        "tutor_assistant_web.app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
    )
