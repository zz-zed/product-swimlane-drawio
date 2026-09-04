import ast
import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules
from evidence_cases import linear_spec
from release_check import EXPECTED_SKILL_FILES

SKILL = ROOT / "skills" / "product-swimlane-drawio"
TOOL = SKILL / "scripts" / "drawio_swimlane.py"
CORE = TOOL.parent / "swimlane_core"
DOCUMENT_FUNCTIONS = set("graph_root find_pool native_cell lane_node_records phase_records edge_records semantic_cells parse_geometry style_values port_from_style edge_waypoints edge_polyline stored_label_bounds node_center_in_pool geometry set_style_option set_edge_points unmanaged_root_entries graph_root_preserves_space unmanaged_cell_signatures element_signature comparison_attributes sibling_order_changes write_tree file_receipt ensure_different ensure_output_available values_from_pool read_tree routing_node_views routing_lane_views read_main_path unmanaged_edge_specs read_lane_order json_attribute managed_metadata_error managed_id_list_attribute".split())
METADATA_FUNCTIONS = set("managed_groups_attribute semantic_model_document semantic_model_hash managed_artifact_summary refresh_managed_metadata".split())


def copy_skill_package(destination: Path) -> None:
    """Copy only the explicit distributable Skill inventory, never caches."""
    for relative_name in EXPECTED_SKILL_FILES:
        source = SKILL / relative_name
        target = destination / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


def set_tree_readonly(root: Path) -> list[tuple[Path, int]]:
    modes = []
    for path in [root, *sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True)]:
        mode = stat.S_IMODE(path.stat().st_mode)
        modes.append((path, mode))
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    return modes


def restore_modes(modes: list[tuple[Path, int]]) -> None:
    for path, mode in reversed(modes):
        os.chmod(path, mode)


def markdown_anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", text.lower()).replace(" ", "-")


def assert_skill_links(skill_root: Path) -> None:
    skill_root = skill_root.resolve()
    for document in skill_root.rglob("*.md"):
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part, _, fragment = target.partition("#")
            target_path = document.resolve() if not path_part else (document.parent / path_part).resolve()
            if not target_path.is_relative_to(skill_root):
                raise AssertionError(f"Link escapes Skill package: {target} in {document}")
            if not target_path.is_file():
                raise AssertionError(f"Missing link target {target} in {document}")
            if fragment:
                anchors = {
                    markdown_anchor(heading)
                    for heading in re.findall(r"^#+\s+(.+?)\s*$", target_path.read_text(encoding="utf-8"), re.MULTILINE)
                }
                if fragment not in anchors:
                    raise AssertionError(f"Missing anchor {target} in {document}")


