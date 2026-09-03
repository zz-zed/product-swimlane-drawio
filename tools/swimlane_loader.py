"""Isolated loader for a bundled swimlane Skill checkout.

File-based test loading does not automatically make the script directory a
package search path.  This helper loads an entrypoint and its private package
from one explicit Skill path, then restores interpreter state so a second
checkout cannot reuse the first checkout's ``swimlane_core`` modules.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType


PACKAGE_PREFIX = "swimlane_core"


@dataclass(frozen=True)
class LoadedSkill:
    tool: ModuleType
    contracts: ModuleType
    geometry: ModuleType
    document: ModuleType
    metadata: ModuleType
    sizing: ModuleType
    routing_policy: ModuleType
    ports: ModuleType
    labels: ModuleType
    routing: ModuleType
    routing_adapter: ModuleType
    validation: ModuleType


def _package_modules() -> dict[str, ModuleType]:
    return {
        name: module
        for name, module in sys.modules.items()
        if name == PACKAGE_PREFIX or name.startswith(PACKAGE_PREFIX + ".")
    }


def load_skill_modules(tool_path: Path, *, module_name: str) -> LoadedSkill:
    """Load one checkout without mutating path, bytecode, or package caches."""
    tool_path = tool_path.resolve()
    scripts_dir = tool_path.parent
    original_path = list(sys.path)
    original_bytecode = sys.dont_write_bytecode
    original_modules = _package_modules()
    for name in original_modules:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(scripts_dir))
    sys.dont_write_bytecode = True
    try:
        contracts = importlib.import_module("swimlane_core.contracts")
        geometry = importlib.import_module("swimlane_core.geometry")
        document = importlib.import_module("swimlane_core.document")
        metadata = importlib.import_module("swimlane_core.metadata")
        sizing = importlib.import_module("swimlane_core.sizing")
        routing_policy = importlib.import_module("swimlane_core.routing_policy")
        ports = importlib.import_module("swimlane_core.ports")
        labels = importlib.import_module("swimlane_core.labels")
        routing = importlib.import_module("swimlane_core.routing")
        routing_adapter = importlib.import_module("swimlane_core.routing_adapter")
        validation = importlib.import_module("swimlane_core.validation")
        spec = importlib.util.spec_from_file_location(module_name, tool_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load swimlane tool: {tool_path}")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        return LoadedSkill(tool=tool, contracts=contracts, geometry=geometry,
                           document=document, metadata=metadata, sizing=sizing,
                           routing_policy=routing_policy, ports=ports, labels=labels,
                           routing=routing, routing_adapter=routing_adapter,
                           validation=validation)
    finally:
        for name in list(_package_modules()):
            sys.modules.pop(name, None)
        sys.modules.update(original_modules)
        sys.path[:] = original_path
        sys.dont_write_bytecode = original_bytecode
