from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.classroom.routes import create_router

MODULE = ModuleDefinition(
    name="classroom",
    dependencies=("scheduling",),
    router_factory=create_router,
)