def imported_modules(tree: ast.AST, *, package: str = "swimlane_core") -> set[str]:
    """Return every imported module, including ``from ... import ...`` forms."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            if node.level:
                package_parts = package.split(".")
                prefix = package_parts[: len(package_parts) - (node.level - 1)]
                base = ".".join(prefix)
                if node.module:
                    modules.add(f"{base}.{node.module}" if base else node.module)
                else:
                    modules.update(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
            elif node.module:
                modules.add(node.module)
    return modules


def assert_only_allowed_imports(tree: ast.AST, allowed: set[str]) -> None:
    unexpected = imported_modules(tree) - allowed
    if unexpected:
        raise AssertionError(f"Unexpected module dependencies: {sorted(unexpected)}")


class CoreBoundaryTests(unittest.TestCase):
    def test_cli_test_helpers_do_not_write_caches_to_a_writable_complete_skill(self) -> None:
        """Each subprocess helper must protect the complete distributed Skill."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "neutral-flow.json"
            spec_path.write_bytes((ROOT / "tests/fixtures/neutral-flow.json").read_bytes())
            saved_environment = {
                key: os.environ.pop(key, None)
                for key in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTHONPATH")
            }
            try:
                for helper_name in ("test_release", "test_v3_layout"):
                    with self.subTest(helper=helper_name):
                        helper_path = ROOT / "tests" / f"{helper_name}.py"
                        helper_spec = importlib.util.spec_from_file_location(
                            f"cache_safe_{helper_name}", helper_path
                        )
                        self.assertIsNotNone(helper_spec)
                        self.assertIsNotNone(helper_spec.loader)
                        helper = importlib.util.module_from_spec(helper_spec)
                        helper_spec.loader.exec_module(helper)
                        copied_skill = root / f"{helper_name} complete Skill"
                        copy_skill_package(copied_skill)
                        helper.TOOL = copied_skill / "scripts" / "drawio_swimlane.py"
                        before = file_hashes(copied_skill)
                        output = root / f"{helper_name}.drawio"
                        result = helper.run_tool(
                            "build", "--spec", str(spec_path), "--output", str(output), "--strict"
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertTrue(output.is_file())
                        self.assertEqual(file_hashes(copied_skill), before)
            finally:
                for key, value in saved_environment.items():
                    if value is not None:
                        os.environ[key] = value

    def test_skill_frontmatter_and_relative_links_are_self_contained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied_skill = Path(temporary) / "complete Skill"
            copy_skill_package(copied_skill)
            self._assert_skill_frontmatter_and_links(copied_skill)
            outside = copied_skill.parent / "outside.md"
            outside.write_text("# outside\n", encoding="utf-8")
            copied_entry = copied_skill / "SKILL.md"
            copied_entry.write_text(copied_entry.read_text(encoding="utf-8") + "\n[escape](../outside.md)\n", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "escapes Skill package"):
                assert_skill_links(copied_skill)

    def _assert_skill_frontmatter_and_links(self, skill_root: Path) -> None:
        skill = skill_root / "SKILL.md"
        content = skill.read_text(encoding="utf-8")
        frontmatter = content.split("---\n", 2)[1]
        self.assertEqual(
            {line.split(":", 1)[0] for line in frontmatter.splitlines() if ":" in line},
            {"name", "description"},
        )
        assert_skill_links(skill_root)

    def test_contract_error_identity_and_group_diagnostics_are_preserved(self) -> None:
        loaded = load_skill_modules(TOOL, module_name="core_boundary_contracts")
        with self.assertRaises(loaded.contracts.DiagramError) as caught:
            loaded.tool.validate_build_spec({
                "title": "Flow", "lanes": "not-an-array", "nodes": [], "edges": [],
            })
        self.assertEqual(caught.exception.code, "schema/type")
        self.assertIsInstance(caught.exception, loaded.tool.contracts.DiagramError)
        with self.assertRaises(loaded.contracts.DiagramError) as group_error:
            loaded.contracts.validate_group_object(
                {"id": "group", "lane": "lane", "kind": "unknown", "nodes": ["start"]},
                "group",
            )
        self.assertEqual(group_error.exception.code, "schema/enum")
        self.assertEqual(group_error.exception.diagnostic()["evidence"]["allowed"], sorted(loaded.contracts.GROUP_KINDS))

    def test_geometry_boundary_cases_preserve_tolerance_and_shape(self) -> None:
        geometry = load_skill_modules(TOOL, module_name="core_boundary_geometry").geometry
        tolerance = geometry.GEOMETRY_TOLERANCE
        self.assertEqual(tolerance, 0.75)
        self.assertEqual(geometry.port_xy("bottom", 0.5), (0.5, 1.0))
        self.assertEqual(geometry.port_point({"left": 1, "right": 11, "top": 2, "bottom": 22, "width": 10, "height": 20}, "right", 0.5), (11, 12.0))
        self.assertEqual(geometry.compact_points([(1, 1), (1, 1), (2, 1)]), [(1, 1), (2, 1)])
        self.assertEqual(geometry.remove_collinear_points([(0, 0), (0.74, 5), (0, 10)]), [(0, 0), (0, 10)])
        self.assertEqual(geometry.remove_collinear_points([(0, 0), (0.75, 5), (0, 10)]), [(0, 0), (0.75, 5), (0, 10)])
        self.assertEqual(geometry.segment_length(((0, 0), (0, 0))), 0)
        self.assertEqual(geometry.polyline_length([(0, 0), (0, 3), (4, 3)]), 7)
        self.assertEqual(geometry.bend_count([(0, 0), (0, 3), (4, 3)]), 1)
        box = {"left": 1, "right": 3, "top": 1, "bottom": 3}
        self.assertFalse(geometry.segment_crosses_bounds(((1 + tolerance, 0), (1 + tolerance, 4)), box))
        self.assertTrue(geometry.segment_crosses_bounds(((1 + tolerance + 0.01, 0), (1 + tolerance + 0.01, 4)), box))
        self.assertTrue(geometry.segment_intersects_box(((0, 2), (4, 2)), box))
        self.assertFalse(geometry.segment_intersects_box(((0, 0), (4, 4)), box))
        self.assertTrue(geometry.segments_conflict(((0, 0), (0, 4)), ((-1, 2), (1, 2))))
        self.assertFalse(geometry.segments_conflict(((0, 0), (0, 4)), ((-1, 0), (1, 0))))

    def test_core_dependency_direction_and_no_duplicate_implementations(self) -> None:
        clearance = ast.parse((CORE / "clearance.py").read_text(encoding="utf-8"))
        contracts = ast.parse((CORE / "contracts.py").read_text(encoding="utf-8"))
        geometry = ast.parse((CORE / "geometry.py").read_text(encoding="utf-8"))
        document = ast.parse((CORE / "document.py").read_text(encoding="utf-8"))
        metadata = ast.parse((CORE / "metadata.py").read_text(encoding="utf-8"))
        sizing = ast.parse((CORE / "sizing.py").read_text(encoding="utf-8"))
        routing_policy = ast.parse((CORE / "routing_policy.py").read_text(encoding="utf-8"))
        ports = ast.parse((CORE / "ports.py").read_text(encoding="utf-8"))
        port_planner = ast.parse((CORE / "port_planner.py").read_text(encoding="utf-8"))
        labels = ast.parse((CORE / "labels.py").read_text(encoding="utf-8"))
        routing = ast.parse((CORE / "routing.py").read_text(encoding="utf-8"))
        adapter = ast.parse((CORE / "routing_adapter.py").read_text(encoding="utf-8"))
        validation = ast.parse((CORE / "validation.py").read_text(encoding="utf-8"))
        assert_only_allowed_imports(validation, {
            "json", "xml.etree.ElementTree", "swimlane_core.contracts",
            "swimlane_core.clearance",
            "swimlane_core.document", "swimlane_core.geometry", "swimlane_core.labels",
            "swimlane_core.metadata", "swimlane_core.routing", "swimlane_core.routing_policy",
            "swimlane_core.sizing",
        })
        assert_only_allowed_imports(routing, {
            "dataclasses",
            "swimlane_core.clearance",
            "swimlane_core.contracts", "swimlane_core.geometry", "swimlane_core.labels",
            "swimlane_core.port_planner", "swimlane_core.ports", "swimlane_core.routing_policy",
        })
        assert_only_allowed_imports(adapter, {
            "xml.etree.ElementTree", "swimlane_core.contracts", "swimlane_core.document",
            "swimlane_core.geometry", "swimlane_core.labels", "swimlane_core.ports",
            "swimlane_core.routing",
        })
        assert_only_allowed_imports(contracts, {"re"})
        assert_only_allowed_imports(clearance, {
            "dataclasses", "math", "typing", "swimlane_core.geometry",
        })
        assert_only_allowed_imports(geometry, {"swimlane_core.contracts"})
        assert_only_allowed_imports(document, {
            "hashlib", "json", "os", "pathlib", "tempfile", "xml.etree.ElementTree",
            "swimlane_core.contracts", "swimlane_core.geometry",
        })
        assert_only_allowed_imports(metadata, {
            "hashlib", "json", "xml.etree.ElementTree",
            "swimlane_core.contracts", "swimlane_core.document",
        })
        expected = {"node_bounds_in_pool", "port_xy", "port_point", "compact_points", "remove_collinear_points", "segment_length", "polyline_length", "bend_count", "bounds_overlap", "segment_axis", "value_between", "segment_crosses_bounds", "segment_intersects_box", "segments_conflict"}
        geometry_functions = {node.name for node in geometry.body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(geometry_functions, expected)
        entry_source = TOOL.read_text(encoding="utf-8")
        entry_functions = {node.name for node in ast.walk(ast.parse(entry_source)) if isinstance(node, ast.FunctionDef)}
        self.assertTrue(expected.isdisjoint(entry_functions))
        assert_only_allowed_imports(sizing, {"unicodedata", "swimlane_core.contracts", "swimlane_core.geometry"})
        assert_only_allowed_imports(routing_policy, set())
        assert_only_allowed_imports(ports, {"swimlane_core.contracts", "swimlane_core.geometry", "swimlane_core.routing_policy"})
        assert_only_allowed_imports(port_planner, {"dataclasses", "swimlane_core.contracts", "swimlane_core.geometry", "swimlane_core.ports"})
        assert_only_allowed_imports(labels, {"unicodedata", "swimlane_core.geometry"})
        self.assertEqual({node.name for node in sizing.body if isinstance(node, ast.FunctionDef)},
                         {"estimated_text_lines", "recommended_process_height", "node_size"})
        self.assertEqual({node.name for node in ports.body if isinstance(node, ast.FunctionDef)},
                         {"validate_side", "validate_offset", "candidate_port_offsets", "port_side_length", "finite_port_offsets", "continuous_port_capacity", "allocate_port_pair"})
        self.assertEqual({node.name for node in ports.body if isinstance(node, ast.ClassDef)}, {"PortAllocator"})
        self.assertEqual({node.name for node in port_planner.body if isinstance(node, ast.ClassDef)},
                         {"PlannerBudget", "EndpointRequest", "EdgePortRequest", "PlannedEndpoint", "EdgePortAssignment", "PortPlanIssue", "ComponentPlan", "PortPlanPreparation", "PortPlan"})
        self.assertEqual({node.name for node in labels.body if isinstance(node, ast.FunctionDef)},
                         {"edge_label_size", "label_box_candidates", "choose_label_box", "polyline_midpoint"})
        for tree, functions in ((document, DOCUMENT_FUNCTIONS), (metadata, METADATA_FUNCTIONS)):
            self.assertEqual({node.name for node in tree.body if isinstance(node, ast.FunctionDef)}, functions)
            self.assertTrue(functions.isdisjoint(entry_functions))
        validation_functions = {node.name for node in validation.body if isinstance(node, ast.FunctionDef)}
        self.assertEqual(len({name for name in validation_functions if name.startswith("_collect_")}), 20)
        self.assertEqual({name for name in validation_functions if not name.startswith("_collect_")},
                         {"effective_label_bounds", "_clearance_evidence",
                          "_summarize_validation", "validate_tree"})
        self.assertTrue(validation_functions.isdisjoint(entry_functions))
        owners = {}
        for path in [TOOL, *sorted(CORE.glob("*.py"))]:
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    self.assertNotIn(node.name, owners, f"Duplicate implementation: {node.name}")
                    owners[node.name] = path.name
        self.assertNotIn('"data-', entry_source)

    def test_dependency_guard_rejects_forbidden_import_and_import_from(self) -> None:
        unsafe_contracts = ast.parse("from drawio_swimlane import build_tree\n")
        unsafe_geometry = ast.parse("from pathlib import Path\n")
        with self.assertRaisesRegex(AssertionError, "drawio_swimlane"):
            assert_only_allowed_imports(unsafe_contracts, {"re"})
        with self.assertRaisesRegex(AssertionError, "pathlib"):
            assert_only_allowed_imports(unsafe_geometry, {"swimlane_core.contracts"})
        with self.assertRaisesRegex(AssertionError, "swimlane_core.metadata"):
            assert_only_allowed_imports(ast.parse("from . import metadata\n"), {"swimlane_core.geometry", "swimlane_core.contracts"})
        with self.assertRaisesRegex(AssertionError, "drawio_swimlane"):
            assert_only_allowed_imports(ast.parse("import drawio_swimlane\n"), {"swimlane_core.document", "swimlane_core.contracts"})

    def test_document_defaults_are_supplied_by_caller_and_styles_keep_order(self) -> None:
        loaded = load_skill_modules(TOOL, module_name="document_defaults")
        pool = ET.Element("mxCell", {"data-row-gap": "123"})
        defaults = {key: value + 7 for key, value in loaded.tool.DEFAULTS.items()}
        values = loaded.document.values_from_pool(pool, defaults)
        self.assertEqual(values["row_gap"], 123)
        self.assertEqual(values["title_height"], defaults["title_height"])
        with self.assertRaises(TypeError):
            loaded.document.values_from_pool(pool)
        cell = ET.Element("mxCell", {"style": "rounded=1;fillColor=red;html=1;fillColor=blue;"})
        loaded.document.set_style_option(cell, "fillColor", "green")
        self.assertEqual(cell.get("style"), "rounded=1;fillColor=green;html=1;")
        self.assertEqual([loaded.contracts.number(v) for v in (1.0, 1.123456, 0.00001)], ["1", "1.1235", "0"])

    def test_document_reader_uses_existing_parser_and_exception_identity(self) -> None:
        loaded = load_skill_modules(TOOL, module_name="document_reader")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "broken.drawio"
            source.write_text("<mxfile>", encoding="utf-8")
            with self.assertRaises(ET.ParseError):
                loaded.document.read_tree(source)
            with self.assertRaises(FileNotFoundError):
                loaded.document.read_tree(Path(temporary) / "missing.drawio")
        with self.assertRaises(loaded.contracts.DiagramError):
            loaded.document.graph_root(ET.ElementTree(ET.Element("mxfile")))

    def test_document_atomic_writer_retains_existing_output_and_cleans_failure(self) -> None:
        loaded = load_skill_modules(TOOL, module_name="document_atomic_writer")
        tree = loaded.tool.build_tree(linear_spec(2))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing.drawio"
            sentinel = b"reviewed output must survive"
            output.write_bytes(sentinel)
            with mock.patch.object(loaded.document.os, "replace", side_effect=OSError("replace failure")):
                with self.assertRaisesRegex(OSError, "replace failure"):
                    loaded.document.write_tree(tree, output)
            self.assertEqual(output.read_bytes(), sentinel)
            self.assertEqual(list(Path(temporary).iterdir()), [output])
            with self.assertRaises(loaded.contracts.DiagramError):
                loaded.document.ensure_output_available(output, False)
            with self.assertRaises(loaded.contracts.DiagramError):
                loaded.document.ensure_different(output, output.parent / "." / output.name)

    def test_readonly_paths_never_refresh_or_mutate_managed_metadata(self) -> None:
        loaded = load_skill_modules(TOOL, module_name="readonly_metadata")
        original = loaded.tool.build_tree(linear_spec(2))
        for state in ("managed", "missing-hash", "mismatched-hash", "unknown-hash-version"):
            with self.subTest(state=state):
                tree = copy.deepcopy(original)
                pool = loaded.document.find_pool(tree)
                if state == "missing-hash":
                    pool.attrib.pop(loaded.contracts.DATA_MODEL_HASH)
                elif state == "mismatched-hash":
                    pool.set(loaded.contracts.DATA_MODEL_HASH, "0" * 64)
                elif state == "unknown-hash-version":
                    pool.set(loaded.contracts.DATA_MODEL_HASH_VERSION, "999")
                before = ET.tostring(tree.getroot())
                with mock.patch.object(loaded.metadata, "refresh_managed_metadata", side_effect=AssertionError("read-only refresh")):
                    loaded.metadata.managed_artifact_summary(tree)
                    loaded.tool.inspect_tree(tree)
                    loaded.validation.validate_tree(tree)
                    loaded.tool.compare_trees(tree, copy.deepcopy(tree))
                self.assertEqual(ET.tostring(tree.getroot()), before)

    def test_metadata_refresh_only_at_original_build_and_patch_sites(self) -> None:
        owners = []
        for function in ast.parse(TOOL.read_text(encoding="utf-8")).body:
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "refresh_managed_metadata":
                    owners.append(function.name)
        self.assertEqual(owners, ["build_tree", "patch_tree"])

    def test_metadata_distinguishes_missing_empty_malformed_and_wrong_type(self) -> None:
        loaded = load_skill_modules(TOOL, module_name="metadata_values")
        cell = ET.Element("mxCell")
        self.assertIsNone(loaded.document.managed_id_list_attribute(cell, "data-main-path", None))
        cell.set("data-main-path", "[]")
        self.assertEqual(loaded.document.managed_id_list_attribute(cell, "data-main-path", None), [])
        for raw in ("", "null", "{}", '["bad id"]'):
            with self.subTest(raw=raw):
                cell.set("data-main-path", raw)
                with self.assertRaises(loaded.contracts.DiagramError) as caught:
                    loaded.document.managed_id_list_attribute(cell, "data-main-path", None)
                self.assertEqual(caught.exception.code, "integrity/schema-composition-mismatch")

    def test_document_lane_order_keeps_strict_errors_and_filtered_native_order(self) -> None:
        loaded = load_skill_modules(TOOL, module_name="strict_lane_order")
        root = ET.Element("root")
        for lane_id in ("b", "outside", "a"):
            ET.SubElement(root, "mxCell", {loaded.contracts.DATA_KIND: "lane", loaded.contracts.DATA_SEMANTIC_ID: lane_id})
        pool = ET.Element("mxCell")
        lanes = {"a": {}, "b": {}}
        self.assertEqual(loaded.document.read_lane_order(pool, root, lanes), ["b", "a"])
        pool.set(loaded.contracts.DATA_LANE_ORDER, '["a", "b"]')
        self.assertEqual(loaded.document.read_lane_order(pool, root, lanes), ["a", "b"])
        cases = (
            ("{", "Invalid managed metadata in data-lane-order", {"attribute": "data-lane-order"}, "JSONDecodeError"),
            ("null", "Managed metadata in data-lane-order has the wrong type", {"attribute": "data-lane-order", "expected_type": "list"}, None),
            ('["a", "a"]', "Managed metadata in data-lane-order must contain semantic IDs", {"attribute": "data-lane-order", "cause": "schema/duplicate"}, "DiagramError"),
        )
        for raw, message, evidence, cause in cases:
            with self.subTest(raw=raw):
                pool.set(loaded.contracts.DATA_LANE_ORDER, raw)
                before = ET.tostring(pool)
                with self.assertRaises(loaded.contracts.DiagramError) as caught:
                    loaded.document.read_lane_order(pool, root, lanes)
                self.assertEqual(caught.exception.diagnostic(), {
                    "code": "integrity/schema-composition-mismatch", "severity": "error",
                    "message": message, "evidence": evidence,
                    "supported_fixes": ["restore-semantic-metadata", "controlled-rebuild"],
                    "subject": {"kind": "pool", "id": "main"},
                })
                actual_cause = type(caught.exception.__cause__).__name__ if caught.exception.__cause__ else None
                self.assertEqual(actual_cause, cause)
                self.assertEqual(ET.tostring(pool), before)
        for order in (["a", "outside"], []):
            pool.set(loaded.contracts.DATA_LANE_ORDER, json.dumps(order))
            with self.assertRaises(loaded.contracts.DiagramError) as caught:
                loaded.document.read_lane_order(pool, root, lanes)
            self.assertEqual(caught.exception.diagnostic(), {
                "code": "integrity/schema-composition-mismatch", "severity": "error",
                "message": "Managed lane order does not match the diagram lanes",
                "evidence": {"lane_order": order, "lane_ids": ["a", "b"]},
                "supported_fixes": ["restore-lane-order", "controlled-rebuild"],
            })

    def test_validation_module_does_not_call_document_or_metadata_mutators(self) -> None:
        loaded = load_skill_modules(TOOL, module_name="readonly_validation_module")
        tree = loaded.tool.build_tree(linear_spec(2))
        before = ET.tostring(tree.getroot())
        with ExitStack() as stack:
            for name in ("geometry", "set_style_option", "set_edge_points", "write_tree"):
                stack.enter_context(mock.patch.object(loaded.document, name, side_effect=AssertionError("validation mutation")))
            stack.enter_context(mock.patch.object(loaded.metadata, "refresh_managed_metadata", side_effect=AssertionError("validation refresh")))
            report = loaded.validation.validate_tree(tree)
        self.assertTrue(report["valid"])
        self.assertEqual(ET.tostring(tree.getroot()), before)
        self.assertFalse(hasattr(loaded.tool, "validate_tree"))
        self.assertFalse(hasattr(loaded.validation, "routing_adapter"))

    def test_two_checkouts_do_not_reuse_core_modules_or_write_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied_skill = Path(temporary) / "skill"
            original_cache_bytes = {
                path.relative_to(SKILL).as_posix(): path.read_bytes()
                for path in SKILL.rglob("*.pyc")
            }
            shutil.copytree(SKILL, copied_skill)
            copied_cache_bytes = {
                path.relative_to(copied_skill).as_posix(): path.read_bytes()
                for path in copied_skill.rglob("*.pyc")
            }
            module_names = ("clearance", "contracts", "geometry", "document", "metadata", "sizing",
                            "routing_policy", "ports", "port_planner", "labels", "routing",
                            "routing_adapter", "validation")
            sentinel_names = ("swimlane_core", "swimlane_core.unrelated",
                              *(f"swimlane_core.{name}" for name in module_names))
            sentinels = {name: ModuleType(name) for name in sentinel_names}
            original_path = list(sys.path)
            with mock.patch.dict(sys.modules, sentinels), mock.patch.object(sys, "dont_write_bytecode", False):
                package_before = {name: value for name, value in sys.modules.items()
                                  if name == "swimlane_core" or name.startswith("swimlane_core.")}

                def assert_restored() -> None:
                    self.assertEqual(sys.path, original_path)
                    self.assertFalse(sys.dont_write_bytecode)
                    self.assertEqual(package_before, {
                        name: value for name, value in sys.modules.items()
                        if name == "swimlane_core" or name.startswith("swimlane_core.")
                    })

                first = load_skill_modules(TOOL, module_name="first_checkout")
                assert_restored()
                second = load_skill_modules(copied_skill / "scripts" / "drawio_swimlane.py", module_name="second_checkout")
                assert_restored()
                third = load_skill_modules(TOOL, module_name="first_checkout_again")
                assert_restored()
                for name in module_names:
                    with self.subTest(module=name):
                        first_module, second_module, third_module = (getattr(loaded, name) for loaded in (first, second, third))
                        self.assertIsNot(first_module, second_module)
                        self.assertIsNot(first_module, third_module)
                        self.assertEqual(Path(first_module.__file__).resolve(), (CORE / f"{name}.py").resolve())
                        self.assertEqual(Path(second_module.__file__).resolve(), (copied_skill / "scripts" / "swimlane_core" / f"{name}.py").resolve())
                        self.assertEqual(first_module.__file__, third_module.__file__)
                with self.assertRaises(FileNotFoundError):
                    load_skill_modules(copied_skill / "scripts" / "missing.py", module_name="failed_checkout")
                assert_restored()
            self.assertIs(first.tool.core_validation, first.validation)
            self.assertIs(first.validation.document, first.document)
            self.assertIs(first.validation.metadata, first.metadata)
            self.assertIs(second.validation.routing, second.routing)
            self.assertIs(first.tool.document, first.document)
            self.assertIs(first.metadata.document, first.document)
            self.assertIs(first.document.contracts, first.contracts)
            self.assertIs(second.metadata.document, second.document)
            self.assertIsNot(first.contracts.DiagramError, second.contracts.DiagramError)
            self.assertEqual(original_cache_bytes, {
                path.relative_to(SKILL).as_posix(): path.read_bytes()
                for path in SKILL.rglob("*.pyc")
            })
            self.assertEqual(copied_cache_bytes, {
                path.relative_to(copied_skill).as_posix(): path.read_bytes()
                for path in copied_skill.rglob("*.pyc")
            })

    def test_complete_skill_runs_all_cli_commands_from_external_paths(self) -> None:
        """The distributed unit is the whole Skill, not one copied entry script."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "外部 工作目录"
            workspace.mkdir()
            original_caches = {
                path.relative_to(SKILL).as_posix(): path.read_bytes()
                for path in SKILL.rglob("*.pyc")
            }

            skill_only = root / "Skill only 空格" / "product-swimlane-drawio"
            copy_skill_package(skill_only)
            plugin_root = root / "complete plugin"
            (plugin_root / ".codex-plugin").mkdir(parents=True)
            shutil.copy2(ROOT / ".codex-plugin" / "plugin.json",
                         plugin_root / ".codex-plugin" / "plugin.json")
            plugin_skill = plugin_root / "skills" / "product-swimlane-drawio"
            copy_skill_package(plugin_skill)
            symlink_skill = root / "linked Skill 空格"
            symlink_skill.symlink_to(skill_only, target_is_directory=True)

            for label, installed_skill, readonly in (
                ("skill-only", skill_only, False),
                ("complete-plugin", plugin_skill, False),
                ("symlink-readonly", symlink_skill, True),
            ):
                with self.subTest(layout=label):
                    modes = set_tree_readonly(skill_only) if readonly else []
                    try:
                        if readonly:
                            readonly_paths = [skill_only, *skill_only.rglob("*")]
                            self.assertTrue(all(
                                not (stat.S_IMODE(path.stat().st_mode) & 0o222)
                                for path in readonly_paths
                            ))
                            before_install = {
                                path.relative_to(skill_only).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in skill_only.rglob("*") if path.is_file()
                            }
                        spec = workspace / f"{label}-spec.json"
                        spec.write_bytes((ROOT / "tests/fixtures/neutral-flow.json").read_bytes())
                        output = workspace / f"{label}-built.drawio"
                        patched = workspace / f"{label}-patched.drawio"
                        changes = workspace / f"{label}-changes.json"
                        changes.write_text(json.dumps({"update_nodes": [{"id": "step-a", "label": "Reviewed"}]}), encoding="utf-8")
                        tool = installed_skill / "scripts" / "drawio_swimlane.py"
                        environment = os.environ.copy()
                        for key in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX"):
                            environment.pop(key, None)

                        def run(*arguments: str) -> dict:
                            result = subprocess.run(
                                [sys.executable, "-B", str(tool), *arguments],
                                cwd=workspace, env=environment, check=True, timeout=20,
                                capture_output=True, text=True,
                            )
                            return json.loads(result.stdout)

                        build = run("build", "--spec", str(spec), "--output", str(output), "--strict")
                        validation = run("validate", "--input", str(output), "--strict")
                        inspected = run("inspect", "--input", str(output))
                        patch = run("patch", "--input", str(output), "--expected-input-sha256",
                                    inspected["input"]["sha256"], "--changes", str(changes),
                                    "--output", str(patched), "--strict")
                        compared = run("compare", "--before", str(output), "--after", str(patched),
                                       "--changes", str(changes))
                        self.assertTrue(build["quality_gate_passed"])
                        self.assertTrue(validation["valid"])
                        self.assertEqual(inspected["managed_state"], "managed")
                        self.assertTrue(patch["quality_gate_passed"])
                        self.assertTrue(compared["preserved"])
                        self.assertTrue(output.is_file())
                        self.assertTrue(patched.is_file())
                        if readonly:
                            after_install = {
                                path.relative_to(skill_only).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in skill_only.rglob("*") if path.is_file()
                            }
                            self.assertEqual(before_install, after_install)
                        else:
                            self.assertFalse(any(installed_skill.rglob("*.pyc")))
                    finally:
                        restore_modes(modes)

            self.assertEqual(original_caches, {
                path.relative_to(SKILL).as_posix(): path.read_bytes()
                for path in SKILL.rglob("*.pyc")
            })


if __name__ == "__main__":
    unittest.main()
