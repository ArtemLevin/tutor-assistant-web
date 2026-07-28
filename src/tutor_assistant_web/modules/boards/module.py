from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.boards.routes import create_router

MODULE = ModuleDefinition(
    name="boards",
    dependencies=("scheduling",),
    router_factory=create_router,
)
