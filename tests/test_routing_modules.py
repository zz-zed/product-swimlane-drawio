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
                for relative in EXPECTED_SKILL_FILES:
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
