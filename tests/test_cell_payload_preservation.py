"""Opaque native XML and the serialized candidate delivery boundary (D0 T16-19)."""

import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from evidence_cases import linear_spec
from swimlane_loader import load_skill_modules

SPACE = "{http://www.w3.org/XML/1998/namespace}space"


class CellPayloadPreservationTests(unittest.TestCase):
    def setUp(self):
        self.loaded = load_skill_modules(
            ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py",
            module_name="payload_tool",
        )
        self.tool = self.loaded.tool
        self.build = self.loaded.build
        self.roundtrip = self.loaded.roundtrip
        self.document = self.loaded.document
        self.before = self.build.build_tree(linear_spec())

    def cell(self, tree, kind="node", semantic_id="n1"):
        return self.document.semantic_cells(tree)[f"{kind}:{semantic_id}"]

    def add_payload(self, tree):
        cell = self.cell(tree)
        cell.text = " intro "
        cell[0].tail = " before extension "
        payload = ET.SubElement(cell, "vendorPayload", {"mode": "retain"})
        payload.text = "  "
        ET.SubElement(payload, "word").text = "red"
        payload[0].tail = " "
        ET.SubElement(payload, "word").text = "blue"
        payload[1].tail = "\n  "
        payload.tail = " after extension "
        return payload

    def test_extensions_added_removed_changed_with_and_without_declaration(self):
        self.add_payload(self.before)
        for changes in (None, {"update_nodes": [{"id": "n1", "label": "Revised"}]}):
            expected = copy.deepcopy(self.before)
            if changes is not None:
                self.roundtrip.patch_tree(expected, changes, False)
            for mutation in ("add", "remove", "attribute", "text"):
                with self.subTest(changes=changes, mutation=mutation):
                    actual = copy.deepcopy(expected)
                    cell = self.cell(actual)
                    payload = cell.find("vendorPayload")
                    if mutation == "add":
                        ET.SubElement(cell, "vendorAdded")
                    elif mutation == "remove":
                        cell.remove(payload)
                    elif mutation == "attribute":
                        payload.set("mode", "changed")
                    else:
                        payload.find("word").text = "changed"
                    report = self.roundtrip.compare_trees(self.before, actual, changes)
                    self.assertFalse(report["preserved"])
                    self.assertTrue(report["unexpected_cell_content"])
                    self.assertTrue(all(item["semantic_id"] == "n1" and item["kind"] == "node"
                                        for item in report["unexpected_cell_content"]))

    def test_declared_edge_label_update_does_not_authorize_vendor_payload(self):
        edge = self.cell(self.before, "edge", "e0")
        ET.SubElement(edge, "vendorPayload").text = " keep "
        ET.SubElement(edge.find("mxGeometry"), "vendorGeometry").text = "  "
        changes = {"update_edges": [{"id": "e0", "label": "Go"}]}
        expected = copy.deepcopy(self.before)
        self.roundtrip.patch_tree(expected, changes, False)
        self.assertTrue(self.roundtrip.compare_trees(self.before, expected, changes)["preserved"])
        for path in ("vendorPayload", "mxGeometry/vendorGeometry"):
            with self.subTest(path=path):
                actual = copy.deepcopy(expected)
                self.cell(actual, "edge", "e0").find(path).text = "changed"
                report = self.roundtrip.compare_trees(self.before, actual, changes)
                self.assertFalse(report["preserved"])
                self.assertTrue(report["unexpected_cell_content"])

    def test_opaque_whitespace_and_cell_mixed_text_are_not_stripped(self):
        self.add_payload(self.before)
        for path, field, value in (
            (".", "text", "intro"),
            ("mxGeometry", "tail", "before extension"),
            ("vendorPayload", "text", " "),
            ("vendorPayload/word", "tail", ""),
            ("vendorPayload", "tail", "after extension"),
        ):
            with self.subTest(path=path, field=field):
                actual = copy.deepcopy(self.before)
                setattr(self.cell(actual).find(path), field, value)
                self.assertFalse(self.roundtrip.compare_trees(self.before, actual)["preserved"])

    def test_xml_space_inheritance_and_child_default_tail(self):
        self.before.getroot().set(SPACE, "preserve")
        cell = self.cell(self.before)
        cell.text = "   "
        cell.tail = "  "
        cell[0].set(SPACE, "default")
        cell[0].tail = "    "
        for path, field in ((".", "text"), (".", "tail"), ("mxGeometry", "tail")):
            actual = copy.deepcopy(self.before)
            setattr(self.cell(actual).find(path), field, " ")
            self.assertFalse(self.roundtrip.compare_trees(self.before, actual)["preserved"])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "saved.drawio"
            self.document.write_tree(self.before, output)
            loaded = ET.parse(output)
            self.assertEqual(self.cell(loaded).text, "   ")
            self.assertEqual(self.cell(loaded).tail, "  ")
            self.assertEqual(self.cell(loaded)[0].tail, "    ")

    def test_known_formatting_whitespace_and_attribute_order_are_equivalent(self):
        actual = copy.deepcopy(self.before)
        ET.indent(actual, space="\t")
        for cell in self.document.semantic_cells(actual).values():
            cell.attrib = dict(reversed(list(cell.attrib.items())))
        self.assertTrue(self.roundtrip.compare_trees(self.before, actual)["preserved"])

    def test_duplicate_geometry_and_nested_vendor_order_are_compared(self):
        cell = self.cell(self.before)
        second = ET.SubElement(cell, "mxGeometry", {"as": "geometry", "vendor": "second"})
        for value in ("first", "second"):
            ET.SubElement(second, "vendorItem", {"value": value})
        for mutation in ("delete-second-geometry", "change-second-geometry", "reorder-vendor"):
            with self.subTest(mutation=mutation):
                actual = copy.deepcopy(self.before)
                other = self.cell(actual)
                if mutation == "delete-second-geometry":
                    other.remove(other[1])
                elif mutation == "change-second-geometry":
                    other[1].set("vendor", "changed")
                else:
                    other[1][:] = list(reversed(list(other[1])))
                report = self.roundtrip.compare_trees(self.before, actual)
                self.assertFalse(report["preserved"])
                self.assertEqual(report["unexpected_geometry"], ["node:n1"])
                self.assertTrue(report["unexpected_cell_content"])

    def test_points_update_preserves_geometry_vendor_then_tamper_is_visible(self):
        edge = self.cell(self.before, "edge", "e0")
        geom = edge.find("mxGeometry")
        vendor = ET.SubElement(geom, "vendorGeometry", {"key": "untouched"})
        vendor.text = "  "
        ET.SubElement(vendor, "part").tail = " "
        original = ET.tostring(vendor)
        self.document.set_edge_points(edge, [(12, 14), (12, 14)], action="replace_explicit")
        self.assertEqual(ET.tostring(geom.find("vendorGeometry")), original)
        points = list(geom.find("Array"))
        self.assertEqual([point.attrib for point in points], [{"x": "12", "y": "14"}] * 2)
        actual = copy.deepcopy(self.before)
        self.cell(actual, "edge", "e0").find("mxGeometry/vendorGeometry/part").tail = ""
        report = self.roundtrip.compare_trees(self.before, actual)
        self.assertFalse(report["preserved"])
        self.assertTrue(report["unexpected_cell_content"])

    def test_writer_uses_copy_preserves_opaque_content_and_is_deterministic(self):
        self.add_payload(self.before)
        geom = self.cell(self.before).find("mxGeometry")
        ET.SubElement(geom, "vendorGeometry").text = "\n  "
        original = ET.tostring(self.before.getroot())
        with tempfile.TemporaryDirectory() as temporary:
            one, two = (Path(temporary) / name for name in ("one.drawio", "two.drawio"))
            seen = []
            self.document.write_tree(self.before, one, candidate_check=lambda candidate: seen.append(candidate))
            self.document.write_tree(self.before, two)
            self.assertEqual(one.read_bytes(), two.read_bytes())
            self.assertEqual(ET.tostring(self.before.getroot()), original)
            self.assertIsNot(seen[0], self.before)
            self.assertEqual(self.cell(seen[0]).find("vendorPayload").text, "  ")
            self.assertEqual(self.cell(seen[0]).find("vendorPayload/word").tail, " ")
            self.assertEqual(self.cell(seen[0]).find("mxGeometry/vendorGeometry").text, "\n  ")
            self.assertTrue(self.roundtrip.compare_trees(self.before, seen[0])["preserved"])

    def test_candidate_callback_failure_keeps_old_output_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing.drawio"
            output.write_bytes(b"old-output-sentinel")
            original = ET.tostring(self.before.getroot())
            def reject(candidate):
                self.assertIsInstance(candidate, ET.ElementTree)
                self.assertEqual(output.read_bytes(), b"old-output-sentinel")
                raise RuntimeError("candidate rejected")
            with self.assertRaisesRegex(RuntimeError, "candidate rejected"):
                self.document.write_tree(self.before, output, candidate_check=reject)
            self.assertEqual(output.read_bytes(), b"old-output-sentinel")
            self.assertEqual(ET.tostring(self.before.getroot()), original)
            self.assertEqual(list(Path(temporary).iterdir()), [output])

    def test_serialized_payload_corruption_is_rejected_before_replace(self):
        self.add_payload(self.before)
        original_write = ET.ElementTree.write
        def corrupt(tree, path, **kwargs):
            original_write(tree, path, **kwargs)
            candidate = ET.parse(path)
            self.cell(candidate).remove(self.cell(candidate).find("vendorPayload"))
            original_write(candidate, path, **kwargs)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "saved.drawio"
            output.write_bytes(b"old-output-sentinel")
            with mock.patch.object(ET.ElementTree, "write", corrupt):
                with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                    self.document.write_tree(
                        self.before, output,
                        candidate_check=lambda candidate: self.roundtrip._check_delivery_candidate(
                            self.before, candidate, False),
                    )
            self.assertEqual(caught.exception.code, "delivery/candidate-preservation-failed")
            self.assertEqual(output.read_bytes(), b"old-output-sentinel")
            self.assertFalse(list(Path(temporary).glob("*.candidate")))

    def test_delivery_checks_full_serialization_without_broadening_public_compare(self):
        actual = copy.deepcopy(self.before)
        actual.getroot().set("serializer-regression", "changed")
        # Public preservation governance is intentionally unchanged. Candidate
        # delivery additionally verifies its own serializer did not drift.
        self.assertTrue(self.roundtrip.compare_trees(self.before, actual)["preserved"])
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            self.roundtrip._check_delivery_candidate(self.before, actual, False)
        self.assertEqual(caught.exception.code, "delivery/candidate-preservation-failed")
        missing_root = copy.deepcopy(self.before)
        model = missing_root.find("./diagram/mxGraphModel")
        model.remove(model.find("root"))
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            self.roundtrip._check_delivery_candidate(self.before, missing_root, False)
        self.assertEqual(caught.exception.code, "delivery/candidate-preservation-failed")

    def assert_guard_rejects(self, before, candidate, changes):
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            self.roundtrip.saved_edge_preservation_guard(before, candidate, changes)
        self.assertEqual(caught.exception.code, "patch/preservation-violation")
        self.assertIn("e0", caught.exception.evidence["edges"])

    def test_independent_guard_rejects_label_only_path_style_and_vendor_edits(self):
        before = copy.deepcopy(self.before)
        edge = self.cell(before, "edge", "e0")
        self.document.set_edge_points(edge, [(120, 140), (120, 140), (120, 160)])
        geom = edge.find("mxGeometry")
        ET.SubElement(geom, "vendorGeometry").text = " keep "
        changes = {"update_edges": [{"id": "e0", "label": "Go"}]}
        for mutation in ("port", "point", "repeat", "origin", "vendor"):
            with self.subTest(mutation=mutation):
                actual = copy.deepcopy(before)
                changed = self.cell(actual, "edge", "e0")
                changed.set("value", "Go")
                points = changed.find("mxGeometry/Array")
                if mutation == "port":
                    self.document.set_style_option(changed, "exitX", "0.25")
                elif mutation == "point":
                    points[0].set("x", "121")
                elif mutation == "repeat":
                    points.remove(points[0])
                elif mutation == "origin":
                    changed.set("data-waypoints-origin", "explicit")
                else:
                    changed.find("mxGeometry/vendorGeometry").text = "changed"
                self.assert_guard_rejects(before, actual, changes)

    def test_independent_guard_same_value_routing_and_false_reroute_freeze_path(self):
        edge = self.cell(self.before, "edge", "e0")
        current = self.loaded.routing_adapter.existing_edge_spec(edge)
        updates = [{"id": "e0", "reroute": False}]
        updates.extend({"id": "e0", field: current[field]}
                       for field in ("from", "to", "type", "route", "exit_side") if field in current)
        for update in updates:
            with self.subTest(update=update):
                self.assertFalse(self.loaded.patch_operations.edge_route_update_requested(edge, update))
                actual = copy.deepcopy(self.before)
                self.document.set_style_option(self.cell(actual, "edge", "e0"), "exitX", "0.25")
                self.assert_guard_rejects(self.before, actual, {"update_edges": [update]})

    def test_independent_guard_label_projection_keeps_offset_text_and_tail(self):
        for field, preserve in (("text", False), ("text", True), ("tail", False), ("tail", True)):
            with self.subTest(field=field, xml_space=preserve):
                before = copy.deepcopy(self.before)
                edge = self.cell(before, "edge", "e0")
                if preserve:
                    edge.set(SPACE, "preserve")
                offset = edge.find("./mxGeometry/mxPoint[@as='offset']")
                if offset is None:
                    offset = ET.SubElement(edge.find("mxGeometry"), "mxPoint", {"as": "offset", "x": "0", "y": "0"})
                # Non-whitespace tail is significant even without xml:space;
                # whitespace text is preserved explicitly on the point.
                if field == "text" and not preserve:
                    offset.set(SPACE, "preserve")
                setattr(offset, field, "  " if field == "text" else " vendor separator ")
                actual = copy.deepcopy(before)
                changed = self.cell(actual, "edge", "e0")
                changed.set("value", "Go")
                setattr(changed.find("./mxGeometry/mxPoint[@as='offset']"), field,
                        " " if field == "text" else " changed separator ")
                self.assert_guard_rejects(before, actual, {"update_edges": [{"id": "e0", "label": "Go"}]})

    def test_independent_guard_explicit_reroute_preserves_raw_array_payload(self):
        before = copy.deepcopy(self.before)
        edge = self.cell(before, "edge", "e0")
        edge.set("data-waypoints-origin", "explicit")
        self.document.set_edge_points(edge, [(120, 140), (120, 140)])
        array = edge.find("mxGeometry/Array")
        vendor = ET.SubElement(array, "vendorPointData")
        vendor.text = "  "
        vendor.tail = " separator "
        ET.SubElement(vendor, "part").tail = " "
        changes = {"update_edges": [{"id": "e0", "reroute": True}]}
        for mutation in ("point", "repeat", "text", "tail", "nested-tail"):
            with self.subTest(mutation=mutation):
                actual = copy.deepcopy(before)
                changed_array = self.cell(actual, "edge", "e0").find("mxGeometry/Array")
                changed_vendor = changed_array.find("vendorPointData")
                if mutation == "point":
                    changed_array[0].set("x", "121")
                elif mutation == "repeat":
                    changed_array.remove(changed_array[0])
                elif mutation == "text":
                    changed_vendor.text = " "
                elif mutation == "tail":
                    changed_vendor.tail = "changed"
                else:
                    changed_vendor.find("part").tail = ""
                self.assert_guard_rejects(before, actual, changes)

    def test_reroute_and_explicit_replacement_preserve_array_vendor_payload(self):
        spec = linear_spec()
        for item in spec["edges"]:
            item["label"] = ""
        before = self.build.build_tree(spec)
        edge = self.cell(before, "edge", "e0")
        array = ET.SubElement(edge.find("mxGeometry"), "Array", {"as": "points", "vendor": "retain"})
        array.text = " before point "
        ET.SubElement(array, "mxPoint", {"x": "120", "y": "110"})
        vendor = ET.SubElement(array, "vendorPointData")
        vendor.text, vendor.tail = " keep ", " after vendor "
        for update in ({"id": "e0", "reroute": True},
                       {"id": "e0", "waypoints": [{"x": 120, "y": 115}]}):
            with self.subTest(update=update):
                candidate = copy.deepcopy(before)
                changes = {"update_edges": [update]}
                if update.get("reroute"):
                    # Native measurement has never supported an unknown child
                    # inside the points Array. Automatic candidate preflight
                    # now refuses it before the native route writer is called.
                    root = self.document.graph_root(candidate)
                    pool = self.document.find_pool(candidate)
                    lanes, nodes = self.document.lane_node_records(root, pool)
                    raw = self.document.native_label_inputs(self.cell(candidate, "edge", "e0"), scene={
                        "pool": pool, "lanes": lanes, "nodes": nodes,
                    })
                    self.assertEqual(raw["reason"], "unsupported_native_waypoint")
                    snapshot = ET.tostring(candidate.getroot())
                    with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                        self.roundtrip.patch_tree(candidate, changes, False)
                    self.assertEqual(caught.exception.code, "routing/no-safe-route")
                    planning = caught.exception.evidence["planning"]
                    self.assertEqual(planning["reason"], "native_profile_unavailable")
                    self.assertIn("unsupported_native_waypoint", [
                        issue.get("evidence", {}).get("reason") for issue in planning["details"]])
                    self.assertEqual(ET.tostring(candidate.getroot()), snapshot)
                    with tempfile.TemporaryDirectory() as temporary:
                        directory = Path(temporary)
                        source, output, patch = (directory / name for name in (
                            "source.drawio", "output.drawio", "patch.json"))
                        before.write(source, encoding="utf-8")
                        source_bytes = source.read_bytes()
                        output.write_bytes(b"old-output-sentinel")
                        patch.write_text(json.dumps(changes), encoding="utf-8")
                        for strict in (False, True):
                            args = argparse.Namespace(
                                input=source, output=output, changes=patch, force=True, strict=strict,
                                allow_geometry_updates=False, accept_model_drift=True,
                                expected_input_sha256=self.document.file_receipt(source)["sha256"],
                            )
                            with self.assertRaises(self.loaded.contracts.DiagramError) as cli_error:
                                self.tool.command_patch(args)
                            self.assertEqual(cli_error.exception.code, "routing/no-safe-route")
                            self.assertEqual(cli_error.exception.evidence["planning"]["reason"],
                                             "native_profile_unavailable")
                            self.assertEqual(source.read_bytes(), source_bytes)
                            self.assertEqual(output.read_bytes(), b"old-output-sentinel")
                            self.assertFalse(list(directory.glob("*.candidate")))
                    continue
                self.roundtrip.patch_tree(candidate, changes, False)
                saved_array = self.cell(candidate, "edge", "e0").find("mxGeometry/Array")
                self.assertIsNotNone(saved_array)
                self.assertEqual(saved_array.get("vendor"), "retain")
                self.assertEqual(saved_array.text, " before point ")
                self.assertEqual(ET.tostring(saved_array.find("vendorPointData")), ET.tostring(vendor))
                self.assertTrue(self.roundtrip.compare_trees(before, candidate, changes)["preserved"])
                if "waypoints" in update:
                    self.assertEqual(saved_array.find("mxPoint").attrib, {"x": "120", "y": "115"})

    def test_actual_automatic_route_writer_preserves_opaque_array_payload(self):
        # Exercise the D0 writer separately from native capability gating:
        # removing old native points must retain Array-level vendor content.
        candidate = copy.deepcopy(self.before)
        root = self.document.graph_root(candidate)
        pool = self.document.find_pool(candidate)
        lanes, nodes = self.document.lane_node_records(root, pool)
        edge = self.cell(candidate, "edge", "e0")
        full_path = self.document.edge_polyline(edge, lanes, nodes)
        adapter = self.loaded.routing_adapter
        edge_spec = adapter.existing_edge_spec(edge, for_reroute=True)
        array = ET.SubElement(edge.find("mxGeometry"), "Array", {"as": "points", "vendor": "retain"})
        array.text = " before point "
        ET.SubElement(array, "mxPoint", {"x": "120", "y": "110"})
        vendor = ET.SubElement(array, "vendorPointData")
        vendor.text, vendor.tail = " keep ", " after vendor "
        expected_vendor = ET.tostring(vendor)
        before = copy.deepcopy(candidate)
        exit_port, entry_port = (self.document.port_from_style(edge, side) for side in ("exit", "entry"))
        decision = self.loaded.routing.RouteDecision("e0", None, {
            "exit_side": exit_port[0], "exit_offset": exit_port[1],
            "entry_side": entry_port[0], "entry_offset": entry_port[1],
            "route": "forward", "points": [], "full_path": full_path, "label_choice": None,
        })
        adapter.apply_route_decision(edge, edge_spec, decision, lanes, nodes,
            existing=True, explicit_fields={"reroute"}, points_action="replace_automatic")
        saved = edge.find("mxGeometry/Array")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.get("vendor"), "retain")
        self.assertEqual(saved.text, " before point ")
        self.assertEqual(saved.findall("mxPoint"), [])
        self.assertEqual(ET.tostring(saved.find("vendorPointData")), expected_vendor)
        changes = {"update_edges": [{"id": "e0", "reroute": True}]}
        # The independent guard does not replay the capability-gated planner.
        self.assertEqual(self.roundtrip.saved_edge_preservation_guard(before, candidate, changes)["changed_edges"], [])

    def test_route_replacement_refuses_unmappable_point_owned_extensions(self):
        for extension in ("attribute", "subtree", "text", "tail"):
            with self.subTest(extension=extension):
                candidate = copy.deepcopy(self.before)
                edge = self.cell(candidate, "edge", "e0")
                self.document.set_edge_points(edge, [(120, 110)])
                point = edge.find("mxGeometry/Array/mxPoint")
                if extension == "attribute":
                    point.set("vendor", "keep")
                elif extension == "subtree":
                    ET.SubElement(point, "vendorPointData").text = " keep "
                else:
                    setattr(point, extension, " keep ")
                original = ET.tostring(edge)
                with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                    self.document.set_edge_points(edge, [(120, 115)], action="replace_explicit")
                self.assertEqual(caught.exception.code, "patch/preservation-violation")
                self.assertEqual(ET.tostring(edge), original)
                self.document.set_edge_points(edge, [(120, 110)], action="replace_explicit")
                self.assertEqual(ET.tostring(edge), original)

    def test_independent_route_guard_detects_producer_opaque_loss(self):
        before = copy.deepcopy(self.before)
        edge = self.cell(before, "edge", "e0")
        edge.set("vendor-edge", "keep")
        geometry = edge.find("mxGeometry")
        geometry.set("vendor-geometry", "keep")
        ET.SubElement(edge, "vendorCellData").text = "  "
        ET.SubElement(geometry, "vendorGeometryData").text = "  "
        array = ET.SubElement(geometry, "Array", {"as": "points", "vendor-array": "keep"})
        point = ET.SubElement(array, "mxPoint", {"x": "120", "y": "110"})
        ET.SubElement(array, "vendorArrayData").text = "  "
        ET.SubElement(point, "vendorPointData").text = "  "
        changes = {"update_edges": [{"id": "e0", "reroute": True}]}
        for path in (".", "mxGeometry", "vendorCellData", "mxGeometry/vendorGeometryData",
                     "mxGeometry/Array", "mxGeometry/Array/vendorArrayData", "mxGeometry/Array/mxPoint/vendorPointData"):
            with self.subTest(path=path):
                candidate = copy.deepcopy(before)
                item = self.cell(candidate, "edge", "e0").find(path)
                if path in (".", "mxGeometry", "mxGeometry/Array"):
                    field = next(key for key in item.attrib if key.startswith("vendor-"))
                    item.set(field, "changed")
                else:
                    item.text = " "
                self.assert_guard_rejects(before, candidate, changes)
        # Root-inherited xml:space is available to this independent guard even
        # though a cell-only route writer cannot discover its ancestors.
        before.getroot().set(SPACE, "preserve")
        point.tail = "   "
        candidate = copy.deepcopy(before)
        self.cell(candidate, "edge", "e0").find("mxGeometry/Array/mxPoint").tail = " "
        self.assert_guard_rejects(before, candidate, changes)

    def test_actual_patch_candidate_must_pass_same_version_compare(self):
        changes = {"update_nodes": [{"id": "n1", "label": "Revised"}]}
        accepted = copy.deepcopy(self.before)
        self.roundtrip.patch_tree(accepted, changes, False)
        compare = self.roundtrip.compare_trees
        calls = []
        def reject_declared_comparison(before, candidate, declaration=None):
            calls.append(declaration)
            result = compare(before, candidate, declaration)
            if declaration is not None:
                result["preserved"] = False
                result["unexpected_attributes"] = ["node:n1"]
            return result
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "saved.drawio"
            output.write_bytes(b"old-output-sentinel")
            with mock.patch.object(self.roundtrip, "compare_trees", reject_declared_comparison):
                with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                    self.document.write_tree(
                        accepted, output,
                        candidate_check=lambda candidate: self.roundtrip._check_delivery_candidate(
                            accepted, candidate, False, before=self.before, changes=changes),
                    )
            self.assertEqual(calls, [None, changes])
            self.assertEqual(caught.exception.code, "delivery/candidate-preservation-failed")
            self.assertEqual(output.read_bytes(), b"old-output-sentinel")
            self.assertFalse(list(Path(temporary).glob("*.candidate")))

    def test_final_candidate_validation_failure_preserves_build_and_patch_outputs(self):
        for command in ("build", "patch"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, output, spec, changes = (directory / name for name in (
                    "source.drawio", "output.drawio", "spec.json", "changes.json"))
                self.before.write(source, encoding="utf-8")
                source_bytes = source.read_bytes()
                output.write_bytes(b"old-output-sentinel")
                spec.write_text(json.dumps(linear_spec()))
                changes.write_text("{}")
                args = argparse.Namespace(
                    input=source, output=output, spec=spec, changes=changes, force=True, strict=True,
                    allow_geometry_updates=False, accept_model_drift=False,
                    expected_input_sha256=self.document.file_receipt(source)["sha256"],
                )
                validate = self.loaded.validation.validate_tree
                count = 0
                target = 2 if command == "build" else 3
                def reject_actual_candidate(tree):
                    nonlocal count
                    count += 1
                    result = validate(tree)
                    if count == target:
                        result = copy.deepcopy(result)
                        result["warnings"] = ["injected actual-candidate warning"]
                        result["diagnostics"].append({"code": "test/candidate-warning", "severity": "warning"})
                    return result
                with mock.patch.object(self.loaded.validation, "validate_tree", reject_actual_candidate):
                    with contextlib.redirect_stdout(io.StringIO()):
                        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                            getattr(self.tool, "command_" + command)(args)
                self.assertEqual(caught.exception.code, "delivery/strict-validation-failed")
                self.assertEqual(count, target)
                self.assertEqual(output.read_bytes(), b"old-output-sentinel")
                self.assertEqual(source.read_bytes(), source_bytes)
                self.assertFalse(list(directory.glob("*.candidate")))


if __name__ == "__main__":
    unittest.main()
