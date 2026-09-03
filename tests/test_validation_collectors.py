"""Neutral behavior contracts for the six read-only diagnostic domains."""

import copy
from pathlib import Path
import sys
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from evidence_cases import linear_spec
from swimlane_loader import load_skill_modules

TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"


class ValidationCollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded = load_skill_modules(TOOL, module_name="validation_collector_tests")
        cls.tool = loaded.tool
        cls.validation = loaded.validation
        cls.document = loaded.document
        cls.geometry = loaded.geometry
        cls.contracts = loaded.contracts

    def setUp(self):
        spec = linear_spec(2, version="2")
        for edge in spec["edges"]:
            edge["label"] = ""
        self.tree = self.tool.build_tree(spec)
        self.pool = self.document.find_pool(self.tree)
        self.root = self.document.graph_root(self.tree)
        self.refresh_records()

    def refresh_records(self):
        self.lanes, self.nodes = self.document.lane_node_records(self.root, self.pool)
        self.edges = self.document.edge_records(self.root)
        self.bounds = {
            node_id: self.geometry.node_bounds_in_pool(node, self.lanes[node["lane"]])
            for node_id, node in self.nodes.items()
        }

    def collect(self, name, *args):
        diagnostics = []
        def add(*values, **fields):
            diagnostics.append(self.contracts.make_diagnostic(*values, **fields))
        getattr(self.validation, name)(*args, add)
        return diagnostics

    def semantic(self, edges=None, unmanaged=None, version="2"):
        return self.collect(
            "_collect_semantic_diagnostics", version, self.pool, self.root,
            self.nodes, list(self.edges.values()) if edges is None else edges,
            [] if unmanaged is None else unmanaged, {},
        )

    def summarize(self, diagnostics, **overrides):
        values = dict(
            diagnostics=diagnostics, pool=self.pool, schema_version="2",
            integrity=self.tool.metadata.managed_artifact_summary(self.tree),
            lanes=self.lanes, nodes=self.nodes, edge_cells=list(self.edges.values()),
            unmanaged_edges=[], edge_cells_by_id=self.edges, edge_points={},
        )
        values.update(overrides)
        return self.validation._summarize_validation(**values)

    @staticmethod
    def codes(diagnostics):
        return [item["code"] for item in diagnostics]

    def test_integrity_schema_reports_original_metadata_order(self):
        diagnostics = self.collect("_collect_schema_integrity", self.pool, self.root, "3")
        self.assertEqual(diagnostics, [{
            "code": "integrity/schema-composition-mismatch", "severity": "error",
            "message": "Schema v3 diagram is missing required semantic metadata",
            "evidence": {"missing_attributes": [
                "data-behavior-pattern", "data-layout-profile",
                "data-phase-presentation", "data-groups",
            ]},
            "supported_fixes": ["restore-semantic-metadata", "controlled-rebuild"],
            "subject": {"kind": "pool", "id": "main"},
        }])
        self.pool.set(self.contracts.DATA_BEHAVIOR_PATTERN, "linear")
        self.nodes["n0"]["cell"].set(self.contracts.DATA_SLOT, "main")
        diagnostics = self.collect("_collect_schema_integrity", self.pool, self.root, "2")
        self.assertEqual(diagnostics[0]["evidence"], {
            "pool_attributes": ["data-behavior-pattern"], "cells": ["n0"],
        })

    def test_integrity_hash_distinguishes_missing_empty_and_metadata_failure(self):
        c = self.contracts
        self.pool.set(c.DATA_MODEL_HASH, "")
        self.assertEqual(self.validation.validate_tree(self.tree)["diagnostics"], [])
        self.pool.attrib.pop(c.DATA_MODEL_HASH)
        self.assertEqual(self.codes(self.validation.validate_tree(self.tree)["diagnostics"]), [
            "integrity/model-hash-missing",
        ])
        self.pool.set(c.DATA_LANE_ORDER, '{"wrong": "type"}')
        emitted = []
        def add(*values, **fields):
            emitted.append(c.make_diagnostic(*values, **fields))
        integrity = self.validation._collect_managed_hash_integrity(
            self.tree, self.pool, emitted, add,
        )
        self.assertEqual(self.codes(emitted), [
            "integrity/schema-composition-mismatch", "integrity/model-hash-missing",
        ])
        self.assertEqual(emitted[0]["evidence"], {
            "attribute": "data-lane-order", "expected_type": "list",
        })
        self.assertIsNone(integrity["computed_model_hash"])
        self.assertIs(integrity["model_hash_matches"], False)

    def test_structure_keeps_recursive_unknowns_duplicates_and_edge_label_exception(self):
        c = self.contracts
        self.root.append(copy.deepcopy(self.edges["e0"]))
        wrapper = ET.SubElement(self.root, "object", {"id": "wrapper"})
        ET.SubElement(wrapper, "mxCell", {"id": "unknown", "vertex": "1", "style": "text"})
        ET.SubElement(self.root, "mxCell", {
            "id": "manual", "edge": "1", "source": self.nodes["n0"]["cell"].get("id"),
            "target": self.nodes["n2"]["cell"].get("id"),
        })
        ET.SubElement(self.root, "mxCell", {
            "id": "manual-label", "parent": "manual", "vertex": "1",
            "style": "edgeLabel", "value": "Manual",
        })
        unknown = self.collect("_collect_unmanaged_vertices", self.root)
        self.assertEqual(unknown[0]["evidence"], {"cell_ids": ["unknown"]})
        duplicate = self.collect("_collect_duplicate_semantic_ids", self.root)
        self.assertEqual(duplicate[0]["message"], "Duplicate semantic cell: edge:e0")
        unmanaged = self.document.unmanaged_edge_specs(self.root, self.nodes)
        warnings = self.collect("_collect_unmanaged_edges", unmanaged)
        self.assertEqual(warnings[0]["evidence"], {"count": 1, "connectors": [
            {"cell_id": "manual", "from": "n0", "to": "n2", "label": "Manual"},
        ]})
        report = self.validation.validate_tree(self.tree)
        self.assertEqual((report["edges"], report["unmanaged_edges"]), (3, 1))
        self.assertEqual(len(self.document.edge_records(self.root)), 2)

    def test_structure_phase_layering_and_local_phase_rank_fallback(self):
        c = self.contracts
        ET.SubElement(self.root, "mxCell", {
            "id": "phase", "parent": self.pool.get("id"), c.DATA_KIND: "phase",
            c.DATA_SEMANTIC_ID: "phase", c.DATA_FROM_RANK: "bad", c.DATA_TO_RANK: "2",
        })
        diagnostics = self.collect("_collect_phase_structure", self.root, self.pool, self.lanes)
        self.assertEqual(self.codes(diagnostics), [
            "layout/phase-z-order", "layout/phase-lane-visibility", "layout/phase-interactive",
        ])
        self.assertEqual(diagnostics[0]["evidence"]["layers"][-1], {
            "index": 9, "kind": "phase", "id": "phase",
        })
        semantic = self.semantic()
        self.assertEqual(semantic[-1]["evidence"], {"from_rank": 0, "to_rank": 0, "max_rank": 3})

    def test_structure_broken_endpoints_uses_native_ids_without_semantic_fallback(self):
        c = self.contracts
        edge = ET.Element("mxCell", {"id": "native-only", "source": "external", "target": "missing"})
        diagnostics = self.collect("_collect_broken_endpoints", [edge], {"external", "missing"})
        self.assertEqual(diagnostics, [])
        diagnostics = self.collect("_collect_broken_endpoints", [edge], {"external"})
        self.assertEqual(diagnostics[0]["subject"], {"kind": "edge", "id": None})
        self.assertEqual(diagnostics[0]["message"], "Broken edge endpoints: None")

    def test_semantics_keep_v3_missing_main_path_message(self):
        self.pool.set(self.contracts.DATA_MAIN_PATH, "[]")
        self.assertEqual(self.semantic(version="3"), [{
            "code": "semantic/main-path-missing", "severity": "error",
            "message": "Schema version 2 requires a main path with at least two nodes",
            "evidence": {}, "supported_fixes": ["supply-main-path"],
            "subject": {"kind": "main_path"},
        }])

    def test_semantics_unmanaged_edges_supply_main_path_branches_and_reachability(self):
        c = self.contracts
        self.nodes["n1"]["cell"].set(c.DATA_NODE_TYPE, "decision")
        manual = [
            {"from": "n0", "to": "n1"}, {"from": "n1", "to": "n2"},
            {"from": "n1", "to": "n0"},
        ]
        self.assertEqual(self.semantic(edges=[], unmanaged=manual), [])
        codes = self.codes(self.semantic(edges=[], unmanaged=[]))
        self.assertEqual(codes, [
            "semantic/main-path-edge", "semantic/main-path-edge",
            "semantic/decision-branches", "semantic/unreachable-node", "semantic/unreachable-node",
        ])

    def test_semantics_missing_pair_continues_and_v3_outcomes_remain_nonbinary(self):
        c = self.contracts
        self.pool.set(c.DATA_MAIN_PATH, '["n0", "absent", "n2"]')
        self.assertEqual(self.codes(self.semantic()), ["semantic/main-path-node", "semantic/main-path-node"])
        self.pool.set(c.DATA_MAIN_PATH, '["n0", "n1", "n2"]')
        self.nodes["n1"]["cell"].set(c.DATA_NODE_TYPE, "decision")
        branches = [copy.deepcopy(self.edges["e1"]) for _ in range(3)]
        for index, edge in enumerate(branches):
            edge.set(c.DATA_OUTCOME, f"outcome-{index}")
        self.assertEqual(self.semantic(edges=[self.edges["e0"], *branches], version="3"), [])
        self.assertEqual(self.codes(self.semantic(edges=[self.edges["e0"], *branches])), ["semantic/decision-outcome"])

    def test_node_geometry_preserves_per_node_rule_order_and_evidence(self):
        cell = self.nodes["n2"]["cell"]
        cell.set("value", "A very long end label " * 4)
        cell.find("mxGeometry").attrib.update(x="-5", y="-5", width="37", height="36")
        self.refresh_records()
        diagnostics = self.collect("_collect_node_geometry", self.nodes, self.lanes, "2")
        self.assertEqual(self.codes(diagnostics), [
            "layout/node-outside-lane-horizontal", "layout/node-outside-lane-vertical",
            "geometry/fixed-aspect-ratio", "schema/end-label-not-empty", "text/node-overflow-risk",
        ])
        self.assertEqual(diagnostics[2]["evidence"], {"width": 37.0, "height": 36.0})
        self.assertEqual(diagnostics[4]["evidence"], {"estimated_lines": 30, "available_lines": 1})

    def test_node_geometry_fixed_aspect_tolerance_and_sorted_overlap_pairs(self):
        for difference, expected in ((0.749, []), (0.75, ["geometry/fixed-aspect-ratio"]), (0.751, ["geometry/fixed-aspect-ratio"])):
            with self.subTest(difference=difference):
                self.nodes["n2"]["geometry"]["width"] = 36 + difference
                self.assertEqual(self.codes(self.collect("_collect_node_geometry", self.nodes, self.lanes, "2")), expected)
        box = dict(left=0, right=10, top=0, bottom=10, width=10, height=10)
        diagnostics = self.collect("_collect_node_overlaps", {"z": box, "a": box, "b": box})
        self.assertEqual([d["evidence"]["nodes"] for d in diagnostics], [["a", "b"], ["a", "z"], ["b", "z"]])

    def test_path_port_reuse_rounding_duplicate_entries_and_explicit_reuse(self):
        c = self.contracts
        edge = copy.deepcopy(self.edges["e0"])
        edge.set("style", edge.get("style").replace("exitX=0.5;", "exitX=0.50001;"))
        diagnostics = self.collect("_collect_port_reuse", [self.edges["e0"], edge])
        self.assertEqual([item["evidence"] for item in diagnostics], [
            {"side": "bottom", "offset": 0.5, "edges": ["e0", "e0"]},
            {"side": "top", "offset": 0.5, "edges": ["e0", "e0"]},
        ])
        edge.set(c.DATA_ALLOW_PORT_REUSE, "1")
        self.assertEqual(self.collect("_collect_port_reuse", [self.edges["e0"], edge]), [])

    def test_path_shape_short_segment_threshold_and_explicit_fixes(self):
        c = self.contracts
        edge = ET.Element("mxCell", {c.DATA_WAYPOINTS_ORIGIN: "explicit"})
        for length, count in ((15.249, 1), (15.25, 0), (16.0, 0)):
            points = [(0, 0), (0, 30), (length, 30), (length, 60)]
            diagnostics = self.collect("_collect_edge_path_shape", edge, "edge", points, list(zip(points, points[1:])), {}, {}, {}, [])
            self.assertEqual(len(diagnostics), count)
            if count:
                self.assertEqual(diagnostics[0]["supported_fixes"], ["edit-explicit-waypoints"])
                self.assertEqual(diagnostics[0]["evidence"]["segments"], [{"index": 1, "length": length}])

    def test_path_segment_emission_and_summary_keep_equal_key_evidence_order(self):
        edge = ET.Element("mxCell")
        segments = [((20, 0), (20, 30)), ((10, 0), (10, 30)), ((20, 40), (20, 70))]
        diagnostics = self.collect("_collect_edge_segment_quality", edge, "edge", segments, [10, 20], {})
        self.assertEqual([item["evidence"]["x"] for item in diagnostics], [20, 10, 20])
        report = self.summarize(diagnostics)
        self.assertEqual([item["evidence"]["x"] for item in report["diagnostics"]], [20, 10])
        self.assertTrue(report["valid"])
        self.assertFalse(report["quality_gate_passed"])

    def test_path_segment_diagonals_skip_obstacles_and_endpoint_exemptions_are_local(self):
        c = self.contracts
        edge = ET.Element("mxCell", {c.DATA_FROM: "source", c.DATA_TO: "target"})
        bounds = {"source": dict(left=0, right=20, top=0, bottom=20)}
        diagnostics = self.collect("_collect_edge_segment_quality", edge, "edge", [((5, 5), (15, 15))], [], bounds)
        self.assertEqual(self.codes(diagnostics), ["routing/non-orthogonal"])
        segments = [((10, 0), (10, 30)), ((10, 30), (10, 0))]
        diagnostics = self.collect("_collect_edge_segment_quality", edge, "edge", segments, [], bounds)
        self.assertEqual(self.codes(diagnostics), ["routing/node-crossing"])

    def test_path_pairs_sort_ids_then_emit_conflict_parallel_reciprocal(self):
        c = self.contracts
        cells = {
            "z": ET.Element("mxCell", {c.DATA_FROM: "b", c.DATA_TO: "a"}),
            "a": ET.Element("mxCell", {c.DATA_FROM: "a", c.DATA_TO: "b"}),
        }
        segments = {"z": [((0, 0), (0, 40)), ((10, 0), (10, 40))], "a": [((0, 0), (0, 40))]}
        diagnostics = self.collect("_collect_edge_pair_quality", segments, cells)
        self.assertEqual(self.codes(diagnostics), [
            "routing/edge-conflict", "routing/near-parallel-conflict", "routing/reciprocal-ambiguity",
        ])
        self.assertEqual([item["subject"]["id"] for item in diagnostics], ["a", "a", "a"])

    def test_label_quality_keeps_stored_blank_label_and_previous_duplicate_choice(self):
        c = self.contracts
        edge = ET.Element("mxCell", {"value": "", c.DATA_LABEL_LEFT: "0", c.DATA_LABEL_TOP: "0", c.DATA_LABEL_WIDTH: "20", c.DATA_LABEL_HEIGHT: "20", c.DATA_LABEL_SEGMENT: "bad"})
        labels = {}
        bounds = {"node": dict(left=5, right=15, top=5, bottom=15)}
        diagnostics = self.collect("_collect_edge_label_quality", edge, "same", [], bounds, labels)
        self.assertEqual(self.codes(diagnostics), ["text/edge-label-node-overlap"])
        self.assertEqual(labels["same"][0], 0)
        previous = copy.deepcopy(labels)
        self.assertEqual(self.collect("_collect_edge_label_quality", ET.Element("mxCell"), "same", [], bounds, labels), [])
        self.assertEqual(labels, previous)
        self.assertEqual(self.collect("_collect_edge_label_quality", ET.Element("mxCell", {"value": "Label"}), "new", [], {}, labels)[0]["evidence"], {"label": "Label"})

    def test_label_fallback_uses_first_candidate_without_collision_selection(self):
        edge = ET.Element("mxCell", {"value": "Label", self.contracts.DATA_LABEL_LEFT: "bad"})
        points = [(0, 0), (100, 0)]
        choice = self.validation.effective_label_bounds(edge, points)
        self.assertEqual(choice, (0, {"left": 29.15, "right": 70.85, "top": -23.0, "bottom": -5.0, "width": 41.7, "height": 18.0}))
        diagnostics = self.collect("_collect_edge_label_quality", edge, "edge", points, {"node": choice[1]}, {})
        self.assertEqual(self.codes(diagnostics), ["text/edge-label-node-overlap"])

    def test_label_path_and_pair_checks_keep_carrier_exclusion_and_sorted_evidence(self):
        box = dict(left=0, right=20, top=0, bottom=20, width=20, height=20)
        labels = {"z": (0, box), "a": (0, box)}
        paths = {"z": [((0, 10), (20, 10))], "a": [((10, 0), (10, 20))]}
        diagnostics = self.collect("_collect_label_path_conflicts", labels, paths)
        self.assertEqual([item["subject"]["id"] for item in diagnostics], ["z", "a"])
        self.assertEqual([item["evidence"] for item in diagnostics], [{"edges": ["a"]}, {"edges": ["z"]}])
        pair = self.collect("_collect_label_pair_conflicts", labels)
        self.assertEqual(pair[0]["message"], "Edge labels overlap: a / z")

    def test_validator_early_report_is_exact_and_late_value_errors_still_escape(self):
        c = self.contracts
        cell = self.nodes["n0"]["cell"]
        cell.remove(cell.find("mxGeometry"))
        report = self.validation.validate_tree(self.tree)
        self.assertEqual(list(report), ["valid", "errors", "warnings", "diagnostics"])
        self.assertEqual(report, {
            "valid": False, "errors": ["Cell psd-node-n0 has no geometry"], "warnings": [],
            "diagnostics": [{"code": "input/invalid", "severity": "error", "message": "Cell psd-node-n0 has no geometry", "evidence": {}, "supported_fixes": []}],
        })
        self.document.geometry(cell, width="36", height="36")
        cell.find("mxGeometry").set("width", "bad")
        with self.assertRaisesRegex(ValueError, "could not convert string to float"):
            self.validation.validate_tree(self.tree)
        cell.find("mxGeometry").set("width", "36")
        cell.set(c.DATA_RANK, "bad")
        with self.assertRaisesRegex(ValueError, "invalid literal for int"):
            self.validation.validate_tree(self.tree)

    def test_validator_missing_root_and_pool_keep_same_compact_keys(self):
        for xml in ("<mxfile/>", "<mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>"):
            report = self.validation.validate_tree(ET.ElementTree(ET.fromstring(xml)))
            self.assertEqual(list(report), ["valid", "errors", "warnings", "diagnostics"])
            self.assertFalse(report["valid"])

    def test_validator_preserves_edge_path_label_segment_interleaving_and_xml(self):
        c = self.contracts
        edge = self.edges["e0"]
        self.document.set_edge_points(edge, [(120, 20), (125, 20), (125, 21), (120, 21)])
        edge.set(c.DATA_WAYPOINTS_ORIGIN, "explicit")
        edge.set("value", "Label")
        edge.attrib.update({c.DATA_LABEL_LEFT: "100", c.DATA_LABEL_TOP: "70", c.DATA_LABEL_WIDTH: "40", c.DATA_LABEL_HEIGHT: "30", c.DATA_LABEL_SEGMENT: "bad"})
        before = ET.tostring(self.tree.getroot())
        emitted = []
        original = c.make_diagnostic
        def recording(*args, **kwargs):
            diagnostic = original(*args, **kwargs)
            emitted.append(diagnostic)
            return diagnostic
        with mock.patch.object(c, "make_diagnostic", side_effect=recording):
            report = self.validation.validate_tree(self.tree)
        self.assertEqual(self.codes(emitted), [
            "integrity/model-hash-mismatch", "routing/short-segment", "routing/excessive-bends",
            "routing/hairpin", "text/edge-label-node-overlap", "routing/node-crossing",
            "text/edge-label-edge-overlap", "layout/main-path-zigzag",
        ])
        self.assertEqual(report["short_segments"], 1)
        self.assertEqual(report["main_path_bends"], 4)
        self.assertEqual(report["label_conflicts"], 2)
        self.assertEqual(ET.tostring(self.tree.getroot()), before)

    def test_validator_readonly_explicit_empty_repeated_and_collinear_points(self):
        for points in ([], [(120, 110), (120, 110), (120, 120), (120, 130)]):
            edge = self.edges["e0"]
            self.document.set_edge_points(edge, points)
            edge.set(self.contracts.DATA_WAYPOINTS_ORIGIN, "explicit")
            ET.SubElement(edge.find("mxGeometry"), "mxPoint", {"as": "offset", "x": "17", "y": "4"})
            edge.set("custom", "preserve")
            before = ET.tostring(self.tree.getroot())
            self.validation.validate_tree(self.tree)
            self.assertEqual(ET.tostring(self.tree.getroot()), before)

    def test_summary_dedup_preserves_complete_objects_and_existing_qa_defaults(self):
        diagnostic = {"code": "routing/short-segment", "severity": "warning", "message": "same", "evidence": {"segments": [1, 2]}, "supported_fixes": ["first", "second"]}
        other = {**diagnostic, "supported_fixes": ["second", "first"]}
        error = {"code": "structure/duplicate-semantic-id", "severity": "error", "message": "error", "evidence": {}, "supported_fixes": []}
        report = self.summarize([diagnostic, copy.deepcopy(diagnostic), other, error])
        self.assertEqual(report["diagnostics"], [error, diagnostic, other])
        self.assertEqual(report["short_segments"], 2)
        self.assertEqual(report["managed_state"], "managed")
        self.assertFalse(report["valid"])
        self.assertIsNone(report["manual_waypoints_preserved"])
        self.assertEqual(report["manual_waypoints_checked"], 0)
        self.assertEqual(report["visual_review"], "not_available")


if __name__ == "__main__":
    unittest.main()
