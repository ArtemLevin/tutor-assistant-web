from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.automation.routes import create_router

MODULE = ModuleDefinition(
    name="automation",
    dependencies=("materials",),
    router_factory=create_router,
)
