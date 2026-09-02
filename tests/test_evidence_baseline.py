import copy
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from evidence_cases import corpus, linear_spec
from regression_baseline import capture, normalize, TOOL
from performance_probe import probe, worker, resource


def load_tool():
    spec = importlib.util.spec_from_file_location("evidence_tool", ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py")
    tool = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(tool)
    finally:
        sys.dont_write_bytecode = previous
    return tool


class EvidenceBaselineTests(unittest.TestCase):
    def editor_tree_with_manual_cell(self, presentation="rail", kind="edge"):
        tool = load_tool()
        spec = json.loads((ROOT / "tests/fixtures/neutral-flow.json").read_text())
        spec.update(schema_version="3", behavior_pattern="approval-loop",
                    layout={"phase_presentation": presentation or "rail"})
        if presentation is None:
            spec.pop("phases")
        tree = tool.build_tree(spec)
        root = tool.graph_root(tree)
        original = list(root)

        def descendants(parent):
            for cell in original:
                if cell.get("parent") == parent:
                    yield cell
                    yield from descendants(cell.get("id"))

        root[:] = list(descendants(None))
        manual = ET.Element("mxCell", {"id": "manual-cell", "parent": "psd-lane-lane-a",
                            kind: "1", "style": "strokeColor=#123456;", "custom": "preserve"})
        if kind == "edge":
            manual.set("source", "psd-node-start")
            manual.set("target", "psd-node-step-a")
        ET.SubElement(manual, "mxGeometry", {"x": "20", "y": "40", "width": "30", "height": "20", "as": "geometry"})
        ET.SubElement(manual, "customPayload", {"value": "untouched"})
        start = next(c for c in root if c.get("id") == "psd-node-start")
        root.insert(list(root).index(start) + 1, manual)
        return tool, tree

    def test_patch_preserves_manual_cell_sibling_order_and_subtree(self):
        for presentation in ("rail", "bands", None):
            for kind in ("edge", "vertex"):
                with self.subTest(presentation=presentation, kind=kind):
                    tool, before = self.editor_tree_with_manual_cell(presentation, kind)
                    after = copy.deepcopy(before)
                    changes = {"update_nodes": [{"id": "step-a", "label": "Revised"}]}
                    tool.patch_tree(after, changes, allow_geometry_updates=False)
                    for parent in {c.get("parent") for c in tool.graph_root(before)}:
                        self.assertEqual(
                            [c.get("id") for c in tool.graph_root(before) if c.get("parent") == parent],
                            [c.get("id") for c in tool.graph_root(after) if c.get("parent") == parent])
                    self.assertEqual(tool.element_signature(before.find(".//mxCell[@id='manual-cell']")),
                                     tool.element_signature(after.find(".//mxCell[@id='manual-cell']")))
                    self.assertTrue(tool.compare_trees(before, after, changes)["preserved"])

    def test_compare_detects_sibling_reorder_with_and_without_patch(self):
        tool, before = self.editor_tree_with_manual_cell()
        changes = {"update_nodes": [{"id": "step-a", "label": "Revised"}]}
        for patch in (None, changes):
            with self.subTest(patch=patch):
                after = copy.deepcopy(before)
                if patch is not None:
                    tool.patch_tree(after, patch, allow_geometry_updates=False)
                root = tool.graph_root(after)
                manual = next(c for c in root if c.get("id") == "manual-cell")
                root.remove(manual)
                root.append(manual)
                result = tool.compare_trees(before, after, patch)
                self.assertFalse(result["preserved"])
                self.assertEqual(result["unexpected_sibling_order"][0]["parent"], "psd-lane-lane-a")

    def test_compare_detects_unknown_cell_mutations(self):
        tool, before = self.editor_tree_with_manual_cell()
        for mutation in ("style", "parent", "source", "geometry", "payload", "remove", "add"):
            with self.subTest(mutation=mutation):
                after = copy.deepcopy(before)
                root = tool.graph_root(after)
                manual = next(c for c in root if c.get("id") == "manual-cell")
                if mutation in ("style", "parent", "source"):
                    manual.set(mutation, "changed")
                elif mutation == "geometry":
                    manual.find("mxGeometry").set("x", "90")
                elif mutation == "payload":
                    manual.find("customPayload").set("value", "changed")
                elif mutation == "remove":
                    root.remove(manual)
                else:
                    added = copy.deepcopy(manual)
                    added.set("id", "manual-extra")
                    root.append(added)
                result = tool.compare_trees(before, after, {})
                self.assertFalse(result["preserved"])
                self.assertTrue(result["unexpected_unmanaged_cells"])

    def test_wrapped_unknown_cell_preserves_order_and_compares_metadata(self):
        tool, before = self.editor_tree_with_manual_cell()
        root = tool.graph_root(before)
        manual = next(c for c in root if c.get("id") == "manual-cell")
        index = list(root).index(manual)
        root.remove(manual)
        wrapper = ET.Element("object", {"id": manual.attrib.pop("id"), "label": "Manual"})
        wrapper.append(manual)
        root.insert(index, wrapper)
        after = copy.deepcopy(before)
        changes = {"update_nodes": [{"id": "step-a", "label": "Revised"}]}
        tool.patch_tree(after, changes, allow_geometry_updates=False)
        def native_ids(tree):
            result = []
            for item in tool.graph_root(tree):
                cell = item if item.tag == "mxCell" else item.find("mxCell")
                if cell is not None and cell.get("parent") == "psd-lane-lane-a":
                    result.append(item.get("id") or cell.get("id"))
            return result
        self.assertEqual(native_ids(before), native_ids(after))
        self.assertTrue(tool.compare_trees(before, after, changes)["preserved"])
        for mutation in ("wrapper-attribute", "order", "tail", "text"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(after)
                changed_root = tool.graph_root(changed)
                item = changed_root.find("object")
                if mutation == "wrapper-attribute":
                    item.set("label", "Changed")
                elif mutation == "order":
                    changed_root.remove(item)
                    changed_root.append(item)
                elif mutation == "tail":
                    item.find("mxCell/customPayload").tail = "meaningful text"
                else:
                    item.find("mxCell/customPayload").text = "important text"
                self.assertFalse(tool.compare_trees(before, changed, changes)["preserved"])

    def test_unknown_mixed_content_including_whitespace_is_protected(self):
        tool, before = self.editor_tree_with_manual_cell()
        payload = before.find(".//customPayload")
        payload.text = " meaningful "
        payload.tail = " trailing "
        for location in ("text", "tail"):
            with self.subTest(location=location):
                after = copy.deepcopy(before)
                setattr(after.find(".//customPayload"), location,
                        getattr(after.find(".//customPayload"), location).strip())
                self.assertFalse(tool.compare_trees(before, after)["preserved"])
        after = copy.deepcopy(before)
        after.find(".//mxCell[@id='manual-cell']/mxGeometry").tail = "\n    "
        self.assertFalse(tool.compare_trees(before, after)["preserved"])
        # The drawing unit's outer tail belongs to root formatting, not its
        # opaque payload, unless an ancestor requests xml:space preservation.
        after = copy.deepcopy(before)
        after.find(".//mxCell[@id='manual-cell']").tail = "\n    "
        self.assertTrue(tool.compare_trees(before, after)["preserved"])

    def test_unknown_whitespace_roundtrip_and_xml_space_inheritance(self):
        tool, before = self.editor_tree_with_manual_cell()
        payload = before.find(".//customPayload")
        payload.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        payload.text = "   "
        ET.SubElement(payload, "word").text = "red"
        payload[0].tail = " "
        ET.SubElement(payload, "word").text = "blue"
        for change in ("text", "separator", "root-tail"):
            with self.subTest(change=change):
                source = copy.deepcopy(before)
                if change == "root-tail":
                    source.getroot().set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                    source.find(".//mxCell[@id='manual-cell']").tail = "   "
                after = copy.deepcopy(source)
                if change == "text":
                    after.find(".//customPayload").text = " "
                elif change == "separator":
                    after.find(".//customPayload/word").tail = ""
                else:
                    after.find(".//mxCell[@id='manual-cell']").tail = " "
                self.assertFalse(tool.compare_trees(source, after)["preserved"])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, output, changes = (directory / n for n in ("before.drawio", "after.drawio", "patch.json"))
            # Do not use the tool writer to prepare this input: that would
            # conceal whether its indentation rewrites the opaque payload.
            before.write(source, encoding="utf-8", xml_declaration=True)
            changes.write_text("{}")
            import hashlib
            result = subprocess.run([sys.executable, "-B", str(TOOL), "patch", "--input", str(source),
                                     "--output", str(output), "--changes", str(changes),
                                     "--expected-input-sha256", hashlib.sha256(source.read_bytes()).hexdigest()],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            final = ET.parse(output)
            self.assertEqual(final.find(".//customPayload").text, "   ")
            self.assertEqual(final.find(".//customPayload/word").tail, " ")
            self.assertTrue(tool.compare_trees(before, final, {})["preserved"])

    def test_foreign_metadata_does_not_hide_unknown_subtree_changes(self):
        tool, before = self.editor_tree_with_manual_cell()
        manual = before.find(".//mxCell[@id='manual-cell']")
        manual.set("data-kind", "foreign-kind")
        manual.set("data-semantic-id", "external-id")
        after = copy.deepcopy(before)
        after.find(".//customPayload").tail = "changed text"
        result = tool.compare_trees(before, after)["unexpected_unmanaged_cells"]
        self.assertEqual(result, [{"cell_id": "manual-cell", "change": "changed"}])

    def test_compare_ignores_cross_parent_serialization_order(self):
        tool, before = self.editor_tree_with_manual_cell()
        after = copy.deepcopy(before)
        root = tool.graph_root(after)
        # Moving a complete sibling group in the XML does not change paint order.
        root[:] = sorted(root, key=lambda c: c.get("parent", ""))
        self.assertTrue(tool.compare_trees(before, after)["preserved"])

    def test_compare_reports_managed_sibling_reorder(self):
        tool = load_tool()
        before = tool.build_tree(linear_spec())
        after = copy.deepcopy(before)
        root = tool.graph_root(after)
        nodes = [c for c in root if c.get("data-kind") == "node"]
        root.remove(nodes[0])
        root.append(nodes[0])
        result = tool.compare_trees(before, after, {})
        self.assertFalse(result["preserved"])
        self.assertEqual(result["unexpected_sibling_order"][0]["parent"], "psd-lane-lane-a")

    def test_compare_accepts_declared_phase_addition_and_deletion(self):
        tool, before = self.editor_tree_with_manual_cell(None)
        add = {"phases": [{"id": "new-phase", "label": "Phase", "from_rank": 1, "to_rank": 3}]}
        after = copy.deepcopy(before)
        tool.patch_tree(after, add, allow_geometry_updates=False)
        self.assertTrue(tool.compare_trees(before, after, add)["preserved"])
        remove = {"delete_phases": ["new-phase"]}
        final = copy.deepcopy(after)
        tool.patch_tree(final, remove, allow_geometry_updates=False)
        self.assertTrue(tool.compare_trees(after, final, remove)["preserved"])

    def test_layer_conflict_with_unknown_anchor_is_refused_atomically(self):
        tool, tree = self.editor_tree_with_manual_cell()
        root = tool.graph_root(tree)
        phase = next(c for c in root if c.get("data-kind") == "phase")
        manual = next(c for c in root if c.get("id") == "manual-cell")
        manual.set("parent", "psd-pool-main")
        root.remove(phase)
        root.append(phase)
        # A lane now precedes the unknown anchor and a phase follows it.
        # Sorting across the anchor would silently alter relative paint order.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, output, changes = (directory / n for n in ("source.drawio", "result.drawio", "changes.json"))
            tree.write(source, encoding="utf-8", xml_declaration=True)
            changes.write_text("{}")
            import hashlib
            before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            result = subprocess.run([sys.executable, "-B", str(TOOL), "patch", "--input", str(source),
                                     "--output", str(output), "--changes", str(changes),
                                     "--expected-input-sha256", before_hash], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(output.exists())
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before_hash)
            diagnostics = json.loads(result.stdout)["diagnostics"]
            self.assertEqual(diagnostics[0]["code"], "delivery/validation-failed")
            self.assertIn("layout/phase-z-order", {d["code"] for d in diagnostics[0]["evidence"]["diagnostics"]})

    def test_cli_contract_matches_frozen_baseline(self):
        expected = json.loads((ROOT / "tests/fixtures/cli-contract-v1.json").read_text())
        actual = capture()
        for name in expected["cases"]:
            with self.subTest(case=name):
                self.assertEqual(actual["cases"][name], expected["cases"][name])
        self.assertEqual(actual.keys(), expected.keys())
        self.assertEqual(actual["cases"].keys(), expected["cases"].keys())

    def test_repeated_builds_and_unordered_list_permutations(self):
        tool = load_tool()
        for name, spec in {"linear": linear_spec(), "decision": corpus()["decision-retry-phases"]}.items():
            with self.subTest(case=name):
                first = tool.build_tree(spec)
                second = tool.build_tree(spec)
                self.assertEqual(ET.tostring(first.getroot()), ET.tostring(second.getroot()))
                permuted = copy.deepcopy(spec)
                permuted["nodes"].reverse()
                permuted["edges"].reverse()
                third = tool.build_tree(permuted)
                # Permutations promise the same geometry/semantics, not paint
                # order. compare must now report real sibling-order changes.
                self.assertEqual({key: tool.element_signature(cell) for key, cell in tool.semantic_cells(first).items()},
                                 {key: tool.element_signature(cell) for key, cell in tool.semantic_cells(third).items()})
                compared = tool.compare_trees(first, third)
                for key in ("unexpected_geometry", "unexpected_attributes", "unexpected_added", "unexpected_missing"):
                    self.assertEqual(compared[key], [])
                self.assertEqual(compared.get("unexpected_sibling_order"), compared.get("changed_sibling_order"))
                self.assertEqual(tool.validate_tree(first), tool.validate_tree(third))

    def test_locked_conflict_is_rejected_not_waived(self):
        tool = load_tool()
        with self.assertRaises(tool.DiagramError) as caught:
            tool.build_tree(corpus()["explicit-port-conflict"])
        self.assertIn("already used", str(caught.exception))

    def test_known_routing_failure_remains_visible(self):
        tool = load_tool()
        report = tool.validate_tree(tool.build_tree(corpus()["request-response-retry"]))
        self.assertFalse(report["quality_gate_passed"])
        self.assertEqual([d["code"] for d in report["diagnostics"]], ["routing/edge-conflict"])

    def test_performance_probe_has_measurements_and_real_timeout(self):
        result = worker(3)
        self.assertTrue(result["quality_gate_passed"])
        if resource is not None:
            self.assertGreater(result["peak_rss_bytes"], 0)
        else:
            self.assertIsNone(result["peak_rss_bytes"])
        self.assertGreater(result["profile"]["route_edge"]["calls"], 0)
        self.assertEqual(result["label_overlap_replay"]["overlap_hits"], 0)
        self.assertEqual(result["label_overlap_replay"]["labels"], 3)
        timeout = probe(500, 0.001)
        self.assertEqual(timeout["status"], "timeout")
        self.assertIsNone(timeout["peak_rss_bytes"])
        self.assertIsNone(timeout["quality_gate_passed"])

    def test_performance_rejects_unbounded_inputs_before_spawning(self):
        for timeout in (0, float("nan"), float("inf"), 61):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                probe(60, timeout)
        with self.assertRaises(ValueError):
            probe(5001, 10)
        with self.assertRaises(ValueError):
            worker(5001)

    def test_version_normalization_preserves_invalid_or_wrong_input_stamp(self):
        for value in (None, 501, True, "unknown", "0.5.0"):
            result = {"result": {"patch_receipt": {"input_tool_version": value}}}
            self.assertEqual(normalize(result, {}, input_version="0.5.1"), result)
        for version in ("0.5.0", "0.5.1"):
            result = {"result": {"patch_receipt": {"input_tool_version": version}}}
            self.assertEqual(normalize(result, {}, input_version=version)["result"]["patch_receipt"]["input_tool_version"], "<tool-version>")

    def test_broken_patch_input_version_changes_frozen_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            tool = Path(temporary) / "mutated.py"
            original = TOOL.read_text(encoding="utf-8")
            mutation = original.replace('"input_tool_version": input_validation.get("tool_version"),',
                                        '"input_tool_version": None,')
            self.assertNotEqual(original, mutation)
            tool.write_text(mutation, encoding="utf-8")
            actual = capture(tool)
            expected = json.loads((ROOT / "tests/fixtures/cli-contract-v1.json").read_text(encoding="utf-8"))
            self.assertNotEqual(actual["cases"]["linear-v3"]["commands"]["patch"],
                                expected["cases"]["linear-v3"]["commands"]["patch"])
