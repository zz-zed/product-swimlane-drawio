import json
import hashlib
import re
import subprocess
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "product-swimlane-drawio"
TOOL = SKILL / "scripts" / "drawio_swimlane.py"
FIXTURES = ROOT / "tests" / "fixtures"


def run_tool(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed with exit code {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def write_boundary_spec(path: Path, *, include_forward: bool) -> None:
    edges = []
    if include_forward:
        edges.append({"id": "forward", "from": "step-a", "to": "step-b"})
    edges.append(
        {"id": "retry", "from": "step-b", "to": "step-a", "type": "retry"}
    )
    path.write_text(
        json.dumps(
            {
                "title": "Neutral flow",
                "lanes": [
                    {"id": "lane-a", "label": "Lane A", "width": 220},
                    {"id": "lane-b", "label": "Lane B", "width": 220},
                ],
                "nodes": [
                    {
                        "id": "step-a",
                        "lane": "lane-b",
                        "rank": 1,
                        "type": "process",
                        "label": "Step A",
                        "x": 20,
                        "width": 160,
                    },
                    {
                        "id": "step-b",
                        "lane": "lane-b",
                        "rank": 2,
                        "type": "process",
                        "label": "Step B",
                        "x": 20,
                        "width": 160,
                    },
                ],
                "edges": edges,
            }
        ),
        encoding="utf-8",
    )


def write_linear_v2_spec(path: Path, *, long_label: bool = False) -> None:
    label = "多语言文字" * 20 if long_label else "Step"
    path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "title": "Linear Flow",
                "lanes": [{"id": "lane-a", "label": "Lane A", "width": 220}],
                "nodes": [
                    {"id": "start", "lane": "lane-a", "rank": 1, "type": "start", "label": ""},
                    {"id": "step", "lane": "lane-a", "rank": 2, "type": "process", "label": label},
                    {"id": "end", "lane": "lane-a", "rank": 3, "type": "end", "label": ""},
                ],
                "edges": [
                    {"id": "edge-a", "from": "start", "to": "step"},
                    {"id": "edge-b", "from": "step", "to": "end"},
                ],
                "main_path": ["start", "step", "end"],
                "phases": [
                    {"id": "phase-a", "label": "Phase A", "from_rank": 1, "to_rank": 3}
                ],
            }
        ),
        encoding="utf-8",
    )


def write_adjacent_decision_spec(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "title": "Adjacent Lane Flow",
                "lanes": [
                    {"id": "lane-a", "label": "Lane A", "width": 180},
                    {"id": "lane-b", "label": "Lane B", "width": 180},
                    {"id": "lane-c", "label": "Lane C", "width": 140},
                    {"id": "lane-d", "label": "Lane D", "width": 140},
                ],
                "nodes": [
                    {"id": "start", "lane": "lane-a", "rank": 1, "type": "start", "label": ""},
                    {"id": "step-a", "lane": "lane-a", "rank": 2, "type": "process", "label": "Step A"},
                    {"id": "history", "lane": "lane-b", "rank": 3, "type": "process", "label": "History"},
                    {"id": "decision", "lane": "lane-c", "rank": 4, "type": "decision", "label": "Condition"},
                    {"id": "target", "lane": "lane-d", "rank": 5, "type": "process", "label": "Target"},
                    {"id": "end", "lane": "lane-d", "rank": 6, "type": "end", "label": ""},
                ],
                "edges": [
                    {"id": "edge-a", "from": "start", "to": "step-a"},
                    {"id": "edge-b", "from": "step-a", "to": "history"},
                    {"id": "edge-c", "from": "history", "to": "decision"},
                    {
                        "id": "edge-forward",
                        "from": "decision",
                        "to": "target",
                        "branch": "positive",
                        "label": "Continue"
                    },
                    {
                        "id": "edge-back",
                        "from": "decision",
                        "to": "history",
                        "route": "back",
                        "branch": "negative",
                        "label": "Return"
                    },
                    {"id": "edge-end", "from": "target", "to": "end"},
                ],
                "main_path": ["start", "step-a", "history", "decision", "target", "end"],
            }
        ),
        encoding="utf-8",
    )


