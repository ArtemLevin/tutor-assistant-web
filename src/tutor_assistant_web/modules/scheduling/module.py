from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.scheduling.routes import create_router

MODULE = ModuleDefinition(
    name="scheduling",
    dependencies=("students",),
    router_factory=create_router,
)
