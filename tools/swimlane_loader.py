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
    build: ModuleType
    construction: ModuleType
    clearance: ModuleType
    contracts: ModuleType
    geometry: ModuleType
    document: ModuleType
    metadata: ModuleType
    migration: ModuleType
    sizing: ModuleType
    routing_policy: ModuleType
    ports: ModuleType
    port_planner: ModuleType
    labels: ModuleType
    patch_operations: ModuleType
    roundtrip: ModuleType
    routing: ModuleType
    routing_adapter: ModuleType
    spec_validation: ModuleType
    layout: ModuleType
    validation: ModuleType
    pattern_rules: ModuleType
    group_rules: ModuleType
    context_native: ModuleType
    semantic_context: ModuleType
    provenance: ModuleType
    context_bundle: ModuleType
    context_workflow: ModuleType
    preview_png: ModuleType
    review_evidence: ModuleType
    review_workflow: ModuleType
    review_preservation: ModuleType
    review_repair: ModuleType
    review_state: ModuleType
    review_cycle: ModuleType


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
        build = importlib.import_module("swimlane_core.build")
        construction = importlib.import_module("swimlane_core.construction")
        clearance = importlib.import_module("swimlane_core.clearance")
        contracts = importlib.import_module("swimlane_core.contracts")
        geometry = importlib.import_module("swimlane_core.geometry")
        document = importlib.import_module("swimlane_core.document")
        metadata = importlib.import_module("swimlane_core.metadata")
        migration = importlib.import_module("swimlane_core.migration")
        sizing = importlib.import_module("swimlane_core.sizing")
        routing_policy = importlib.import_module("swimlane_core.routing_policy")
        ports = importlib.import_module("swimlane_core.ports")
        port_planner = importlib.import_module("swimlane_core.port_planner")
        labels = importlib.import_module("swimlane_core.labels")
        patch_operations = importlib.import_module("swimlane_core.patch_operations")
        roundtrip = importlib.import_module("swimlane_core.roundtrip")
        routing = importlib.import_module("swimlane_core.routing")
        routing_adapter = importlib.import_module("swimlane_core.routing_adapter")
        spec_validation = importlib.import_module("swimlane_core.spec_validation")
        layout = importlib.import_module("swimlane_core.layout")
        validation = importlib.import_module("swimlane_core.validation")
        pattern_rules = importlib.import_module("swimlane_core.pattern_rules")
        group_rules = importlib.import_module("swimlane_core.group_rules")
        context_native = importlib.import_module("swimlane_core.context_native")
        semantic_context = importlib.import_module("swimlane_core.semantic_context")
        provenance = importlib.import_module("swimlane_core.provenance")
        context_bundle = importlib.import_module("swimlane_core.context_bundle")
        context_workflow = importlib.import_module("swimlane_core.context_workflow")
        preview_png = importlib.import_module("swimlane_core.preview_png")
        review_evidence = importlib.import_module("swimlane_core.review_evidence")
        review_workflow = importlib.import_module("swimlane_core.review_workflow")
        review_preservation = importlib.import_module("swimlane_core.review_preservation")
        review_repair = importlib.import_module("swimlane_core.review_repair")
        review_state = importlib.import_module("swimlane_core.review_state")
        review_cycle = importlib.import_module("swimlane_core.review_cycle")
        spec = importlib.util.spec_from_file_location(module_name, tool_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load swimlane tool: {tool_path}")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        return LoadedSkill(tool=tool, build=build, construction=construction,
                           clearance=clearance, contracts=contracts, geometry=geometry,
                           document=document, metadata=metadata, migration=migration, sizing=sizing,
                           routing_policy=routing_policy, ports=ports,
                           port_planner=port_planner, labels=labels,
                           patch_operations=patch_operations, roundtrip=roundtrip,
                           routing=routing, routing_adapter=routing_adapter,
                           spec_validation=spec_validation, layout=layout,
                           validation=validation, pattern_rules=pattern_rules,
                           group_rules=group_rules, context_native=context_native,
                           semantic_context=semantic_context, provenance=provenance,
                           context_bundle=context_bundle, context_workflow=context_workflow,
                           preview_png=preview_png,
                           review_evidence=review_evidence, review_workflow=review_workflow,
                           review_preservation=review_preservation, review_repair=review_repair,
                           review_state=review_state, review_cycle=review_cycle)
    finally:
        for name in list(_package_modules()):
            sys.modules.pop(name, None)
        sys.modules.update(original_modules)
        sys.path[:] = original_path
        sys.dont_write_bytecode = original_bytecode
