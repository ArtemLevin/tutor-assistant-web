from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.students.routes import create_router

MODULE = ModuleDefinition(
    name="students",
    dependencies=("identity",),
    router_factory=create_router,
)
