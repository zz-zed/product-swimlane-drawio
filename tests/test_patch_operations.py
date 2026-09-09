"""Operation ordering and saved-scene boundaries across combined patches."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"
sys.path.insert(0, str(ROOT / "tools"))
from evidence_cases import linear_spec
from swimlane_loader import load_skill_modules
from tests.test_v3_layout import v3_incremental_spec


class PatchOperationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(TOOL, module_name="patch_operation_tests")
        cls.build = cls.loaded.build
        cls.operations = cls.loaded.patch_operations
        cls.document = cls.loaded.document
        cls.contracts = cls.loaded.contracts
        cls.roundtrip = cls.loaded.roundtrip

    def edge_bytes(self, tree):
        return {
            edge_id: ET.tostring(cell)
            for edge_id, cell in self.document.edge_records(self.document.graph_root(tree)).items()
        }

    def test_deletion_errors_keep_dependency_and_missing_object_priority(self):
        cases = [
            ({"delete_lanes": ["lane-a"], "delete_edges": ["missing-edge"]},
             "semantic/lane-not-empty"),
            ({"delete_edges": ["missing-edge"], "delete_nodes": ["missing-node"],
              "delete_phases": ["missing-phase"]}, "patch/missing-edge"),
            ({"delete_nodes": ["missing-node"], "delete_phases": ["missing-phase"]},
             "patch/missing-node"),
            ({"delete_phases": ["missing-phase"]}, "patch/missing-phase"),
        ]
        for changes, expected in cases:
            with self.subTest(expected=expected):
                tree = self.build.build_tree(linear_spec(2))
                before = ET.tostring(tree.getroot())
                with self.assertRaises(self.contracts.DiagramError) as caught:
                    self.roundtrip.patch_tree(tree, changes, False)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(ET.tostring(tree.getroot()), before)
                if expected == "semantic/lane-not-empty":
                    self.assertEqual(caught.exception.evidence, {"lanes": {"lane-a": ["n0", "n1", "n2"]}})

    def test_node_type_dependencies_are_checked_before_geometry_authority(self):
        tree = self.build.build_tree(linear_spec(2))
        before = ET.tostring(tree.getroot())
        with self.assertRaises(self.contracts.DiagramError) as caught:
            self.roundtrip.patch_tree(tree, {"update_nodes": [
                {"id": "n1", "type": "decision", "x": 20},
            ]}, False)
        self.assertEqual(caught.exception.code, "patch/node-type-incident-edge")
        self.assertEqual(caught.exception.evidence, {
            "edges": ["e0", "e1"], "from": "process", "to": "decision",
        })
        self.assertEqual(ET.tostring(tree.getroot()), before)

    def test_group_dependencies_run_without_group_changes_before_edge_updates(self):
        for edge_updates in ([], [{"id": "missing-edge", "label": "Changed"}]):
            with self.subTest(edge_updates=edge_updates):
                tree = self.build.build_tree(v3_incremental_spec())
                with self.assertRaises(self.contracts.DiagramError) as caught:
                    self.roundtrip.patch_tree(tree, {
                        "delete_lanes": ["optional"], "delete_nodes": ["note"],
                        "update_edges": edge_updates,
                    }, False)
                self.assertEqual(caught.exception.code, "patch/group-lane-dependency")
                self.assertEqual(caught.exception.evidence, {"lane": "optional"})
                cells = self.document.semantic_cells(tree)
                self.assertNotIn("lane:optional", cells)
                self.assertNotIn("node:note", cells)
                self.assertIn("node:work", cells)

    def test_group_update_after_delete_retains_missing_group_error(self):
        tree = self.build.build_tree(v3_incremental_spec())
        with self.assertRaises(self.contracts.DiagramError) as caught:
            self.roundtrip.patch_tree(tree, {
                "delete_groups": ["support"],
                "update_groups": [{"id": "support", "label": "Revised"}],
            }, False)
        self.assertEqual(caught.exception.code, "patch/missing-group")
        self.assertEqual(str(caught.exception), "Cannot update missing group: support")

    def test_added_lane_anchor_group_and_edge_share_refreshed_node_records(self):
        tree = self.build.build_tree(v3_incremental_spec())
        original = copy.deepcopy(tree)
        saved_edges = self.edge_bytes(tree)
        changes = {
            "lanes": [{"id": "extension", "label": "Extension", "width": 180, "after": "primary"}],
            # The anchor appears first in the input; its new target must be
            # constructed first while same-class input order remains intact.
            "nodes": [
                {"id": "context", "lane": "extension", "rank": 2, "type": "note",
                 "label": "Context", "anchor": {"node": "branch", "side": "right"}},
                {"id": "branch", "lane": "extension", "rank": 2, "type": "process", "label": "Branch"},
            ],
            "groups": [{"id": "extra-support", "lane": "extension", "kind": "support", "nodes": ["context"]}],
            "edges": [{"id": "handoff", "from": "work", "to": "branch", "label": ""}],
            "phases": [{"id": "phase-a", "label": "Phase", "from_rank": 1, "to_rank": 3}],
        }
        receipt = self.roundtrip.patch_tree(tree, changes, False)
        pool, root = self.document.find_pool(tree), self.document.graph_root(tree)
        lanes, nodes = self.document.lane_node_records(root, pool)
        self.assertEqual(receipt["lane_order"], ["primary", "extension", "optional"])
        self.assertGreater(lanes["extension"]["geometry"]["width"], 180)
        self.assertGreater(nodes["context"]["geometry"]["x"], nodes["branch"]["geometry"]["x"])
        self.assertEqual(nodes["context"]["cell"].get(self.contracts.DATA_GROUP_ID), "extra-support")
        child_ids = [child.get(self.contracts.DATA_SEMANTIC_ID) for child in root]
        self.assertLess(child_ids.index("branch"), child_ids.index("context"))
        edge = self.document.edge_records(root)["handoff"]
        self.assertEqual(edge.get("target"), nodes["branch"]["cell"].get("id"))
        phase = self.document.phase_records(root, pool)["phase-a"]
        expected_phase = self.loaded.construction.phase_geometry_values(
            changes["phases"][0], self.document.values_from_pool(pool, self.loaded.layout.DEFAULTS),
            self.document.parse_geometry(pool)["width"],
        )
        self.assertEqual(self.document.parse_geometry(phase), expected_phase)
        self.assertEqual(receipt["added_groups"], ["extra-support"])
        self.assertEqual(receipt["auto_rerouted_edges"], [])
        self.assertEqual({key: self.edge_bytes(tree)[key] for key in saved_edges}, saved_edges)
        self.assertTrue(self.roundtrip.compare_trees(original, tree, changes)["preserved"])

    def test_label_and_same_value_updates_do_not_enter_mutable_route_set(self):
        tree = self.build.build_tree(linear_spec(2))
        captured = []
        plan = self.loaded.routing.plan_route_batch

        def record_batch(*args, **kwargs):
            captured.append(set(kwargs["mutable_edge_ids"]))
            return plan(*args, **kwargs)

        with mock.patch.object(self.loaded.routing, "plan_route_batch", side_effect=record_batch):
            receipt = self.roundtrip.patch_tree(tree, {"update_edges": [
                {"id": "e0", "label": "Go"},
                {"id": "e1", "reroute": False, "from": "n1", "to": "n2", "type": "flow"},
            ]}, False)
        self.assertEqual(captured, [set()])
        self.assertEqual(receipt["label_updated_edges"], ["e0"])
        self.assertEqual(receipt["rerouted_edges"], [])
        self.assertEqual(receipt["auto_rerouted_edges"], [])

    def test_frozen_label_support_is_checked_before_new_edge_duplicate(self):
        tree = self.build.build_tree(linear_spec(2))
        edge = self.document.edge_records(self.document.graph_root(tree))["e0"]
        edge.set("style", edge.get("style") + "rotation=30;")
        saved = self.edge_bytes(tree)
        with self.assertRaises(self.contracts.DiagramError) as caught:
            self.roundtrip.patch_tree(tree, {"edges": [
                {"id": "e0", "from": "n0", "to": "n2"},
            ]}, False)
        self.assertEqual(caught.exception.code, "text/edge-label-geometry-unavailable")
        self.assertEqual(caught.exception.subject, {"kind": "edge", "id": "e0"})
        self.assertEqual(self.edge_bytes(tree), saved)

    def test_planning_failure_preserves_edges_and_cli_files_after_node_edit(self):
        original = self.build.build_tree(linear_spec(2))
        tree = copy.deepcopy(original)
        saved = self.edge_bytes(tree)
        changes = {
            "update_nodes": [{"id": "n1", "label": "Edited node"}],
            "update_edges": [{"id": "e1", "label": "Go"}],
            "edges": [{"id": "collision", "from": "n0", "to": "n2",
                       "exit_side": "bottom", "entry_side": "top",
                       "exit_offset": 0.5, "entry_offset": 0.5}],
        }
        with self.assertRaises(self.contracts.DiagramError) as caught:
            self.roundtrip.patch_tree(tree, changes, False)
        self.assertEqual(caught.exception.code, "routing/port-plan-conflict")
        self.assertEqual(self.edge_bytes(tree), saved)
        # This is the existing narrow edge-stage boundary; previous operations
        # have already applied to an internal tree that callers must discard.
        self.assertEqual(self.document.semantic_cells(tree)["node:n1"].get("value"), "Edited node")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, output, patch = (directory / name for name in ("source.drawio", "output.drawio", "patch.json"))
            self.document.write_tree(original, source)
            before = source.read_bytes()
            output.write_bytes(b"existing-output-sentinel")
            patch.write_text(json.dumps(changes))
            result = subprocess.run([
                sys.executable, "-B", str(TOOL), "patch", "--input", str(source),
                "--expected-input-sha256", hashlib.sha256(before).hexdigest(),
                "--changes", str(patch), "--output", str(output), "--force",
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("routing/port-plan-conflict", result.stdout)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(output.read_bytes(), b"existing-output-sentinel")
            self.assertEqual(list(directory.glob(".*.candidate")), [])


if __name__ == "__main__":
    unittest.main()
