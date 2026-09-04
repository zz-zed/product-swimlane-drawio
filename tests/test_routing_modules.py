"""Neutral routing decisions and native-adapter boundary contracts."""

import ast
import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from release_check import EXPECTED_SKILL_FILES
from swimlane_loader import load_skill_modules

TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"


class RoutingModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(TOOL, module_name="routing_module_tests")
        cls.routing = cls.loaded.routing
        cls.adapter = cls.loaded.routing_adapter
        cls.document = cls.loaded.document
        cls.ports = cls.loaded.ports
        cls.contracts = cls.loaded.contracts
        cls.clearance = cls.loaded.clearance

    def setUp(self):
        self.lanes = {"a": {"geometry": {"x": 0, "y": 0, "width": 500, "height": 400}}}
        self.nodes = {
            "s": {"lane": "a", "geometry": {"x": 20, "y": 20, "width": 100, "height": 40},
                  "semantic": {"rank": "1", "type": "process"}},
            "t": {"lane": "a", "geometry": {"x": 260, "y": 200, "width": 100, "height": 40},
                  "semantic": {"rank": "2", "type": "process"}},
        }
        self.edge = {"id": "e", "from": "s", "to": "t", "route": "forward",
                     "exit_side": "right", "entry_side": "left",
                     "exit_offset": 0.9, "entry_offset": 0.1}

    def raw_nodes(self):
        records = {}
        for node_id, node in self.nodes.items():
            record = {key: value for key, value in node.items() if key != "semantic"}
            record["cell"] = ET.Element("mxCell", {
                "id": node_id,
                self.contracts.DATA_RANK: node["semantic"]["rank"],
                self.contracts.DATA_NODE_TYPE: node["semantic"]["type"],
            })
            records[node_id] = record
        return records

    def route(self, edge=None, allocator=None, context=None):
        return self.routing.route_edge(
            self.edge if edge is None else edge, self.lanes, self.nodes,
            self.ports.PortAllocator() if allocator is None else allocator, context,
        )

    def assignment(self, edge_id="e", exit_offset=0.5, entry_offset=0.5,
                   exit_side="right", entry_side="left", source="s", target="t"):
        planner = self.routing.port_planner
        return planner.EdgePortAssignment(
            edge_id,
            planner.PlannedEndpoint(source, exit_side, exit_offset, "derived"),
            planner.PlannedEndpoint(target, entry_side, entry_offset, "derived"),
        )

    def alternate_plan(self, current, edge_id, *, exit_offset=None, entry_offset=None):
        planner = self.routing.port_planner
        assignments = []
        for assignment in current.assignments:
            if assignment.edge_id != edge_id:
                assignments.append(assignment)
                continue
            assignments.append(planner.EdgePortAssignment(
                edge_id,
                planner.PlannedEndpoint(
                    assignment.exit.node_id, assignment.exit.side,
                    assignment.exit.offset if exit_offset is None else exit_offset,
                    assignment.exit.source,
                ),
                planner.PlannedEndpoint(
                    assignment.entry.node_id, assignment.entry.side,
                    assignment.entry.offset if entry_offset is None else entry_offset,
                    assignment.entry.source,
                ),
            ))
        by_edge = {assignment.edge_id: assignment for assignment in assignments}
        components = tuple(
            planner.ComponentPlan(
                component.key,
                component.edge_ids,
                component.attempts,
                tuple(sorted(by_edge[item].assignment_key for item in component.edge_ids)),
            )
            for component in current.components
        )
        return planner.PortPlan(
            current.status, tuple(assignments), current.issues, current.attempts,
            current.candidate_pairs, current.budget, components,
            current.max_component_attempts, current.components_solved,
        )

    def clearance_profile(self, edge=None, assignment=None, *, target_style=None, edge_style=None):
        edge = self.edge if edge is None else edge
        assignment = self.assignment() if assignment is None else assignment
        target = self.nodes[edge["to"]]
        bounds = self.routing.core_geometry.node_bounds_in_pool(
            target, self.lanes[target["lane"]]
        )
        return {
            "edge_style": edge_style or self.adapter.edge_style(
                edge.get("type", "flow"),
                assignment.exit.side,
                assignment.entry.side,
                assignment.exit.offset,
                assignment.entry.offset,
            ),
            "target_style": target_style or (
                "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;"
                "fontColor=#333333;strokeColor=#666666;fontSize=12;"
            ),
            "target_type": target["semantic"].get("type", "process"),
            "target_bounds": bounds,
        }

    def test_views_preserve_optional_fields_raw_values_order_and_current_geometry(self):
        records = self.raw_nodes()
        records["s"].update(rank=None, type="start")
        records["t"]["cell"].attrib.pop(self.contracts.DATA_RANK)
        records["spec"] = {"rank": None, "lane": "a"}
        records["malformed"] = {"cell": ET.Element("mxCell"), "geometry": None}
        before = copy.deepcopy(records)
        views = self.document.routing_node_views(records)
        self.assertEqual(list(views), ["s", "t", "spec", "malformed"])
        self.assertIsNone(views["s"]["rank"])
        self.assertEqual(views["s"]["semantic"], {"rank": "1", "type": "process"})
        self.assertNotIn("rank", views["t"]["semantic"])
        self.assertNotIn("semantic", views["spec"])
        self.assertEqual(views["malformed"], {"geometry": None, "semantic": {}})
        self.assertIs(views["s"]["geometry"], records["s"]["geometry"])
        records["s"]["geometry"] = {"x": "raw"}
        self.assertEqual(self.document.routing_node_views(records)["s"]["geometry"], {"x": "raw"})
        for node_id in before:
            if "cell" in before[node_id]:
                self.assertEqual(ET.tostring(records[node_id]["cell"]), ET.tostring(before[node_id]["cell"]))
        lane_views = self.document.routing_lane_views({"missing": {}, "raw": {"geometry": None}})
        self.assertEqual(lane_views, {"missing": {}, "raw": {"geometry": None}})
        def assert_plain(value):
            self.assertNotIsInstance(value, ET.Element)
            if isinstance(value, dict):
                self.assertNotIn("cell", value)
                self.assertFalse(any(str(key).startswith("data-") for key in value))
                for item in value.values():
                    assert_plain(item)
        assert_plain(views)

    def test_rank_presence_and_provenance_keep_distinct_inference_rules(self):
        auto = {**self.edge, "route": "auto"}
        self.nodes["s"]["semantic"] = {"rank": "2", "type": "decision"}
        self.nodes["t"]["semantic"] = {"rank": "4", "type": "end"}
        source, target = self.nodes["s"], self.nodes["t"]
        self.assertEqual(self.routing.inferred_spec_route_class(auto, self.nodes), "forward")
        source.update(rank=None, type="start")
        with self.assertRaises(TypeError):
            self.routing.inferred_spec_route_class(auto, self.nodes)
        self.assertEqual(self.routing.infer_route_class(auto, source, target), "forward")
        self.assertEqual(self.routing.edge_routing_order([self.edge], [], self.nodes), [self.edge])
        source["rank"] = 9
        self.assertEqual(self.routing.inferred_spec_route_class(auto, self.nodes), "back")
        self.assertEqual(self.routing.infer_route_class(auto, source, target), "forward")
        context = self.routing.new_routing_context([], [self.edge], self.nodes)
        self.assertEqual(context["bottom_reserved_sources"], {"s"})
        source["semantic"]["rank"] = "bad"
        self.assertIsNone(self.routing.inferred_spec_route_class({"route": None}, {}))
        with self.assertRaisesRegex(self.contracts.DiagramError, "Unsupported route class: bad"):
            self.routing.infer_route_class({"route": "bad"}, source, target)
        self.assertEqual(self.routing.infer_route_class(self.edge, source, target), "forward")

    def test_side_validation_order_and_main_path_shortcut(self):
        self.nodes["s"]["semantic"]["rank"] = "bad"
        source, target = self.nodes["s"], self.nodes["t"]
        with self.assertRaisesRegex(self.contracts.DiagramError, "Unsupported branch class: bad"):
            self.routing.preferred_sides({**self.edge, "branch": "bad"}, "back", source, target, self.lanes)
        with self.assertRaisesRegex(ValueError, "invalid literal for int"):
            self.routing.preferred_sides({**self.edge, "exit_side": "bad"}, "back", source, target, self.lanes)
        self.assertEqual(self.routing.edge_routing_order([self.edge], ["s", "t"], self.nodes), [self.edge])
        with self.assertRaises(ValueError):
            self.routing.edge_routing_order([self.edge], [], self.nodes)
        # Main-pair sorting bypasses missing nodes and an unsupported route.
        edge = {**self.edge, "route": "bad"}
        self.assertEqual(self.routing.edge_routing_order([edge], ["s", "t"], {}), [edge])

    def test_context_skip_defaults_and_fresh_per_operation_state(self):
        for nodes in ({}, {"s": self.nodes["s"]}):
            context = self.routing.new_routing_context([], [self.edge], nodes)
            self.assertEqual(context["bottom_reserved_sources"], set())
            self.assertEqual(context["outgoing_counts"], {"s": 1})
        spec_nodes = {"s": {"lane": "a", "type": "decision"},
                      "t": {"lane": "a", "type": "end", "rank": 1}}
        self.assertEqual(self.routing.new_routing_context([], [self.edge], spec_nodes)["bottom_reserved_sources"], {"s"})
        spec_nodes["s"]["rank"] = None
        with self.assertRaises(TypeError):
            self.routing.new_routing_context([], [self.edge], spec_nodes)
        first = self.routing.new_routing_context([], [self.edge], self.nodes)
        second = self.routing.new_routing_context([], [self.edge], self.nodes)
        first["paths"]["e"] = [(1, 2)]
        self.assertEqual(second["paths"], {})

    def test_port_limits_derive_from_current_raw_bounds_and_explicit_return(self):
        self.nodes["s"]["geometry"]["y"] = 200
        self.nodes["s"]["semantic"]["rank"] = "2"
        self.nodes["t"]["geometry"].update(x=20, y=20)
        self.nodes["t"]["semantic"]["rank"] = "1"
        edges = [
            {"id": "back", "from": "s", "to": "t", "route": "back", "exit_side": "right", "exit_offset": 0.2},
            {"id": "forward", "from": "t", "to": "s", "route": "forward", "exit_side": "bottom", "entry_side": "right"},
        ]
        context = {}
        before = copy.deepcopy((edges, self.lanes, self.nodes))
        self.routing.derive_port_limits(context, edges, self.lanes, self.nodes)
        self.assertEqual(context, {"port_limits": {"forward": {"entry": {"min": 0.6000000000000001}}}})
        self.assertEqual((edges, self.lanes, self.nodes), before)
        # A forward-only operation never reads unrelated malformed geometry.
        self.nodes["other"] = {"lane": "a", "geometry": {}}
        context = {}
        self.routing.derive_port_limits(context, edges[1:], self.lanes, self.nodes)
        self.assertEqual(context, {})

    def test_main_path_reader_remains_tolerant_without_strict_metadata_parsing(self):
        for raw, expected in (("broken", []), ("null", []), ('{"node":"s"}', []),
                              ('["s", null, 1, "t", "s"]', ["s", "t", "s"])):
            pool = ET.Element("mxCell", {self.contracts.DATA_MAIN_PATH: raw})
            before = ET.tostring(pool)
            self.assertEqual(self.document.read_main_path(pool), expected)
            self.assertEqual(ET.tostring(pool), before)

    def test_missing_endpoint_and_late_geometry_failure_preserve_reservations(self):
        allocator = self.ports.PortAllocator()
        context = self.routing.new_routing_context([], [], {})
        before = copy.deepcopy(context)
        with self.assertRaisesRegex(self.contracts.DiagramError, "references a missing node"):
            self.route({**self.edge, "to": "missing"}, allocator, context)
        self.assertEqual(allocator.occupied, {})
        self.nodes["other"] = {"lane": "a", "geometry": {"x": 0, "width": 10, "height": 10}, "semantic": {}}
        with self.assertRaises(KeyError) as caught:
            self.route(allocator=allocator, context=context)
        self.assertEqual(caught.exception.args, ("y",))
        self.assertEqual(allocator.occupied, {("s", "right", 0.9): ["e"], ("t", "left", 0.1): ["e"]})
        self.assertEqual(context, before)

    def test_explicit_points_keep_duplicates_collinearity_and_inputs_readonly(self):
        for values, expected in ((None, []), ([], []),
                                 ([(180, 60), (180, 60)], [(180.0, 60.0), (180.0, 60.0)]),
                                 ([(180, 60), (220, 60), (260, 60)], [(180.0, 60.0), (220.0, 60.0), (260.0, 60.0)])):
            with self.subTest(values=values):
                edge = {**self.edge, "waypoints": values}
                before = copy.deepcopy((edge, self.lanes, self.nodes))
                routed = self.route(edge)
                self.assertEqual(routed["points"], expected)
                compact = [(120, 56.0), *expected, (260, 204.0)]
                if len(expected) == 2:
                    compact.pop(2)
                self.assertEqual(routed["full_path"], compact)
                self.assertNotIn("style", routed)
                self.assertEqual((edge, self.lanes, self.nodes), before)
        with self.assertRaisesRegex(self.contracts.DiagramError, "Every waypoint object must contain x and y"):
            self.routing.normalize_waypoints([{"x": 1}])
        with self.assertRaisesRegex(self.contracts.DiagramError, "Waypoints must be"):
            self.routing.normalize_waypoints([[1, 2, 3]])
        self.nodes["t"]["geometry"]["y"] = 220
        self.assertEqual(self.route({**self.edge, "waypoints": []})["full_path"][-1], (260, 224.0))

    def test_empty_safe_candidate_set_preserves_base_path_fallback(self):
        with mock.patch.object(self.routing, "route_candidates", return_value=[]), \
             mock.patch.object(self.routing, "automatic_polyline_is_safe", return_value=False):
            routed = self.route()
        self.assertEqual(routed["points"], [(144.0, 56.0), (144.0, 130.0), (260.0, 130.0)])
        self.assertEqual(routed["full_path"], [(120, 56.0), (144.0, 56.0), (144.0, 130.0), (260.0, 130.0), (260, 204.0)])

    def test_fixed_port_routing_rejects_unsafe_base_without_allocator_or_context_writes(self):
        context = self.routing.new_routing_context([], [self.edge], self.nodes)
        before = copy.deepcopy(context)
        allocator = self.ports.PortAllocator()
        assignment = self.assignment(exit_offset=0.9, entry_offset=0.1)
        with mock.patch.object(self.routing, "route_candidates", return_value=[]), \
             mock.patch.object(self.routing, "automatic_polyline_is_safe", return_value=False), \
             mock.patch.object(self.ports, "allocate_port_pair", side_effect=AssertionError("allocator called")):
            outcome = self.routing.route_edge_at_ports(
                self.edge, assignment, self.lanes, self.nodes, context
            )
        self.assertIsInstance(outcome, self.routing.RouteFailure)
        self.assertEqual(outcome.code, "routing/no-safe-route")
        self.assertTrue(outcome.locked)
        self.assertEqual(context, before)
        self.assertEqual(allocator.occupied, {})
        mismatch = self.routing.route_edge_at_ports(
            self.edge, self.assignment(), self.lanes, self.nodes, context
        )
        self.assertEqual(mismatch.code, "routing/port-assignment-mismatch")
        self.assertTrue(mismatch.locked)

    def test_automatic_candidates_reject_measured_arrowhead_clearance_violations(self):
        edge = {
            "id": "e", "from": "s", "to": "t", "route": "forward",
            "exit_side": "right", "entry_side": "left",
        }
        assignment = self.assignment()
        short = [(120.0, 40.0), (250.0, 40.0), (250.0, 220.0), (260.0, 220.0)]
        clear = [(120.0, 40.0), (220.0, 40.0), (220.0, 220.0), (260.0, 220.0)]
        with mock.patch.object(self.routing, "route_candidates", return_value=[short, clear]), \
             mock.patch.object(self.routing, "automatic_polyline_is_safe", return_value=True):
            outcome = self.routing.route_edge_at_ports(
                edge,
                assignment,
                self.lanes,
                self.nodes,
                clearance_profile=self.clearance_profile(edge, assignment),
                require_clearance=True,
            )
        self.assertIsInstance(outcome, self.routing.RouteDecision)
        self.assertEqual(outcome.routed["full_path"], clear)
        evidence = outcome.routed["arrowhead_clearance"]
        self.assertEqual(evidence["status"], self.clearance.STATUS_COMPLETE)
        self.assertEqual(evidence["terminal_run_px"], 40.0)
        self.assertFalse(evidence["violation"])
        with mock.patch.object(self.routing, "route_candidates", return_value=[short]), \
             mock.patch.object(self.routing, "automatic_polyline_is_safe", return_value=True):
            blocked = self.routing.route_edge_at_ports(
                edge,
                assignment,
                self.lanes,
                self.nodes,
                clearance_profile=self.clearance_profile(edge, assignment),
                require_clearance=True,
            )
        self.assertIsInstance(blocked, self.routing.RouteFailure)
        self.assertEqual(blocked.code, "routing/arrowhead-clearance")
        self.assertFalse(blocked.locked)

    def test_unavailable_or_missing_clearance_routes_without_becoming_a_checked_pass(self):
        edge = {
            "id": "e", "from": "s", "to": "t", "route": "forward",
            "exit_side": "right", "entry_side": "left",
        }
        assignment = self.assignment()
        candidate = [(120.0, 40.0), (220.0, 40.0), (220.0, 220.0), (260.0, 220.0)]
        unavailable = self.clearance_profile(
            edge, assignment, target_style="shape=note;whiteSpace=wrap;html=1;"
        )
        for profile in (None, unavailable):
            with self.subTest(profile=profile), \
                 mock.patch.object(self.routing, "route_candidates", return_value=[candidate]), \
                 mock.patch.object(self.routing, "automatic_polyline_is_safe", return_value=True):
                outcome = self.routing.route_edge_at_ports(
                    edge,
                    assignment,
                    self.lanes,
                    self.nodes,
                    clearance_profile=profile,
                    require_clearance=True,
                )
            self.assertIsInstance(outcome, self.routing.RouteDecision)
            evidence = outcome.routed["arrowhead_clearance"]
            self.assertEqual(evidence["status"], self.clearance.STATUS_NOT_AVAILABLE)
            self.assertIsNone(evidence["violation"])

    def test_explicit_waypoints_bypass_clearance_gating_without_rewrite(self):
        edge = {
            "id": "e", "from": "s", "to": "t", "route": "forward",
            "exit_side": "right", "entry_side": "left",
            "waypoints": [(250, 40), (250, 40), (250, 220)],
        }
        assignment = self.assignment()
        before = copy.deepcopy(edge)
        outcome = self.routing.route_edge_at_ports(
            edge,
            assignment,
            self.lanes,
            self.nodes,
            clearance_profile=self.clearance_profile(
                edge, assignment, target_style="shape=note;"
            ),
            require_clearance=True,
        )
        self.assertIsInstance(outcome, self.routing.RouteDecision)
        self.assertEqual(outcome.routed["points"], [(250.0, 40.0), (250.0, 40.0), (250.0, 220.0)])
        self.assertNotIn("arrowhead_clearance", outcome.routed)
        self.assertEqual(edge, before)

    def test_batch_reads_clearance_profiles_from_context(self):
        edge = {
            "id": "e", "from": "s", "to": "t", "route": "forward",
            "exit_side": "right", "entry_side": "left",
        }
        assignment = self.assignment()
        context = self.routing.new_routing_context([], [edge], self.nodes)
        context["arrowhead_clearance_profiles"] = {
            "e": self.clearance_profile(edge, assignment)
        }
        result = self.routing.plan_route_batch(
            [edge], self.lanes, self.nodes, routing_context=context
        )
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(
            result.decisions[0].routed["arrowhead_clearance"]["status"],
            self.clearance.STATUS_COMPLETE,
        )
        missing = self.routing.plan_route_batch(
            [edge], self.lanes, self.nodes, clearance_profiles={}
        )
        self.assertEqual(missing.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(
            missing.decisions[0].routed["arrowhead_clearance"]["status"],
            self.clearance.STATUS_NOT_AVAILABLE,
        )

    def test_measured_clearance_failure_replans_a_variable_port_component(self):
        edge = {
            "id": "e", "from": "s", "to": "t", "route": "forward",
            "exit_side": "right", "entry_side": "left",
        }
        profile = self.clearance_profile(edge, self.assignment())
        first_ports = []

        def measured(points, **kwargs):
            endpoint_pair = (points[0], points[-1])
            if not first_ports:
                first_ports.append(endpoint_pair)
            violation = endpoint_pair == first_ports[0]
            return self.clearance.ClearanceMeasurement(
                status=self.clearance.STATUS_COMPLETE,
                profile_id=self.clearance.PROFILE_ID,
                terminal_run_px=10.0 if violation else 24.0,
                minimum_terminal_run_px=self.clearance.CLEARANCE_THRESHOLD_PX,
                violation=violation,
            )

        with mock.patch.object(
            self.clearance, "measure_arrowhead_clearance", side_effect=measured
        ):
            result = self.routing.plan_route_batch(
                [edge], self.lanes, self.nodes,
                clearance_profiles={"e": profile},
            )
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(result.batch_replays, 2)
        self.assertEqual(sum(result.component_replans.values()), 1)
        self.assertFalse(
            result.decisions[0].routed["arrowhead_clearance"]["violation"]
        )

    def test_back_route_with_sufficient_gutter_keeps_internal_slot_and_clearance(self):
        lanes = {
            "target": {"geometry": {"x": 0.0, "y": 0.0, "width": 220.0, "height": 420.0}},
            "source": {"geometry": {"x": 220.0, "y": 0.0, "width": 220.0, "height": 420.0}},
        }
        nodes = {
            "history": {
                "lane": "target", "geometry": {"x": 40.0, "y": 80.0, "width": 132.0, "height": 42.0},
                "semantic": {"rank": "1", "type": "process"},
            },
            "decision": {
                "lane": "source", "geometry": {"x": 62.0, "y": 250.0, "width": 96.0, "height": 72.0},
                "semantic": {"rank": "2", "type": "decision"},
            },
        }
        edge = {
            "id": "back", "from": "decision", "to": "history",
            "route": "back", "exit_side": "left", "entry_side": "left",
        }
        target_bounds = self.routing.core_geometry.node_bounds_in_pool(
            nodes["history"], lanes["target"]
        )
        profile = {
            "edge_style": self.adapter.edge_style("flow", "left", "left", 0.5, 0.5),
            "target_style": (
                "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;"
                "strokeColor=#666666;fontSize=12;"
            ),
            "target_type": "process",
            "target_bounds": target_bounds,
        }
        result = self.routing.plan_route_batch(
            [edge], lanes, nodes, clearance_profiles={"back": profile}
        )
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        path = result.decisions[0].routed["full_path"]
        internal_vertical = [
            segment[0][0]
            for segment in zip(path, path[1:])
            if self.routing.core_geometry.segment_axis(segment) == "vertical"
            and 16.0 <= segment[0][0] < target_bounds["left"]
        ]
        self.assertTrue(internal_vertical)
        self.assertGreaterEqual(
            result.decisions[0].routed["arrowhead_clearance"]["terminal_run_px"],
            16.0 - self.routing.core_geometry.GEOMETRY_TOLERANCE,
        )

    def test_back_route_chooses_the_available_right_gutter_for_explicit_node_geometry(self):
        lanes = {
            "a": {"geometry": {"x": 0.0, "y": 0.0, "width": 220.0, "height": 420.0}}
        }
        nodes = {
            "history": {
                "lane": "a", "geometry": {"x": 20.0, "y": 80.0, "width": 160.0, "height": 42.0},
                "semantic": {"rank": "1", "type": "process"},
            },
            "retry": {
                "lane": "a", "geometry": {"x": 20.0, "y": 250.0, "width": 160.0, "height": 42.0},
                "semantic": {"rank": "2", "type": "process"},
            },
        }
        edge = {"id": "retry", "from": "retry", "to": "history", "type": "retry"}
        self.assertEqual(
            self.routing.preferred_sides(
                edge, "back", nodes["retry"], nodes["history"], lanes
            ),
            ("right", "right"),
        )
        target_bounds = self.routing.core_geometry.node_bounds_in_pool(
            nodes["history"], lanes["a"]
        )
        profile = {
            "edge_style": self.adapter.edge_style("retry", "right", "right", 0.5, 0.5),
            "target_style": (
                "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;"
                "strokeColor=#666666;fontSize=12;"
            ),
            "target_type": "process",
            "target_bounds": target_bounds,
        }
        result = self.routing.plan_route_batch(
            [edge], lanes, nodes, clearance_profiles={"retry": profile}
        )
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        routed = result.decisions[0].routed
        self.assertEqual((routed["exit_side"], routed["entry_side"]), ("right", "right"))
        vertical_x = [
            segment[0][0]
            for segment in zip(routed["full_path"], routed["full_path"][1:])
            if self.routing.core_geometry.segment_axis(segment) == "vertical"
        ]
        self.assertTrue(any(196.0 <= value < 204.0 for value in vertical_x))

    def test_batch_replans_after_primary_ports_have_no_safe_route(self):
        edge = {"id": "e", "from": "s", "to": "t", "route": "forward"}
        real_route = self.routing.route_edge_at_ports
        seen = []

        def route_at_ports(candidate_edge, assignment, lanes, nodes, context, **kwargs):
            seen.append((assignment.exit.offset, assignment.entry.offset))
            if len(seen) == 1:
                return self.routing.RouteFailure(
                    "routing/no-safe-route", candidate_edge["id"], "primary ports blocked"
                )
            return real_route(candidate_edge, assignment, lanes, nodes, context, **kwargs)

        planner = self.routing.port_planner
        with mock.patch.object(self.routing, "route_edge_at_ports", side_effect=route_at_ports), \
             mock.patch.object(planner, "replan_port_plan", wraps=planner.replan_port_plan) as replan:
            result = self.routing.plan_route_batch(
                [edge], self.lanes, self.nodes,
                main_path=["s", "t"],
            )
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(result.batch_replays, 2)
        self.assertEqual(seen[0], (0.5, 0.5))
        self.assertNotEqual(seen[1], seen[0])
        self.assertEqual(sum(result.component_replans.values()), 1)
        component_identifier = replan.call_args.args[1]
        rejected_signatures = replan.call_args.kwargs["rejected_assignment_signatures"]
        self.assertNotEqual(rejected_signatures[0], component_identifier)
        self.assertEqual(len(rejected_signatures[0]), 1)
        self.assertEqual(len(rejected_signatures[0][0]), 7)

    def test_compact_side_to_top_route_uses_the_free_band_between_nodes(self):
        lanes = {
            "requester": {"geometry": {"x": 0.0, "y": 36.0, "width": 180.0, "height": 644.0}},
            "workspace": {"geometry": {"x": 180.0, "y": 36.0, "width": 330.0, "height": 644.0}},
        }
        nodes = {
            "classify": {
                "lane": "workspace",
                "geometry": {"x": 38.0, "y": 196.0, "width": 96.0, "height": 72.0},
                "semantic": {"rank": "3", "type": "decision"},
            },
            "update": {
                "lane": "workspace",
                "geometry": {"x": 23.0, "y": 291.0, "width": 132.0, "height": 42.0},
                "semantic": {"rank": "4", "type": "process"},
            },
        }
        edge = {
            "id": "e3", "from": "classify", "to": "update",
            "route": "forward", "exit_side": "left", "entry_side": "top",
            "branch": "positive", "flow_role": "main",
        }
        result = self.routing.plan_route_batch(
            [edge], lanes, nodes, main_path=["classify", "update"],
            v3_semantics=True,
        )
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(result.batch_replays, 1)
        path = result.decisions[0].routed["full_path"]
        self.assertEqual(path[0], (218.0, 268.0))
        self.assertEqual(path[-1], (269.0, 327.0))
        self.assertGreaterEqual(path[-1][1] - path[-2][1], 16.0)
        self.assertTrue(
            self.routing.automatic_polyline_is_safe(
                path, lanes, nodes, "classify", "update"
            )
        )

    def test_main_path_zigzag_feedback_focuses_the_second_plan_on_aligned_ports(self):
        lanes = {"a": {"geometry": {"x": 0.0, "y": 0.0, "width": 220.0, "height": 400.0}}}
        nodes = {
            "start": {
                "lane": "a", "geometry": {"x": 92.0, "y": 90.0, "width": 36.0, "height": 36.0},
                "semantic": {"rank": "1", "type": "start"},
            },
            "step": {
                "lane": "a", "geometry": {"x": 10.0, "y": 183.0, "width": 132.0, "height": 42.0},
                "semantic": {"rank": "2", "type": "process"},
            },
        }
        edge = {"id": "main", "from": "start", "to": "step", "route": "forward"}
        result = self.routing.plan_route_batch(
            [edge], lanes, nodes, main_path=["start", "step"]
        )
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(result.batch_replays, 2)
        assignment = result.decisions[0].assignment
        source_x = 92.0 + 36.0 * assignment.exit.offset
        target_x = 10.0 + 132.0 * assignment.entry.offset
        self.assertLess(abs(source_x - target_x), 0.001)
        self.assertEqual(
            self.routing.core_geometry.bend_count(
                result.decisions[0].routed["full_path"]
            ),
            0,
        )
        self.assertEqual(assignment.exit.offset, 0.5)
        self.assertEqual(assignment.exit.source, "derived")
        self.assertEqual(assignment.entry.source, "derived")

    def test_single_explicit_exit_offset_keeps_entry_available_for_replan(self):
        edge = {
            "id": "e", "from": "s", "to": "t", "route": "forward",
            "exit_offset": 0.5,
        }
        real_route = self.routing.route_edge_at_ports
        seen = []

        def first_assignment_blocked(candidate_edge, assignment, lanes, nodes, context, **kwargs):
            seen.append((assignment.exit.offset, assignment.entry.offset))
            if len(seen) == 1:
                return self.routing.RouteFailure(
                    "routing/no-safe-route", candidate_edge["id"], "primary entry blocked"
                )
            return real_route(candidate_edge, assignment, lanes, nodes, context, **kwargs)

        with mock.patch.object(
            self.routing, "route_edge_at_ports", side_effect=first_assignment_blocked
        ):
            result = self.routing.plan_route_batch(
                [edge], self.lanes, self.nodes, main_path=["s", "t"]
            )
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(result.batch_replays, 2)
        self.assertEqual([pair[0] for pair in seen], [0.5, 0.5])
        self.assertNotEqual(seen[0][1], seen[1][1])

    def test_batch_rejects_repeated_assignment_with_bounded_replans(self):
        edge = {"id": "e", "from": "s", "to": "t", "route": "forward"}

        def blocked(candidate_edge, assignment, lanes, nodes, context, **kwargs):
            return self.routing.RouteFailure(
                "routing/no-safe-route", candidate_edge["id"], "blocked"
            )

        replans = []

        def same_plan(preparation, current, failed_edge_id, rejected):
            replans.append((failed_edge_id, len(rejected)))
            return current

        with mock.patch.object(self.routing, "route_edge_at_ports", side_effect=blocked):
            result = self.routing.plan_route_batch(
                [edge], self.lanes, self.nodes, replanner=same_plan
            )
        self.assertEqual(result.status, self.routing.ROUTE_FAILED)
        self.assertEqual(result.failure.code, "routing/port-plan-exhausted")
        self.assertEqual(result.batch_replays, 1)
        self.assertEqual(len(replans), 6)
        self.assertEqual(sum(result.component_replans.values()), 6)
        self.assertEqual(result.failure.evidence["component"], result.failure.component_key)
        self.assertEqual(result.failure.evidence["assignment"], result.failure.assignment_key)
        self.assertEqual(result.failure.evidence["rejected_assignments"], 1)
        self.assertIn("allocate-distinct-port", result.failure.supported_fixes)
        self.assertIn("reroute-edge", result.failure.supported_fixes)

    def test_port_plan_failures_expose_status_specific_fixes_and_budget_evidence(self):
        planner = self.routing.port_planner
        cases = (
            (
                planner.PLAN_CONSTRAINT_CONFLICT,
                "routing/port-plan-conflict",
                "allocate-distinct-port",
            ),
            (
                planner.PLAN_CAPACITY_EXHAUSTED,
                "routing/port-capacity",
                "increase-lane-width",
            ),
            (
                planner.PLAN_CANDIDATE_EXHAUSTED,
                "routing/port-plan-exhausted",
                "reroute-edge",
            ),
        )
        for status, code, expected_fix in cases:
            with self.subTest(status=status):
                plan = planner.PortPlan(
                    status,
                    (),
                    (planner.PortPlanIssue(code, "failed", ("e",), "s", "bottom"),),
                    3,
                    7,
                    planner.PlannerBudget(),
                )
                result = self.routing._port_plan_failure(plan)
                self.assertEqual(result.failure.code, code)
                self.assertEqual(result.failure.evidence["plan_status"], status)
                self.assertEqual(result.failure.evidence["attempts"], 3)
                self.assertEqual(result.failure.evidence["candidate_pairs"], 7)
                self.assertEqual(result.failure.evidence["node"], "s")
                self.assertEqual(result.failure.evidence["side"], "bottom")
                self.assertIn(expected_fix, result.failure.supported_fixes)

    def test_route_search_budget_failure_exposes_replay_evidence_and_fixes(self):
        edge = {"id": "e", "from": "s", "to": "t", "route": "forward"}

        def blocked(candidate_edge, assignment, lanes, nodes, context, **kwargs):
            return self.routing.RouteFailure(
                "routing/no-safe-route", candidate_edge["id"], "blocked"
            )

        def new_plan(preparation, current, failed_edge_id, rejected):
            return self.alternate_plan(
                current, failed_edge_id, exit_offset=0.2, entry_offset=0.2
            )

        with mock.patch.object(self.routing, "route_edge_at_ports", side_effect=blocked):
            result = self.routing.plan_route_batch(
                [edge], self.lanes, self.nodes,
                replanner=new_plan,
                route_budget=self.routing.RouteSearchBudget(
                    max_component_replans=6, max_batch_replays=1
                ),
            )
        self.assertEqual(result.status, self.routing.ROUTE_FAILED)
        self.assertEqual(result.failure.code, "routing/route-search-budget")
        self.assertEqual(result.failure.evidence["batch_replays"], 1)
        self.assertEqual(result.failure.evidence["max_batch_replays"], 1)
        self.assertEqual(sum(result.failure.evidence["component_replans"].values()), 1)
        self.assertEqual(
            result.failure.supported_fixes, ("reroute-edge", "increase-lane-width")
        )

    def test_failed_batch_trial_does_not_leak_context_into_replay(self):
        self.nodes["m"] = {
            "lane": "a", "geometry": {"x": 20, "y": 120, "width": 100, "height": 40},
            "semantic": {"rank": "2", "type": "process"},
        }
        self.nodes["t"]["semantic"]["rank"] = "3"
        edges = [
            {"id": "first", "from": "s", "to": "m", "route": "forward"},
            {"id": "second", "from": "m", "to": "t", "route": "forward"},
        ]
        supplied = self.routing.new_routing_context(["s", "m", "t"], edges, self.nodes)
        supplied_before = copy.deepcopy(supplied)
        calls = []

        def route_at_ports(edge, assignment, lanes, nodes, context, **kwargs):
            calls.append((edge["id"], tuple(sorted(context["paths"]))))
            if edge["id"] == "second" and sum(edge_id == "second" for edge_id, _ in calls) == 1:
                return self.routing.RouteFailure("routing/no-safe-route", "second", "blocked")
            routed = {
                "points": [], "route": "forward",
                "exit_side": assignment.exit.side, "entry_side": assignment.entry.side,
                "exit_offset": assignment.exit.offset, "entry_offset": assignment.entry.offset,
                "full_path": [(0.0, 0.0), (0.0, 20.0)], "label_choice": None,
            }
            return self.routing.RouteDecision(edge["id"], assignment, routed)

        def replan(preparation, current, failed_edge_id, rejected):
            return self.alternate_plan(current, failed_edge_id, exit_offset=0.2, entry_offset=0.2)

        with mock.patch.object(self.routing, "route_edge_at_ports", side_effect=route_at_ports):
            result = self.routing.plan_route_batch(
                edges, self.lanes, self.nodes, main_path=["s", "m", "t"],
                routing_context=supplied, replanner=replan,
            )
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(calls, [
            ("first", ()), ("second", ("first",)),
            ("first", ()), ("second", ("first",)),
        ])
        self.assertEqual(supplied, supplied_before)

    def test_mutable_edge_discards_its_seeded_route_label_and_endpoints(self):
        edge = {"id": "e", "from": "s", "to": "t", "route": "forward"}
        fresh = self.routing.plan_route_batch([edge], self.lanes, self.nodes)
        supplied = self.routing.new_routing_context([], [edge], self.nodes)
        supplied["paths"]["e"] = [(0.0, 0.0), (500.0, 0.0), (500.0, 400.0)]
        supplied["endpoints"]["e"] = ("s", "t")
        supplied["labels"]["e"] = {"left": 0.0, "right": 500.0, "top": 0.0, "bottom": 400.0}
        before = copy.deepcopy(supplied)
        seeded = self.routing.plan_route_batch(
            [edge], self.lanes, self.nodes, routing_context=supplied
        )
        self.assertEqual(fresh.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(seeded.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(seeded.decisions[0].routed, fresh.decisions[0].routed)
        self.assertEqual(supplied, before)

    def test_batch_routes_main_before_forward_before_return_and_links_reciprocals(self):
        self.nodes["u"] = {
            "lane": "a", "geometry": {"x": 380, "y": 200, "width": 100, "height": 40},
            "semantic": {"rank": "2", "type": "process"},
        }
        edges = [
            {"id": "return", "from": "t", "to": "s", "route": "back", "label": "request"},
            {"id": "branch", "from": "s", "to": "u", "route": "forward", "label": "response"},
            {"id": "main", "from": "s", "to": "t", "route": "forward", "label": "retry"},
        ]
        result = self.routing.plan_route_batch(
            edges, self.lanes, self.nodes, main_path=["s", "t"]
        )
        self.assertEqual(result.routing_order, ("main", "branch", "return"))
        self.assertEqual(result.linked_edge_pairs, (("main", "return"),))
        changed = [{**edge, "label": "completely different"} for edge in edges]
        changed_result = self.routing.plan_route_batch(
            changed, self.lanes, self.nodes, main_path=["s", "t"]
        )
        self.assertEqual(changed_result.routing_order, result.routing_order)
        self.assertEqual(changed_result.linked_edge_pairs, result.linked_edge_pairs)
        lanes = {
            "client": {"geometry": {"x": 0, "y": 0, "width": 240, "height": 400}},
            "service": {"geometry": {"x": 240, "y": 0, "width": 240, "height": 400}},
        }
        nodes = {
            "caller": {"lane": "client", "geometry": {"x": 60, "y": 80, "width": 120, "height": 40},
                       "semantic": {"rank": "1", "type": "process"}},
            "callee": {"lane": "service", "geometry": {"x": 60, "y": 200, "width": 120, "height": 40},
                       "semantic": {"rank": "2", "type": "process"}},
        }
        request = {"id": "request", "from": "caller", "to": "callee", "route": "auto",
                   "type": "call", "flow_role": "main", "label": "Retry"}
        response = {"id": "response", "from": "callee", "to": "caller", "route": "auto",
                    "type": "return", "flow_role": "response", "label": "Approved"}
        semantic = self.routing.plan_route_batch(
            [response, request], lanes, nodes, main_path=["caller", "callee"]
        )
        self.assertEqual(semantic.linked_edge_pairs, (("request", "response"),))
        self.assertEqual(semantic.routing_order, ("request", "response"))
        self.assertEqual(self.routing.inferred_spec_route_class(request, nodes), "forward")
        self.assertEqual(self.routing.inferred_spec_route_class(response, nodes), "back")
        self.assertEqual((request["type"], response["type"]), ("call", "return"))

    def test_frozen_batch_requires_seeded_path_and_locked_ports(self):
        edge = {"id": "e", "from": "s", "to": "t", "route": "forward"}
        missing = self.routing.plan_route_batch(
            [edge], self.lanes, self.nodes, mutable_edge_ids=set(),
            locked_offsets={"e": (0.5, 0.5)},
        )
        self.assertEqual(missing.failure.code, "routing/frozen-route-missing")
        context = self.routing.new_routing_context([], [edge], self.nodes)
        context["paths"]["e"] = [(120.0, 40.0), (260.0, 220.0)]
        context["endpoints"]["e"] = ("s", "t")
        seeded = self.routing.plan_route_batch(
            [edge], self.lanes, self.nodes, mutable_edge_ids=set(),
            locked_offsets={"e": (0.5, 0.5)}, routing_context=context,
        )
        self.assertEqual(seeded.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(seeded.decisions, ())

    def test_fixed_port_route_preserves_explicit_waypoints_and_defers_quality_diagnostics(self):
        assignment = self.assignment()
        cases = (
            (None, []),
            ([], []),
            ([(180, 40), (180, 40), (180, 220)], [(180.0, 40.0), (180.0, 40.0), (180.0, 220.0)]),
            ([(160, 40), (180, 40), (180, 220)], [(160.0, 40.0), (180.0, 40.0), (180.0, 220.0)]),
        )
        for values, expected in cases:
            edge = {**self.edge, "exit_offset": 0.5, "entry_offset": 0.5, "waypoints": values}
            before = copy.deepcopy(edge)
            outcome = self.routing.route_edge_at_ports(
                edge, assignment, self.lanes, self.nodes
            )
            self.assertIsInstance(outcome, self.routing.RouteDecision)
            self.assertEqual(outcome.routed["points"], expected)
            self.assertEqual(edge, before)
        non_orthogonal = {**self.edge, "exit_offset": 0.5, "entry_offset": 0.5,
                          "waypoints": [(150, 70)]}
        outcome = self.routing.route_edge_at_ports(
            non_orthogonal, assignment, self.lanes, self.nodes
        )
        self.assertIsInstance(outcome, self.routing.RouteDecision)
        self.assertEqual(outcome.routed["points"], [(150.0, 70.0)])
        self.assertEqual(non_orthogonal["waypoints"], [(150, 70)])
        self.nodes["blocker"] = {
            "lane": "a", "geometry": {"x": 150, "y": 100, "width": 60, "height": 60},
            "semantic": {"rank": "2", "type": "process"},
        }
        crossing = {**self.edge, "exit_offset": 0.5, "entry_offset": 0.5,
                    "waypoints": [(180, 40), (180, 220)]}
        outcome = self.routing.route_edge_at_ports(
            crossing, assignment, self.lanes, self.nodes
        )
        self.assertIsInstance(outcome, self.routing.RouteDecision)
        self.assertEqual(outcome.routed["points"], [(180.0, 40.0), (180.0, 220.0)])

    def test_independent_batch_needs_one_replay_and_no_conflict_budget(self):
        lanes, nodes, edges = {}, {}, []
        for index in range(40):
            lane_id = f"lane-{index:02d}"
            source_id = f"source-{index:02d}"
            target_id = f"target-{index:02d}"
            edge_id = f"edge-{index:02d}"
            lanes[lane_id] = {"geometry": {"x": index * 220, "y": 0, "width": 200, "height": 400}}
            nodes[source_id] = {
                "lane": lane_id, "geometry": {"x": 50, "y": 40, "width": 100, "height": 40},
                "semantic": {"rank": "1", "type": "process"},
            }
            nodes[target_id] = {
                "lane": lane_id, "geometry": {"x": 50, "y": 220, "width": 100, "height": 40},
                "semantic": {"rank": "2", "type": "process"},
            }
            edges.append({"id": edge_id, "from": source_id, "to": target_id, "route": "forward"})
        result = self.routing.plan_route_batch(edges, lanes, nodes)
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(result.batch_replays, 1)
        self.assertEqual(result.component_replans, {})
        self.assertEqual(len(result.decisions), 40)

    def test_candidate_ties_keep_raw_scores_and_stable_rounded_coordinates(self):
        p1 = [(120, 56.0), (1.12341, 60.0), (260, 204.0)]
        p2 = [(120, 56.0), (1.12344, 60.0), (260, 204.0)]
        cases = (([p1, p2], [100.00002, 100.00001]), ([p2, p1], [100.0, 100.0]))
        for candidates, scores in cases:
            with self.subTest(candidates=candidates), \
                 mock.patch.object(self.routing, "route_candidates", return_value=candidates), \
                 mock.patch.object(self.routing, "automatic_polyline_is_safe", return_value=True), \
                 mock.patch.object(self.routing, "candidate_score", side_effect=scores):
                self.assertEqual(self.route()["points"], [(1.12344, 60.0)])

    def test_near_parallel_and_short_segment_score_thresholds(self):
        for distance, expected in ((0.74, False), (0.75, False), (0.7501, True),
                                   (15.9999, True), (16.0, False), (16.0001, False)):
            self.assertEqual(self.routing.segments_near_parallel(((0, 0), (20, 0)), ((0, distance), (20, distance))), expected)
        for length, expected in ((15.24, 5099.24), (15.25, 5099.25), (15.9999, 5099.9999), (16.0, 100.0), (16.0001, 100.0001)):
            score = self.routing.candidate_score(
                [(0, 0), (10, 0), (10, length), (20, length)], route_class="forward",
                is_main_path=False, same_lane_down=False, target_lane={"x": 0.0, "width": 200.0},
                target_bounds={"left": 100.0, "right": 200.0}, entry_side="top",
                existing_segments=[], reciprocal_segments=[], label_choice=None, has_label=False,
            )
            self.assertEqual(score, expected)

    def test_local_validity_and_automatic_safety_keep_distinct_endpoint_exemptions(self):
        path = [(120, 40), (150, 40), (150, 30), (50, 30), (50, 10)]
        self.assertTrue(self.routing.polyline_is_locally_valid(path, self.lanes, self.nodes, "s", "t"))
        self.assertFalse(self.routing.automatic_polyline_is_safe(path, self.lanes, self.nodes, "s", "t"))
        self.nodes["other"] = {"lane": "a", "geometry": {}}
        self.assertFalse(self.routing.polyline_is_locally_valid([], self.lanes, self.nodes, "s", "t"))
        with self.assertRaises(KeyError):
            self.routing.automatic_polyline_is_safe([], self.lanes, self.nodes, "s", "t")
        self.assertFalse(self.adapter.edge_route_is_locally_valid(ET.Element("mxCell"), self.lanes, {}))

    def test_adapter_updates_context_after_success_and_preserves_failed_xml(self):
        cell = ET.Element("mxCell", {"id": "e", "style": "sentinel=1;",
                                      self.contracts.DATA_SEMANTIC_ID: "e"})
        raw = self.raw_nodes()
        raw["other"] = {"lane": "a", "geometry": {}}
        context = self.routing.new_routing_context([], [], {})
        before = ET.tostring(cell)
        allocator = self.ports.PortAllocator()
        with self.assertRaises(KeyError):
            self.adapter.apply_edge_route(cell, self.edge, self.lanes, raw, allocator, context)
        self.assertEqual(ET.tostring(cell), before)
        self.assertEqual(context["paths"], {})
        self.assertEqual(allocator.occupied[("s", "right", 0.9)], ["e"])
        context["labels"]["e"] = {"retained": True}
        edge = {**self.edge, "waypoints": []}
        with mock.patch.object(self.adapter, "edge_style", wraps=self.adapter.edge_style) as style:
            result = self.adapter.apply_edge_route(cell, edge, self.lanes, self.raw_nodes(), self.ports.PortAllocator(), context)
        self.assertIs(result, cell)
        style.assert_called_once_with("flow", "right", "left", 0.9, 0.1)
        self.assertEqual(context["paths"]["e"], [(120, 56.0), (260, 204.0)])
        self.assertEqual(context["endpoints"]["e"], ("s", "t"))
        self.assertEqual(context["labels"]["e"], {"retained": True})
        self.assertEqual(self.adapter.existing_edge_spec(cell)["waypoints"], [])

    def test_unmanaged_edges_do_not_enter_existing_port_or_path_state(self):
        root = ET.Element("root")
        cell = ET.SubElement(root, "mxCell", {"id": "manual", "edge": "1",
                                              "source": "s", "target": "t"})
        cell.set("style", self.adapter.edge_style("flow", "right", "left", 0.9, 0.1))
        allocator = self.ports.PortAllocator()
        self.adapter.reserve_existing_ports(root, allocator)
        context = {}
        self.adapter.seed_routing_context(context, self.document.edge_records(root), self.lanes, self.raw_nodes())
        self.assertEqual(allocator.occupied, {})
        self.assertEqual(context, {})

    def test_pure_routing_has_no_xml_names_or_adapter_dependency(self):
        source = Path(self.routing.__file__).read_text(encoding="utf-8")
        for name in (".attrib", "data-", "ET.", "document", "routing_adapter", "drawio_swimlane"):
            self.assertNotIn(name, source)
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id in {"eval", "exec", "locals", "globals"}
                             for node in ast.walk(tree)))

    def test_loader_isolates_both_routing_modules_and_their_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            loaded = []
            for name in ("first", "second"):
                destination = Path(temporary) / name
                isolated_files = set(EXPECTED_SKILL_FILES)
                isolated_files.add("scripts/swimlane_core/port_planner.py")
                isolated_files.add("scripts/swimlane_core/clearance.py")
                for relative in sorted(isolated_files):
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(TOOL.parent.parent / relative, target)
                loaded.append(load_skill_modules(destination / "scripts/drawio_swimlane.py", module_name=name))
            one, two = loaded
            self.assertIsNot(one.routing, two.routing)
            self.assertIsNot(one.routing_adapter, two.routing_adapter)
            self.assertIs(one.routing_adapter.routing, one.routing)
            self.assertIs(two.routing_adapter.routing, two.routing)
            self.assertIs(one.routing.ports, one.ports)
            self.assertIsNot(one.routing.ports, two.routing.ports)
            self.assertNotIn("swimlane_core.routing", sys.modules)
            self.assertNotIn("swimlane_core.routing_adapter", sys.modules)


if __name__ == "__main__":
    unittest.main()
