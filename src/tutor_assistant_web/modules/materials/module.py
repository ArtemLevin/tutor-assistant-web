from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.materials.routes import create_router

MODULE = ModuleDefinition(
    name="materials",
    dependencies=("classroom",),
    router_factory=create_router,
)
