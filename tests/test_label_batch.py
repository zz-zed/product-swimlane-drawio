"""Native label placement, finite repair, and all-or-nothing batch contracts."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules


class LabelBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(
            ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py", module_name="label_batch_tests")
        cls.labels = cls.loaded.labels

    def item(self, edge_id="a", text="Label"):
        def terminal(left, right):
            return {"present": True, "identity_matches": True, "style": "rounded=0;",
                    "type": "process", "bounds": {"left": left, "right": right,
                    "top": 80, "bottom": 120, "width": right-left, "height": 40}}
        profile = {
            "text": text, "edge_style": "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;fontSize=11;",
            "path": [], "geometry_count": 1, "scene_available": True,
            "terminals": {"exit": terminal(-20, 0), "entry": terminal(300, 320)},
            "native_geometry": {"attributes": {"relative": "1"}, "offsets": [], "points_arrays": []},
            "parent_origin": (0, 0),
        }
        assignment = {"exit_side": "right", "entry_side": "left", "exit_offset": .5, "entry_offset": .5}
        native = self.labels.preflight_candidate(profile, assignment, [])
        self.assertEqual(native["path_status"], "available")
        return {"id": edge_id, "text": text, "route": "side", "assignment": assignment,
                "hints": [], "profile": profile, "native": native}

    @staticmethod
    def box(left, top, width, height):
        return {"left": left, "right": left+width, "top": top, "bottom": top+height,
                "width": width, "height": height}

    def options(self, item, lefts):
        width, height = item["native"]["size"]
        return [(0, self.box(left, 70, width, height), 1000) for left in lefts]

    def test_native_batch_uses_real_size_and_is_deterministic_and_readonly(self):
        item = self.item()
        paths = {"a": item["native"]["path"]}
        snapshot = copy.deepcopy((item, paths))
        results = [self.labels.plan_label_batch([item], paths, {}, None) for _ in range(3)]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        result = results[0]
        self.assertEqual(result["status"], "complete")
        choice = result["choices"]["a"]
        measured = self.labels.preflight_candidate(item["profile"], item["assignment"], [], choice)
        self.assertEqual(choice, (measured["carrier_segment"], measured["bounds"]))
        self.assertEqual((choice[1]["width"], choice[1]["height"]), item["native"]["size"])
        self.assertEqual((item, paths), snapshot)

    def test_sorted_candidates_preserve_legacy_side_and_source_preferences(self):
        item = self.item()
        points = [(0, 0), (0, 200), (300, 200)]
        for side in (None, "left", "right"):
            for proximity in (False, True):
                options = self.labels.ordered_label_box_candidates(points, "Label", side, proximity)
                chosen = self.labels.choose_label_box(points, "Label", [], [], [], side, None, proximity)
                # No obstacle except the own path: test the expected first clear
                # option independently with the legacy 2px own-line rule.
                first = next((index, box) for index, box, _ in options
                             if not self.labels.label_path_conflicts(
                                 "a", index, box, {"a": list(zip(points, points[1:]))}, gap=2))
                self.assertEqual(chosen, first)

    def test_frozen_labels_and_later_routes_block_first_position(self):
        item = self.item()
        options = self.options(item, [50, 200])
        path = item["native"]["path"]
        blocking_path = [(60, 60), (60, 95)]
        with mock.patch.object(self.labels, "ordered_label_box_candidates", return_value=options):
            result = self.labels.plan_label_batch([item], {"a": path, "later": blocking_path}, {}, None)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["choices"]["a"][1]["left"], 200)
        with mock.patch.object(self.labels, "ordered_label_box_candidates", return_value=options):
            result = self.labels.plan_label_batch([item], {"a": path}, {}, None,
                                                   frozen_labels={"saved": options[0][1]})
        self.assertEqual(result["choices"]["a"][1]["left"], 200)

    def test_visible_no_carrier_fails_instead_of_returning_none(self):
        item = self.item(text="W" * 100)
        result = self.labels.plan_label_batch([item], {"a": item["native"]["path"]}, {}, None)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["choices"], {})
        self.assertEqual(result["failure"]["reason"], "label_placement_unavailable")
        self.assertEqual(result["generated_candidates"], 0)

    def test_hidden_and_empty_labels_are_not_applicable_but_bad_path_is_not(self):
        for text in ("", "Label"):
            item = self.item(text=text)
            if text:
                item["profile"]["edge_style"] += "noLabel=1;"
                item["native"] = self.labels.preflight_candidate(item["profile"], item["assignment"], [])
            result = self.labels.plan_label_batch([item], {"a": item["native"]["path"]}, {}, None)
            self.assertEqual(result["choices"], {"a": None})
            item["native"]["path_status"] = "not_available"
            item["native"]["path_reason"] = "editor_router_additional_turns_required"
            failed = self.labels.plan_label_batch([item], {"a": item["native"]["path"]}, {}, None)
            self.assertEqual(failed["failure"]["native_reason"], "editor_router_additional_turns_required")

    def test_actual_native_unavailability_and_path_mismatch_are_explicit(self):
        item = self.item()
        item["profile"]["edge_style"] += "customToken=1;"
        result = self.labels.plan_label_batch([item], {"a": item["native"]["path"]}, {}, None)
        self.assertEqual(result["failure"]["reason"], "native_profile_unavailable")
        self.assertEqual(result["failure"]["native_reason"], "unsupported_style:customToken")
        item = self.item()
        result = self.labels.plan_label_batch([item], {"a": [(1, 2), (3, 4)]}, {}, None)
        self.assertEqual(result["failure"]["reason"], "native_path_mismatch")

    def test_double_label_repair_moves_one_actual_blocker_atomically(self):
        first, second = self.item("a"), self.item("b")
        options = [self.options(first, [50, 200]), self.options(second, [50])]
        snapshot = copy.deepcopy((first, second))
        with mock.patch.object(self.labels, "ordered_label_box_candidates", side_effect=options):
            result = self.labels.plan_label_batch([first, second], {
                "a": first["native"]["path"], "b": second["native"]["path"]}, {}, None)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["choices"]["a"][1]["left"], 200)
        self.assertEqual(result["choices"]["b"][1]["left"], 50)
        self.assertEqual(result["pair_attempts"], 1)
        self.assertEqual((first, second), snapshot)

    def test_pair_budgets_stop_before_next_attempt_and_expose_no_partial_choices(self):
        first, second = self.item("a"), self.item("b")
        paths = {"a": first["native"]["path"], "b": second["native"]["path"]}
        for budgets, reason in (({"max_label_pairs": 1}, "label_pairs"),
                                 ({"max_batch_label_pairs": 1}, "batch_label_pairs")):
            options = [self.options(first, [50, 100, 200]), self.options(second, [50])]
            with mock.patch.object(self.labels, "ordered_label_box_candidates", side_effect=options):
                result = self.labels.plan_label_batch([first, second], paths,
                    {"block": self.box(100, 60, 40, 30)}, None, **budgets)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["choices"], {})
            self.assertEqual(result["pair_attempts"], 1)
            self.assertEqual(set(result["provisional_choices"]), {"a"})
            self.assertEqual(result["provisional_choices"]["a"][1]["left"], 50)
            self.assertEqual(result["failure"]["reason"], "budget_exhausted")
            self.assertEqual(result["failure"]["budget"], reason)
            self.assertEqual(result["failure"]["blocking_label_ids"], ["a"])

    def test_locked_blocker_is_never_repositioned(self):
        first, second = self.item("a"), self.item("b")
        choice = self.options(first, [50])[0][:2]
        locked = {"a": choice}
        snapshot = copy.deepcopy(locked)
        with mock.patch.object(self.labels, "ordered_label_box_candidates", return_value=self.options(second, [50])):
            result = self.labels.plan_label_batch([first, second], {
                "a": first["native"]["path"], "b": second["native"]["path"]}, {}, None,
                locked_choices=locked)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["pair_attempts"], 0)
        self.assertEqual(result["failure"]["blocking_label_ids"], ["a"])
        self.assertEqual(locked, snapshot)

    def test_measured_carrier_not_requested_carrier_controls_own_path_exemption(self):
        item = self.item()
        options = self.options(item, [50])
        with mock.patch.object(self.labels, "ordered_label_box_candidates", return_value=[(99, options[0][1], 0)]):
            result = self.labels.plan_label_batch([item], {"a": item["native"]["path"]}, {}, None)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["choices"]["a"][0], 0)

    def test_strict_gap_thresholds_and_carrier_exemption(self):
        box = self.box(10, 10, 10, 10)
        paths = {"self": [((0, 15), (30, 15)), ((0, 21), (30, 21))],
                 "far": [((0, 21.001), (30, 21.001))]}
        self.assertEqual(self.labels.label_path_conflicts("self", 0, box, paths), ("self",))
        self.assertEqual(self.labels.label_pair_conflicts("self", box, {
            "equal": self.box(22, 10, 10, 10), "inside": self.box(21.999, 10, 10, 10)}), ("inside",))
        conflicts = self.labels.label_placement_conflicts("self", 0, box, {}, {
            "equal": self.box(21, 10, 10, 10), "inside": self.box(20.999, 10, 10, 10)}, {}, None)
        self.assertEqual(conflicts["blocking_node_ids"], ["inside"])


if __name__ == "__main__":
    unittest.main()
