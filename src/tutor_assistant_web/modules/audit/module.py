from tutor_assistant_web.bootstrap.registry import ModuleDefinition
from tutor_assistant_web.modules.audit.routes import create_router

MODULE = ModuleDefinition(name="audit", router_factory=create_router)
