from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.portal.routes import create_router

MODULE = ModuleDefinition(
    name="portal",
    dependencies=("automation",),
    router_factory=create_router,
)
