"""Command/tree integration contracts for batch route planning.

These are deliberately small real workflows.  They exercise the CLI or the
actual ``build_tree``/``patch_tree`` writeback path and never replace the
planner, router, or XML adapter with a mock.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills" / "product-swimlane-drawio" / "scripts" / "drawio_swimlane.py"
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules


def linear_spec(*, explicit: bool = False, waypoints=None) -> dict:
    edge = {"id": "first", "from": "start", "to": "step"}
    if explicit:
        edge.update({"exit_side": "bottom", "entry_side": "top", "exit_offset": 0.5, "entry_offset": 0.5})
    if waypoints is not None:
        edge["waypoints"] = waypoints
    return {
        "schema_version": "2", "title": "Batch fixture",
        "lanes": [{"id": "lane", "label": "Lane", "width": 220}],
        "nodes": [
            {"id": "start", "lane": "lane", "rank": 1, "type": "start", "label": ""},
            {"id": "step", "lane": "lane", "rank": 2, "type": "process", "label": "Step"},
            {"id": "end", "lane": "lane", "rank": 3, "type": "end", "label": ""},
        ],
        "edges": [edge, {"id": "second", "from": "step", "to": "end"}],
        "main_path": ["start", "step", "end"],
    }


class BatchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(TOOL, module_name="batch_integration_tests")
        cls.tool = cls.loaded.tool
        cls.document = cls.loaded.document
        cls.adapter = cls.loaded.routing_adapter
        cls.contracts = cls.loaded.contracts

    def run_tool(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-B", str(TOOL), *args], cwd=ROOT,
            capture_output=True, text=True, check=False,
        )
        if check and result.returncode:
            self.fail(f"command failed ({result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    @staticmethod
    def edge(root: ET.Element, edge_id: str) -> ET.Element:
        return next(cell for cell in root.iter("mxCell") if cell.attrib.get("data-semantic-id") == edge_id)

    def test_build_batch_failure_leaves_existing_output_sentinel(self):
        """A planning failure is reported before command delivery opens output."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            spec = directory / "invalid-route.json"
            output = directory / "result.drawio"
            conflict = linear_spec(explicit=True)
            # Two explicit non-reusable terminals collide on start/bottom.
            conflict["edges"].append({
                "id": "collision", "from": "start", "to": "end",
                "exit_side": "bottom", "entry_side": "top",
                "exit_offset": 0.5, "entry_offset": 0.5,
            })
            spec.write_text(json.dumps(conflict), encoding="utf-8")
            sentinel = b"do-not-replace-on-route-failure"
            output.write_bytes(sentinel)
            result = self.run_tool("build", "--spec", str(spec), "--output", str(output), "--force", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), sentinel)
            self.assertIn("routing/port-plan", result.stdout)

    def test_patch_batch_failure_is_edge_atomic_and_does_not_write_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            spec_path = directory / "spec.json"
            before_path = directory / "before.drawio"
            output = directory / "output.drawio"
            changes_path = directory / "changes.json"
            spec_path.write_text(json.dumps(linear_spec(explicit=True)), encoding="utf-8")
            self.run_tool("build", "--spec", str(spec_path), "--output", str(before_path))
            original_bytes = before_path.read_bytes()
            tree = ET.parse(before_path)
            root = self.document.graph_root(tree)
            first_before = ET.tostring(self.edge(root, "first"))
            second_before = ET.tostring(self.edge(root, "second"))
            changes = {"update_edges": [{
                "id": "second", "from": "start", "to": "end", "label": "must not leak",
                "exit_side": "bottom", "entry_side": "top",
                "exit_offset": 0.5, "entry_offset": 0.5,
            }]}
            with self.assertRaises(self.contracts.DiagramError):
                self.tool.patch_tree(tree, changes, allow_geometry_updates=False)
            self.assertEqual(ET.tostring(self.edge(root, "first")), first_before)
            self.assertEqual(ET.tostring(self.edge(root, "second")), second_before)
            changes_path.write_text(json.dumps(changes), encoding="utf-8")
            sentinel = b"pre-existing-output"
            output.write_bytes(sentinel)
            result = self.run_tool(
                "patch", "--input", str(before_path), "--expected-input-sha256", hashlib.sha256(original_bytes).hexdigest(),
                "--changes", str(changes_path), "--output", str(output), "--force", check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(before_path.read_bytes(), original_bytes)
            self.assertEqual(output.read_bytes(), sentinel)

    def test_patch_keeps_frozen_edge_xml_style_geometry_label_and_order(self):
        tree = self.tool.build_tree(linear_spec())
        root = self.document.graph_root(tree)
        frozen = self.edge(root, "second")
        frozen.attrib["style"] += "vendorToken=keep;"
        ET.SubElement(frozen, "vendor-extension", {"mode": "keep"}).text = "opaque"
        geometry = frozen.find("mxGeometry")
        ET.SubElement(geometry, "mxPoint", {"as": "vendor-offset", "x": "3", "y": "4"})
        frozen_before = ET.tostring(frozen)
        sibling_before = [child.attrib.get("id") for child in list(root)]
        self.tool.patch_tree(tree, {"update_edges": [{"id": "first", "label": "Changed", "reroute": True}]}, allow_geometry_updates=False)
        frozen_after = self.edge(root, "second")
        self.assertEqual(ET.tostring(frozen_after), frozen_before)
        self.assertEqual([child.attrib.get("id") for child in list(root)], sibling_before)

    def test_provenance_and_explicit_waypoint_writeback_are_distinct(self):
        derived = self.tool.build_tree(linear_spec())
        explicit = self.tool.build_tree(linear_spec(explicit=True))
        derived_edge = self.edge(self.document.graph_root(derived), "first")
        explicit_edge = self.edge(self.document.graph_root(explicit), "first")
        for key in (
            self.contracts.DATA_EXIT_SIDE_EXPLICIT, self.contracts.DATA_ENTRY_SIDE_EXPLICIT,
            self.contracts.DATA_EXIT_OFFSET_EXPLICIT, self.contracts.DATA_ENTRY_OFFSET_EXPLICIT,
        ):
            self.assertEqual(derived_edge.attrib[key], "0")
            self.assertEqual(explicit_edge.attrib[key], "1")

        # A frozen explicit route is not compacted when a different edge is patched.
        tree = self.tool.build_tree(linear_spec(waypoints=[]))
        root = self.document.graph_root(tree)
        first = self.edge(root, "first")
        geometry_before = ET.tostring(first.find("mxGeometry"))
        self.tool.patch_tree(tree, {"update_edges": [{"id": "second", "label": "Later", "reroute": True}]}, allow_geometry_updates=False)
        self.assertEqual(ET.tostring(self.edge(root, "first").find("mxGeometry")), geometry_before)

        repeated = self.tool.build_tree(linear_spec())
        repeated_root = self.document.graph_root(repeated)
        repeated_first = self.edge(repeated_root, "first")
        # A calculation may normalize these points; a patch must not normalize
        # the authoring XML of this frozen explicit edge.
        self.document.set_edge_points(repeated_first, [(110.0, 130.0), (110.0, 130.0), (110.0, 160.0), (110.0, 190.0)])
        repeated_first.attrib[self.contracts.DATA_WAYPOINTS_ORIGIN] = "explicit"
        repeated_before = ET.tostring(repeated_first.find("mxGeometry"))
        self.tool.patch_tree(repeated, {"update_edges": [{"id": "second", "label": "Again", "reroute": True}]}, allow_geometry_updates=False)
        self.assertEqual(ET.tostring(self.edge(repeated_root, "first").find("mxGeometry")), repeated_before)

    def test_build_preserves_object_and_compact_array_waypoint_forms(self):
        baseline = self.tool.build_tree(linear_spec())
        baseline_root = self.document.graph_root(baseline)
        pool = self.document.find_pool(baseline)
        lanes, nodes = self.document.lane_node_records(baseline_root, pool)
        path = self.document.edge_polyline(self.edge(baseline_root, "first"), lanes, nodes)
        self.assertEqual(path[0][0], path[-1][0])
        x = path[0][0]
        first_y = path[0][1] + 20.0
        second_y = path[0][1] + 40.0
        expected = [(x, first_y), (x, first_y), (x, second_y)]
        forms = (
            [{"x": x, "y": first_y}, {"x": x, "y": first_y}, {"x": x, "y": second_y}],
            [[x, first_y], [x, first_y], [x, second_y]],
        )
        for waypoints in forms:
            with self.subTest(waypoints=waypoints):
                tree = self.tool.build_tree(linear_spec(waypoints=waypoints))
                root = self.document.graph_root(tree)
                self.assertEqual(self.document.edge_waypoints(self.edge(root, "first")), expected)

    def test_existing_style_updates_only_anchor_tokens_and_profile_uses_effective_style(self):
        tree = self.tool.build_tree(linear_spec())
        root = self.document.graph_root(tree)
        first = self.edge(root, "first")
        first.attrib["style"] = "vendorBefore=1;" + first.attrib["style"] + "vendorAfter=2;"
        before = first.attrib["style"].split(";")
        self.tool.patch_tree(tree, {"update_edges": [{"id": "first", "label": "Changed", "reroute": True}]}, allow_geometry_updates=False)
        after = self.edge(root, "first").attrib["style"].split(";")
        anchors = {"exitX", "exitY", "exitDx", "exitDy", "entryX", "entryY", "entryDx", "entryDy"}
        self.assertEqual([part for part in before if part.split("=", 1)[0] not in anchors], [part for part in after if part.split("=", 1)[0] not in anchors])
        lanes, nodes = self.document.lane_node_records(root, self.document.find_pool(tree))
        profiles = self.adapter.arrowhead_clearance_profiles(
            [self.adapter.existing_edge_spec(self.edge(root, "first"))], lanes, nodes,
            existing_edges={"first": self.edge(root, "first")},
        )
        profile = profiles["first"]
        self.assertEqual(profile["edge_style"], self.edge(root, "first").attrib["style"])
        self.assertEqual(profile["target_style"], nodes["step"]["cell"].attrib["style"])
        self.assertEqual(profile["target_type"], nodes["step"]["cell"].attrib[self.contracts.DATA_NODE_TYPE])

    def test_new_edge_attribute_order_matches_canonical_history_for_all_schema_versions(self):
        specs = []
        specs.append(linear_spec())
        v1 = linear_spec()
        v1["schema_version"] = "1"
        specs.append(v1)
        v3 = {
            "schema_version": "3", "title": "V3 canonical", "behavior_pattern": "linear",
            "layout": {"profile": "review"}, "lanes": [{"id": "lane", "label": "Lane", "width": 220}],
            "nodes": [
                {"id": "start", "lane": "lane", "rank": 1, "type": "start", "label": ""},
                {"id": "step", "lane": "lane", "rank": 2, "type": "process", "label": "Step"},
                {"id": "end", "lane": "lane", "rank": 3, "type": "end", "label": ""},
            ],
            "edges": [{"id": "first", "from": "start", "to": "step", "flow_role": "main"}, {"id": "second", "from": "step", "to": "end", "flow_role": "main"}],
            "main_path": ["start", "step", "end"],
        }
        specs.append(v3)
        for spec in specs:
            with self.subTest(schema_version=spec["schema_version"]):
                tree = self.tool.build_tree(spec)
                edge = self.edge(self.document.graph_root(tree), "first")
                keys = list(edge.attrib)
                self.assertEqual(keys[:8], [
                    "id", "parent", "edge", self.contracts.DATA_KIND,
                    self.contracts.DATA_SEMANTIC_ID, "source", "target", "style",
                ])
                self.assertEqual(keys[8], "value")
                self.assertEqual(keys[9], self.contracts.DATA_EDGE_TYPE)

    def test_repeated_build_and_patch_are_byte_deterministic_and_v2_cli_round_trips(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            spec_path = directory / "spec.json"
            one, two = directory / "one.drawio", directory / "two.drawio"
            patched_one, patched_two = directory / "patched-one.drawio", directory / "patched-two.drawio"
            changes = directory / "changes.json"
            spec_path.write_text(json.dumps(linear_spec()), encoding="utf-8")
            changes.write_text(json.dumps({"update_edges": [{"id": "first", "label": "Reviewed", "reroute": True}]}), encoding="utf-8")
            self.run_tool("build", "--spec", str(spec_path), "--output", str(one))
            self.run_tool("build", "--spec", str(spec_path), "--output", str(two))
            self.assertEqual(one.read_bytes(), two.read_bytes())
            digest = hashlib.sha256(one.read_bytes()).hexdigest()
            self.run_tool("patch", "--input", str(one), "--expected-input-sha256", digest, "--changes", str(changes), "--output", str(patched_one))
            digest = hashlib.sha256(two.read_bytes()).hexdigest()
            self.run_tool("patch", "--input", str(two), "--expected-input-sha256", digest, "--changes", str(changes), "--output", str(patched_two))
            self.assertEqual(patched_one.read_bytes(), patched_two.read_bytes())
            inspected = json.loads(self.run_tool("inspect", "--input", str(patched_one)).stdout)
            self.assertEqual(inspected["schema_version"], "2")
            self.assertTrue(json.loads(self.run_tool("validate", "--input", str(patched_one)).stdout)["valid"])
            compared = json.loads(self.run_tool("compare", "--before", str(one), "--after", str(patched_one), "--changes", str(changes)).stdout)
            self.assertTrue(compared["preserved"])


if __name__ == "__main__":
    unittest.main()
