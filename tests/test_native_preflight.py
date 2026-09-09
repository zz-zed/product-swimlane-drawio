"""Native candidate projection, independent of XML and routing search strategy."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from evidence_cases import linear_spec
from swimlane_loader import load_skill_modules


class NativePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py", module_name="native_preflight_tests")
        cls.document, cls.labels = cls.loaded.document, cls.loaded.labels

    def setup_native(self):
        tree = self.loaded.build.build_tree(linear_spec(2, version="2"))
        root = self.document.graph_root(tree)
        pool = self.document.find_pool(tree)
        lanes, nodes = self.document.lane_node_records(root, pool)
        cell = self.document.edge_records(root)["e0"]
        cell.set("value", "Native 文本")
        scene = {"lanes": lanes, "nodes": nodes, "pool": pool}
        assignment = {"exit_side": "bottom", "entry_side": "top", "exit_offset": .5, "entry_offset": .5}
        return cell, scene, assignment

    def test_extraction_is_plain_and_compatible(self):
        cell, scene, _ = self.setup_native()
        raw = self.document.extract_native_label_profile(cell, scene=scene)
        json.dumps(raw)  # No Element or callback survives the adapter boundary.
        snapshot = copy.deepcopy(raw)
        self.assertEqual(self.labels.resolve_native_label_inputs(raw), self.document.native_label_inputs(cell, scene=scene))
        self.assertEqual(raw, snapshot)

    def test_projection_matches_actual_writer_and_final_label_measurement(self):
        cell, scene, assignment = self.setup_native()
        assignment.update(exit_offset=.500049, entry_offset=.500049)
        raw = self.document.extract_native_label_profile(cell, scene=scene)
        snapshot = copy.deepcopy(raw)
        initial = self.labels.preflight_candidate(raw, assignment, [])
        self.assertEqual(initial["path_status"], "available")
        size = initial["size"]
        choice = (0, {"left": 124.123456 - size[0]/2, "top": 154.123456 - size[1]/2,
                      "width": size[0], "height": size[1]})
        projected = self.labels.project_candidate_native_inputs(raw, assignment, [], choice)
        for prefix in ("exit", "entry"):
            x, y = self.loaded.geometry.port_xy(assignment[prefix + "_side"], assignment[prefix + "_offset"])
            for axis, value in (("X", x), ("Y", y)):
                self.document.set_style_option(cell, prefix + axis, self.loaded.contracts.number(value))
        self.document.set_edge_points(cell, [])
        self.loaded.routing_adapter.set_edge_label_position(cell, projected["path"], choice)
        del projected["input_signature"]
        self.assertEqual(projected, self.document.native_label_inputs(cell, scene=scene))
        self.assertEqual(self.labels.measure_native_label(projected), self.document.edge_label_measurement(cell, scene=scene))
        self.assertEqual(raw, snapshot)

    def test_empty_and_hidden_labels_do_not_mask_bad_native_path(self):
        cell, scene, assignment = self.setup_native()
        scene["pool"].find("mxGeometry").set("x", "40.25")
        scene["pool"].find("mxGeometry").set("y", "40.25")
        for text, hidden in (("", False), ("Visible", True)):
            cell.set("value", text)
            if hidden:
                self.document.set_style_option(cell, "noLabel", "1")
            profile = self.document.extract_native_label_profile(cell, scene=scene)
            result = self.labels.preflight_candidate(profile, assignment, [(150, 150)])
            self.assertEqual(result["label_status"], "not_applicable")
            self.assertEqual(result["path_status"], "not_available")
            self.assertEqual(result["path_reason"], "editor_router_additional_turns_required")

    def test_midpoint_carrier_is_not_a_size_support_gate(self):
        cell, scene, assignment = self.setup_native()
        cell.set("value", "Native text " * 40)
        result = self.labels.preflight_candidate(self.document.extract_native_label_profile(cell, scene=scene), assignment, [])
        self.assertEqual(result["path_status"], "available")
        self.assertEqual(result["label_status"], "available")
        # Horizontal label width is irrelevant to a vertical carrier; a tall
        # multiline label forces an unusable carrier without losing dimensions.
        cell.set("value", "Text\n" * 40)
        result = self.labels.preflight_candidate(self.document.extract_native_label_profile(cell, scene=scene), assignment, [])
        self.assertFalse(result["carrier_usable"])
        self.assertEqual(result["label_status"], "available")
        self.assertIsNotNone(result["size"])

    def test_center_only_nonrectangular_support_is_unchanged(self):
        cell, scene, assignment = self.setup_native()
        raw = self.document.extract_native_label_profile(cell, scene=scene)
        for kind, style in (("decision", "rhombus;"), ("start", "ellipse;aspect=fixed;")):
            with self.subTest(kind=kind):
                raw["terminals"]["exit"].update(type=kind, style=style)
                assignment["exit_offset"] = .5
                self.assertEqual(self.labels.preflight_candidate(raw, assignment, [])["path_status"], "available")
                assignment["exit_offset"] = .35
                self.assertEqual(self.labels.preflight_candidate(raw, assignment, [])["path_reason"], "uncalibrated_off_center_perimeter")

    def test_structure_and_reason_precedence(self):
        cell, scene, assignment = self.setup_native()
        raw = self.document.extract_native_label_profile(cell, scene=scene)
        cases = []
        def case(reason, mutate):
            value = copy.deepcopy(raw)
            mutate(value)
            cases.append((reason, value))
        case("missing_or_ambiguous_geometry", lambda r: r.update(geometry_count=2, scene_available=False))
        case("unsupported_native_offset", lambda r: r["native_geometry"].update(offsets=[{"tag": "bad", "attributes": {}}, {"tag": "mxPoint", "attributes": {}}]))
        case("ambiguous_native_offset", lambda r: r["native_geometry"].update(offsets=[{"tag": "mxPoint", "attributes": {}}] * 2))
        case("unsupported_label_width_or_height", lambda r: r["native_geometry"]["attributes"].update(width="2"))
        case("invalid_native_geometry", lambda r: r["native_geometry"]["attributes"].update(x="bad"))
        case("terminal_scene_unavailable", lambda r: r.update(scene_available=False))
        case("terminal_identity_mismatch", lambda r: r["terminals"]["exit"].update(identity_matches=False))
        case("ambiguous_native_waypoints", lambda r: r["native_geometry"].update(points_arrays=[[], []]))
        case("unsupported_native_waypoint", lambda r: r["native_geometry"].update(points_arrays=[[{"tag": "bad", "attributes": {}}]]))
        for reason, profile in cases:
            with self.subTest(reason=reason):
                self.assertEqual(self.labels.resolve_native_label_inputs(profile)["reason"], reason)

    def test_unknown_style_and_hint_order_are_not_discarded(self):
        cell, scene, assignment = self.setup_native()
        raw = self.document.extract_native_label_profile(cell, scene=scene)
        raw["edge_style"] += "customToken=1;"
        result = self.labels.preflight_candidate(raw, assignment, [])
        self.assertEqual(result["label_reason"], "unsupported_style:customToken")
        self.assertEqual(result["path_reason"], "unsupported_style:customToken")
        first = self.labels.project_candidate_native_inputs(raw, assignment, [(150, 150), (150, 150)])
        second = self.labels.project_candidate_native_inputs(raw, assignment, [(150, 150)])
        self.assertNotEqual(first["input_signature"], second["input_signature"])
        self.assertEqual(first["input_signature"], self.labels.project_candidate_native_inputs(raw, assignment, [(150, 150), (150, 150)])["input_signature"])

    def test_serialized_hint_precision_matches_xml_without_compaction(self):
        cell, scene, assignment = self.setup_native()
        raw = self.document.extract_native_label_profile(cell, scene=scene)
        hints = [(150.000049, 150.000049), (150.000049, 150.000049)]
        initial = self.labels.project_candidate_native_inputs(raw, assignment, hints)
        midpoint, _ = self.labels.native_label_anchor(initial["path"])
        choice = (0, {"left": midpoint[0] - 5, "top": midpoint[1] - 5,
                      "width": 10, "height": 10})
        projected = self.labels.project_candidate_native_inputs(raw, assignment, hints, choice)
        self.document.set_edge_points(cell, hints)
        self.loaded.routing_adapter.set_edge_label_position(cell, projected["path"], choice)
        del projected["input_signature"]
        self.assertEqual(projected, self.document.native_label_inputs(cell, scene=scene))
        saved_points = cell.findall("./mxGeometry/Array[@as='points']/mxPoint")
        self.assertEqual(len(saved_points), 2)
        self.assertEqual(saved_points[0].get("x"), "150")

    def test_fixed_44545_endpoint_residual_is_not_rounded_into_support(self):
        cell, scene, assignment = self.setup_native()
        raw = self.document.extract_native_label_profile(cell, scene=scene)
        raw["parent_origin"] = (0.0, 0.0)
        for terminal in raw["terminals"].values():
            bounds = terminal["bounds"]
            bounds["left"] = 445.45 - bounds["width"] / 2
            bounds["right"] = bounds["left"] + bounds["width"]
        snapshot = copy.deepcopy(raw)
        result = self.labels.preflight_candidate(raw, assignment, [(445.5, 150)])
        self.assertEqual(result["path_reason"], "editor_router_additional_turns_required")
        self.assertEqual(raw, snapshot)
        reconstructed = self.labels.native_fixed_segment_path((445.45, 126), (445.45, 183), [(445.5, 150)])
        self.assertEqual(reconstructed[0][0], 445.45)
        self.assertTrue(any(point[0] == 445.5 for point in reconstructed[1:-1]))

    def test_nonfinite_candidate_values_are_rejected(self):
        cell, scene, assignment = self.setup_native()
        raw = self.document.extract_native_label_profile(cell, scene=scene)
        with self.assertRaises(ValueError):
            self.labels.project_candidate_native_inputs(raw, assignment, [(float("nan"), 2)])
        assignment["exit_offset"] = float("inf")
        with self.assertRaises(ValueError):
            self.labels.project_candidate_native_inputs(raw, assignment, [])

    def test_existing_profile_preserves_geometry_structure_and_parent_without_mutation(self):
        cell, scene, assignment = self.setup_native()
        edge = self.loaded.routing_adapter.existing_edge_spec(cell, for_reroute=True)
        cases = []
        def add(reason, mutate):
            changed = copy.deepcopy(cell)
            mutate(changed)
            cases.append((reason, changed))
        add("unsupported_label_width_or_height", lambda c: c.find("mxGeometry").set("width", "7"))
        add("missing_or_ambiguous_geometry", lambda c: ET.SubElement(c, "mxGeometry", {"relative": "1"}))
        add("unsupported_native_offset", lambda c: ET.SubElement(c.find("mxGeometry"), "vendorOffset", {"as": "offset"}))
        add("parent_origin_unavailable", lambda c: c.set("parent", "unknown-parent"))
        for reason, changed in cases:
            with self.subTest(reason=reason):
                before = ET.tostring(changed)
                raw = self.loaded.routing_adapter.native_label_profiles(
                    [edge], scene["pool"], scene["lanes"], scene["nodes"],
                    existing_edges={edge["id"]: changed}, explicit_by_edge={edge["id"]: {"reroute"}},
                )[edge["id"]]
                measured = self.labels.preflight_candidate(raw, assignment, [])
                self.assertEqual(measured["path_reason"], reason)
                self.assertEqual(ET.tostring(changed), before)
                if reason == "unsupported_label_width_or_height":
                    self.assertEqual(raw["native_geometry"]["attributes"]["width"], "7")

    def test_existing_profile_matches_explicit_type_label_and_anchor_writer(self):
        cell, scene, assignment = self.setup_native()
        adapter = self.loaded.routing_adapter
        edge = adapter.existing_edge_spec(cell, for_reroute=True)
        edge.update(type="flow", label="Changed")
        cell.set("style", cell.get("style") + "dashed=1;dashed=1;exitDx=4;entryDy=8;")
        cell.find("mxGeometry").set("x", ".5")
        cell.find("mxGeometry").set("y", "3")
        snapshot = ET.tostring(cell)
        explicit = {"reroute", "type", "label"}
        profile = adapter.native_label_profiles(
            [edge], scene["pool"], scene["lanes"], scene["nodes"],
            existing_edges={edge["id"]: cell}, explicit_by_edge={edge["id"]: explicit},
        )[edge["id"]]
        self.assertEqual(ET.tostring(cell), snapshot)
        initial = self.labels.preflight_candidate(profile, assignment, [])
        self.assertEqual(initial["label_status"], "available")
        width, height = initial["size"]
        choice = (0, {"left": 140, "top": 145, "right": 140+width, "bottom": 145+height,
                      "width": width, "height": height})
        planned = self.labels.project_candidate_native_inputs(profile, assignment, [], choice)
        decision = self.loaded.routing.RouteDecision(edge["id"], None, {
            **assignment, "route": "forward", "points": [], "full_path": initial["path"], "label_choice": None,
        })
        adapter.apply_route_decision(cell, edge, decision, scene["lanes"], scene["nodes"],
                                     existing=True, explicit_fields=explicit)
        adapter.set_edge_label_position(cell, initial["path"], choice)
        del planned["input_signature"]
        self.assertEqual(planned, self.document.native_label_inputs(cell, scene=scene))
        style = self.document.style_values(cell.get("style"))
        self.assertEqual(style["dashed"], "0")
        self.assertEqual(style["exitDx"], "0")
        self.assertEqual(style["entryDy"], "0")
        self.assertEqual(cell.get("value"), "Changed")

    def test_existing_profile_ignores_compiled_defaults_without_explicit_updates(self):
        cell, scene, assignment = self.setup_native()
        adapter = self.loaded.routing_adapter
        cell.set("style", cell.get("style") + "dashed=1;")
        edge = adapter.existing_edge_spec(cell, for_reroute=True)
        edge.update(label="Compiled default", type="flow")
        profile = adapter.native_label_profiles(
            [edge], scene["pool"], scene["lanes"], scene["nodes"], existing_edges={edge["id"]: cell},
            explicit_by_edge={edge["id"]: {"reroute"}},
        )[edge["id"]]
        self.assertEqual(profile["text"], "Native 文本")
        self.assertEqual(self.document.style_values(profile["edge_style"])["dashed"], "1")
        self.assertEqual(self.labels.preflight_candidate(profile, assignment, [])["label_status"], "available")

    def test_existing_missing_geometry_follows_points_writer_action(self):
        cell, scene, assignment = self.setup_native()
        adapter = self.loaded.routing_adapter
        edge = adapter.existing_edge_spec(cell, for_reroute=True)
        cell.remove(cell.find("mxGeometry"))
        for action, count in (("preserve_existing", 0), ("replace_automatic", 1), ("replace_explicit", 1)):
            with self.subTest(action=action):
                raw = adapter.native_label_profiles(
                    [edge], scene["pool"], scene["lanes"], scene["nodes"],
                    existing_edges={edge["id"]: cell}, explicit_by_edge={edge["id"]: {"reroute"}},
                    points_actions_by_edge={edge["id"]: action},
                )[edge["id"]]
                actual = copy.deepcopy(cell)
                self.document.set_edge_points(actual, [], action=action)
                self.assertEqual(raw["geometry_count"], count)
                self.assertEqual(raw["geometry_count"], len(actual.findall("mxGeometry")))
                if count:
                    self.assertEqual(raw["native_geometry"]["attributes"], dict(actual.find("mxGeometry").attrib))

    def test_existing_profile_keeps_opaque_points_array_children_visible(self):
        cell, scene, assignment = self.setup_native()
        adapter = self.loaded.routing_adapter
        edge = adapter.existing_edge_spec(cell, for_reroute=True)
        geom = cell.find("mxGeometry")
        array = ET.SubElement(geom, "Array", {"as": "points", "vendor": "saved"})
        ET.SubElement(array, "vendorExtension", {"value": "keep"})
        snapshot = ET.tostring(cell)
        raw = adapter.native_label_profiles(
            [edge], scene["pool"], scene["lanes"], scene["nodes"], existing_edges={edge["id"]: cell},
            explicit_by_edge={edge["id"]: {"reroute"}},
        )[edge["id"]]
        self.assertEqual(raw["native_geometry"]["points_arrays"][0][0]["tag"], "vendorExtension")
        self.assertEqual(self.labels.preflight_candidate(raw, assignment, [])["path_reason"], "unsupported_native_waypoint")
        self.assertEqual(ET.tostring(cell), snapshot)

    def test_patch_profile_and_writer_share_explicit_type_and_label_updates(self):
        tree = self.loaded.build.build_tree(linear_spec(2, version="2"))
        adapter = self.loaded.routing_adapter
        original = adapter.native_label_profiles
        captured = []
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            captured.append((copy.deepcopy(kwargs["explicit_by_edge"]), copy.deepcopy(result)))
            return result
        with mock.patch.object(adapter, "native_label_profiles", side_effect=capture):
            self.loaded.roundtrip.patch_tree(tree, {"update_edges": [
                {"id": "e0", "reroute": True, "type": "async", "label": "Updated"},
            ]}, False)
        self.assertEqual(len(captured), 1)
        explicit, profiles = captured[0]
        self.assertTrue({"type", "label", "reroute"}.issubset(explicit["e0"]))
        actual = self.document.edge_records(self.document.graph_root(tree))["e0"]
        self.assertEqual(profiles["e0"]["text"], actual.get("value"))
        self.assertEqual(actual.get("value"), "Updated")
        self.assertEqual(self.document.style_values(profiles["e0"]["edge_style"])["dashed"], "1")
        self.assertEqual(self.document.style_values(actual.get("style"))["dashed"], "1")


if __name__ == "__main__":
    unittest.main()
