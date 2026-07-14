from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.dashboard.routes import create_router

MODULE = ModuleDefinition(
    name="dashboard",
    dependencies=("identity", "materials"),
    router_factory=create_router,
)
