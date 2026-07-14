from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI


@dataclass(frozen=True)
class ModuleDefinition:
    name: str
    router_factory: Callable[[Any], APIRouter]
    dependencies: tuple[str, ...] = ()


class ModuleRegistry:
    def __init__(self, modules: Iterable[ModuleDefinition]) -> None:
        module_list = list(modules)
        self._modules = {module.name: module for module in module_list}
        if len(self._modules) != len(module_list):
            raise ValueError("module names must be unique")

    def ordered(self, enabled: set[str] | None = None) -> list[ModuleDefinition]:
        result: list[ModuleDefinition] = []
        visiting: set[str] = set()
        installed: set[str] = set()

        def visit(name: str) -> None:
            if name in installed:
                return
            if name in visiting:
                raise ValueError(f"cyclic module dependency at {name}")
            module = self._modules.get(name)
            if module is None:
                raise ValueError(f"missing module dependency: {name}")
            visiting.add(name)
            for dependency in module.dependencies:
                visit(dependency)
            visiting.remove(name)
            installed.add(name)
            result.append(module)

        names = sorted(enabled) if enabled is not None else self._modules
        for name in names:
            visit(name)
        return result

    def install(
        self,
        app: FastAPI,
        container: Any,
        enabled: set[str] | None = None,
    ) -> list[str]:
        ordered = self.ordered(enabled)
        for module in ordered:
            app.include_router(module.router_factory(container))
        return [module.name for module in ordered]
