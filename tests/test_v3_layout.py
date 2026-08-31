import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills" / "product-swimlane-drawio" / "scripts" / "drawio_swimlane.py"


def run_tool(
    *args: str,
    check: bool = True,
    bind_patch_baseline: bool = True,
) -> subprocess.CompletedProcess[str]:
    command_args = list(args)
    if (
        bind_patch_baseline
        and command_args
        and command_args[0] == "patch"
        and "--expected-input-sha256" not in command_args
    ):
        input_index = command_args.index("--input") + 1
        input_path = Path(command_args[input_index])
        command_args.extend(
            ["--expected-input-sha256", hashlib.sha256(input_path.read_bytes()).hexdigest()]
        )
    result = subprocess.run(
        [sys.executable, str(TOOL), *command_args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed with exit code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def v3_fork_join_spec() -> dict:
    return {
        "schema_version": "3",
        "title": "Generic Review Flow",
        "behavior_pattern": "fork-join",
        "layout": {"profile": "review"},
        "lanes": [
            {"id": "requester", "label": "Requester", "width": 180},
            {"id": "workspace", "label": "Workspace", "width": 200},
        ],
        "nodes": [
            {"id": "start", "lane": "requester", "rank": 1, "type": "start", "label": ""},
            {"id": "submit", "lane": "requester", "rank": 2, "type": "process", "label": "Submit item"},
            {"id": "classify", "lane": "workspace", "rank": 3, "type": "decision", "label": "Existing item?"},
            {"id": "context", "lane": "workspace", "rank": 6, "type": "note", "label": "Confirm the result", "anchor": {"node": "review", "side": "right"}},
            {"id": "update", "lane": "workspace", "rank": 4, "type": "process", "label": "Update existing", "slot": "left"},
            {"id": "create", "lane": "workspace", "rank": 4, "type": "process", "label": "Create new", "slot": "right"},
            {"id": "merge", "lane": "workspace", "rank": 5, "type": "process", "label": "Merge result"},
            {"id": "review", "lane": "workspace", "rank": 6, "type": "process", "label": "Review result"},
            {"id": "end", "lane": "workspace", "rank": 7, "type": "end", "label": ""},
        ],
        "groups": [
            {"id": "choice", "lane": "workspace", "kind": "branch", "label": "Handling paths", "nodes": ["update", "create"]},
            {"id": "guidance", "lane": "workspace", "kind": "support", "nodes": ["context"]},
        ],
        "edges": [
            {"id": "e1", "from": "start", "to": "submit", "flow_role": "main"},
            {"id": "e2", "from": "submit", "to": "classify", "flow_role": "main"},
            {"id": "e3", "from": "classify", "to": "update", "branch": "positive", "outcome": "existing", "flow_role": "main"},
            {"id": "e4", "from": "classify", "to": "create", "branch": "negative", "outcome": "new", "flow_role": "branch"},
            {"id": "e5", "from": "update", "to": "merge", "flow_role": "main"},
            {"id": "e6", "from": "create", "to": "merge", "flow_role": "join"},
            {"id": "e7", "from": "merge", "to": "review", "flow_role": "main"},
            {"id": "e8", "from": "review", "to": "end", "flow_role": "main"},
        ],
        "main_path": ["start", "submit", "classify", "update", "merge", "review", "end"],
    }


class V3LayoutTests(unittest.TestCase):
    def test_multi_outcome_decision_uses_outcome_ids_and_three_distinct_sides(self) -> None:
        spec = {
            "schema_version": "3",
            "title": "Generic Multi Outcome Decision",
            "behavior_pattern": "custom",
            "layout": {"profile": "review"},
            "lanes": [
                {"id": "lane-a", "label": "Lane A", "width": 180},
                {"id": "lane-b", "label": "Lane B", "width": 200},
                {"id": "lane-c", "label": "Lane C", "width": 180},
            ],
            "nodes": [
                {"id": "start", "lane": "lane-b", "rank": 1, "type": "start", "label": ""},
                {"id": "decision", "lane": "lane-b", "rank": 2, "type": "decision", "label": "Choose?"},
                {"id": "primary", "lane": "lane-a", "rank": 3, "type": "process", "label": "Primary"},
                {"id": "alternate", "lane": "lane-c", "rank": 2, "type": "process", "label": "Alternate"},
                {"id": "blocked", "lane": "lane-b", "rank": 3, "type": "end", "label": ""},
                {"id": "alternate-end", "lane": "lane-c", "rank": 4, "type": "end", "label": ""},
                {"id": "end", "lane": "lane-a", "rank": 4, "type": "end", "label": ""},
            ],
            "edges": [
                {"id": "e1", "from": "start", "to": "decision", "flow_role": "main"},
                {"id": "primary-outcome", "from": "decision", "to": "primary", "branch": "positive", "outcome": "primary", "flow_role": "main"},
                {"id": "alternate-outcome", "from": "decision", "to": "alternate", "branch": "negative", "outcome": "alternate", "flow_role": "branch"},
                {"id": "blocked-outcome", "from": "decision", "to": "blocked", "branch": "negative", "outcome": "blocked", "flow_role": "exception"},
                {"id": "e5", "from": "primary", "to": "end", "flow_role": "main"},
                {"id": "e6", "from": "alternate", "to": "alternate-end", "flow_role": "exception"},
            ],
            "main_path": ["start", "decision", "primary", "end"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "spec.json"
            output = root / "output.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_tool("build", "--spec", str(spec_path), "--output", str(output))
            strict = json.loads(
                run_tool("validate", "--input", str(output), "--strict").stdout
            )
            self.assertEqual(strict["warnings"], [])

            inspected = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            edges = {edge["id"]: edge for edge in inspected["edges"]}
            self.assertEqual(edges["primary-outcome"]["exit_side"], "left")
            self.assertEqual(edges["alternate-outcome"]["exit_side"], "right")
            self.assertEqual(edges["blocked-outcome"]["exit_side"], "bottom")
            self.assertEqual(edges["alternate-outcome"]["entry_side"], "left")
            self.assertEqual(edges["alternate-outcome"].get("waypoints", []), [])
            self.assertEqual(edges["blocked-outcome"]["entry_side"], "top")
            self.assertEqual(edges["blocked-outcome"].get("waypoints", []), [])
            for edge_id in ("primary-outcome", "alternate-outcome", "blocked-outcome"):
                self.assertEqual(edges[edge_id]["exit_offset"], 0.5)

            invalid = json.loads(json.dumps(spec))
            next(
                edge
                for edge in invalid["edges"]
                if edge["id"] == "blocked-outcome"
            ).pop("outcome")
            invalid_path = root / "invalid.json"
            invalid_output = root / "invalid.drawio"
            invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
            build = json.loads(
                run_tool(
                    "build",
                    "--spec",
                    str(invalid_path),
                    "--output",
                    str(invalid_output),
                ).stdout
            )
            self.assertIn(
                "semantic/decision-outcome",
                {item["code"] for item in build["diagnostics"]},
            )

    def test_request_response_keeps_adjacent_retry_outside_response_corridor(self) -> None:
        spec = {
            "schema_version": "3",
            "title": "Generic Request Response",
            "behavior_pattern": "request-response",
            "layout": {"profile": "long-form"},
            "lanes": [
                {"id": "lane-a", "label": "Lane A", "width": 180},
                {"id": "lane-b", "label": "Lane B", "width": 180},
                {"id": "lane-c", "label": "Lane C", "width": 180},
                {"id": "lane-d", "label": "Lane D", "width": 180},
            ],
            "nodes": [
                {"id": "start", "lane": "lane-a", "rank": 1, "type": "start", "label": ""},
                {"id": "send", "lane": "lane-b", "rank": 2, "type": "process", "label": "Send"},
                {"id": "invoke", "lane": "lane-c", "rank": 3, "type": "process", "label": "Invoke"},
                {"id": "check", "lane": "lane-b", "rank": 4, "type": "decision", "label": "Received?"},
                {"id": "retry-wait", "lane": "lane-b", "rank": 5, "type": "process", "label": "Retry", "slot": "right"},
                {"id": "compose", "lane": "lane-a", "rank": 5, "type": "process", "label": "Compose"},
                {"id": "notify", "lane": "lane-d", "rank": 6, "type": "process", "label": "Notify"},
                {"id": "end", "lane": "lane-a", "rank": 6, "type": "end", "label": ""},
            ],
            "edges": [
                {"id": "e1", "from": "start", "to": "send", "flow_role": "main"},
                {"id": "request", "from": "send", "to": "invoke", "type": "call", "flow_role": "main"},
                {"id": "response", "from": "invoke", "to": "check", "type": "return", "flow_role": "response"},
                {"id": "success", "from": "check", "to": "compose", "branch": "positive", "flow_role": "main"},
                {"id": "timeout", "from": "check", "to": "retry-wait", "branch": "negative", "flow_role": "exception"},
                {"id": "retry", "from": "retry-wait", "to": "invoke", "type": "retry", "route": "back", "flow_role": "retry"},
                {"id": "async", "from": "compose", "to": "notify", "type": "async", "flow_role": "branch"},
                {"id": "e8", "from": "compose", "to": "end", "flow_role": "main"},
            ],
            "main_path": ["start", "send", "invoke", "check", "compose", "end"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "spec.json"
            output = root / "output.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_tool("build", "--spec", str(spec_path), "--output", str(output))
            strict = json.loads(
                run_tool("validate", "--input", str(output), "--strict").stdout
            )
            self.assertEqual(strict["warnings"], [])

            inspected = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            edges = {edge["id"]: edge for edge in inspected["edges"]}
            self.assertEqual(
                (edges["retry"]["exit_side"], edges["retry"]["entry_side"]),
                ("right", "right"),
            )
            self.assertEqual(edges["retry"]["exit_offset"], 0.5)
            self.assertEqual(edges["retry"]["entry_offset"], 0.5)
            self.assertEqual(
                (edges["response"]["exit_side"], edges["response"]["entry_side"]),
                ("bottom", "top"),
            )

            xml_root = ET.parse(output).getroot()
            async_cell = next(
                cell
                for cell in xml_root.iter("mxCell")
                if cell.attrib.get("data-semantic-id") == "async"
            )
            self.assertIn("dashed=1", async_cell.attrib["style"])

    def test_approval_loop_uses_semantic_decision_ports_and_center_offsets(self) -> None:
        spec = {
            "schema_version": "3",
            "title": "Generic Approval Loop",
            "behavior_pattern": "approval-loop",
            "layout": {"profile": "long-form"},
            "lanes": [
                {"id": f"lane-{index}", "label": f"Lane {index}", "width": 180}
                for index in range(1, 6)
            ],
            "nodes": [
                {"id": "start", "lane": "lane-1", "rank": 1, "type": "start", "label": ""},
                {"id": "prepare", "lane": "lane-1", "rank": 2, "type": "process", "label": "Prepare"},
                {"id": "submit", "lane": "lane-2", "rank": 3, "type": "process", "label": "Submit"},
                {"id": "complete", "lane": "lane-2", "rank": 4, "type": "decision", "label": "Complete?"},
                {"id": "review", "lane": "lane-3", "rank": 5, "type": "process", "label": "Review"},
                {"id": "correct", "lane": "lane-5", "rank": 5, "type": "process", "label": "Correct"},
                {"id": "approved", "lane": "lane-3", "rank": 6, "type": "decision", "label": "Approved?"},
                {"id": "rejected", "lane": "lane-3", "rank": 7, "type": "end", "label": ""},
                {"id": "accept", "lane": "lane-4", "rank": 7, "type": "process", "label": "Accept"},
                {"id": "end", "lane": "lane-4", "rank": 8, "type": "end", "label": ""},
            ],
            "edges": [
                {"id": "e1", "from": "start", "to": "prepare", "flow_role": "main"},
                {"id": "e2", "from": "prepare", "to": "submit", "flow_role": "main"},
                {"id": "e3", "from": "submit", "to": "complete", "flow_role": "main"},
                {"id": "complete-main", "from": "complete", "to": "review", "branch": "positive", "flow_role": "main"},
                {"id": "needs-change", "from": "complete", "to": "correct", "branch": "negative", "flow_role": "exception"},
                {"id": "retry", "from": "correct", "to": "prepare", "type": "retry", "route": "back", "flow_role": "retry"},
                {"id": "e7", "from": "review", "to": "approved", "flow_role": "main"},
                {"id": "approved-main", "from": "approved", "to": "accept", "branch": "positive", "flow_role": "main"},
                {"id": "rejected-end", "from": "approved", "to": "rejected", "branch": "negative", "flow_role": "exception"},
                {"id": "e10", "from": "accept", "to": "end", "flow_role": "main"},
            ],
            "main_path": ["start", "prepare", "submit", "complete", "review", "approved", "accept", "end"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "spec.json"
            output = root / "output.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_tool("build", "--spec", str(spec_path), "--output", str(output))
            inspected = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            edges = {edge["id"]: edge for edge in inspected["edges"]}

            self.assertEqual(
                (edges["complete-main"]["exit_side"], edges["complete-main"]["entry_side"]),
                ("bottom", "top"),
            )
            self.assertEqual(
                (edges["needs-change"]["exit_side"], edges["needs-change"]["entry_side"]),
                ("right", "top"),
            )
            self.assertEqual(
                (edges["rejected-end"]["exit_side"], edges["rejected-end"]["entry_side"]),
                ("bottom", "top"),
            )
            for edge_id in ("complete-main", "needs-change", "retry", "rejected-end"):
                self.assertEqual(edges[edge_id]["exit_offset"], 0.5, edge_id)
                self.assertEqual(edges[edge_id]["entry_offset"], 0.5, edge_id)

    def test_long_cross_lane_retry_uses_outer_source_side_and_target_gutter(self) -> None:
        spec = {
            "schema_version": "3",
            "title": "Generic Retry Flow",
            "behavior_pattern": "approval-loop",
            "layout": {"profile": "long-form"},
            "lanes": [
                {"id": f"lane-{index}", "label": f"Lane {index}", "width": 160}
                for index in range(1, 6)
            ],
            "nodes": [
                {"id": "start", "lane": "lane-1", "rank": 1, "type": "start", "label": ""},
                {"id": "target", "lane": "lane-1", "rank": 2, "type": "process", "label": "Prepare"},
                {"id": "middle", "lane": "lane-3", "rank": 3, "type": "process", "label": "Process"},
                {"id": "source", "lane": "lane-5", "rank": 4, "type": "process", "label": "Review"},
                {"id": "end", "lane": "lane-5", "rank": 5, "type": "end", "label": ""},
            ],
            "edges": [
                {"id": "e1", "from": "start", "to": "target", "flow_role": "main"},
                {"id": "e2", "from": "target", "to": "middle", "flow_role": "main"},
                {"id": "e3", "from": "middle", "to": "source", "flow_role": "main"},
                {"id": "e4", "from": "source", "to": "end", "flow_role": "main"},
                {"id": "retry", "from": "source", "to": "target", "type": "retry", "route": "back", "flow_role": "retry", "label": "Retry request"},
            ],
            "main_path": ["start", "target", "middle", "source", "end"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "spec.json"
            output = root / "output.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_tool("build", "--spec", str(spec_path), "--output", str(output))
            strict = json.loads(
                run_tool("validate", "--input", str(output), "--strict").stdout
            )
            self.assertEqual(strict["warnings"], [])
            inspected = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            retry = next(edge for edge in inspected["edges"] if edge["id"] == "retry")
            self.assertEqual(retry["exit_side"], "right")
            self.assertEqual(retry["entry_side"], "left")
            xml_root = ET.parse(output).getroot()
            retry_cell = next(
                cell
                for cell in xml_root.iter("mxCell")
                if cell.attrib.get("data-semantic-id") == "retry"
            )
            outer_y = max(point["y"] for point in retry["waypoints"])
            outer_horizontal_segments = {
                index
                for index, (first, second) in enumerate(
                    zip(retry["waypoints"], retry["waypoints"][1:]),
                    start=1,
                )
                if first["y"] == second["y"] == outer_y
            }
            self.assertNotIn(
                int(retry_cell.attrib["data-label-segment"]),
                outer_horizontal_segments,
            )
            self.assertLess(float(retry_cell.attrib["data-label-top"]), outer_y)

    def test_phase_rail_reserves_space_and_keeps_lanes_opaque(self) -> None:
        spec = v3_fork_join_spec()
        spec["layout"] = {"profile": "long-form", "phase_presentation": "rail"}
        spec["phases"] = [
            {"id": "intake", "label": "Intake", "from_rank": 1, "to_rank": 3},
            {"id": "handling", "label": "Handling", "from_rank": 4, "to_rank": 7},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "spec.json"
            output = root / "output.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_tool("build", "--spec", str(spec_path), "--output", str(output))
            strict = json.loads(
                run_tool("validate", "--input", str(output), "--strict").stdout
            )
            self.assertEqual(strict["warnings"], [])

            inspected = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            self.assertEqual(inspected["layout"]["phase_presentation"], "rail")
            self.assertEqual(inspected["lanes"][0]["x"], 76.0)

            xml_root = ET.parse(output).getroot()
            phase_cells = [
                cell
                for cell in xml_root.iter("mxCell")
                if cell.attrib.get("data-kind") == "phase"
            ]
            self.assertEqual(len(phase_cells), 2)
            self.assertTrue(
                all(float(cell.find("mxGeometry").attrib["width"]) == 76.0 for cell in phase_cells)
            )
            lane_cells = [
                cell
                for cell in xml_root.iter("mxCell")
                if cell.attrib.get("data-kind") == "lane"
            ]
            self.assertTrue(all("swimlaneFillColor=#ffffff" in cell.attrib["style"] for cell in lane_cells))

    def test_layout_profiles_change_spacing_without_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = {}
            for profile in ("compact", "long-form"):
                spec = v3_fork_join_spec()
                spec["layout"] = {"profile": profile}
                spec_path = root / f"{profile}.json"
                output = root / f"{profile}.drawio"
                spec_path.write_text(json.dumps(spec), encoding="utf-8")
                run_tool("build", "--spec", str(spec_path), "--output", str(output))
                reports[profile] = json.loads(
                    run_tool("inspect", "--input", str(output)).stdout
                )

            compact = reports["compact"]
            long_form = reports["long-form"]
            compact_lane = next(lane for lane in compact["lanes"] if lane["id"] == "workspace")
            long_lane = next(lane for lane in long_form["lanes"] if lane["id"] == "workspace")
            self.assertGreater(long_lane["width"], compact_lane["width"])

            compact_nodes = {node["id"]: node for node in compact["nodes"]}
            long_nodes = {node["id"]: node for node in long_form["nodes"]}
            compact_gap = compact_nodes["submit"]["y"] - compact_nodes["start"]["y"]
            long_gap = long_nodes["submit"]["y"] - long_nodes["start"]["y"]
            self.assertGreater(long_gap, compact_gap)

    def test_slot_layout_metadata_and_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "spec.json"
            first = root / "first.drawio"
            second = root / "second.drawio"
            spec_path.write_text(json.dumps(v3_fork_join_spec()), encoding="utf-8")

            run_tool("build", "--spec", str(spec_path), "--output", str(first))
            run_tool("build", "--spec", str(spec_path), "--output", str(second))
            self.assertEqual(first.read_bytes(), second.read_bytes())

            strict = json.loads(
                run_tool("validate", "--input", str(first), "--strict").stdout
            )
            self.assertTrue(strict["valid"])
            self.assertEqual(strict["warnings"], [])

            inspected = json.loads(run_tool("inspect", "--input", str(first)).stdout)
            self.assertEqual(inspected["schema_version"], "3")
            self.assertEqual(inspected["behavior_pattern"], "fork-join")
            self.assertEqual(
                inspected["layout"],
                {"profile": "review", "phase_presentation": "bands"},
            )
            self.assertEqual([group["id"] for group in inspected["groups"]], ["choice", "guidance"])

            nodes = {node["id"]: node for node in inspected["nodes"]}
            self.assertLess(nodes["update"]["x"], nodes["create"]["x"])
            self.assertEqual(nodes["context"]["anchor"], {"node": "review", "side": "right"})
            self.assertEqual(nodes["context"]["slot"], "right")
            self.assertEqual(nodes["update"]["group_id"], "choice")

            edges = {edge["id"]: edge for edge in inspected["edges"]}
            self.assertEqual(edges["e4"]["flow_role"], "branch")
            self.assertEqual(edges["e4"]["outcome"], "new")

    def test_v3_local_edit_patch_compare_preserves_semantics_and_unrelated_geometry(self) -> None:
        spec = v3_fork_join_spec()
        spec["layout"] = {"profile": "long-form", "phase_presentation": "rail"}
        spec["phases"] = [
            {"id": "intake", "label": "Intake", "from_rank": 1, "to_rank": 3},
            {"id": "handling", "label": "Handling", "from_rank": 4, "to_rank": 7},
        ]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "spec.json"
            built = root / "built.drawio"
            locally_edited = root / "locally-edited.drawio"
            patched = root / "patched.drawio"
            changes_path = root / "changes.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_tool("build", "--spec", str(spec_path), "--output", str(built))

            # Simulate a user moving an unrelated note in Draw.io before asking
            # the agent to make a semantic label-only update.
            tree = ET.parse(built)
            context_cell = next(
                cell
                for cell in tree.getroot().iter("mxCell")
                if cell.attrib.get("data-semantic-id") == "context"
            )
            context_geometry = context_cell.find("mxGeometry")
            self.assertIsNotNone(context_geometry)
            original_context_x = float(context_geometry.attrib["x"])
            context_geometry.attrib["x"] = str(original_context_x + 8)
            tree.write(locally_edited, encoding="utf-8", xml_declaration=False)

            before_patch = json.loads(
                run_tool("inspect", "--input", str(locally_edited)).stdout
            )
            edited_context = next(
                node for node in before_patch["nodes"] if node["id"] == "context"
            )
            self.assertEqual(edited_context["x"], original_context_x + 8)

            changes = {"update_nodes": [{"id": "review", "label": "Review updated result"}]}
            changes_path.write_text(json.dumps(changes), encoding="utf-8")
            run_tool(
                "patch",
                "--input",
                str(locally_edited),
                "--changes",
                str(changes_path),
                "--output",
                str(patched),
            )

            strict = json.loads(
                run_tool("validate", "--input", str(patched), "--strict").stdout
            )
            self.assertEqual(strict["warnings"], [])
            comparison = json.loads(
                run_tool(
                    "compare",
                    "--before",
                    str(locally_edited),
                    "--after",
                    str(patched),
                    "--changes",
                    str(changes_path),
                ).stdout
            )
            self.assertTrue(comparison["preserved"])
            self.assertEqual(comparison["unexpected_geometry"], [])

            inspected = json.loads(run_tool("inspect", "--input", str(patched)).stdout)
            self.assertEqual(inspected["schema_version"], "3")
            self.assertEqual(inspected["behavior_pattern"], "fork-join")
            self.assertEqual(
                inspected["layout"],
                {"profile": "long-form", "phase_presentation": "rail"},
            )
            self.assertEqual(
                [group["id"] for group in inspected["groups"]],
                ["choice", "guidance"],
            )
            self.assertEqual(
                [phase["id"] for phase in inspected["phases"]],
                ["handling", "intake"],
            )

            nodes = {node["id"]: node for node in inspected["nodes"]}
            self.assertEqual(nodes["review"]["label"], "Review updated result")
            self.assertEqual(nodes["context"]["x"], original_context_x + 8)
            self.assertEqual(
                nodes["context"]["anchor"],
                {"node": "review", "side": "right"},
            )

            edges = {edge["id"]: edge for edge in inspected["edges"]}
            self.assertEqual(edges["e3"]["outcome"], "existing")
            self.assertEqual(edges["e3"]["flow_role"], "main")
            self.assertEqual(edges["e4"]["outcome"], "new")
            self.assertEqual(edges["e4"]["flow_role"], "branch")

    def test_v3_main_path_patch_does_not_downgrade_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = v3_fork_join_spec()
            spec_path = root / "spec.json"
            before = root / "before.drawio"
            after = root / "after.drawio"
            changes_path = root / "changes.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            changes_path.write_text(
                json.dumps({"main_path": spec["main_path"]}),
                encoding="utf-8",
            )

            run_tool("build", "--spec", str(spec_path), "--output", str(before))
            run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(changes_path),
                "--output",
                str(after),
            )

            inspected = json.loads(run_tool("inspect", "--input", str(after)).stdout)
            self.assertEqual(inspected["schema_version"], "3")
            self.assertEqual(inspected["behavior_pattern"], "fork-join")
            self.assertEqual(inspected["layout"]["profile"], "review")

    def test_duplicate_slot_is_rejected(self) -> None:
        spec = v3_fork_join_spec()
        spec["nodes"].append(
            {"id": "duplicate", "lane": "workspace", "rank": 4, "type": "process", "label": "Duplicate", "slot": "left"}
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "spec.json"
            output = root / "output.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            result = run_tool("build", "--spec", str(spec_path), "--output", str(output), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("same lane, rank, and slot", result.stderr)

    def test_v3_requires_behavior_pattern(self) -> None:
        spec = v3_fork_join_spec()
        spec.pop("behavior_pattern")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec_path = root / "spec.json"
            output = root / "output.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            result = run_tool("build", "--spec", str(spec_path), "--output", str(output), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires behavior_pattern", result.stderr)


if __name__ == "__main__":
    unittest.main()
