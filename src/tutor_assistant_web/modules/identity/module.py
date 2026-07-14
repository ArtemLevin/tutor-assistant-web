from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.identity.routes import create_router

MODULE = ModuleDefinition(name="identity", router_factory=create_router)
