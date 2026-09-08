"""Coordinate-based contracts for shared route feasibility predicates."""

import copy
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules


class CandidateQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded = load_skill_modules(
            ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py",
            module_name="candidate_quality_tests",
        )
        cls.routing = loaded.routing
        cls.geometry = loaded.geometry
        cls.validation = loaded.validation
        cls.contracts = loaded.contracts

    @staticmethod
    def codes(issues):
        return [issue.code for issue in issues]

    def test_short_internal_threshold_and_endpoint_exemption(self):
        edge = {"id": "edge", "waypoints_origin": "explicit"}
        for length, expected in ((15.249, ["routing/short-segment"]), (15.25, []), (16, [])):
            points = [(0, 0), (0, 1), (length, 1), (length, 2)]
            issues = self.routing.path_shape_issues(edge, points, {}, {}, {}, [])
            self.assertEqual(self.codes(issues), expected)
            if issues:
                self.assertEqual(issues[0].evidence, {
                    "segments": [{"index": 1, "length": length}],
                    "minimum": 16.0, "waypoints_origin": "explicit",
                })
        # A one-pixel terminal segment alone has no internal segment to reject.
        self.assertEqual(self.routing.path_shape_issues(edge, [(0, 0), (0, 1)], {}, {}, {}, []), ())

    def test_near_parallel_distance_and_overlap_boundaries(self):
        first = {"id": "a", "from": "p", "to": "q"}
        second = {"id": "b", "from": "r", "to": "s"}
        for gap, overlap, expected in ((15.999, 16, True), (16, 16, False),
                                       (10, 15.999, False), (0.75, 16, False)):
            issues = self.routing.edge_pair_issues(
                first, [((0, 0), (0, 40))], second, [((gap, 0), (gap, overlap))],
            )
            self.assertEqual(self.codes(issues), ["routing/near-parallel-conflict"] if expected else [])

    def test_lane_boundary_overlap_and_clearance_have_distinct_thresholds(self):
        for x, code in ((100.749, "routing/lane-boundary-overlap"),
                        (100.75, "routing/lane-boundary-clearance"),
                        (115.999, "routing/lane-boundary-clearance"), (116, None)):
            issues = self.routing.segment_quality_issues(
                {"id": "a"}, [((x, 0), (x, 40))], [100], {},
            )
            self.assertEqual(self.codes(issues), [code] if code else [])

    def test_shared_endpoint_is_exempt_but_interior_crossing_is_not(self):
        first, second = {"id": "a"}, {"id": "b", "from": "p"}
        for other, expected in (([(0, 40), (40, 40)], []),
                                 ([(-20, 20), (20, 20)], ["routing/edge-conflict"])):
            issues = self.routing.edge_pair_issues(
                first, [((0, 0), (0, 40))], second, [tuple(other)],
            )
            self.assertEqual(self.codes(issues), expected)

    def test_reciprocal_evidence_order_and_input_preservation(self):
        first = {"id": "a", "from": "p", "to": "q"}
        second = {"id": "b", "from": "q", "to": "p"}
        first_segments = [((0, 0), (0, 40))]
        second_segments = [((0, 0), (0, 40)), ((10, 0), (10, 40))]
        before = copy.deepcopy((first, second, first_segments, second_segments))
        issues = self.routing.edge_pair_issues(first, first_segments, second, second_segments)
        self.assertEqual(self.codes(issues), [
            "routing/edge-conflict", "routing/near-parallel-conflict", "routing/reciprocal-ambiguity",
        ])
        self.assertEqual([issue.subject_ids for issue in issues], [("a", "b")] * 3)
        self.assertEqual(issues[1].to_dict(), {
            "code": "routing/near-parallel-conflict", "subject_ids": ["a", "b"],
            "evidence": {"other_edge": "b", "minimum": 16.0}, "scope": "edge_pair",
        })
        self.assertEqual((first, second, first_segments, second_segments), before)

    def test_node_reentry_and_diagonal_preserve_collector_exemptions(self):
        edge = {"id": "a", "from": "source", "to": "target"}
        bounds = {"source": {"left": 0, "right": 20, "top": 0, "bottom": 20}}
        issues = self.routing.segment_quality_issues(
            edge, [((10, 0), (10, 30)), ((10, 30), (10, 0))], [], bounds,
        )
        self.assertEqual(self.codes(issues), ["routing/node-crossing"])
        self.assertEqual(issues[0].evidence, {"node": "source"})
        issues = self.routing.segment_quality_issues(edge, [((5, 5), (15, 15))], [], bounds)
        self.assertEqual(self.codes(issues), ["routing/non-orthogonal"])

    def test_back_corridor_uses_target_lane_and_automatic_origin_only(self):
        edge = {"id": "a", "to": "target", "route": "back",
                "waypoints_origin": "automatic", "entry_port": ("left", 0.5)}
        lanes = {"lane": {"geometry": {"x": 100, "width": 200}}}
        nodes = {"target": {"lane": "lane"}}
        bounds = {"target": {"left": 150, "right": 250}}
        for x, expected in ((115.249, True), (115.25, False), (149.99, False), (150, True)):
            issues = self.routing.back_corridor_issues(edge, [((x, 0), (x, 40))], lanes, nodes, bounds)
            self.assertEqual(bool(issues), expected)
            if issues:
                self.assertEqual(issues[0].evidence, {
                    "target_lane": "lane", "entry_side": "left", "vertical_x": [x],
                })
        edge["waypoints_origin"] = "explicit"
        self.assertEqual(self.routing.back_corridor_issues(edge, [], lanes, nodes, bounds), ())

    def test_cross_lane_return_does_not_require_an_extra_target_lane_vertical_leg(self):
        edge = {"id": "return", "from": "source", "to": "target", "route": "back",
                "waypoints_origin": "automatic", "entry_port": ("left", .5)}
        lanes = {"a": {"geometry": {"x": 0, "width": 100}},
                 "b": {"geometry": {"x": 100, "width": 200}}}
        nodes = {"source": {"lane": "a"}, "target": {"lane": "b"}}
        bounds = {"source": {"left": 30, "right": 70, "top": 200, "bottom": 240},
                  "target": {"left": 150, "right": 250, "top": 80, "bottom": 120}}
        segments = [((50, 200), (50, 100)), ((50, 100), (150, 100))]
        self.assertEqual(self.routing.back_corridor_issues(edge, segments, lanes, nodes, bounds), ())
        # The general collision rule still rejects the same cross-lane path
        # when a third node occupies its corridor.
        blocked = dict(bounds, blocker={"left": 40, "right": 60, "top": 130, "bottom": 170})
        self.assertIn("routing/node-crossing", self.codes(
            self.routing.segment_quality_issues(edge, segments, [], blocked)))
        nodes["source"]["lane"] = "b"
        self.assertEqual(self.codes(self.routing.back_corridor_issues(edge, segments, lanes, nodes, bounds)),
                         ["routing/back-corridor-outside-target-lane"])

    def test_excessive_bends_requires_a_safe_simpler_forward_candidate(self):
        lanes = {"lane": {"geometry": {"x": 0, "y": 0, "width": 500, "height": 400}}}
        nodes = {
            "source": {"lane": "lane", "geometry": {"x": 80, "y": 0, "width": 40, "height": 40}},
            "target": {"lane": "lane", "geometry": {"x": 80, "y": 200, "width": 40, "height": 40}},
        }
        edge = {"id": "a", "from": "source", "to": "target", "route": "forward",
                "exit_port": ("bottom", 0.5), "entry_port": ("top", 0.5)}
        points = [(100, 40), (100, 60), (350, 60), (350, 180), (100, 180), (100, 200)]
        def check():
            bounds = {key: self.geometry.node_bounds_in_pool(value, lanes["lane"])
                      for key, value in nodes.items()}
            return self.routing.path_shape_issues(edge, points, lanes, nodes, bounds, [])
        self.assertEqual(self.codes(check()), ["routing/excessive-bends"])
        nodes["blocker"] = {"lane": "lane", "geometry": {"x": 0, "y": 80, "width": 300, "height": 80}}
        self.assertEqual(check(), ())

    def test_hairpin_is_reported_independently_of_short_internal_segments(self):
        issues = self.routing.path_shape_issues(
            {"id": "a"}, [(0, 0), (40, 0), (20, 0)], {}, {}, {}, [],
        )
        self.assertEqual(self.codes(issues), ["routing/hairpin"])


if __name__ == "__main__":
    unittest.main()
