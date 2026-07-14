from __future__ import annotations

import uvicorn

from tutor_assistant_web.bootstrap.app_factory import create_app

app = create_app()


def run() -> None:
    uvicorn.run("tutor_assistant_web.app:app", host="0.0.0.0", port=8000, reload=True)
