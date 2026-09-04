"""Arrowhead-clearance validation integration contracts."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from evidence_cases import linear_spec
from swimlane_loader import load_skill_modules


TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"


class ClearanceValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded = load_skill_modules(TOOL, module_name="clearance_validation_tests")
        cls.tool = loaded.tool
        cls.validation = loaded.validation
        cls.document = loaded.document
        cls.contracts = loaded.contracts

    def test_summary_exposes_complete_clearance_check_without_warning(self):
        tree = self.tool.build_tree(linear_spec(2, version="2"))
        report = self.validation.validate_tree(tree)

        self.assertEqual(report["arrowhead_clearance"]["status"], "complete")
        self.assertEqual(report["arrowhead_clearance"]["checked_edges"], ["e0", "e1"])
        self.assertEqual(report["arrowhead_clearance"]["checked_count"], 2)
        self.assertEqual(report["arrowhead_clearance"]["unavailable_edges"], [])
        self.assertEqual(report["arrowhead_clearance"]["not_applicable_edges"], [])
        self.assertEqual(report["arrowhead_clearance"]["violations"], 0)
        self.assertNotIn("routing/arrowhead-clearance", {
            item["code"] for item in report["diagnostics"]
        })

    def test_short_terminal_run_emits_reproducible_evidence_and_is_read_only(self):
        tree = self.tool.build_tree(linear_spec(2, version="2"))
        edge = self.tool.document.edge_records(self.tool.document.graph_root(tree))["e0"]
        # e0 is a vertical bottom-to-top edge.  This final waypoint leaves a
        # 10px terminal run before the target process perimeter.
        edge.set("style", edge.get("style").replace(
            "entryX=0.5;entryY=0", "entryX=0;entryY=0.5"
        ))
        self.document.set_edge_points(
            edge, [(120.0, 160.0), (44.0, 160.0), (44.0, 204.0)]
        )
        root_before = self.tool.ET.tostring(tree.getroot())

        report = self.validation.validate_tree(tree)
        warnings = [
            item for item in report["diagnostics"]
            if item["code"] == "routing/arrowhead-clearance"
        ]

        self.assertEqual(len(warnings), 1)
        evidence = warnings[0]["evidence"]
        self.assertEqual(evidence["edge_id"], "e0")
        self.assertEqual(evidence["target_id"], "n1")
        self.assertEqual(evidence["threshold"], 16.0)
        self.assertEqual(evidence["profile"], "drawio-31.3.2-default-block-model-perimeter-v1")
        self.assertEqual(evidence["shape"], "rectangle")
        self.assertEqual(evidence["perimeter"], "model_perimeter_terminal_run")
        self.assertEqual(evidence["coordinate_space"], "unscaled_diagram_px")
        self.assertEqual(evidence["view_scale"], 1)
        self.assertEqual(evidence["renderer_version"], "31.3.2")
        self.assertEqual(evidence["terminal_segment"]["to"], evidence["model_attachment"])
        self.assertEqual(evidence["terminal_segment"]["length_px"], evidence["terminal_run_px"])
        self.assertIn("nominal_endpoint", evidence)
        self.assertEqual(evidence["arrow"], "block")
        self.assertEqual(evidence["endSize"], "6")
        self.assertEqual(evidence["stroke"], "1")
        self.assertIn("coverage", evidence)
        self.assertLess(evidence["clearance_px"], evidence["threshold"])
        self.assertEqual(report["arrowhead_clearance"]["violations"], 1)
        self.assertEqual(root_before, self.tool.ET.tostring(tree.getroot()))

    def test_explicit_short_terminal_run_is_diagnostic_only(self):
        tree = self.tool.build_tree(linear_spec(2, version="2"))
        root = self.document.graph_root(tree)
        edge = self.document.edge_records(root)["e0"]
        edge.set("data-waypoints-origin", "explicit")
        edge.set("style", edge.get("style").replace(
            "entryX=0.5;entryY=0", "entryX=0;entryY=0.5"
        ))
        self.document.set_edge_points(
            edge, [(120.0, 160.0), (44.0, 160.0), (44.0, 204.0)]
        )
        report = self.validation.validate_tree(tree)
        warning = next(
            item for item in report["diagnostics"]
            if item["code"] == "routing/arrowhead-clearance"
        )
        self.assertEqual(warning["supported_fixes"], ["edit-explicit-waypoints"])

    def test_partial_and_empty_clearance_summaries_are_not_claimed_complete(self):
        tree = self.tool.build_tree(linear_spec(2, version="2"))
        root = self.document.graph_root(tree)
        edges = self.document.edge_records(root)
        edges["e0"].set("style", edges["e0"].get("style").replace(
            "endArrow=block", "endArrow=classic"
        ))
        partial = self.validation.validate_tree(tree)["arrowhead_clearance"]
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["checked_edges"], ["e1"])
        self.assertEqual(partial["checked_count"], 1)
        self.assertEqual(partial["unavailable_edges"], ["e0"])
        self.assertEqual(partial["violations"], 0)

        empty_tree = self.tool.build_tree(linear_spec(2, version="2"))
        empty_root = self.document.graph_root(empty_tree)
        for edge in list(self.document.edge_records(empty_root).values()):
            empty_root.remove(edge)
        empty = self.validation.validate_tree(empty_tree)["arrowhead_clearance"]
        self.assertEqual(empty["status"], "not_applicable")
        self.assertEqual(empty["checked_edges"], [])
        self.assertEqual(empty["unavailable_edges"], [])
        self.assertIsNone(empty["violations"])

    def test_no_arrow_is_not_applicable_and_unknown_style_is_not_available(self):
        tree = self.tool.build_tree(linear_spec(2, version="2"))
        root = self.document.graph_root(tree)
        edges = self.document.edge_records(root)
        edges["e0"].set("style", edges["e0"].get("style").replace(
            "endArrow=block", "endArrow=none"
        ))
        edges["e1"].set("style", edges["e1"].get("style") + "endArrow=classic;")

        report = self.validation.validate_tree(tree)
        summary = report["arrowhead_clearance"]
        self.assertEqual(summary["status"], "not_available")
        self.assertEqual(summary["checked_edges"], [])
        self.assertEqual(summary["checked_count"], 0)
        self.assertEqual(summary["not_applicable_edges"], ["e0"])
        self.assertEqual(summary["unavailable_edges"], ["e1"])
        self.assertIsNone(summary["violations"])
        self.assertNotIn("routing/arrowhead-clearance", {
            item["code"] for item in report["diagnostics"]
        })

    def test_all_unmeasurable_edges_keep_null_violations(self):
        tree = self.tool.build_tree(linear_spec(2, version="2"))
        root = self.document.graph_root(tree)
        for edge in self.document.edge_records(root).values():
            edge.set("style", edge.get("style").replace(
                "endArrow=block", "endArrow=classic"
            ))

        summary = self.validation.validate_tree(tree)["arrowhead_clearance"]
        self.assertEqual(summary["status"], "not_available")
        self.assertEqual(summary["checked_edges"], [])
        self.assertEqual(summary["checked_count"], 0)
        self.assertIsNone(summary["violations"])


if __name__ == "__main__":
    unittest.main()