def write_geometry_spec(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "title": "Geometry Flow",
                "lanes": [{"id": "lane-a", "label": "Lane A", "width": 220}],
                "nodes": [
                    {
                        "id": "start",
                        "lane": "lane-a",
                        "rank": 1,
                        "type": "start",
                        "label": "Begin",
                        "height": 48,
                    },
                    {
                        "id": "step",
                        "lane": "lane-a",
                        "rank": 2,
                        "type": "process",
                        "label": "First line\nSecond line\nThird line",
                    },
                    {
                        "id": "end",
                        "lane": "lane-a",
                        "rank": 3,
                        "type": "end",
                        "label": "",
                        "width": 52,
                    },
                ],
                "edges": [
                    {"id": "edge-a", "from": "start", "to": "step"},
                    {"id": "edge-b", "from": "step", "to": "end"},
                ],
                "main_path": ["start", "step", "end"],
            }
        ),
        encoding="utf-8",
    )


class ReleasePackageTests(unittest.TestCase):
    def test_readme_language_navigation_and_structure(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("[简体中文](README.zh-CN.md)", english)
        self.assertIn("[English](README.md)", chinese)
        self.assertIn("**Quick navigation:**", english)
        self.assertIn("**快速导航：**", chinese)
        install_command = "npx skills add zz-zed/product-swimlane-drawio -g"
        self.assertIn(install_command, english)
        self.assertIn(install_command, chinese)
        self.assertNotIn("--skill product-swimlane-drawio", english)
        self.assertNotIn("--skill product-swimlane-drawio", chinese)
        self.assertIn(
            "![product-swimlane-drawio overview](docs/illustrations/product-swimlane-readme/overview-en.png)",
            english,
        )
        self.assertIn(
            "![product-swimlane-drawio overview](docs/illustrations/product-swimlane-readme/overview-zh.png)",
            chinese,
        )
        shared_images = {
            "docs/illustrations/product-swimlane-readme/create-update.png",
            "docs/illustrations/product-swimlane-readme/quality-gate.png",
        }
        for image_reference in shared_images:
            self.assertIn(image_reference, english)
            self.assertIn(image_reference, chinese)
        self.assertEqual(english.count("\n## "), chinese.count("\n## "))
        self.assertEqual(english.count("```"), chinese.count("```"))

    def test_readme_infographic_is_valid_landscape_png(self) -> None:
        image_dir = ROOT / "docs" / "illustrations" / "product-swimlane-readme"
        image_names = {
            "overview-en.png",
            "overview-zh.png",
            "create-update.png",
            "quality-gate.png",
        }
        for image_name in image_names:
            data = (image_dir / image_name).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            width, height = struct.unpack(">II", data[16:24])
            self.assertGreater(width, height)
            self.assertAlmostEqual(width / height, 16 / 9, delta=0.03)

    def test_readme_discloses_multimodal_review_reliability(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("Model capability and output reliability", english)
        self.assertIn("does not claim a measured accuracy percentage", english)
        self.assertIn("模型能力与输出可靠度", chinese)
        self.assertIn("没有为模型生成的流程图声明经过测量的准确率", chinese)

    def test_expected_skill_files_only(self) -> None:
        relative_files = {
            path.relative_to(SKILL).as_posix()
            for path in SKILL.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            relative_files,
            {
                "SKILL.md",
                "agents/openai.yaml",
                "references/schema.json",
                "references/schema.md",
                "scripts/drawio_swimlane.py",
            },
        )

    def test_v2_json_schema_is_strict_and_valid_json(self) -> None:
        schema = json.loads((SKILL / "references" / "schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("schema_version", schema["required"])
        self.assertIn("main_path", schema["required"])
        self.assertFalse(schema["$defs"]["edge"]["additionalProperties"])

    def test_frontmatter_is_minimal(self) -> None:
        content = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---\n"))
        frontmatter = content.split("---\n", 2)[1]
        keys = {
            line.split(":", 1)[0].strip()
            for line in frontmatter.splitlines()
            if re.match(r"^[a-zA-Z0-9_-]+:", line)
        }
        self.assertEqual(keys, {"name", "description"})

    def test_skill_has_no_absolute_user_paths_or_generated_files(self) -> None:
        for path in SKILL.rglob("*"):
            if not path.is_file():
                continue
            self.assertNotIn(path.suffix.lower(), {".drawio", ".png", ".svg", ".pdf"})
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"/Users/|/home/|[A-Za-z]:\\Users\\")


class DiagramWorkflowTests(unittest.TestCase):
    def build_diagram(self, directory: Path) -> Path:
        output = directory / "neutral.drawio"
        build = run_tool(
            "build",
            "--spec",
            str(FIXTURES / "neutral-flow.json"),
            "--output",
            str(output),
        )
        report = json.loads(build.stdout)
        self.assertTrue(report["valid"])
        self.assertEqual(report["warnings"], [])
        self.assertTrue(output.is_file())
        return output

    def test_build_and_strict_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            diagram = self.build_diagram(Path(temp))
            validate = run_tool("validate", "--input", str(diagram), "--strict")
            report = json.loads(validate.stdout)
            self.assertTrue(report["valid"])
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["warnings"], [])

    def test_patch_validate_and_compare(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            before = self.build_diagram(directory)
            after = directory / "neutral-updated.drawio"
            changes = FIXTURES / "neutral-patch.json"
            run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(changes),
                "--output",
                str(after),
            )
            validate = run_tool("validate", "--input", str(after), "--strict")
            self.assertEqual(json.loads(validate.stdout)["warnings"], [])
            compare = run_tool(
                "compare",
                "--before",
                str(before),
                "--after",
                str(after),
                "--changes",
                str(changes),
            )
            self.assertTrue(json.loads(compare.stdout)["preserved"])

    def test_build_rejects_unknown_fields_with_structured_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = json.loads((FIXTURES / "neutral-flow.json").read_text(encoding="utf-8"))
            spec["unexpected"] = True
            spec_path = directory / "invalid.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            result = run_tool(
                "build",
                "--spec",
                str(spec_path),
                "--output",
                str(directory / "invalid.drawio"),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(result.stdout)
            self.assertEqual(report["diagnostics"][0]["code"], "schema/unknown-field")
            self.assertFalse((directory / "invalid.drawio").exists())

    def test_build_rejects_broken_main_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = json.loads((FIXTURES / "neutral-flow.json").read_text(encoding="utf-8"))
            spec["main_path"] = ["start", "condition", "step-b", "end"]
            spec_path = directory / "invalid-main.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            result = run_tool(
                "build",
                "--spec",
                str(spec_path),
                "--output",
                str(directory / "invalid-main.drawio"),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                json.loads(result.stdout)["diagnostics"][0]["code"],
                "semantic/main-path-edge",
            )

    def test_inspect_returns_semantics_geometry_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            diagram = self.build_diagram(Path(temp))
            result = run_tool("inspect", "--input", str(diagram))
            report = json.loads(result.stdout)
            self.assertTrue(report["compatible"])
            self.assertEqual(report["schema_version"], "2")
            self.assertEqual(report["main_path"][0], "start")
            self.assertEqual(len(report["phases"]), 2)
            self.assertIn("x", report["nodes"][0])
            self.assertTrue(report["validation"]["valid"])
            self.assertEqual(report["validation"]["diagnostics"], [])

    def test_geometry_update_repairs_only_invalid_incident_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "linear.json"
            before = directory / "linear.drawio"
            after = directory / "linear-moved.drawio"
            patch = directory / "move.json"
            write_linear_v2_spec(spec)
            run_tool("build", "--spec", str(spec), "--output", str(before))
            patch.write_text(
                json.dumps({"update_nodes": [{"id": "step", "x": 10}]}),
                encoding="utf-8",
            )
            result = run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(patch),
                "--output",
                str(after),
                "--allow-geometry-updates",
            )
            report = json.loads(result.stdout)
            self.assertEqual(report["patch_receipt"]["auto_rerouted_edges"], ["edge-a", "edge-b"])
            self.assertEqual(report["warnings"], [])
            compare = run_tool(
                "compare",
                "--before",
                str(before),
                "--after",
                str(after),
                "--changes",
                str(patch),
            )
            self.assertTrue(json.loads(compare.stdout)["preserved"])

    def test_geometry_update_preserves_valid_manual_waypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec_path = directory / "manual-route.json"
            write_linear_v2_spec(spec_path)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            spec["edges"][0]["waypoints"] = [{"x": 110, "y": 150}]
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            before = directory / "manual-route.drawio"
            after = directory / "manual-route-moved.drawio"
            run_tool("build", "--spec", str(spec_path), "--output", str(before))
            patch = directory / "move-vertical.json"
            patch.write_text(
                json.dumps({"update_nodes": [{"id": "step", "y": 155}]}),
                encoding="utf-8",
            )
            result = run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(patch),
                "--output",
                str(after),
                "--allow-geometry-updates",
            )
            self.assertEqual(json.loads(result.stdout)["patch_receipt"]["auto_rerouted_edges"], [])
            before_inspect = json.loads(run_tool("inspect", "--input", str(before)).stdout)
            after_inspect = json.loads(run_tool("inspect", "--input", str(after)).stdout)
            before_edge = next(edge for edge in before_inspect["edges"] if edge["id"] == "edge-a")
            after_edge = next(edge for edge in after_inspect["edges"] if edge["id"] == "edge-a")
            self.assertEqual(before_edge["waypoints"], after_edge["waypoints"])

    def test_invalid_replacement_main_path_is_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            before = self.build_diagram(directory)
            patch = directory / "invalid-main-patch.json"
            patch.write_text(
                json.dumps({"main_path": ["start", "step-a", "condition", "end"]}),
                encoding="utf-8",
            )
            output = directory / "invalid-main.drawio"
            result = run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(patch),
                "--output",
                str(output),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            report = json.loads(result.stdout)
            self.assertEqual(report["diagnostics"][0]["code"], "delivery/validation-failed")

    def test_deleting_node_requires_explicit_incident_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            before = self.build_diagram(directory)
            patch = directory / "unsafe-delete.json"
            patch.write_text(json.dumps({"delete_nodes": ["step-b"]}), encoding="utf-8")
            result = run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(patch),
                "--output",
                str(directory / "unsafe.drawio"),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["diagnostics"][0]["code"], "patch/incident-edge")

    def test_declared_deletion_preserves_unrelated_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            before = self.build_diagram(directory)
            expanded = directory / "expanded.drawio"
            run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(FIXTURES / "neutral-patch.json"),
                "--output",
                str(expanded),
            )
            delete_patch = directory / "delete.json"
            delete_patch.write_text(
                json.dumps(
                    {
                        "delete_nodes": ["note-new", "step-new"],
                        "delete_edges": ["edge-new-a", "edge-new-b"],
                    }
                ),
                encoding="utf-8",
            )
            after = directory / "contracted.drawio"
            run_tool(
                "patch",
                "--input",
                str(expanded),
                "--changes",
                str(delete_patch),
                "--output",
                str(after),
            )
            validate = run_tool("validate", "--input", str(after), "--strict")
            self.assertEqual(json.loads(validate.stdout)["warnings"], [])
            compare = run_tool(
                "compare",
                "--before",
                str(expanded),
                "--after",
                str(after),
                "--changes",
                str(delete_patch),
            )
            report = json.loads(compare.stdout)
            self.assertTrue(report["preserved"])
            self.assertEqual(report["unexpected_missing"], [])

    def test_phase_patch_is_inspectable_and_preserves_other_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            before = self.build_diagram(directory)
            patch = directory / "phases.json"
            patch.write_text(
                json.dumps(
                    {
                        "update_phases": [{"id": "phase-a", "label": "Phase Updated"}],
                        "delete_phases": ["phase-b"],
                        "phases": [
                            {"id": "phase-c", "label": "Phase C", "from_rank": 4, "to_rank": 5}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            after = directory / "phases-updated.drawio"
            run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(patch),
                "--output",
                str(after),
            )
            report = json.loads(run_tool("inspect", "--input", str(after)).stdout)
            phases = {phase["id"]: phase for phase in report["phases"]}
            self.assertEqual(set(phases), {"phase-a", "phase-c"})
            self.assertEqual(phases["phase-a"]["label"], "Phase Updated")
            compare = run_tool(
                "compare",
                "--before",
                str(before),
                "--after",
                str(after),
                "--changes",
                str(patch),
            )
            self.assertTrue(json.loads(compare.stdout)["preserved"])

    def test_existing_output_is_not_replaced_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            output = self.build_diagram(directory)
            before_hash = hashlib.sha256(output.read_bytes()).hexdigest()
            result = run_tool(
                "build",
                "--spec",
                str(FIXTURES / "neutral-flow.json"),
                "--output",
                str(output),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["diagnostics"][0]["code"], "delivery/output-exists")
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), before_hash)

    def test_v2_reports_text_overflow_risk(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "long-label.json"
            output = directory / "long-label.drawio"
            write_linear_v2_spec(spec, long_label=True)
            value = json.loads(spec.read_text(encoding="utf-8"))
            value["nodes"][1]["height"] = 42
            spec.write_text(json.dumps(value), encoding="utf-8")
            build = run_tool("build", "--spec", str(spec), "--output", str(output))
            report = json.loads(build.stdout)
            self.assertIn("text/node-overflow-risk", {item["code"] for item in report["diagnostics"]})

    def test_fixed_aspect_nodes_sync_single_size_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "geometry.json"
            output = directory / "geometry.drawio"
            write_geometry_spec(spec)

            build = run_tool("build", "--spec", str(spec), "--output", str(output))
            self.assertEqual(json.loads(build.stdout)["warnings"], [])
            report = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            nodes = {node["id"]: node for node in report["nodes"]}
            self.assertEqual((nodes["start"]["width"], nodes["start"]["height"]), (48.0, 48.0))
            self.assertEqual((nodes["end"]["width"], nodes["end"]["height"]), (52.0, 52.0))

    def test_fixed_aspect_node_rejects_unequal_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "geometry.json"
            output = directory / "geometry.drawio"
            write_geometry_spec(spec)
            value = json.loads(spec.read_text(encoding="utf-8"))
            value["nodes"][0].update({"width": 48, "height": 52})
            spec.write_text(json.dumps(value), encoding="utf-8")

            result = run_tool("build", "--spec", str(spec), "--output", str(output), check=False)
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(result.stdout)
            self.assertEqual(report["diagnostics"][0]["code"], "geometry/fixed-aspect-ratio")

    def test_solid_end_node_rejects_nonempty_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "geometry.json"
            output = directory / "geometry.drawio"
            write_geometry_spec(spec)
            value = json.loads(spec.read_text(encoding="utf-8"))
            value["nodes"][-1]["label"] = "Complete"
            spec.write_text(json.dumps(value), encoding="utf-8")

            result = run_tool("build", "--spec", str(spec), "--output", str(output), check=False)
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(result.stdout)
            self.assertEqual(report["diagnostics"][0]["code"], "schema/end-label-not-empty")

    def test_end_node_is_unlabeled_solid_black_circle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "geometry.json"
            output = directory / "geometry.drawio"
            write_geometry_spec(spec)
            run_tool("build", "--spec", str(spec), "--output", str(output))

            tree = ET.parse(output)
            end = next(
                cell
                for cell in tree.iter("mxCell")
                if cell.attrib.get("data-semantic-id") == "end"
            )
            self.assertEqual(end.attrib.get("value"), "")
            self.assertIn("ellipse", end.attrib["style"])
            self.assertIn("aspect=fixed", end.attrib["style"])
            self.assertIn("fillColor=#333333", end.attrib["style"])
            self.assertNotIn("fontColor=#ffffff", end.attrib["style"])

    def test_strict_validation_reports_malformed_existing_end_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "geometry.json"
            output = directory / "geometry.drawio"
            write_geometry_spec(spec)
            run_tool("build", "--spec", str(spec), "--output", str(output))

            tree = ET.parse(output)
            end = next(
                cell
                for cell in tree.iter("mxCell")
                if cell.attrib.get("data-semantic-id") == "end"
            )
            end.attrib["value"] = "Complete"
            geometry = end.find("mxGeometry")
            self.assertIsNotNone(geometry)
            geometry.attrib["height"] = "48"
            tree.write(output, encoding="utf-8", xml_declaration=False)

            validate = run_tool("validate", "--input", str(output), "--strict", check=False)
            self.assertNotEqual(validate.returncode, 0)
            codes = {item["code"] for item in json.loads(validate.stdout)["diagnostics"]}
            self.assertIn("schema/end-label-not-empty", codes)
            self.assertIn("geometry/fixed-aspect-ratio", codes)

    def test_multiline_process_uses_recommended_automatic_height(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "geometry.json"
            output = directory / "geometry.drawio"
            write_geometry_spec(spec)

            run_tool("build", "--spec", str(spec), "--output", str(output))
            report = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            step = next(node for node in report["nodes"] if node["id"] == "step")
            self.assertEqual(step["height"], 52.0)
            self.assertEqual(report["validation"]["warnings"], [])

    def test_excessive_process_height_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "geometry.json"
            output = directory / "geometry.drawio"
            write_geometry_spec(spec)
            value = json.loads(spec.read_text(encoding="utf-8"))
            value["nodes"][1]["height"] = 80
            spec.write_text(json.dumps(value), encoding="utf-8")

            build = run_tool("build", "--spec", str(spec), "--output", str(output))
            report = json.loads(build.stdout)
            self.assertIn(
                "layout/excessive-node-height",
                {item["code"] for item in report["diagnostics"]},
            )
            strict = run_tool("validate", "--input", str(output), "--strict", check=False)
            self.assertNotEqual(strict.returncode, 0)

    def test_unintentional_port_reuse_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            before = self.build_diagram(directory)
            after = directory / "collision.drawio"
            changes = directory / "collision.json"
            changes.write_text(
                json.dumps(
                    {
                        "update_edges": [
                            {
                                "id": "edge-5",
                                "reroute": True,
                                "entry_side": "top",
                                "entry_offset": 0.5,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            result = run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(changes),
                "--output",
                str(after),
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("port", (result.stdout + result.stderr).lower())

    def test_retry_corridor_keeps_safe_lane_boundary_clearance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "boundary.json"
            output = directory / "boundary.drawio"
            write_boundary_spec(spec, include_forward=True)

            build = run_tool("build", "--spec", str(spec), "--output", str(output))
            self.assertEqual(json.loads(build.stdout)["warnings"], [])

            tree = ET.parse(output)
            retry = next(
                cell
                for cell in tree.iter("mxCell")
                if cell.attrib.get("data-semantic-id") == "retry"
            )
            points = retry.findall("./mxGeometry/Array[@as='points']/mxPoint")
            self.assertTrue(points)
            self.assertTrue(all(abs(float(point.attrib["x"]) - 220) >= 16 for point in points))

    def test_adjacent_decision_forward_route_uses_single_safe_elbow(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "adjacent-decision.json"
            output = directory / "adjacent-decision.drawio"
            write_adjacent_decision_spec(spec)

            result = run_tool("build", "--spec", str(spec), "--output", str(output))
            self.assertEqual(json.loads(result.stdout)["warnings"], [])
            report = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            forward = next(edge for edge in report["edges"] if edge["id"] == "edge-forward")
            self.assertEqual(forward["exit_side"], "right")
            self.assertEqual(forward["entry_side"], "top")
            self.assertEqual(forward["waypoints"], [{"x": 570.0, "y": 396.0}])
            self.assertGreaterEqual(abs(forward["waypoints"][0]["x"] - 500.0), 16)

    def test_back_route_prefers_target_lane_internal_gutter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "adjacent-decision.json"
            output = directory / "adjacent-decision.drawio"
            write_adjacent_decision_spec(spec)

            run_tool("build", "--spec", str(spec), "--output", str(output))
            report = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            back = next(edge for edge in report["edges"] if edge["id"] == "edge-back")
            self.assertEqual(back["label"], "Return")
            self.assertEqual(back["route"], "back")
            vertical_x = {point["x"] for point in back["waypoints"]}
            self.assertEqual(len(vertical_x), 1)
            corridor_x = vertical_x.pop()
            self.assertGreaterEqual(corridor_x - 180.0, 16.0)
            self.assertLess(corridor_x, 204.0)
            validation = report["validation"]
            self.assertTrue(validation["valid"])
            self.assertEqual(validation["warnings"], [])

    def test_narrow_back_target_lane_is_expanded_before_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "adjacent-decision.json"
            output = directory / "adjacent-decision.drawio"
            write_adjacent_decision_spec(spec)
            value = json.loads(spec.read_text(encoding="utf-8"))
            next(lane for lane in value["lanes"] if lane["id"] == "lane-b")["width"] = 140
            spec.write_text(json.dumps(value), encoding="utf-8")

            result = run_tool("build", "--spec", str(spec), "--output", str(output))
            self.assertEqual(json.loads(result.stdout)["warnings"], [])
            report = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            lanes = {lane["id"]: lane for lane in report["lanes"]}
            nodes = {node["id"]: node for node in report["nodes"]}
            self.assertGreaterEqual(lanes["lane-b"]["width"], 168.0)
            expected_x = (lanes["lane-b"]["width"] - nodes["history"]["width"]) / 2
            self.assertEqual(nodes["history"]["x"], expected_x)
            self.assertEqual(
                lanes["lane-c"]["x"],
                lanes["lane-b"]["x"] + lanes["lane-b"]["width"],
            )
            back = next(edge for edge in report["edges"] if edge["id"] == "edge-back")
            corridor_x = back["waypoints"][0]["x"]
            self.assertGreaterEqual(corridor_x - lanes["lane-b"]["x"], 16.0)
            self.assertLess(
                corridor_x,
                lanes["lane-b"]["x"] + nodes["history"]["x"],
            )
            self.assertEqual(report["validation"]["warnings"], [])

    def test_back_route_outside_target_lane_is_diagnosed_when_gutter_is_unavailable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "adjacent-decision.json"
            output = directory / "adjacent-decision.drawio"
            write_adjacent_decision_spec(spec)
            value = json.loads(spec.read_text(encoding="utf-8"))
            history = next(node for node in value["nodes"] if node["id"] == "history")
            history.update({"x": 4, "width": 172})
            spec.write_text(json.dumps(value), encoding="utf-8")

            result = run_tool("build", "--spec", str(spec), "--output", str(output))
            report = json.loads(result.stdout)
            codes = {diagnostic["code"] for diagnostic in report["diagnostics"]}
            self.assertIn("routing/back-corridor-outside-target-lane", codes)

    def test_explicit_waypoints_are_not_simplified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "adjacent-decision.json"
            output = directory / "adjacent-decision.drawio"
            write_adjacent_decision_spec(spec)
            value = json.loads(spec.read_text(encoding="utf-8"))
            explicit = [{"x": 520, "y": 396}, {"x": 520, "y": 430}, {"x": 570, "y": 430}]
            next(edge for edge in value["edges"] if edge["id"] == "edge-forward")["waypoints"] = explicit
            spec.write_text(json.dumps(value), encoding="utf-8")

            run_tool("build", "--spec", str(spec), "--output", str(output))
            report = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            forward = next(edge for edge in report["edges"] if edge["id"] == "edge-forward")
            self.assertEqual(
                forward["waypoints"],
                [
                    {"x": 520.0, "y": 396.0},
                    {"x": 520.0, "y": 430.0},
                    {"x": 570.0, "y": 430.0},
                ],
            )

    def test_near_lane_boundary_connector_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "boundary.json"
            output = directory / "boundary.drawio"
            write_boundary_spec(spec, include_forward=False)
            run_tool("build", "--spec", str(spec), "--output", str(output))

            tree = ET.parse(output)
            retry = next(
                cell
                for cell in tree.iter("mxCell")
                if cell.attrib.get("data-semantic-id") == "retry"
            )
            for point in retry.findall("./mxGeometry/Array[@as='points']/mxPoint"):
                point.attrib["x"] = "228"
            tree.write(output, encoding="utf-8", xml_declaration=False)

            validate = run_tool(
                "validate", "--input", str(output), "--strict", check=False
            )
            self.assertNotEqual(validate.returncode, 0)
            report = json.loads(validate.stdout)
            self.assertTrue(
                any("too close to a lane boundary" in warning for warning in report["warnings"])
            )


if __name__ == "__main__":
    unittest.main()
