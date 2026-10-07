import uvicorn

from tutor_assistant_web.bootstrap.app_factory import create_app as create_app
from tutor_assistant_web.bootstrap.profile import create_runtime_app, load_runtime_configuration

runtime = load_runtime_configuration()
app = create_runtime_app(runtime)


def run() -> None:
    settings = runtime.settings
    uvicorn.run(
        "tutor_assistant_web.app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
    )
