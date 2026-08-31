from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.practice.routes import create_router

MODULE = ModuleDefinition(
    name="practice",
    dependencies=("identity", "students", "audit"),
    router_factory=create_router,
)
