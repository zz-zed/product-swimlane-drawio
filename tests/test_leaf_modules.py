"""Direct, XML-free contracts for shared sizing, port, and label helpers."""

import shutil
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from release_check import EXPECTED_SKILL_FILES
from swimlane_loader import load_skill_modules


TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"


class LeafModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(TOOL, module_name="leaf_module_tests")
        cls.sizing = cls.loaded.sizing
        cls.ports = cls.loaded.ports
        cls.labels = cls.loaded.labels
        cls.geometry = cls.loaded.geometry

    def test_sizing_keeps_text_units_fixed_aspect_and_height_cap(self):
        self.assertEqual(self.sizing.estimated_text_lines("中文", 28), 4)
        self.assertEqual(self.sizing.estimated_text_lines("中文", 40), 2)
        self.assertEqual(self.sizing.estimated_text_lines("中文", 40, diamond=True), 4)
        self.assertEqual(self.sizing.estimated_text_lines("a\nb", 80), 2)
        self.assertEqual(self.sizing.recommended_process_height("中文\n流程", 40), 66.0)
        self.assertEqual(self.sizing.recommended_process_height("a" * 100, 40), 66.0)
        self.assertEqual(self.sizing.node_size({"id": "s", "type": "start", "label": "x"}), (48.0, 48.0))
        self.assertEqual(self.sizing.node_size({"id": "d", "type": "decision", "label": "a" * 40}), (168.0, 72.0))
        self.assertEqual(self.sizing.node_size({"id": "s", "type": "start", "width": 48.74, "height": 49.489}),
                         (48.74, 48.74))
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            self.sizing.node_size({"id": "s", "type": "start", "width": 48.75, "height": 49.5})
        self.assertEqual(caught.exception.code, "geometry/fixed-aspect-ratio")

    def test_ports_pair_center_alignment_reuse_and_exhaustion(self):
        bounds = {"left": 0.0, "top": 0.0, "width": 100.0, "height": 100.0,
                  "right": 100.0, "bottom": 100.0}
        target = {"left": 200.0, "top": 40.0, "width": 100.0, "height": 80.0,
                  "right": 300.0, "bottom": 120.0}
        self.assertEqual(self.ports.candidate_port_offsets(bounds, "right", target, "left"),
                         [0.5, 0.35, 0.65, 0.275, 0.725, 0.2, 0.8, 0.1, 0.9])
        allocator = self.ports.PortAllocator()
        self.assertEqual([allocator.choose("n", "right", f"e{i}") for i in range(9)],
                         list(self.ports.PORT_OFFSETS))
        with self.assertRaisesRegex(self.loaded.contracts.DiagramError, "No free right port remains"):
            allocator.choose("n", "right", "e9")
        allocator = self.ports.PortAllocator()
        self.assertEqual(self.ports.allocate_port_pair(allocator, {"id": "e", "from": "s", "to": "t"},
                                                       bounds, target, "right", "left"), (0.8, 0.5))
        allocator = self.ports.PortAllocator()
        self.assertEqual(self.ports.allocate_port_pair(allocator, {"id": "e", "from": "s", "to": "t"},
                                                       bounds, target, "right", "left",
                                                       prefer_center_ports=True), (0.5, 0.5))
        allocator = self.ports.PortAllocator()
        edge = {"id": "e", "from": "s", "to": "t", "exit_offset": 0.5, "entry_offset": 0.5}
        self.assertEqual(self.ports.allocate_port_pair(allocator, edge, bounds, bounds, "right", "left"), (0.5, 0.5))
        with self.assertRaisesRegex(self.loaded.contracts.DiagramError, "Port s:right@0.5"):
            self.ports.allocate_port_pair(allocator, {**edge, "id": "e2"}, bounds, bounds, "right", "left")
        self.assertEqual(self.ports.allocate_port_pair(allocator, {**edge, "id": "e3", "allow_port_reuse": True},
                                                       bounds, bounds, "right", "left"), (0.5, 0.5))
        self.assertEqual(allocator.occupied[("s", "right", 0.5)], ["e", "e3"])
        rounded = self.ports.PortAllocator()
        self.assertEqual(rounded.reserve("n", "right", 0.50004, "e1"), 0.50004)
        with self.assertRaisesRegex(self.loaded.contracts.DiagramError, "Port n:right@0.5"):
            rounded.reserve("n", "right", 0.50005, "e2")
        self.assertEqual(rounded.reserve("n", "right", 0.50006, "e3"), 0.50006)
        self.assertEqual(sorted(rounded.occupied), [("n", "right", 0.5), ("n", "right", 0.5001)])
        partial = self.ports.PortAllocator()
        partial.reserve("t", "left", 0.5, "blocker")
        with self.assertRaisesRegex(self.loaded.contracts.DiagramError, "Port t:left@0.5"):
            self.ports.allocate_port_pair(partial, edge, bounds, bounds, "right", "left")
        self.assertEqual(partial.occupied[("s", "right", 0.5)], ["e"])
        self.assertEqual(partial.occupied[("t", "left", 0.5)], ["blocker"])

    def test_labels_are_xml_free_and_preserve_candidate_order(self):
        self.assertEqual(self.labels.edge_label_size("a\nb"), (28.0, 32.0))
        self.assertEqual(self.labels.edge_label_size("中文"), (35.760000000000005, 18.0))
        self.assertEqual(self.labels.edge_label_size("   "), (29.82, 18.0))
        candidates = self.labels.label_box_candidates([(0, 0), (200, 0)], "A")
        self.assertEqual(len(candidates), 10)
        self.assertEqual([(box["left"], box["top"], box["right"], box["bottom"]) for _, box, _ in candidates[:4]],
                         [(86.0, -23.0, 114.0, -5.0), (86.0, 5.0, 114.0, 23.0),
                          (5.0, -23.0, 33.0, -5.0), (5.0, 5.0, 33.0, 23.0)])
        self.assertEqual(self.labels.label_box_candidates([(0, 0), (200, 0)], "   "), [])
        self.assertIsNone(self.labels.choose_label_box([(0, 0), (200, 0)], "", [], [], []))
        self.assertEqual(self.labels.polyline_midpoint([(0, 0), (0, 10), (10, 10)]), (0.0, 10.0))

    def test_loader_isolates_new_modules_across_two_checkouts(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first"
            second = Path(temporary) / "second"
            for destination in (first, second):
                for relative in EXPECTED_SKILL_FILES:
                    source = ROOT / "skills/product-swimlane-drawio" / relative
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            one = load_skill_modules(first / "scripts/drawio_swimlane.py",
                                     module_name="leaf_first")
            two = load_skill_modules(second / "scripts/drawio_swimlane.py",
                                     module_name="leaf_second")
            self.assertIsNot(one.sizing, two.sizing)
            self.assertIsNot(one.ports, two.ports)
            self.assertIsNot(one.labels, two.labels)
            self.assertNotIn("swimlane_core.ports", sys.modules)
            self.assertNotIn("swimlane_core.labels", sys.modules)


if __name__ == "__main__":
    unittest.main()
