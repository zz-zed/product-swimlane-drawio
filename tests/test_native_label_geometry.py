"""Native-label geometry contracts independent of generation-time caches."""

import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from evidence_cases import linear_spec
from swimlane_loader import load_skill_modules


class NativeLabelGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py", module_name="native_label_tests")
        cls.tool = cls.loaded.tool
        cls.document = cls.loaded.document
        cls.labels = cls.tool.labels
        cls.validation = cls.loaded.validation

    def make_label(self):
        tree = self.tool.build_tree(linear_spec(2, version="2"))
        root = self.document.graph_root(tree)
        pool = self.document.find_pool(tree)
        lanes, nodes = self.document.lane_node_records(root, pool)
        cell = self.document.edge_records(root)["e0"]
        cell.set("value", "Neutral 文本")
        return tree, cell, {"lanes": lanes, "nodes": nodes, "pool": pool}

    def test_relative_horizontal_rounding_and_normal(self):
        anchor = self.labels.native_label_anchor
        self.assertEqual(anchor([(0, 0), (101, 0)], x=0, y=7, offset=(3, -2)), ((54, -9), 0))
        self.assertEqual(anchor([(0, 0), (101, 0)], x=-1)[0], (0, 0))
        self.assertEqual(anchor([(0, 0), (101, 0)], x=1)[0], (101, 0))
        self.assertEqual(anchor([(0, 0), (101, 0)], scale=2)[0], (50.5, 0))

    def test_vertical_normal_and_rounded_segment_transition(self):
        self.assertEqual(self.labels.native_label_anchor([(10, 0), (10, 100)], y=8, offset=(-3, 4))[0], (15, 54))
        # At the junction mxGraph selects the next segment, including its normal.
        self.assertEqual(self.labels.native_label_anchor([(0, 0), (50, 0), (50, 50)], y=5), ((55, 0), 1))

    def test_repeated_points_and_fractional_negative_rounding(self):
        self.assertEqual(self.labels.native_label_anchor([(0, 0), (0, 0), (5, 0)], x=-1), ((0, 0), 1))
        self.assertEqual(self.labels.native_label_anchor([(0, 0), (5, 0)], x=-1.2)[0], (0, 0))

    def test_nonrelative_is_endpoint_midpoint_and_ignores_xy(self):
        self.assertEqual(self.labels.native_label_anchor([(0, 0), (100, 0), (100, 300)], relative=False, x=1, y=20, offset=(3, -7)), ((53, 143), None))

    def test_cache_changes_do_not_change_measurement_or_mutate_input(self):
        tree, cell, scene = self.make_label()
        baseline = self.document.edge_label_measurement(cell, scene=scene)
        self.assertEqual(baseline["status"], "available")
        for key in list(cell.attrib):
            if key.startswith("data-label-"):
                del cell.attrib[key]
        self.assertEqual(self.document.edge_label_measurement(cell, scene=scene), baseline)
        cell.set("data-label-left", "nan")
        cell.set("data-label-segment", "9999")
        before = ET.tostring(tree.getroot())
        self.assertEqual(self.document.edge_label_measurement(cell, scene=scene), baseline)
        self.validation.validate_tree(tree)
        self.assertEqual(ET.tostring(tree.getroot()), before)

    def test_fractional_parent_origin_does_not_round_fixed_endpoints(self):
        _, cell, scene = self.make_label()
        geom = cell.find("mxGeometry")
        geom.set("relative", "1")
        geom.set("x", "0")
        geom.set("y", "0")
        for offset in geom.findall("./mxPoint[@as='offset']"):
            geom.remove(offset)
        pool_geom = scene["pool"].find("mxGeometry")
        pool_geom.set("x", "40.25")
        pool_geom.set("y", "40.25")
        measured = self.document.edge_label_measurement(cell, scene=scene)
        self.assertEqual(measured["path"], [(120.0, 126.0), (120.0, 183.0)])
        self.assertEqual(measured["position"], (120.0, 155.0))
        del scene["pool"]
        self.assertEqual(self.document.edge_label_measurement(cell, scene=scene)["reason"], "parent_origin_unavailable")

    def test_actual_native_dimensions_can_drive_fixed_path_candidates(self):
        path = [(0, 0), (300, 0)]
        candidates = self.labels.label_box_candidates(path, "W"*30, size=(280, 18))
        self.assertTrue(candidates)
        self.assertTrue(all(item[1]["width"] == 280 for item in candidates))
        self.assertIsNone(self.labels.choose_label_box(path, "Label", [], [], [], size=(400, 18)))

    def test_native_export_width_samples_have_small_estimated_allowance(self):
        # Draw.io Desktop 31.3.2, Helvetica regular 11px, SVG fallback image
        # widths at both 1x and 2x. This is independent native data, not a
        # reimplementation of the estimator; typography is still estimated.
        for text, native_width in [("H-even-x-1", 54), ("P-x-1", 26),
                                   ("中"*16, 176), ("process straight", 78),
                                   ("decision off-center", 90)]:
            with self.subTest(text=text):
                measured = self.labels.measure_native_label({
                    "text": text, "style": "fontSize=11;fontFamily=Helvetica;",
                    "path": [(0, 0), (400, 0)]})
                self.assertEqual(measured["bounds_quality"], "estimated")
                allowance = measured["bounds"]["width"] - native_width
                self.assertGreaterEqual(allowance, 0)
                self.assertLessEqual(allowance, 4)

    def test_uncalibrated_scripts_and_combining_glyphs_are_unavailable(self):
        for text in ["العربية", "ภาษาไทย", "αβ", "e\u0301", "字\tText", "🙂", "a\u200db"]:
            with self.subTest(text=text):
                measured = self.labels.measure_native_label({"text": text, "path": [(0, 0), (400, 0)]})
                self.assertEqual(measured["status"], "not_available")
                self.assertEqual(measured["reason"], "unsupported_text_glyphs")
                self.assertIsNone(measured["bounds"])

    def test_native_font_size_and_literal_newline_samples(self):
        # Actual Draw.io 31.3.2 SVG image extents at 1x and 2x. Width and
        # height allowances are independent of the 1px anchor tolerance.
        sizes = (8, 11, 16, 24)
        single_heights = (12, 15.75, 23, 35)
        samples = [
            ("calibration label", (56, 76, 111, 167), single_heights),
            ("CALIBRATION LABEL", (80, 110, 160, 240), single_heights),
            ("中文标签测量", (48, 66, 96, 144), single_heights),
            ("Route 路径 A17", (56, 77, 112, 168), single_heights),
            ("long lowercase calibration label width sample", (160, 220, 320, 480), single_heights),
            ("这是用于测量较长中文连接线标签宽度的样本", (160, 220, 320, 480), single_heights),
            ("Line one\n第二行", (31, 42, 61, 92), (21, 28.75, 42, 64)),
        ]
        for text, widths, heights in samples:
            for size, width, height in zip(sizes, widths, heights):
                with self.subTest(text=text, size=size):
                    measured = self.labels.measure_native_label({
                        "text": text, "style": f"fontSize={size};fontFamily=Helvetica;html=1;",
                        "path": [(0, 0), (800, 0)]})
                    self.assertEqual(measured["status"], "available")
                    self.assertEqual(measured["bounds_quality"], "estimated")
                    self.assertGreaterEqual(measured["bounds"]["width"], width)
                    self.assertLessEqual(measured["bounds"]["width"]-width, 4)
                    self.assertGreaterEqual(measured["bounds"]["height"], height)
                    self.assertLessEqual(measured["bounds"]["height"]-height, 3)

    def test_native_newline_normalization_keeps_trailing_empty_line(self):
        samples = [("First\n第二\nThird", 25, 42.75), ("\nSecond", 37, 28.75),
                   ("First\n", 21, 28.75), ("First\r\n第二", 22, 28.75),
                   ("First\rSecond", 62, 15.75)]
        for text, width, height in samples:
            with self.subTest(text=text):
                measured = self.labels.measure_native_label({"text": text, "path": [(0, 0), (400, 0)]})
                self.assertEqual(measured["status"], "available")
                self.assertGreaterEqual(measured["bounds"]["width"], width)
                self.assertLessEqual(measured["bounds"]["width"]-width, 4)
                self.assertGreaterEqual(measured["bounds"]["height"], height)
                self.assertLessEqual(measured["bounds"]["height"]-height, 3)

    def test_dragged_label_hits_node_despite_old_safe_cache(self):
        tree, cell, scene = self.make_label()
        measurement = self.document.edge_label_measurement(cell, scene=scene)
        node = scene["nodes"]["n1"]
        center = self.document.node_center_in_pool(node, scene["lanes"][node["lane"]])
        geom = cell.find("mxGeometry")
        geom.set("relative", "1")
        geom.set("x", "0.5")
        geom.set("y", "-7")
        base, _ = self.labels.native_label_anchor(measurement["path"], x=.5, y=-7)
        offset = geom.find("./mxPoint[@as='offset']")
        if offset is None:
            offset = ET.SubElement(geom, "mxPoint", {"as": "offset"})
        offset.set("x", str(center[0]-base[0]))
        offset.set("y", str(center[1]-base[1]))
        report = self.validation.validate_tree(tree)
        self.assertIn("text/edge-label-node-overlap", {d["code"] for d in report["diagnostics"]})
        self.assertFalse(report["quality_gate_passed"])

    def test_long_chinese_text_uncapped_and_multiline_font_size(self):
        _, cell, scene = self.make_label()
        cell.set("value", "中"*30)
        first = self.document.edge_label_measurement(cell, scene=scene)
        self.assertGreater(first["bounds"]["width"], 190)
        cell.set("value", "中文\nNeutral")
        self.document.set_style_option(cell, "fontSize", "20")
        second = self.document.edge_label_measurement(cell, scene=scene)
        self.assertEqual(second["bounds_quality"], "estimated")
        self.assertGreater(second["bounds"]["height"], 45)

    def test_unknown_styles_are_unavailable_not_empty_bounds(self):
        for extra, text in [("fontFamily=Custom", "Text"), ("fontFamily=Arial", "Text"), ("rotation=30", "Text"), ("namedStyle", "Text"), ("", "<b>Text</b>"), ("whiteSpace=wrap", "Text"), ("html=0", "Text")]:
            with self.subTest(extra=extra, text=text):
                tree, cell, scene = self.make_label()
                cell.set("style", cell.get("style")+extra+";")
                cell.set("value", text)
                measurement = self.document.edge_label_measurement(cell, scene=scene)
                self.assertEqual(measurement["status"], "not_available")
                self.assertIsNone(measurement["bounds"])
                report = self.validation.validate_tree(tree)
                self.assertIn("e0", report["label_geometry"]["unavailable_edges"])
                self.assertIn("text/edge-label-geometry-unavailable", {d["code"] for d in report["diagnostics"]})

    def test_blank_label_is_not_applicable_even_without_geometry(self):
        cell = ET.Element("mxCell", {"value": "   ", "style": "unrecognized"})
        self.assertEqual(self.document.edge_label_measurement(cell)["status"], "not_applicable")

    def test_missing_terminal_scene_and_unknown_router_are_unavailable(self):
        _, cell, scene = self.make_label()
        self.assertEqual(self.document.edge_label_measurement(cell)["reason"], "terminal_scene_unavailable")
        self.document.set_style_option(cell, "edgeStyle", "elbowEdgeStyle")
        self.assertEqual(self.document.edge_label_measurement(cell, scene=scene)["status"], "not_available")

    def test_fixed_terminal_router_reconstructs_diagonal_and_duplicate_hints(self):
        path = self.labels.native_fixed_segment_path((0, 0), (100, 100), [(30, 20), (60, 70)])
        self.assertEqual(path, [(0, 0), (0, 20), (30, 20), (30, 70), (100, 70), (100, 100)])
        # Repeated hints still toggle router orientation; simply compacting the
        # saved array would silently compute a different rendered path.
        repeated = self.labels.native_fixed_segment_path((0, 0), (100, 100), [(50, 0), (50, 0), (50, 100)])
        self.assertEqual(repeated, [(0, 0), (50, 0), (50, 100), (100, 100)])

    def test_router_rounds_working_copies_and_keeps_fractional_fixed_points(self):
        # Packaged SegmentConnector.scalePointArray rounds its working copy;
        # getFixedTerminalPoint(false) and updatePoints retain real terminals.
        path = self.labels.native_fixed_segment_path(
            (.25, .25), (100.25, 100.25), [(50.25, .25), (50.25, 100.25)])
        self.assertEqual(path, [(.25, .25), (50.3, .3), (50.3, 100.3), (100.25, 100.25)])

    def test_router_preserves_collinear_state_points_for_anchor_traversal(self):
        path = self.labels.native_fixed_segment_path((0, 0), (100, 100), [(50, 0), (60, 0), (70, 0)])
        self.assertEqual(path, [(0, 0), (50, 0), (70, 0), (70, 100), (100, 100)])
        self.assertEqual(self.labels.native_label_anchor(path, x=-.4), ((60, 0), 1))

    def test_nearly_orthogonal_native_hint_path_is_not_tolerance_compacted(self):
        _, cell, scene = self.make_label()
        scene["pool"].find("mxGeometry").set("x", "40.25")
        scene["pool"].find("mxGeometry").set("y", "40.25")
        geometry = cell.find("mxGeometry")
        for child in geometry.findall("./Array[@as='points']"):
            geometry.remove(child)
        points = ET.SubElement(geometry, "Array", {"as": "points"})
        ET.SubElement(points, "mxPoint", {"x": "150", "y": "150"})
        measured = self.document.edge_label_measurement(cell, scene=scene)
        self.assertEqual(measured["status"], "not_available")
        self.assertEqual(measured["reason"], "editor_router_additional_turns_required")

    def test_current_carrier_does_not_use_arc_midpoint_or_cache(self):
        data = {"text": "yes", "style": "fontSize=11;", "path": [(0, 0), (100, 0), (100, 100)], "offset": (-60, 10)}
        result = self.labels.measure_native_label(data)
        self.assertEqual(result["carrier_segment"], 0)
        data["offset"] = (0, 0)
        self.assertIsNone(self.labels.measure_native_label(data)["carrier_segment"])

    def test_unknown_carrier_does_not_exempt_own_segments(self):
        diagnostics = []
        self.validation._collect_label_path_conflicts({"e": (None, {"left": 45, "right": 55, "top": -5, "bottom": 5})}, {"e": [((0, 0), (100, 0))]}, lambda *args, **kwargs: diagnostics.append(args[0]))
        self.assertEqual(diagnostics, ["text/edge-label-edge-overlap"])

    def test_coverage_states_and_conflicts_are_separate(self):
        summary = self.validation.label_geometry_summary
        self.assertEqual(summary({})["status"], "not_applicable")
        self.assertEqual(summary({"a": {"status": "available"}})["status"], "complete")
        self.assertEqual(summary({"a": {"status": "not_available"}})["status"], "not_available")
        self.assertEqual(summary({"a": {"status": "available"}, "b": {"status": "not_available"}})["status"], "partial")


if __name__ == "__main__":
    unittest.main()
