"""Model-perimeter arrowhead-clearance contracts."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/product-swimlane-drawio/scripts"))

from swimlane_core import clearance


EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
    "endArrow=block;endFill=1;labelBackgroundColor=#ffffff;fontSize=11;"
    "exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
)
PROCESS_STYLE = (
    "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;fontColor=#333333;"
    "strokeColor=#666666;fontSize=12;"
)
DECISION_STYLE = (
    "rhombus;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;"
    "fontColor=#333333;fontSize=12;"
)
END_STYLE = (
    "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#333333;"
    "strokeColor=#333333;strokeWidth=1.5;"
)
PROCESS_BOUNDS = {"left": 100.0, "right": 232.0, "top": 100.0, "bottom": 142.0}


class ClearanceTests(unittest.TestCase):
    def measure_process(self, length: float, **overrides):
        arguments = {
            "points": [(40.0, 80.0), (100.0 - length, 80.0), (100.0 - length, 121.0), (100.0, 121.0)],
            "target_bounds": PROCESS_BOUNDS,
            "target_type": "process",
            "target_style": PROCESS_STYLE,
            "edge_style": EDGE_STYLE,
        }
        arguments.update(overrides)
        return clearance.measure_arrowhead_clearance(**arguments)

    def test_process_threshold_uses_unscaled_model_perimeter_run(self):
        passing = self.measure_process(16.0)
        tolerated = self.measure_process(15.9)
        failing = self.measure_process(15.0)

        self.assertEqual(passing.status, clearance.STATUS_COMPLETE)
        self.assertEqual(passing.rule_version, "drawio-31.3.2-default-block-v1")
        self.assertEqual(passing.measurement_basis, "model_perimeter_terminal_run")
        self.assertEqual(passing.profile_id, "drawio-31.3.2-default-block-model-perimeter-v1")
        self.assertEqual(passing.terminal_run_px, 16.0)
        self.assertEqual(passing.minimum_terminal_run_px, 16.0)
        self.assertEqual(passing.model_attachment, (100.0, 121.0))
        self.assertFalse(passing.violation)
        self.assertFalse(tolerated.violation)
        self.assertTrue(failing.violation)

    def test_duplicate_and_monotonic_collinear_points_only_change_a_calculation_copy(self):
        points = [
            (40.0, 80.0), (84.0, 80.0), (84.0, 80.0),
            (84.0, 100.0), (84.0, 110.0), (84.0, 121.0), (100.0, 121.0),
        ]
        original = list(points)
        result = self.measure_process(16.0, points=points)
        self.assertEqual(points, original)
        self.assertEqual(result.last_actual_turn, (84.0, 121.0))
        self.assertEqual(result.terminal_run_px, 16.0)

    def test_nonzero_short_terminal_dogleg_is_not_silently_folded_away(self):
        points = [(40.0, 80.0), (99.0, 80.0), (99.0, 121.0), (100.0, 121.0)]
        result = self.measure_process(1.0, points=points)
        self.assertEqual(result.status, clearance.STATUS_COMPLETE)
        self.assertEqual(result.last_actual_turn, (99.0, 121.0))
        self.assertEqual(result.terminal_run_px, 1.0)
        self.assertTrue(result.violation)

    def test_process_center_and_offset_attach_to_rectangle_perimeter(self):
        result = self.measure_process(
            20.0,
            points=[(80.0, 80.0), (80.0, 114.7), (100.0, 114.7)],
        )
        self.assertEqual(result.status, clearance.STATUS_COMPLETE)
        self.assertEqual(result.model_attachment, (100.0, 114.7))
        self.assertEqual(result.terminal_run_px, 20.0)

    def test_subpixel_perpendicular_drift_does_not_inflate_terminal_run(self):
        result = self.measure_process(
            14.6,
            points=[(70.0, 80.0), (85.4, 120.3), (100.0, 121.0)],
        )
        self.assertEqual(result.status, clearance.STATUS_COMPLETE)
        self.assertEqual(result.terminal_axis, "horizontal")
        self.assertAlmostEqual(result.terminal_run_px, 14.6)
        self.assertTrue(result.violation)

    def test_decision_center_and_offset_project_to_diamond_perimeter(self):
        bounds = {"left": 100.0, "right": 196.0, "top": 100.0, "bottom": 172.0}
        offset = clearance.measure_arrowhead_clearance(
            [(70.0, 90.0), (84.0, 90.0), (84.0, 125.2), (100.0, 125.2)],
            target_bounds=bounds,
            target_type="decision",
            target_style=DECISION_STYLE,
            edge_style=EDGE_STYLE,
        )
        center = clearance.measure_arrowhead_clearance(
            [(84.0, 120.0), (84.0, 136.0), (100.0, 136.0)],
            target_bounds=bounds,
            target_type="decision",
            target_style=DECISION_STYLE,
            edge_style=EDGE_STYLE,
        )
        self.assertAlmostEqual(offset.model_attachment[0], 114.4)
        self.assertAlmostEqual(offset.terminal_run_px, 30.4)
        self.assertEqual(center.model_attachment, (100.0, 136.0))
        self.assertEqual(center.terminal_run_px, 16.0)

    def test_vertical_decision_and_end_center_offset_use_true_perimeters(self):
        decision_bounds = {"left": 100.0, "right": 196.0, "top": 100.0, "bottom": 172.0}
        vertical = clearance.measure_arrowhead_clearance(
            [(120.0, 40.0), (120.0, 100.0)],
            target_bounds=decision_bounds,
            target_type="decision",
            target_style=DECISION_STYLE,
            edge_style=EDGE_STYLE,
        )
        end_bounds = {"left": 100.0, "right": 136.0, "top": 100.0, "bottom": 136.0}
        end_offset = clearance.measure_arrowhead_clearance(
            [(70.0, 90.0), (84.0, 90.0), (84.0, 112.6), (100.0, 112.6)],
            target_bounds=end_bounds,
            target_type="end",
            target_style=END_STYLE,
            edge_style=EDGE_STYLE,
        )
        end_center = clearance.measure_arrowhead_clearance(
            [(84.0, 118.0), (100.0, 118.0)],
            target_bounds=end_bounds,
            target_type="end",
            target_style=END_STYLE,
            edge_style=EDGE_STYLE,
        )
        self.assertEqual(vertical.model_attachment, (120.0, 121.0))
        self.assertEqual(vertical.terminal_run_px, 81.0)
        self.assertAlmostEqual(end_offset.model_attachment[0], 118.0 - 18.0 * (1.0 - 0.3 ** 2) ** 0.5)
        self.assertEqual(end_center.model_attachment, (100.0, 118.0))
        self.assertEqual(end_center.terminal_run_px, 16.0)

    def test_no_end_arrow_is_not_applicable(self):
        result = self.measure_process(16.0, edge_style=EDGE_STYLE.replace("endArrow=block", "endArrow=none"))
        self.assertEqual(result.status, clearance.STATUS_NOT_APPLICABLE)
        self.assertEqual(result.reason, "no_end_arrow")
        self.assertIsNone(result.violation)

    def test_unknown_edge_and_target_rendering_are_not_available(self):
        edge_cases = {
            "unsupported_end_arrow": EDGE_STYLE.replace("endArrow=block", "endArrow=classic"),
            "unsupported_end_size": EDGE_STYLE + "endSize=10;",
            "unsupported_edge_stroke_width": EDGE_STYLE + "strokeWidth=2;",
            "unsupported_rounded_edge": EDGE_STYLE.replace("rounded=0", "rounded=1"),
        }
        for reason, style in edge_cases.items():
            with self.subTest(reason=reason):
                result = self.measure_process(16.0, edge_style=style)
                self.assertEqual(result.status, clearance.STATUS_NOT_AVAILABLE)
                self.assertEqual(result.reason, reason)

        target_cases = [
            ("start", END_STYLE, "unsupported_target_type"),
            ("decision", PROCESS_STYLE, "target_shape_mismatch"),
            ("process", PROCESS_STYLE + "rotation=10;", "unsupported_target_rotation"),
            ("process", PROCESS_STYLE + "perimeter=ellipsePerimeter;", "unsupported_custom_perimeter"),
            ("end", END_STYLE.replace("strokeWidth=1.5", "strokeWidth=2"), "unsupported_target_stroke_width"),
        ]
        for target_type, target_style, reason in target_cases:
            with self.subTest(reason=reason):
                result = self.measure_process(16.0, target_type=target_type, target_style=target_style)
                self.assertEqual(result.status, clearance.STATUS_NOT_AVAILABLE)
                self.assertEqual(result.reason, reason)

    def test_invalid_and_unattached_geometry_are_not_available(self):
        cases = [
            ([(1.0, 1.0)], PROCESS_BOUNDS, "insufficient_path"),
            ([(50.0, 50.0), (100.0, 100.0)], PROCESS_BOUNDS, "unsupported_terminal_segment"),
            ([(50.0, 121.0), (99.0, 121.0)], PROCESS_BOUNDS, "floating_or_unattached_target"),
            ([(84.0, 121.0), (100.0, 121.0)], {"left": 100.0, "right": 99.0, "top": 0.0, "bottom": 10.0}, "invalid_target_bounds"),
        ]
        for points, bounds, reason in cases:
            with self.subTest(reason=reason):
                result = self.measure_process(16.0, points=points, target_bounds=bounds)
                self.assertEqual(result.status, clearance.STATUS_NOT_AVAILABLE)
                self.assertEqual(result.reason, reason)

    def test_evidence_dict_names_model_geometry_and_legacy_aliases(self):
        evidence = self.measure_process(16.0).to_dict()
        self.assertEqual(evidence["status"], clearance.STATUS_COMPLETE)
        self.assertEqual(evidence["measurement_basis"], "model_perimeter_terminal_run")
        self.assertEqual(evidence["terminal_run_px"], 16.0)
        self.assertEqual(evidence["minimum_terminal_run_px"], 16.0)
        self.assertEqual(evidence["model_attachment"], (100.0, 121.0))
        self.assertEqual(evidence["clearance_px"], 16.0)
        self.assertEqual(evidence["attachment"], (100.0, 121.0))


if __name__ == "__main__":
    unittest.main()
