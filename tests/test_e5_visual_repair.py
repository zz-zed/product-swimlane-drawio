import copy
from concurrent.futures import ThreadPoolExecutor
import binascii
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
import zlib


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules


def png_bytes(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = binascii.crc32(data, binascii.crc32(kind)) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = b"".join(b"\x00" + bytes(width * 3) for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def run_tool(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([sys.executable, "-B", str(TOOL), *args], cwd=ROOT,
                          capture_output=True, check=False)


def repair_spec() -> dict:
    return {
        "schema_version": "3", "title": "Neutral repair fixture",
        "behavior_pattern": "linear", "layout": {"profile": "review"},
        "lanes": [{"id": "lane-a", "label": "Lane A", "width": 240}],
        "nodes": [
            {"id": "n0", "lane": "lane-a", "rank": 1, "type": "start", "label": ""},
            {"id": "n1", "lane": "lane-a", "rank": 2, "type": "process", "label": "Step 1"},
            {"id": "n2", "lane": "lane-a", "rank": 3, "type": "end", "label": ""},
        ],
        "edges": [
            {"id": "e0", "from": "n0", "to": "n1", "label": "Next"},
            {"id": "e1", "from": "n1", "to": "n2", "label": "Next"},
        ],
        "main_path": ["n0", "n1", "n2"],
    }


class E5RepairPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e5_repair_preservation_tests")
        cls.preserve = cls.loaded.review_preservation
        cls.repair = cls.loaded.review_repair
        cls.document = cls.loaded.document
        cls.contracts = cls.loaded.contracts

    def tree(self):
        return self.loaded.build.build_tree(repair_spec())

    def edge(self, tree, sid: str = "e0"):
        return self.document.edge_records(self.document.graph_root(tree))[sid]

    @staticmethod
    def action(intent: str = "reroute-edge") -> list[dict]:
        return [{"target": {"kind": "edge", "id": "e0"}, "intent": intent}]

    def comparison(self, before, after, intent: str = "reroute-edge") -> dict:
        return self.preserve.protected_repair_comparison(before, after, self.action(intent))

    def set_style_value(self, cell, key: str, value: str) -> None:
        tokens = cell.get("style").split(";")
        cell.set("style", ";".join(
            f"{key}={value}" if token.split("=", 1)[0] == key else token
            for token in tokens
        ))

    def make_detour(self, tree, *, opaque_array: bool = False) -> None:
        geometry = self.edge(tree).find("mxGeometry")
        offset = geometry.find("mxPoint[@as='offset']")
        offset.set("x", "-18.5")
        offset.set("y", "-0.5")
        attributes = {"as": "points"}
        if opaque_array:
            attributes["opaque"] = "keep"
        points = ET.SubElement(geometry, "Array", attributes)
        for x, y in ((120, 138), (220, 138), (220, 171), (120, 171)):
            ET.SubElement(points, "mxPoint", {"x": str(x), "y": str(y)})

    def test_label_projection_allows_only_frozen_position_leaves(self) -> None:
        before = self.tree()
        old = self.edge(before)
        old_geometry = old.find("mxGeometry")
        old_geometry.set("x", "0")
        old_geometry.set("y", "0")
        after = copy.deepcopy(before)
        new = self.edge(after)
        new_geometry = new.find("mxGeometry")
        new_geometry.set("x", "1")
        new_geometry.set("y", "-2")
        new_offset = new_geometry.find("mxPoint[@as='offset']")
        new_offset.set("x", "3")
        new_offset.set("y", "4")
        for index, key in enumerate(sorted(self.preserve.REPAIR_LABEL_ATTRIBUTES)):
            new.set(key, str(index))
        self.assertTrue(self.comparison(before, after, "reposition-edge-label")["preserved"])

        changed_text = copy.deepcopy(after)
        self.edge(changed_text).set("value", "Changed")
        result = self.comparison(before, changed_text, "reposition-edge-label")
        self.assertFalse(result["preserved"])

    def test_label_projection_preserves_geometry_attribute_presence_and_offset_payload(self) -> None:
        before = self.tree()
        after = copy.deepcopy(before)
        self.edge(after).find("mxGeometry").set("x", "1")
        self.assertFalse(self.comparison(before, after, "reposition-edge-label")["preserved"])

        before = self.tree()
        geometry = self.edge(before).find("mxGeometry")
        offset = ET.SubElement(geometry, "mxPoint", {"as": "offset", "x": "0", "y": "0", "opaque": "keep"})
        offset.text = "payload"
        after = copy.deepcopy(before)
        changed = self.edge(after).find("mxGeometry/mxPoint[@as='offset']")
        changed.set("x", "1")
        changed.set("opaque", "changed")
        self.assertFalse(self.comparison(before, after, "reposition-edge-label")["preserved"])

    def test_reroute_allows_native_point_changes_but_not_nonempty_array_deletion(self) -> None:
        before = self.tree()
        geometry = self.edge(before).find("mxGeometry")
        points = ET.SubElement(geometry, "Array", {"as": "points"})
        ET.SubElement(points, "mxPoint", {"x": "100", "y": "100"})
        after = copy.deepcopy(before)
        new_points = self.edge(after).find("mxGeometry/Array[@as='points']")
        new_points[0].set("x", "110")
        ET.SubElement(new_points, "mxPoint", {"x": "120", "y": "120"})
        self.assertTrue(self.comparison(before, after)["preserved"])

        removed = copy.deepcopy(before)
        removed_geometry = self.edge(removed).find("mxGeometry")
        removed_geometry.remove(removed_geometry.find("Array[@as='points']"))
        result = self.comparison(before, removed)
        self.assertFalse(result["preserved"])
        self.assertIn("review/protected-change", result["differences"])

    def test_reroute_empty_points_container_boundary(self) -> None:
        before = self.tree()
        geometry = self.edge(before).find("mxGeometry")
        empty = ET.SubElement(geometry, "Array", {"as": "points"})
        after = copy.deepcopy(before)
        new_geometry = self.edge(after).find("mxGeometry")
        new_geometry.remove(new_geometry.find("Array[@as='points']"))
        self.assertTrue(self.comparison(before, after)["preserved"])

        opaque_before = self.tree()
        opaque_geometry = self.edge(opaque_before).find("mxGeometry")
        ET.SubElement(opaque_geometry, "Array", {"as": "points", "opaque": "keep"})
        opaque_after = copy.deepcopy(opaque_before)
        changed_geometry = self.edge(opaque_after).find("mxGeometry")
        changed_geometry.remove(changed_geometry.find("Array[@as='points']"))
        self.assertFalse(self.comparison(opaque_before, opaque_after)["preserved"])

    def test_reroute_preserves_opaque_array_payload_while_points_change(self) -> None:
        before = self.tree()
        self.make_detour(before, opaque_array=True)
        after = copy.deepcopy(before)
        new_array = self.edge(after).find("mxGeometry/Array[@as='points']")
        new_array[1].set("x", "210")
        self.assertTrue(self.comparison(before, after)["preserved"])

        changed = copy.deepcopy(after)
        self.edge(changed).find("mxGeometry/Array[@as='points']").set("opaque", "changed")
        self.assertFalse(self.comparison(before, changed)["preserved"])

    def test_unlocked_ports_may_change_but_any_explicit_lock_freezes_endpoint(self) -> None:
        before = self.tree()
        after = copy.deepcopy(before)
        changed = self.edge(after)
        changed.set(self.contracts.DATA_EXIT_SIDE, "left")
        changed.set(self.contracts.DATA_EXIT_OFFSET, "0.25")
        self.assertTrue(self.comparison(before, after)["preserved"])

        locked_before = self.tree()
        self.edge(locked_before).set(self.contracts.DATA_EXIT_SIDE_EXPLICIT, "1")
        locked_after = copy.deepcopy(locked_before)
        self.edge(locked_after).set(self.contracts.DATA_EXIT_OFFSET, "0.25")
        self.assertFalse(self.comparison(locked_before, locked_after)["preserved"])

    def test_waypoints_and_unsupported_native_profiles_refuse_before_generation(self) -> None:
        cases = []
        explicit = self.tree()
        self.edge(explicit).set(self.contracts.DATA_WAYPOINTS_ORIGIN, "explicit")
        cases.append(explicit)
        duplicate_port = self.tree()
        self.edge(duplicate_port).set("style", self.edge(duplicate_port).get("style") + "exitX=0.5;")
        cases.append(duplicate_port)
        missing_relative = self.tree()
        self.edge(missing_relative).find("mxGeometry").attrib.pop("relative")
        cases.append(missing_relative)
        for tree in cases:
            with self.subTest(case=len(cases)):
                with self.assertRaises(self.contracts.DiagramError):
                    self.preserve.preservation_native_eligibility(tree, "e0", "reroute-edge")

    def test_parse_rejects_payload_that_cannot_be_serialized_exactly(self) -> None:
        raw = ET.tostring(self.tree().getroot(), encoding="utf-8")
        namespaced = raw.replace(b"<mxfile ", b'<mxfile xmlns:x="urn:opaque" ', 1)
        with self.assertRaises(self.contracts.DiagramError):
            self.preserve.preservation_parse(namespaced)
        with self.assertRaises(self.contracts.DiagramError):
            self.preserve.preservation_parse(b"<!--opaque-->" + raw)

    def test_tool_stamp_cannot_change_without_actual_target_geometry_change(self) -> None:
        before = self.tree()
        self.document.find_pool(before).set(self.contracts.DATA_TOOL_VERSION, "0.7.0-test")
        after = copy.deepcopy(before)
        self.document.find_pool(after).set(
            self.contracts.DATA_TOOL_VERSION, self.contracts.TOOL_VERSION)
        self.assertFalse(self.comparison(before, after, "reposition-edge-label")["preserved"])

        changed = copy.deepcopy(before)
        geometry = self.edge(changed).find("mxGeometry")
        offset = geometry.find("mxPoint[@as='offset']")
        offset.set("x", str(float(offset.get("x")) + 1))
        self.document.find_pool(changed).set(
            self.contracts.DATA_TOOL_VERSION, self.contracts.TOOL_VERSION)
        self.assertTrue(self.comparison(before, changed, "reposition-edge-label")["preserved"])

    def test_metric_comparison_uses_native_thresholds_not_severity(self) -> None:
        target = {"kind": "edge", "id": "e0"}
        label_before = {"kind": "edge-label", "conflict_count": 2, "carrier_usable": False}
        label_improved = {"kind": "edge-label", "conflict_count": 1, "carrier_usable": True}
        label_resolved = {"kind": "edge-label", "conflict_count": 0, "carrier_usable": True}
        self.assertEqual(
            self.preserve.repair_metric_comparison(label_before, label_improved, "visual/edge-label-collision"),
            {"improved": True, "non_regressing": True, "resolved": False},
        )
        self.assertTrue(self.preserve.repair_metric_comparison(
            label_before, label_resolved, "visual/edge-label-collision")["resolved"])

        route_before = {"kind": "edge-route", "bend_count": 4, "manhattan_length": 300,
                        "terminal_clearance": 0, "clearance_violation": True, "node_crossing_count": 0}
        route_shorter = {**route_before, "bend_count": 2, "manhattan_length": 200,
                         "terminal_clearance": 2, "clearance_violation": False}
        self.assertTrue(self.preserve.repair_metric_comparison(
            route_before, route_shorter, "visual/excessive-detour")["improved"])
        crossing_regression = {**route_shorter, "node_crossing_count": 1}
        result = self.preserve.repair_metric_comparison(
            route_before, crossing_regression, "visual/excessive-detour")
        self.assertFalse(result["improved"])
        self.assertFalse(result["non_regressing"])

    def test_generate_candidate_is_bounded_and_does_not_self_accept(self) -> None:
        tree = self.tree()
        edge = self.edge(tree)
        geometry = edge.find("mxGeometry")
        geometry.set("x", "0")
        geometry.set("y", "0")
        offset = geometry.find("mxPoint[@as='offset']")
        offset.set("x", "0")
        offset.set("y", "49")
        raw = ET.tostring(tree.getroot(), encoding="utf-8")
        result = self.repair.generate_repair_candidate(raw, self.action("reposition-edge-label"))
        self.assertEqual(result["status"], "technical_passed")
        self.assertTrue(result["protected_projection"]["preserved"])
        self.assertNotEqual(result["raw"], raw)
        metric = result["metrics"][0]
        self.assertLess(metric["after"]["conflict_count"], metric["before"]["conflict_count"])
        self.assertNotIn("accepted", result)

    def test_generate_route_candidate_preserves_semantics_and_explicit_endpoint(self) -> None:
        tree = self.tree()
        self.make_detour(tree)
        before_edge = self.edge(tree)
        before_edge.set(self.contracts.DATA_EXIT_SIDE_EXPLICIT, "1")
        protected = {
            key: before_edge.get(key)
            for key in ("value", self.contracts.DATA_FROM, self.contracts.DATA_TO,
                        self.contracts.DATA_EDGE_TYPE, self.contracts.DATA_ROUTE,
                        self.contracts.DATA_EXIT_SIDE_EXPLICIT,
                        self.contracts.DATA_EXIT_SIDE, self.contracts.DATA_EXIT_OFFSET)
        }
        before_style = {
            token.split("=", 1)[0]: token.split("=", 1)[1]
            for token in before_edge.get("style").split(";") if "=" in token
            if token.split("=", 1)[0] in self.preserve.REPAIR_PORT_KEYS["exit"]
        }
        raw = ET.tostring(tree.getroot(), encoding="utf-8")
        result = self.repair.generate_repair_candidate(raw, self.action())
        self.assertEqual(result["status"], "technical_passed")
        self.assertTrue(result["protected_projection"]["preserved"])
        after_edge = self.edge(result["tree"])
        self.assertEqual(protected, {key: after_edge.get(key) for key in protected})
        after_style = {
            token.split("=", 1)[0]: token.split("=", 1)[1]
            for token in after_edge.get("style").split(";") if "=" in token
            if token.split("=", 1)[0] in self.preserve.REPAIR_PORT_KEYS["exit"]
        }
        self.assertEqual(before_style, after_style)
        metric = result["metrics"][0]
        decision = self.preserve.repair_metric_comparison(
            metric["before"], metric["after"], "visual/excessive-detour")
        self.assertTrue(decision["improved"])
        self.assertTrue(decision["non_regressing"])

    def test_generate_candidate_rejects_duplicate_targets_and_unknown_intent(self) -> None:
        raw = ET.tostring(self.tree().getroot(), encoding="utf-8")
        for actions in (
            [],
            self.action() + self.action("reposition-edge-label"),
            [{"target": {"kind": "node", "id": "n0"}, "intent": "reroute-edge"}],
            [{"target": {"kind": "edge", "id": "e0"}, "intent": "move-anything"}],
        ):
            with self.subTest(actions=actions):
                with self.assertRaises(self.contracts.DiagramError):
                    self.repair.generate_repair_candidate(raw, actions)


class E5ReviewStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e5_review_state_tests")
        cls.state = cls.loaded.review_state
        cls.contracts = cls.loaded.contracts
        cls.run_id = "12345678-1234-4234-9234-123456789abc"

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name).resolve()
        prepared_dir = root / "prepared"
        prepared_dir.mkdir()
        completion = prepared_dir / "completion.json"
        completion.write_bytes(b"{}\n")
        prepared = self.state.context_bundle.bundle_read_input(completion)
        expected_root = {
            "state_version": 1,
            "run_id": self.run_id,
            "prepared_sha256": prepared.sha256,
            "prepared_path_sha256": hashlib.sha256(str(completion).encode()).hexdigest(),
            "original": {"artifact_sha256": "a" * 64, "context_sha256": None},
            "authorization_sha256": "b" * 64,
            "tool_version": self.contracts.TOOL_VERSION,
            "repair_rule_version": self.loaded.review_preservation.REPAIR_RULE_VERSION,
            "maximum_attempts": 2,
        }
        claim = {
            "claim_version": 1,
            "run_id": self.run_id,
            "attempt_index": 1,
            "root_manifest_sha256": None,
            "parent_assessment_sha256": None,
            "parent_record_sha256": "c" * 64,
            "input_artifact_sha256": "a" * 64,
            "input_context_sha256": None,
            "plan_sha256": "d" * 64,
            "authorization_sha256": "b" * 64,
            "tool_version": self.contracts.TOOL_VERSION,
            "repair_rule_version": self.loaded.review_preservation.REPAIR_RULE_VERSION,
            "action_fingerprint": "e" * 64,
        }
        return temporary, prepared, expected_root, claim

    def assert_error(self, code: str, call, *args, **kwargs):
        with self.assertRaises(self.contracts.DiagramError) as raised:
            call(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_claim_commits_root_then_slot_and_cannot_be_replayed(self) -> None:
        temporary, prepared, root, claim = self.fixture()
        self.addCleanup(temporary.cleanup)
        saved, claim_input, delivery, ledger = self.state.claim_review_attempt(
            prepared, root, claim, [prepared])
        self.assertEqual(saved["root_manifest_sha256"], ledger["root"].sha256)
        self.assertEqual(claim_input.sha256, ledger["attempts"][0]["claim_input"].sha256)
        self.assertTrue(delivery["committed"])
        self.assertTrue(delivery["complete"])
        self.assertEqual(ledger["next_attempt"], 2)
        self.assertFalse(ledger["terminal"])
        self.assert_error("review/attempt-pending", self.state.claim_review_attempt,
                          prepared, root, claim, [prepared])

    def test_invalid_claim_is_rejected_before_state_initialization(self) -> None:
        for field, value, code in (
            ("claim_version", True, "schema/type"),
            ("attempt_index", 3, "review/attempt-exhausted"),
            ("parent_assessment_sha256", "f" * 64, "review/state-conflict"),
        ):
            with self.subTest(field=field):
                temporary, prepared, root, claim = self.fixture()
                self.addCleanup(temporary.cleanup)
                claim[field] = value
                self.assert_error(code, self.state.claim_review_attempt,
                                  prepared, root, claim, [prepared])
                self.assertFalse(self.state.state_ledger_path(prepared, self.run_id).exists())

    def test_package_header_run_must_match_payload_and_expected_ledger(self) -> None:
        temporary, prepared, root, _ = self.fixture()
        self.addCleanup(temporary.cleanup)
        different_run = "87654321-4321-4321-8321-cba987654321"
        ledger_path = self.state.state_ledger_path(prepared, self.run_id)
        self.assert_error(
            "review/state-conflict", self.state.state_single_publish,
            ledger_path, "state", different_run, "state.json", root, [prepared],
        )
        self.assertTrue((ledger_path / "completion.json").is_file())
        self.assert_error("review/state-conflict", self.state.inspect_review_state,
                          prepared, root)

    def test_partial_result_is_immutable_incomplete_state(self) -> None:
        temporary, prepared, root, claim = self.fixture()
        self.addCleanup(temporary.cleanup)
        self.state.claim_review_attempt(prepared, root, claim, [prepared])
        result_dir = self.state.state_ledger_path(prepared, self.run_id) / "attempt-1/result"
        result_dir.mkdir()
        (result_dir / "result.json").write_text("partial", encoding="utf-8")
        self.assert_error("review/state-incomplete", self.state.inspect_review_state,
                          prepared, root)
        observed = self.state.inspect_review_state(prepared, root, allow_partial_result=True)
        self.assertTrue(observed["incomplete"])
        self.assertIsNone(observed["attempts"][0]["result"])
        self.assertEqual((result_dir / "result.json").read_text(encoding="utf-8"), "partial")

    def test_reserved_slot_without_claim_commit_is_not_reused_or_stopped(self) -> None:
        temporary, prepared, root, claim = self.fixture()
        self.addCleanup(temporary.cleanup)
        ledger = self.state.state_ledger_path(prepared, self.run_id)
        self.state.state_single_publish(
            ledger, "state", self.run_id, "state.json", root, [prepared])
        slot = ledger / "attempt-1"
        slot.mkdir()
        partial = b'{"claim_version":1'
        (slot / "claim.json").write_bytes(partial)
        self.assert_error("review/state-incomplete", self.state.inspect_review_state,
                          prepared, root)
        self.assert_error("review/state-incomplete", self.state.claim_review_attempt,
                          prepared, root, claim, [prepared])
        self.assertEqual((slot / "claim.json").read_bytes(), partial)
        self.assertFalse((slot / "completion.json").exists())

    def test_committed_claim_tampering_fails_closed_without_new_slot(self) -> None:
        temporary, prepared, root, claim = self.fixture()
        self.addCleanup(temporary.cleanup)
        self.state.claim_review_attempt(prepared, root, claim, [prepared])
        ledger = self.state.state_ledger_path(prepared, self.run_id)
        claim_path = ledger / "attempt-1/claim.json"
        claim_path.chmod(0o600)
        claim_path.write_bytes(claim_path.read_bytes() + b" ")
        self.assert_error("delivery/bundle-incomplete", self.state.inspect_review_state,
                          prepared, root)
        self.assertFalse((ledger / "attempt-2").exists())

    def test_concurrent_claim_has_one_committed_winner(self) -> None:
        temporary, prepared, root, claim = self.fixture()
        self.addCleanup(temporary.cleanup)

        def run_claim():
            try:
                saved = self.state.claim_review_attempt(prepared, root, claim, [prepared])
                return "committed", saved[1].sha256
            except self.contracts.DiagramError as exc:
                return "rejected", exc.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: run_claim(), range(2)))
        self.assertEqual([kind for kind, _ in outcomes].count("committed"), 1)
        ledger = self.state.inspect_review_state(prepared, root)
        self.assertEqual(len(ledger["attempts"]), 1)
        self.assertEqual(
            [value for kind, value in outcomes if kind == "committed"],
            [ledger["attempts"][0]["claim_input"].sha256],
        )

    def test_rejected_marker_terminates_chain_without_advancing_pointers(self) -> None:
        temporary, prepared, root, claim = self.fixture()
        self.addCleanup(temporary.cleanup)
        saved, claim_input, _, ledger = self.state.claim_review_attempt(prepared, root, claim, [prepared])
        slot = ledger["attempts"][0]["directory"]
        result = {
            "attempt_result_version": 1, "run_id": self.run_id, "attempt_index": 1,
            "claim_sha256": claim_input.sha256, "plan_sha256": saved["plan_sha256"],
            "status": "technical_failed", "candidate_sha256": None,
            "candidate_artifact_sha256": None, "candidate_context_sha256": None,
            "diagnostics": [], "delivery": None,
        }
        self.state.state_single_publish(slot / "result", "result", self.run_id,
                                        "result.json", result, [prepared, claim_input])
        marker = {
            "assessment_marker_version": 1, "run_id": self.run_id, "attempt_index": 1,
            "claim_sha256": claim_input.sha256, "assessment_sha256": "f" * 64,
            "decision": "rejected", "continue": False, "original": root["original"],
            "last_accepted": None, "last_good": None,
        }
        self.state.state_single_publish(slot / "assessment", "assessment-marker", self.run_id,
                                        "assessment.json", marker, [prepared, claim_input])
        observed = self.state.inspect_review_state(prepared, root)
        self.assertTrue(observed["terminal"])
        self.assertIsNone(observed["last_accepted"])
        second = dict(claim, attempt_index=2, parent_assessment_sha256="f" * 64)
        self.assert_error("review/state-conflict", self.state.claim_review_attempt,
                          prepared, root, second, [prepared])

    def test_accepted_unresolved_candidate_advances_last_accepted_only_and_enables_round_two(self) -> None:
        temporary, prepared, root, claim = self.fixture()
        self.addCleanup(temporary.cleanup)
        saved, claim_input, _, ledger = self.state.claim_review_attempt(prepared, root, claim, [prepared])
        slot = ledger["attempts"][0]["directory"]
        pointer = {
            "artifact_sha256": "1" * 64, "context_sha256": None,
            "candidate_sha256": "2" * 64, "record_sha256": "3" * 64,
        }
        result = {
            "attempt_result_version": 1, "run_id": self.run_id, "attempt_index": 1,
            "claim_sha256": claim_input.sha256, "plan_sha256": saved["plan_sha256"],
            "status": "technical_passed", "candidate_sha256": pointer["candidate_sha256"],
            "candidate_artifact_sha256": pointer["artifact_sha256"],
            "candidate_context_sha256": None, "diagnostics": [], "delivery": None,
        }
        self.state.state_single_publish(slot / "result", "result", self.run_id,
                                        "result.json", result, [prepared, claim_input])
        marker = {
            "assessment_marker_version": 1, "run_id": self.run_id, "attempt_index": 1,
            "claim_sha256": claim_input.sha256, "assessment_sha256": "4" * 64,
            "decision": "accepted", "continue": True, "original": root["original"],
            "last_accepted": pointer, "last_good": None,
        }
        self.state.state_single_publish(slot / "assessment", "assessment-marker", self.run_id,
                                        "assessment.json", marker, [prepared, claim_input])
        observed = self.state.inspect_review_state(prepared, root)
        self.assertEqual(observed["last_accepted"], pointer)
        self.assertIsNone(observed["last_good"])
        self.assertFalse(observed["terminal"])

        second = dict(
            claim, attempt_index=2, parent_assessment_sha256=marker["assessment_sha256"],
            parent_record_sha256=pointer["record_sha256"],
            input_artifact_sha256=pointer["artifact_sha256"], plan_sha256="5" * 64,
            action_fingerprint="6" * 64,
        )
        _, _, _, after_second = self.state.claim_review_attempt(prepared, root, second, [prepared])
        self.assertEqual([item["index"] for item in after_second["attempts"]], [1, 2])
        third = dict(second, attempt_index=3, parent_assessment_sha256="7" * 64)
        self.assert_error("review/attempt-exhausted", self.state.claim_review_attempt,
                          prepared, root, third, [prepared])


class E5CyclePlanningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e5_cycle_planning_tests")
        cls.cycle = cls.loaded.review_cycle

    def edge(self, tree, sid: str = "e0"):
        return self.loaded.document.edge_records(self.loaded.document.graph_root(tree))[sid]

    @staticmethod
    def issue(code: str) -> dict:
        return {
            "issue_id": code.rsplit("/", 1)[-1], "reviewer_kind": "agent",
            "code": code, "severity": "warning",
            "targets": [{"kind": "edge", "id": "e0"}],
            "binding_status": "spatial_bound",
        }

    def plan_for(self, issues: list[dict], *, assessment=None) -> dict:
        tree = self.loaded.build.build_tree(repair_spec())
        E5RepairPreservationTests.make_detour(self, tree)
        raw = ET.tostring(tree.getroot(), encoding="utf-8")
        initial = {"run": {"run_id": "12345678-1234-4234-9234-123456789abc"},
                   "manifest": SimpleNamespace(sha256="a" * 64)}
        authorization = {"actions": [{
            "target": {"kind": "edge", "id": "e0"},
            "intents": ["reposition-edge-label", "reroute-edge"],
        }]}
        auth_input = SimpleNamespace(sha256="b" * 64)
        record = {
            "manifest": SimpleNamespace(sha256="c" * 64),
            "receipt": {
                "issues": issues,
                "states": {"preview_export": "passed", "agent_image_review": "failed"},
                "review_consensus": "single_reviewer",
            },
            "report": {"reviews": {"agent": {"full_image_viewed": True}}},
        }
        baseline = {
            "graph": SimpleNamespace(raw=raw, sha256="d" * 64),
            "context": None, "tree": tree,
            "assessment": assessment,
            "validation": {"valid": True, "diagnostics": []},
        }
        state = {"terminal": False, "attempts": [], "next_attempt": 1, "root": None}
        return self.cycle.cycle_plan_data(
            initial, authorization, auth_input, None, record, baseline, state, None)

    def test_route_precedence_keeps_all_same_edge_issue_keys_in_stable_order(self) -> None:
        label = self.issue("visual/edge-label-collision")
        route = self.issue("visual/excessive-detour")
        first = self.plan_for([label, route])
        second = self.plan_for([route, label])
        self.assertTrue(first["can_repair"])
        self.assertEqual(first["actions"], second["actions"])
        self.assertEqual(first["actions"][0]["intent"], "reroute-edge")
        self.assertEqual(
            [key["code"] for key in first["actions"][0]["issue_keys"]],
            ["visual/edge-label-collision", "visual/excessive-detour"],
        )

    def test_source_incomplete_does_not_override_passed_sc_and_group_gates(self) -> None:
        assessment = {
            "gate": "incomplete", "source_gate": "incomplete", "group_gate": "passed",
            "scope_results": [{"scope_id": "primary", "gate": "passed"}],
        }
        plan = self.plan_for([self.issue("visual/excessive-detour")], assessment=assessment)
        self.assertTrue(plan["can_repair"])
        self.assertIsNone(plan["stop_reason"])
        self.assertEqual(plan["context_assessment"]["source_gate"], "incomplete")

    def test_candidate_metadata_validates_nested_actions_metrics_and_receipts(self) -> None:
        tree = self.loaded.build.build_tree(repair_spec())
        edge = self.edge(tree)
        geometry = edge.find("mxGeometry")
        geometry.set("x", "0")
        geometry.set("y", "0")
        geometry.find("mxPoint[@as='offset']").set("y", "49")
        raw = ET.tostring(tree.getroot(), encoding="utf-8")
        generated = self.loaded.review_repair.generate_repair_candidate(
            raw, [{"target": {"kind": "edge", "id": "e0"},
                   "intent": "reposition-edge-label"}])
        action = {
            "intent": "reposition-edge-label", "target": {"kind": "edge", "id": "e0"},
            "issue_keys": [{"code": "visual/edge-label-collision",
                            "targets": [{"kind": "edge", "id": "e0"}]}],
            "before_metric": json.loads(json.dumps(generated["metrics"][0]["before"])),
            "strategy_id": "native-label-reflow-v1",
        }
        metadata = {
            "actions": [action],
            "metrics": json.loads(json.dumps(generated["metrics"])),
            "protected_projection": json.loads(json.dumps(generated["protected_projection"])),
            "source_preserved": True, "semantic_context": None,
        }
        baseline = {"tree": generated["tree"]}
        self.cycle.cycle_candidate_shapes(metadata, baseline)

        cases = [
            ("strategy", lambda value: value["actions"][0].__setitem__("strategy_id", "free-form"),
             "review/action-not-authorized"),
            ("metric-type", lambda value: value["metrics"][0]["after"].__setitem__("conflict_count", True),
             "schema/type"),
            ("projection", lambda value: value["protected_projection"].__setitem__("preserved", "true"),
             "schema/type"),
            ("source", lambda value: value.__setitem__("source_preserved", 1), "schema/type"),
            ("context", lambda value: value.__setitem__("semantic_context", []), "schema/type"),
        ]
        for name, mutate, code in cases:
            with self.subTest(name=name):
                changed = copy.deepcopy(metadata)
                mutate(changed)
                with self.assertRaises(self.loaded.contracts.DiagramError) as raised:
                    self.cycle.cycle_candidate_shapes(changed, baseline)
                self.assertEqual(raised.exception.code, code)


class E5PlanRepairWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e5_plan_repair_workflow_tests")
        cls.bundle = cls.loaded.review_workflow.context_bundle
        cls.review = cls.loaded.review_evidence

    def assert_success(self, result: subprocess.CompletedProcess[bytes]) -> dict:
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return json.loads(result.stdout)

    def assert_error(self, result: subprocess.CompletedProcess[bytes], code: str,
                     exit_code: int = 2) -> dict:
        self.assertEqual(result.returncode, exit_code, result.stderr.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertEqual(body["diagnostics"][0]["code"], code, body)
        return body

    @staticmethod
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def indexed(self, objects: dict, target: dict) -> dict:
        key = self.review.review_target_key(target)
        return next(item for item in objects["objects"]
                    if self.review.review_target_key(item["target"]) == key)

    def initial_chain(self, root: Path, *, waypoint_origin: str = "automatic",
                      repair_kind: str = "label", with_context: bool = False) -> dict:
        tree = self.loaded.build.build_tree(repair_spec())
        edge = self.loaded.document.edge_records(self.loaded.document.graph_root(tree))["e0"]
        geometry = edge.find("mxGeometry")
        offset = geometry.find("mxPoint[@as='offset']")
        if repair_kind == "label":
            geometry.set("x", "0")
            geometry.set("y", "0")
            offset.set("x", "0")
            offset.set("y", "49")
        else:
            offset.set("x", "-18.5")
            offset.set("y", "-0.5")
            points = ET.SubElement(geometry, "Array", {"as": "points"})
            for x, y in ((120, 138), (220, 138), (220, 171), (120, 171)):
                ET.SubElement(points, "mxPoint", {"x": str(x), "y": str(y)})
        edge.set(self.loaded.contracts.DATA_WAYPOINTS_ORIGIN, waypoint_origin)
        graph = root / "diagram.drawio"
        if repair_kind == "route":
            graph.write_bytes(ET.tostring(tree.getroot(), encoding="utf-8",
                                          short_empty_elements=True))
        else:
            self.loaded.document.write_tree(tree, graph)
        graph_sha = self.sha(graph)

        context = context_manifest = None
        context_sha = None
        if with_context:
            template = {
                "context_template_version": 1,
                "provenance": {
                    "version": 1, "sources": [],
                    "facts": [{
                        "id": "open-fact", "status": "assumption",
                        "source_refs": [], "confirmation": None,
                    }],
                    "bindings": [{
                        "id": "open-binding", "fact_id": "open-fact",
                        "target": {"kind": "node", "id": "n1"},
                        "fields": {"label": "Step 1"},
                        "source_snapshots": [], "validity": "current",
                    }],
                    "history": [],
                },
            }
            bound = self.loaded.tool.context_workflow.workflow_template(template, tree, graph_sha)
            context = root / "context.json"
            context.write_bytes(self.bundle.bundle_json(bound))
            context_sha = self.sha(context)
            context_manifest = root / "completion.json"
            context_manifest.write_bytes(self.bundle.bundle_json({
                "bundle_version": 1,
                "members": [
                    {"role": "diagram", "name": graph.name, "sha256": graph_sha,
                     "bytes": len(graph.read_bytes())},
                    {"role": "context", "name": context.name, "sha256": context_sha,
                     "bytes": len(context.read_bytes())},
                ],
            }))

        prepared = root / "prepared"
        prepare_args = [
            "review", "prepare", "--input", str(graph),
            "--expected-input-sha256", graph_sha,
        ]
        if context is not None:
            prepare_args.extend([
                "--context", str(context), "--expected-context-sha256", context_sha,
                "--input-completion-manifest", str(context_manifest),
            ])
        prepare_args.extend(["--output", str(prepared)])
        self.assert_success(run_tool(*prepare_args))
        prepared_manifest = prepared / "completion.json"
        run = json.loads((prepared / "run.json").read_text())
        objects = json.loads((prepared / "objects.json").read_text())

        evidence = root / "evidence"
        evidence.mkdir()
        image = png_bytes(600, 600)
        image_path = evidence / "full.png"
        image_path.write_bytes(image)
        image_sha = self.sha(image_path)
        pool_target = {"kind": "pool", "id": "main"}
        node_target = {"kind": "node", "id": "n0"}
        anchors = []
        for target, anchor in ((pool_target, "top_left"), (pool_target, "top_right"),
                               (node_target, "center")):
            item = self.indexed(objects, target)
            x, y = self.review.review_anchor(item["bounds"], anchor)
            anchors.append({"target": target, "anchor": anchor, "pixel": {"x": x, "y": y}})
        mapping = {
            "mapping_version": 1, "kind": "axis_aligned", "scale_x": 1.0,
            "scale_y": 1.0, "translate_x": 0.0, "translate_y": 0.0,
            "calibration_id": "neutral-calibration",
        }
        edge_target = {"kind": "edge", "id": "e0"}
        edge_object = self.indexed(objects, edge_target)
        bounds = edge_object["label_bounds"]
        report = {
            "visual_report_version": 1, "run_id": run["run_id"],
            "prepared_sha256": self.sha(prepared_manifest), "artifact_sha256": graph_sha,
            "context_sha256": context_sha, "round_index": 0,
            "export": {
                "state": "passed", "reason": None,
                "renderer": {"name": "neutral-renderer", "version": "1",
                             "profile_id": "neutral-profile", "fonts": None},
                "command": ["neutral-renderer", "--export"], "exit_code": 0,
                "input_before_sha256": graph_sha, "input_after_sha256": graph_sha,
                "full_preview_sha256": image_sha, "stdout": None, "stderr": None,
                "calibration": {
                    "calibration_version": 1, "id": "neutral-calibration",
                    "profile_id": "neutral-profile", "artifact_sha256": graph_sha,
                    "preview_sha256": image_sha, "method": "externally_observed_anchors",
                    "anchors": anchors, "tolerance_px": 2.0,
                },
            },
            "previews": [{
                "id": "full", "kind": "full", "name": "full.png", "sha256": image_sha,
                "bytes": len(image), "width": 600, "height": 600, "mapping": mapping,
            }],
            "reviews": {
                "agent": {
                    "state": "failed", "reason": None,
                    "reviewer": {"kind": "agent", "name": "Protocol fixture", "host": None,
                                 "model": None, "tool_receipt_ids": []},
                    "full_image_viewed": True, "viewed_previews": ["full"],
                    "issues": [{
                        "issue_id": f"{repair_kind}-e0",
                        "code": ("visual/edge-label-collision" if repair_kind == "label"
                                 else "visual/excessive-detour"),
                        "severity": "warning", "targets": [edge_target], "preview_id": "full",
                        "preview_sha256": image_sha,
                        "region": {key: float(bounds[key]) for key in ("x", "y", "width", "height")},
                        "observation": "Externally declared protocol test observation",
                        "suggested_intent": ("reposition-edge-label" if repair_kind == "label"
                                             else "reroute-edge"),
                        "binding": "bound",
                        "uncertainty": None,
                    }],
                },
                "human": {"state": "not_run", "reason": None, "reviewer": None,
                          "full_image_viewed": False, "viewed_previews": [], "issues": []},
            },
        }
        report_path = evidence / "report.json"
        report_path.write_bytes(self.bundle.bundle_json(report))
        record = root / "record"
        record_args = [
            "review", "record", "--prepared", str(prepared_manifest),
            "--expected-prepared-sha256", self.sha(prepared_manifest),
            "--input", str(graph), "--expected-input-sha256", graph_sha,
        ]
        if context is not None:
            record_args.extend([
                "--context", str(context), "--expected-context-sha256", context_sha,
                "--input-completion-manifest", str(context_manifest),
            ])
        record_args.extend([
            "--evidence-dir", str(evidence), "--report", str(report_path),
            "--expected-report-sha256", self.sha(report_path), "--output", str(record),
        ])
        self.assert_success(run_tool(*record_args))
        authorization = {
            "review_authorization_version": 1, "run_id": run["run_id"],
            "prepared_sha256": self.sha(prepared_manifest),
            "prepared_path_sha256": hashlib.sha256(str(prepared_manifest).encode()).hexdigest(),
            "original_artifact_sha256": graph_sha, "original_context_sha256": context_sha,
            "maximum_attempts": 2, "asserted_by": "Protocol fixture actor",
            "basis": "Explicit test authorization",
            "actions": [{"target": edge_target,
                         "intents": [("reposition-edge-label" if repair_kind == "label"
                                      else "reroute-edge")]}],
        }
        authorization_path = root / "authorization.json"
        authorization_path.write_bytes(self.bundle.bundle_json(authorization))
        return {
            "graph": graph, "graph_sha": graph_sha, "prepared": prepared,
            "prepared_manifest": prepared_manifest, "record": record,
            "record_manifest": record / "completion.json", "authorization": authorization_path,
            "run_id": run["run_id"], "context": context, "context_sha": context_sha,
            "context_manifest": context_manifest,
        }

    def common_args(self, chain: dict) -> list[str]:
        args = [
            "--prepared", str(chain["prepared_manifest"]),
            "--expected-prepared-sha256", self.sha(chain["prepared_manifest"]),
            "--parent-prepared", str(chain["prepared_manifest"]),
            "--expected-parent-prepared-sha256", self.sha(chain["prepared_manifest"]),
            "--record", str(chain["record_manifest"]),
            "--expected-record-sha256", self.sha(chain["record_manifest"]),
            "--input", str(chain["graph"]), "--expected-input-sha256", chain["graph_sha"],
            "--authorization", str(chain["authorization"]),
            "--expected-authorization-sha256", self.sha(chain["authorization"]),
        ]
        if chain["context"] is not None:
            args.extend([
                "--context", str(chain["context"]),
                "--expected-context-sha256", chain["context_sha"],
                "--input-completion-manifest", str(chain["context_manifest"]),
            ])
        return args

    def candidate_record(self, root: Path, chain: dict, candidate: Path,
                         claim_sha256: str, report_mutator=None) -> tuple[Path, Path, str]:
        candidate_manifest = candidate / "completion.json"
        candidate_graph = candidate / "artifact/diagram.drawio"
        prepared = root / "candidate-prepared"
        prepare_args = [
            "review", "prepare", "--candidate", str(candidate_manifest),
            "--expected-candidate-sha256", self.sha(candidate_manifest),
            "--prepared", str(chain["prepared_manifest"]),
            "--expected-prepared-sha256", self.sha(chain["prepared_manifest"]),
            "--attempt", "1", "--claim-sha256", claim_sha256,
            "--input", str(candidate_graph),
            "--expected-input-sha256", self.sha(candidate_graph),
        ]
        candidate_context = candidate / "artifact/context.json"
        candidate_context_manifest = candidate / "artifact/completion.json"
        if chain["context"] is not None:
            prepare_args.extend([
                "--context", str(candidate_context),
                "--expected-context-sha256", self.sha(candidate_context),
                "--input-completion-manifest", str(candidate_context_manifest),
            ])
        prepare_args.extend(["--output", str(prepared)])
        self.assert_success(run_tool(*prepare_args))
        prepared_manifest = prepared / "completion.json"
        run = json.loads((prepared / "run.json").read_text())
        objects = json.loads((prepared / "objects.json").read_text())
        evidence = root / "candidate-evidence"
        evidence.mkdir()
        image = png_bytes(601, 600)
        image_path = evidence / "full.png"
        image_path.write_bytes(image)
        image_sha = self.sha(image_path)
        pool_target = {"kind": "pool", "id": "main"}
        node_target = {"kind": "node", "id": "n0"}
        anchors = []
        for target, anchor in ((pool_target, "top_left"), (pool_target, "top_right"),
                               (node_target, "center")):
            item = self.indexed(objects, target)
            x, y = self.review.review_anchor(item["bounds"], anchor)
            anchors.append({"target": target, "anchor": anchor, "pixel": {"x": x, "y": y}})
        edge_target = {"kind": "edge", "id": "e0"}
        bounds = self.indexed(objects, edge_target)["label_bounds"]
        report = {
            "visual_report_version": 2, "run_id": run["run_id"],
            "prepared_sha256": self.sha(prepared_manifest),
            "artifact_sha256": self.sha(candidate_graph),
            "context_sha256": self.sha(candidate_context) if chain["context"] is not None else None,
            "round_index": 1, "claim_sha256": claim_sha256,
            "candidate_sha256": self.sha(candidate_manifest),
            "parent_record_sha256": self.sha(chain["record_manifest"]),
            "export": {
                "state": "passed", "reason": None,
                "renderer": {"name": "neutral-renderer", "version": "1",
                             "profile_id": "neutral-profile", "fonts": None},
                "command": ["neutral-renderer", "--export"], "exit_code": 0,
                "input_before_sha256": self.sha(candidate_graph),
                "input_after_sha256": self.sha(candidate_graph),
                "full_preview_sha256": image_sha, "stdout": None, "stderr": None,
                "calibration": {
                    "calibration_version": 1, "id": "neutral-candidate-calibration",
                    "profile_id": "neutral-profile", "artifact_sha256": self.sha(candidate_graph),
                    "preview_sha256": image_sha, "method": "externally_observed_anchors",
                    "anchors": anchors, "tolerance_px": 2.0,
                },
            },
            "previews": [{
                "id": "full", "kind": "full", "name": "full.png", "sha256": image_sha,
                "bytes": len(image), "width": 601, "height": 600,
                "mapping": {"mapping_version": 1, "kind": "axis_aligned", "scale_x": 1.0,
                            "scale_y": 1.0, "translate_x": 0.0, "translate_y": 0.0,
                            "calibration_id": "neutral-candidate-calibration"},
            }],
            "reviews": {
                "agent": {
                    "state": "passed", "reason": None,
                    "reviewer": {"kind": "agent", "name": "Protocol fixture", "host": None,
                                 "model": None, "tool_receipt_ids": []},
                    "full_image_viewed": True, "viewed_previews": ["full"], "issues": [],
                },
                "human": {"state": "not_run", "reason": None, "reviewer": None,
                          "full_image_viewed": False, "viewed_previews": [], "issues": []},
            },
        }
        if report_mutator is not None:
            report_mutator(report, image_sha, bounds)
        report_path = evidence / "report.json"
        report_path.write_bytes(self.bundle.bundle_json(report))
        record = root / "candidate-record"
        record_args = [
            "review", "record", "--prepared", str(prepared_manifest),
            "--expected-prepared-sha256", self.sha(prepared_manifest),
            "--input", str(candidate_graph),
            "--expected-input-sha256", self.sha(candidate_graph),
        ]
        if chain["context"] is not None:
            record_args.extend([
                "--context", str(candidate_context),
                "--expected-context-sha256", self.sha(candidate_context),
                "--input-completion-manifest", str(candidate_context_manifest),
            ])
        record_args.extend([
            "--evidence-dir", str(evidence), "--report", str(report_path),
            "--expected-report-sha256", self.sha(report_path), "--output", str(record),
        ])
        self.assert_success(run_tool(*record_args))
        return prepared, record, image_sha

    def repair_chain(self, root: Path, chain: dict) -> tuple[Path, Path, dict]:
        plan = root / "plan"
        self.assert_success(run_tool(
            "review", "plan", *self.common_args(chain), "--output", str(plan)))
        candidate = root / "candidate"
        repaired = self.assert_success(run_tool(
            "review", "repair", *self.common_args(chain),
            "--plan", str(plan / "completion.json"),
            "--expected-plan-sha256", self.sha(plan / "completion.json"),
            "--output", str(candidate),
        ))
        return plan, candidate, repaired

    def resolution_data(self, chain: dict, claim_sha: str, candidate_record: Path,
                        preview_sha: str, **changes) -> dict:
        item = {
            "issue_key": {"code": "visual/edge-label-collision",
                          "targets": [{"kind": "edge", "id": "e0"}]},
            "previous_issue_ids": ["label-e0"], "disposition": "resolved",
            "candidate_issue_ids": [], "preview_id": "full",
            "preview_sha256": preview_sha,
            "region": {"x": 100.0, "y": 100.0, "width": 100.0, "height": 200.0},
            "observation": "Declared candidate region no longer reports the prior issue",
            "uncertainty": None,
        }
        item.update(changes)
        return {
            "issue_resolution_version": 1, "run_id": chain["run_id"],
            "claim_sha256": claim_sha,
            "parent_record_sha256": self.sha(chain["record_manifest"]),
            "candidate_record_sha256": self.sha(candidate_record / "completion.json"),
            "reviewer_kind": "agent", "items": [item],
        }

    def assessment_args(self, chain: dict, plan: Path, candidate: Path,
                        candidate_prepared: Path, candidate_record: Path,
                        claim_sha: str, resolution_path: Path, output: Path,
                        *, attempt: int = 1) -> list[str]:
        args = [
            "review", "assess",
            "--prepared", str(chain["prepared_manifest"]),
            "--expected-prepared-sha256", self.sha(chain["prepared_manifest"]),
            "--parent-prepared", str(chain["prepared_manifest"]),
            "--expected-parent-prepared-sha256", self.sha(chain["prepared_manifest"]),
            "--parent-record", str(chain["record_manifest"]),
            "--expected-parent-record-sha256", self.sha(chain["record_manifest"]),
            "--plan", str(plan / "completion.json"),
            "--expected-plan-sha256", self.sha(plan / "completion.json"),
            "--authorization", str(chain["authorization"]),
            "--expected-authorization-sha256", self.sha(chain["authorization"]),
            "--before", str(chain["graph"]), "--expected-before-sha256", chain["graph_sha"],
            "--attempt", str(attempt), "--claim-sha256", claim_sha,
            "--candidate", str(candidate / "completion.json"),
            "--expected-candidate-sha256", self.sha(candidate / "completion.json"),
            "--candidate-prepared", str(candidate_prepared / "completion.json"),
            "--expected-candidate-prepared-sha256", self.sha(candidate_prepared / "completion.json"),
            "--record", str(candidate_record / "completion.json"),
            "--expected-record-sha256", self.sha(candidate_record / "completion.json"),
            "--resolutions", str(resolution_path),
            "--expected-resolutions-sha256", self.sha(resolution_path),
            "--output", str(output),
        ]
        if chain["context"] is not None:
            args.extend([
                "--before-context", str(chain["context"]),
                "--expected-before-context-sha256", chain["context_sha"],
                "--before-completion-manifest", str(chain["context_manifest"]),
            ])
        return args

    def test_plan_then_repair_commits_candidate_after_claim_and_preserves_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root)
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in (chain["graph"], chain["prepared_manifest"],
                                   chain["record_manifest"], chain["authorization"])}
            plan_dir = root / "plan"
            plan_body = self.assert_success(run_tool(
                "review", "plan", *self.common_args(chain), "--output", str(plan_dir)))
            self.assertTrue(plan_body["plan"]["can_repair"])
            self.assertFalse((root / f'.review-state-{chain["run_id"]}').exists())

            candidate = root / "candidate"
            repair_body = self.assert_success(run_tool(
                "review", "repair", *self.common_args(chain),
                "--plan", str(plan_dir / "completion.json"),
                "--expected-plan-sha256", self.sha(plan_dir / "completion.json"),
                "--output", str(candidate),
            ))
            self.assertTrue(repair_body["attempt_consumed"])
            self.assertTrue(repair_body["delivery"]["complete"])
            ledger = root / f'.review-state-{chain["run_id"]}'
            self.assertTrue((ledger / "attempt-1/completion.json").is_file())
            self.assertTrue((ledger / "attempt-1/result/completion.json").is_file())
            self.assertFalse((ledger / "attempt-1/assessment").exists())
            metadata = json.loads((candidate / "candidate.json").read_text())
            self.assertFalse(metadata["accepted"])
            self.assertEqual(metadata["technical_status"], "passed")
            self.assertNotEqual(metadata["artifact_sha256"], chain["graph_sha"])
            after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}
            self.assertEqual(after, before)

    def test_plan_is_byte_deterministic_and_action_flags_are_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root)
            first = root / "plan-a"
            second = root / "plan-b"
            for output in (first, second):
                self.assert_success(run_tool(
                    "review", "plan", *self.common_args(chain), "--output", str(output)))
            self.assertEqual(
                {path.relative_to(first): path.read_bytes() for path in first.rglob("*") if path.is_file()},
                {path.relative_to(second): path.read_bytes() for path in second.rglob("*") if path.is_file()},
            )
            ledger = root / f'.review-state-{chain["run_id"]}'
            self.assertFalse(ledger.exists())

            wrong_flags = [
                ("plan", ["--plan", str(first / "completion.json")]),
                ("repair", ["--before", str(chain["graph"])]),
                ("prepare", ["--authorization", str(chain["authorization"])]),
                ("record", ["--candidate", str(first / "completion.json")]),
            ]
            for action, extra in wrong_flags:
                with self.subTest(action=action):
                    output = root / ("wrong-" + action)
                    result = run_tool("review", action, *extra, "--output", str(output))
                    self.assert_error(result, "input/invalid")
                    self.assertFalse(output.exists())
                    self.assertFalse(ledger.exists())

    def test_authorization_bounds_and_identity_reject_before_state(self) -> None:
        cases = [
            ("bool-attempts", lambda data: data.__setitem__("maximum_attempts", True), "schema/type"),
            ("wrong-original", lambda data: data.__setitem__("original_artifact_sha256", "f" * 64),
             "review/authorization-mismatch"),
            ("empty-actions", lambda data: data.__setitem__("actions", []), "review/resource-limit"),
            ("too-many", lambda data: data.__setitem__("actions", data["actions"] * 33),
             "review/resource-limit"),
            ("non-edge", lambda data: data["actions"][0].__setitem__(
                "target", {"kind": "node", "id": "n0"}), "review/action-not-authorized"),
            ("duplicate-intent", lambda data: data["actions"][0].__setitem__(
                "intents", ["reposition-edge-label", "reposition-edge-label"]), "schema/duplicate"),
        ]
        for name, mutate, code in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                chain = self.initial_chain(root)
                authorization = json.loads(chain["authorization"].read_text())
                mutate(authorization)
                chain["authorization"].write_bytes(self.bundle.bundle_json(authorization))
                output = root / "plan"
                result = run_tool(
                    "review", "plan", *self.common_args(chain), "--output", str(output))
                self.assert_error(result, code)
                self.assertFalse(output.exists())
                self.assertFalse((root / f'.review-state-{chain["run_id"]}').exists())

    def test_known_candidate_output_collision_rejects_before_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root)
            plan = root / "plan"
            self.assert_success(run_tool(
                "review", "plan", *self.common_args(chain), "--output", str(plan)))
            candidate = root / "candidate"
            candidate.mkdir()
            result = run_tool(
                "review", "repair", *self.common_args(chain),
                "--plan", str(plan / "completion.json"),
                "--expected-plan-sha256", self.sha(plan / "completion.json"),
                "--output", str(candidate),
            )
            body = self.assert_error(result, "delivery/output-exists")
            self.assertNotIn("attempt_consumed", body["diagnostics"][0]["evidence"])
            self.assertFalse((root / f'.review-state-{chain["run_id"]}').exists())

    def test_candidate_delivery_failure_after_claim_is_truthful_and_restart_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root)
            plan = root / "plan"
            self.assert_success(run_tool(
                "review", "plan", *self.common_args(chain), "--output", str(plan)))
            argv = [
                "review", "repair", *self.common_args(chain),
                "--plan", str(plan / "completion.json"),
                "--expected-plan-sha256", self.sha(plan / "completion.json"),
                "--output", str(root / "candidate"),
            ]
            args = self.loaded.tool.build_parser().parse_args(argv)
            original_publish = self.loaded.review_state.state_package_publish

            def fail_candidate(directory, kind, run_id, members, inputs):
                if kind == "candidate":
                    raise self.loaded.contracts.DiagramError(
                        "Injected candidate member failure", code="delivery/io-error",
                        evidence={"delivery": {
                            "stage": "write-member", "committed": False, "complete": False,
                            "members_written": [], "failed_member": "artifact/diagram.drawio",
                            "residue": [{"name": "artifact/diagram.drawio", "bytes": 17}],
                        }},
                    )
                return original_publish(directory, kind, run_id, members, inputs)

            with mock.patch.object(
                    self.loaded.review_state, "state_package_publish", side_effect=fail_candidate):
                with self.assertRaises(self.loaded.contracts.DiagramError) as raised:
                    self.loaded.review_cycle.run_review_cycle(args)
            self.assertEqual(raised.exception.code, "delivery/io-error")
            evidence = raised.exception.evidence
            self.assertTrue(evidence["attempt_consumed"])
            self.assertEqual(evidence["attempt_result"]["status"], "delivery_failed")
            self.assertEqual(evidence["attempt_result"]["delivery"]["failed_member"],
                             "artifact/diagram.drawio")
            self.assertTrue(evidence["result_delivery"]["complete"])
            ledger = root / f'.review-state-{chain["run_id"]}'
            self.assertTrue((ledger / "attempt-1/completion.json").is_file())
            self.assertTrue((ledger / "attempt-1/result/completion.json").is_file())
            replay = run_tool(*argv[:-1], str(root / "candidate-replay"))
            self.assert_error(replay, "review/attempt-pending")

    def test_native_waypoint_refusal_and_stale_authorization_happen_before_claim(self) -> None:
        for mode in ("waypoint", "authorization"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                chain = self.initial_chain(
                    root, waypoint_origin="explicit" if mode == "waypoint" else "automatic",
                    repair_kind="route" if mode == "waypoint" else "label",
                )
                plan_dir = root / "plan"
                plan_body = self.assert_success(run_tool(
                    "review", "plan", *self.common_args(chain), "--output", str(plan_dir)))
                if mode == "waypoint":
                    self.assertFalse(plan_body["plan"]["can_repair"])
                    result = run_tool(
                        "review", "repair", *self.common_args(chain),
                        "--plan", str(plan_dir / "completion.json"),
                        "--expected-plan-sha256", self.sha(plan_dir / "completion.json"),
                        "--output", str(root / "candidate"),
                    )
                    self.assert_error(result, "review/candidate-ineligible")
                else:
                    chain["authorization"].write_bytes(chain["authorization"].read_bytes() + b" ")
                    result = run_tool(
                        "review", "repair", *self.common_args(chain),
                        "--plan", str(plan_dir / "completion.json"),
                        "--expected-plan-sha256", self.sha(plan_dir / "completion.json"),
                        "--output", str(root / "candidate"),
                    )
                    self.assert_error(result, "review/plan-mismatch")
                self.assertFalse((root / f'.review-state-{chain["run_id"]}').exists())

    def test_clean_candidate_assessment_advances_both_pointers_from_explicit_before_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root)
            plan = root / "plan"
            self.assert_success(run_tool(
                "review", "plan", *self.common_args(chain), "--output", str(plan)))
            candidate = root / "candidate"
            repaired = self.assert_success(run_tool(
                "review", "repair", *self.common_args(chain),
                "--plan", str(plan / "completion.json"),
                "--expected-plan-sha256", self.sha(plan / "completion.json"),
                "--output", str(candidate),
            ))
            claim_sha = repaired["claim_sha256"]
            candidate_prepared, candidate_record, preview_sha = self.candidate_record(
                root, chain, candidate, claim_sha)
            resolutions = {
                "issue_resolution_version": 1, "run_id": chain["run_id"],
                "claim_sha256": claim_sha,
                "parent_record_sha256": self.sha(chain["record_manifest"]),
                "candidate_record_sha256": self.sha(candidate_record / "completion.json"),
                "reviewer_kind": "agent",
                "items": [{
                    "issue_key": {"code": "visual/edge-label-collision",
                                  "targets": [{"kind": "edge", "id": "e0"}]},
                    "previous_issue_ids": ["label-e0"], "disposition": "resolved",
                    "candidate_issue_ids": [], "preview_id": "full",
                    "preview_sha256": preview_sha,
                    "region": {"x": 100.0, "y": 100.0, "width": 100.0, "height": 200.0},
                    "observation": "Declared candidate region no longer reports the prior issue",
                    "uncertainty": None,
                }],
            }
            resolution_path = root / "resolutions.json"
            resolution_path.write_bytes(self.bundle.bundle_json(resolutions))
            assessment = root / "assessment"
            candidate_manifest = candidate / "completion.json"
            candidate_graph = candidate / "artifact/diagram.drawio"
            result = run_tool(
                "review", "assess",
                "--prepared", str(chain["prepared_manifest"]),
                "--expected-prepared-sha256", self.sha(chain["prepared_manifest"]),
                "--parent-prepared", str(chain["prepared_manifest"]),
                "--expected-parent-prepared-sha256", self.sha(chain["prepared_manifest"]),
                "--parent-record", str(chain["record_manifest"]),
                "--expected-parent-record-sha256", self.sha(chain["record_manifest"]),
                "--plan", str(plan / "completion.json"),
                "--expected-plan-sha256", self.sha(plan / "completion.json"),
                "--authorization", str(chain["authorization"]),
                "--expected-authorization-sha256", self.sha(chain["authorization"]),
                "--before", str(chain["graph"]), "--expected-before-sha256", chain["graph_sha"],
                "--attempt", "1", "--claim-sha256", claim_sha,
                "--candidate", str(candidate_manifest),
                "--expected-candidate-sha256", self.sha(candidate_manifest),
                "--candidate-prepared", str(candidate_prepared / "completion.json"),
                "--expected-candidate-prepared-sha256", self.sha(candidate_prepared / "completion.json"),
                "--record", str(candidate_record / "completion.json"),
                "--expected-record-sha256", self.sha(candidate_record / "completion.json"),
                "--resolutions", str(resolution_path),
                "--expected-resolutions-sha256", self.sha(resolution_path),
                "--output", str(assessment),
            )
            body = self.assert_success(result)
            self.assertEqual(body["assessment"]["decision"], "accepted")
            self.assertEqual(set(body["assessment"]["checks"].values()), {"passed"})
            self.assertEqual(body["assessment"]["last_accepted"], body["assessment"]["last_good"])
            self.assertFalse(body["assessment"]["continue"])
            marker = root / f'.review-state-{chain["run_id"]}/attempt-1/assessment/completion.json'
            self.assertTrue(marker.is_file())

    def test_geometry_only_context_rebind_preserves_unconfirmed_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root, with_context=True)
            inputs = (chain["graph"], chain["context"], chain["context_manifest"],
                      chain["prepared_manifest"], chain["record_manifest"], chain["authorization"])
            before_inputs = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in inputs}
            original_context = json.loads(chain["context"].read_text())
            plan, candidate, repaired = self.repair_chain(root, chain)
            metadata = json.loads((candidate / "candidate.json").read_text())
            candidate_context_path = candidate / "artifact/context.json"
            candidate_context = json.loads(candidate_context_path.read_text())
            self.assertTrue(metadata["source_preserved"])
            self.assertEqual(metadata["semantic_context"]["source_gate"], "incomplete")
            self.assertEqual(
                {key: value for key, value in original_context.items() if key != "artifact"},
                {key: value for key, value in candidate_context.items() if key != "artifact"},
            )
            self.assertEqual(candidate_context["artifact"]["model_hash"],
                             original_context["artifact"]["model_hash"])
            self.assertEqual(candidate_context["artifact"]["sha256"],
                             self.sha(candidate / "artifact/diagram.drawio"))
            self.assertNotEqual(candidate_context["artifact"]["sha256"], chain["graph_sha"])
            inner = json.loads((candidate / "artifact/completion.json").read_text())
            self.assertEqual([item["role"] for item in inner["members"]], ["diagram", "context"])

            prepared, record, preview_sha = self.candidate_record(
                root, chain, candidate, repaired["claim_sha256"])
            resolutions = self.resolution_data(
                chain, repaired["claim_sha256"], record, preview_sha)
            resolution_path = root / "resolutions.json"
            resolution_path.write_bytes(self.bundle.bundle_json(resolutions))
            result = self.assert_success(run_tool(*self.assessment_args(
                chain, plan, candidate, prepared, record, repaired["claim_sha256"],
                resolution_path, root / "assessment")))
            self.assertEqual(result["assessment"]["decision"], "accepted")
            self.assertEqual(result["assessment"]["last_accepted"]["context_sha256"],
                             self.sha(candidate_context_path))
            self.assertEqual(
                {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in inputs},
                before_inputs,
            )

    def test_resolution_shape_alias_and_preview_reference_fail_before_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root)
            plan, candidate, repaired = self.repair_chain(root, chain)
            prepared, record, preview_sha = self.candidate_record(
                root, chain, candidate, repaired["claim_sha256"])
            valid = self.resolution_data(
                chain, repaired["claim_sha256"], record, preview_sha)
            cases = [
                ("preview-type", lambda value: value["items"][0].__setitem__("preview_id", []),
                 "schema/type"),
                ("missing-item", lambda value: value.__setitem__("items", []),
                 "review/resolution-incomplete"),
                ("wrong-previous", lambda value: value["items"][0].__setitem__(
                    "previous_issue_ids", ["renamed-only"]), "review/resolution-invalid"),
                ("invented-candidate", lambda value: value["items"][0].__setitem__(
                    "candidate_issue_ids", ["label-e0"]), "review/resolution-invalid"),
                ("duplicate-target", lambda value: value["items"][0]["issue_key"].__setitem__(
                    "targets", [{"kind": "edge", "id": "e0"}] * 2), "schema/duplicate"),
            ]
            ledger_marker = root / f'.review-state-{chain["run_id"]}/attempt-1/assessment'
            for name, mutate, code in cases:
                with self.subTest(name=name):
                    changed = copy.deepcopy(valid)
                    mutate(changed)
                    sidecar = root / ("resolutions-" + name + ".json")
                    sidecar.write_bytes(self.bundle.bundle_json(changed))
                    output = root / ("assessment-" + name)
                    result = run_tool(*self.assessment_args(
                        chain, plan, candidate, prepared, record, repaired["claim_sha256"],
                        sidecar, output))
                    self.assert_error(result, code)
                    self.assertFalse(output.exists())
                    self.assertFalse(ledger_marker.exists())

    def test_uncertainty_and_renderer_drift_stop_without_candidate_pointers(self) -> None:
        for mode in ("uncertain", "environment"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                chain = self.initial_chain(root)
                plan, candidate, repaired = self.repair_chain(root, chain)

                def mutate(report, _preview_sha, _bounds):
                    if mode == "environment":
                        report["export"]["renderer"]["version"] = "2"

                prepared, record, preview_sha = self.candidate_record(
                    root, chain, candidate, repaired["claim_sha256"], mutate)
                changes = ({"disposition": "uncertain", "uncertainty": "Visual evidence is inconclusive"}
                           if mode == "uncertain" else {})
                resolutions = self.resolution_data(
                    chain, repaired["claim_sha256"], record, preview_sha, **changes)
                sidecar = root / "resolutions.json"
                sidecar.write_bytes(self.bundle.bundle_json(resolutions))
                body = self.assert_success(run_tool(*self.assessment_args(
                    chain, plan, candidate, prepared, record, repaired["claim_sha256"],
                    sidecar, root / "assessment")))
                assessment = body["assessment"]
                self.assertEqual(assessment["decision"], "stopped")
                self.assertEqual(assessment["stop_reason"],
                                 "uncertain_resolution" if mode == "uncertain" else "environment_mismatch")
                self.assertIsNone(assessment["last_accepted"])
                self.assertIsNone(assessment["last_good"])
                self.assertFalse(assessment["continue"])

    def test_same_severity_unchanged_issue_cannot_claim_metric_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root)
            plan, candidate, repaired = self.repair_chain(root, chain)

            def keep_issue(report, preview_sha, bounds):
                report["reviews"]["agent"]["state"] = "failed"
                report["reviews"]["agent"]["issues"] = [{
                    "issue_id": "candidate-label-e0", "code": "visual/edge-label-collision",
                    "severity": "warning", "targets": [{"kind": "edge", "id": "e0"}],
                    "preview_id": "full", "preview_sha256": preview_sha,
                    "region": {key: float(bounds[key]) for key in ("x", "y", "width", "height")},
                    "observation": "Externally declared unchanged protocol observation",
                    "suggested_intent": "reposition-edge-label", "binding": "bound",
                    "uncertainty": None,
                }]

            prepared, record, preview_sha = self.candidate_record(
                root, chain, candidate, repaired["claim_sha256"], keep_issue)
            resolutions = self.resolution_data(
                chain, repaired["claim_sha256"], record, preview_sha,
                disposition="unchanged", candidate_issue_ids=["candidate-label-e0"])
            sidecar = root / "resolutions.json"
            sidecar.write_bytes(self.bundle.bundle_json(resolutions))
            body = self.assert_success(run_tool(*self.assessment_args(
                chain, plan, candidate, prepared, record, repaired["claim_sha256"],
                sidecar, root / "assessment")))
            assessment = body["assessment"]
            self.assertEqual(assessment["decision"], "rejected")
            self.assertEqual(assessment["stop_reason"], "metric_improvement_failed")
            self.assertIsNone(assessment["last_accepted"])
            self.assertIsNone(assessment["last_good"])

    def test_second_round_requires_committed_parent_and_cannot_open_a_third_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root, repair_kind="route")
            plan1, candidate1, repaired1 = self.repair_chain(root, chain)

            def retain_repairable_issue(report, preview_sha, bounds):
                report["reviews"]["agent"]["state"] = "failed"
                report["reviews"]["agent"]["issues"] = [{
                    "issue_id": "candidate-route-e0", "code": "visual/excessive-detour",
                    "severity": "warning", "targets": [{"kind": "edge", "id": "e0"}],
                    "preview_id": "full", "preview_sha256": preview_sha,
                    "region": {key: float(bounds[key]) for key in ("x", "y", "width", "height")},
                    "observation": "Externally declared remaining repairable issue",
                    "suggested_intent": "reroute-edge", "binding": "bound",
                    "uncertainty": None,
                }]

            prepared1, record1, preview_sha = self.candidate_record(
                root, chain, candidate1, repaired1["claim_sha256"], retain_repairable_issue)
            resolutions1 = self.resolution_data(
                chain, repaired1["claim_sha256"], record1, preview_sha,
                issue_key={"code": "visual/excessive-detour",
                           "targets": [{"kind": "edge", "id": "e0"}]},
                previous_issue_ids=["route-e0"], disposition="improved",
                candidate_issue_ids=["candidate-route-e0"])
            resolutions_path = root / "resolutions-1.json"
            resolutions_path.write_bytes(self.bundle.bundle_json(resolutions1))
            assessment1 = root / "assessment-1"
            body1 = self.assert_success(run_tool(*self.assessment_args(
                chain, plan1, candidate1, prepared1, record1, repaired1["claim_sha256"],
                resolutions_path, assessment1)))
            self.assertEqual(body1["assessment"]["decision"], "accepted")
            self.assertTrue(body1["assessment"]["continue"], body1)
            self.assertIsNotNone(body1["assessment"]["last_accepted"])
            self.assertIsNone(body1["assessment"]["last_good"])

            candidate_graph = candidate1 / "artifact/diagram.drawio"
            second_common = [
                "--prepared", str(chain["prepared_manifest"]),
                "--expected-prepared-sha256", self.sha(chain["prepared_manifest"]),
                "--parent-prepared", str(prepared1 / "completion.json"),
                "--expected-parent-prepared-sha256", self.sha(prepared1 / "completion.json"),
                "--record", str(record1 / "completion.json"),
                "--expected-record-sha256", self.sha(record1 / "completion.json"),
                "--input", str(candidate_graph),
                "--expected-input-sha256", self.sha(candidate_graph),
                "--authorization", str(chain["authorization"]),
                "--expected-authorization-sha256", self.sha(chain["authorization"]),
                "--parent-assessment", str(assessment1 / "completion.json"),
                "--expected-assessment-sha256", self.sha(assessment1 / "completion.json"),
            ]
            plan2 = root / "plan-2"
            plan2_body = self.assert_success(run_tool(
                "review", "plan", *second_common, "--output", str(plan2)))
            self.assertEqual(plan2_body["plan"]["attempt_index"], 2)
            self.assertEqual(plan2_body["plan"]["parent_assessment_sha256"],
                             self.sha(assessment1 / "completion.json"))
            self.assertTrue(plan2_body["plan"]["can_repair"])

            candidate2 = root / "candidate-2"
            second_repair = run_tool(
                "review", "repair", *second_common,
                "--plan", str(plan2 / "completion.json"),
                "--expected-plan-sha256", self.sha(plan2 / "completion.json"),
                "--output", str(candidate2),
            )
            self.assertEqual(second_repair.returncode, 1, second_repair.stdout.decode())
            second_body = json.loads(second_repair.stdout)
            self.assertTrue(second_body["attempt_consumed"])
            self.assertEqual(second_body["result"]["attempt_index"], 2)
            ledger = root / f'.review-state-{chain["run_id"]}'
            self.assertTrue((ledger / "attempt-2/completion.json").is_file())
            self.assertFalse((ledger / "attempt-3").exists())

            repeated = run_tool(
                "review", "repair", *second_common,
                "--plan", str(plan2 / "completion.json"),
                "--expected-plan-sha256", self.sha(plan2 / "completion.json"),
                "--output", str(root / "candidate-3"),
            )
            self.assert_error(repeated, "review/attempt-pending")
            self.assertFalse((ledger / "attempt-3").exists())

    def test_minimal_stop_records_no_candidate_pointer_and_rejects_mixed_acceptance_args(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root)
            plan = root / "plan"
            self.assert_success(run_tool(
                "review", "plan", *self.common_args(chain), "--output", str(plan)))
            candidate = root / "candidate"
            repaired = self.assert_success(run_tool(
                "review", "repair", *self.common_args(chain),
                "--plan", str(plan / "completion.json"),
                "--expected-plan-sha256", self.sha(plan / "completion.json"),
                "--output", str(candidate),
            ))
            base = [
                "review", "assess", "--prepared", str(chain["prepared_manifest"]),
                "--expected-prepared-sha256", self.sha(chain["prepared_manifest"]),
                "--authorization", str(chain["authorization"]),
                "--expected-authorization-sha256", self.sha(chain["authorization"]),
                "--attempt", "1", "--claim-sha256", repaired["claim_sha256"],
                "--stop-reason", "incomplete-attempt",
            ]
            mixed_output = root / "mixed-stop"
            mixed = run_tool(*base, "--candidate", str(candidate / "completion.json"),
                             "--output", str(mixed_output))
            self.assert_error(mixed, "input/invalid")
            self.assertFalse(mixed_output.exists())

            stopped = self.assert_success(run_tool(*base, "--output", str(root / "stopped")))
            assessment = stopped["assessment"]
            self.assertEqual(assessment["decision"], "stopped")
            self.assertEqual(assessment["stop_reason"], "incomplete-attempt")
            self.assertEqual(set(assessment["checks"].values()), {"not_assessed"})
            self.assertIsNone(assessment["candidate_sha256"])
            self.assertIsNone(assessment["last_accepted"])
            self.assertIsNone(assessment["last_good"])
            self.assertFalse(assessment["continue"])

    def test_cycle_rejects_non_object_plan_and_v2_record_roots_before_side_effects(self) -> None:
        roots = {
            "empty-array": b"[]\n", "array": b"[1]\n", "null": b"null\n",
            "string": b'"text"\n', "integer": b"1\n", "float": b"1.5\n",
            "true": b"true\n", "false": b"false\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            chain = self.initial_chain(root)
            tracked = (chain["graph"], chain["prepared_manifest"],
                       chain["record_manifest"], chain["authorization"])
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tracked}
            ledger = root / f'.review-state-{chain["run_id"]}'
            prepared_input = self.bundle.bundle_read_input(chain["prepared_manifest"])

            for name, raw in roots.items():
                with self.subTest(path="plan", root=name):
                    malformed = root / f"invalid-plan-{name}"
                    malformed.mkdir()
                    manifest = malformed / "completion.json"
                    manifest.write_bytes(raw)
                    args = self.common_args(chain)
                    args[args.index("--prepared") + 1] = str(manifest)
                    args[args.index("--expected-prepared-sha256") + 1] = hashlib.sha256(raw).hexdigest()
                    output = root / f"plan-output-{name}"
                    result = run_tool("review", "plan", *args, "--output", str(output))
                    self.assert_error(result, "schema/type")
                    self.assertFalse(output.exists())
                    self.assertFalse(ledger.exists())

                with self.subTest(path="v2-record", root=name):
                    package = root / f"invalid-v2-prepared-{name}"
                    members = [
                        self.loaded.review_workflow.review_member("run.json", raw),
                        self.loaded.review_workflow.review_member("objects.json", b"{}\n"),
                        self.loaded.review_workflow.review_member(
                            "initial-prepared.json", chain["prepared_manifest"].read_bytes()),
                        self.loaded.review_workflow.review_member(
                            "candidate-manifest.json", chain["prepared_manifest"].read_bytes()),
                        self.loaded.review_workflow.review_member("candidate.json", b"{}\n"),
                        self.loaded.review_workflow.review_member(
                            "candidate/diagram.drawio", chain["graph"].read_bytes(),
                            limit=self.loaded.review_workflow.REVIEW_GRAPH_BYTES),
                    ]
                    with self.assertRaises(self.loaded.contracts.DiagramError) as raised:
                        self.loaded.review_state.state_package_publish(
                            package, "candidate-prepared", chain["run_id"], members,
                            [prepared_input],
                        )
                    self.assertEqual(raised.exception.code, "schema/type")
                    package_manifest = package / "completion.json"
                    self.assertTrue(package_manifest.is_file())
                    evidence = root / f"invalid-v2-evidence-{name}"
                    evidence.mkdir()
                    report = evidence / "report.json"
                    report.write_bytes(b"{}\n")
                    output = root / f"record-output-{name}"
                    result = run_tool(
                        "review", "record", "--prepared", str(package_manifest),
                        "--expected-prepared-sha256", self.sha(package_manifest),
                        "--input", str(chain["graph"]),
                        "--expected-input-sha256", chain["graph_sha"],
                        "--evidence-dir", str(evidence), "--report", str(report),
                        "--expected-report-sha256", self.sha(report),
                        "--output", str(output),
                    )
                    self.assert_error(result, "schema/type")
                    self.assertFalse(output.exists())
                    self.assertFalse(ledger.exists())

            self.assertEqual(
                {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in tracked}, before)


if __name__ == "__main__":
    unittest.main()
