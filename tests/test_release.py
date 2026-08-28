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
        install_command = "npx skills add zz-zed/product-swimlane-drawio"
        self.assertIn(install_command, english)
        self.assertIn(install_command, chinese)
        self.assertNotIn("--skill product-swimlane-drawio", english)
        self.assertNotIn("--skill product-swimlane-drawio", chinese)
        self.assertIn("### Agent Skills quick install", english)
        self.assertIn("### Claude Code Plugin Marketplace", english)
        self.assertIn("### Codex Plugin Marketplace", english)
        self.assertIn("### Ask an agent", english)
        self.assertIn("### Verify installation", english)
        self.assertIn("### Agent Skills 快速安装", chinese)
        self.assertIn("### Claude Code Plugin Marketplace", chinese)
        self.assertIn("### Codex Plugin Marketplace", chinese)
        self.assertIn("### 告诉 Agent 安装", chinese)
        self.assertIn("### 验证安装结果", chinese)
        self.assertIn("github.com/zz-zed/product-swimlane-drawio", english)
        self.assertIn("github.com/zz-zed/product-swimlane-drawio", chinese)
        self.assertIn("npx skills list -g", english)
        self.assertIn("npx skills list -g", chinese)
        self.assertIn("codex plugin marketplace add zz-zed/product-swimlane-drawio", english)
        self.assertIn("codex plugin marketplace add zz-zed/product-swimlane-drawio", chinese)
        self.assertIn("/plugin marketplace add zz-zed/product-swimlane-drawio", english)
        self.assertIn("/plugin marketplace add zz-zed/product-swimlane-drawio", chinese)
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

    def test_plugin_marketplace_manifests_share_one_skill_source(self) -> None:
        claude_plugin = json.loads(
            (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        claude_marketplace = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        codex_plugin = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        codex_marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )

        plugin_name = "product-swimlane-drawio"
        self.assertEqual(claude_plugin["name"], plugin_name)
        self.assertEqual(codex_plugin["name"], plugin_name)
        self.assertNotIn("version", claude_plugin)
        self.assertEqual(claude_marketplace["plugins"][0]["name"], plugin_name)
        self.assertEqual(claude_marketplace["plugins"][0]["source"], ".")
        self.assertEqual(
            claude_marketplace["plugins"][0]["version"], codex_plugin["version"]
        )
        self.assertEqual(codex_plugin["version"], "0.3.1")
        self.assertEqual(codex_marketplace["plugins"][0]["name"], plugin_name)
        self.assertEqual(
            codex_marketplace["plugins"][0]["source"],
            {"source": "local", "path": "."},
        )
        self.assertEqual(codex_plugin["skills"], "./skills/")
        self.assertTrue((SKILL / "SKILL.md").is_file())

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

    def test_inspect_reports_manually_redrawn_connector_without_semantic_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "linear.json"
            diagram = directory / "linear.drawio"
            write_linear_v2_spec(spec)
            run_tool("build", "--spec", str(spec), "--output", str(diagram))

            tree = ET.parse(diagram)
            edge = next(
                cell
                for cell in tree.iter("mxCell")
                if cell.attrib.get("data-semantic-id") == "edge-a"
            )
            edge.attrib["id"] = "drawio-redrawn-connector"
            for attribute in (
                "data-kind",
                "data-semantic-id",
                "data-from",
                "data-to",
                "data-edge-type",
                "data-route",
            ):
                edge.attrib.pop(attribute, None)
            tree.write(diagram, encoding="utf-8", xml_declaration=True)

            report = json.loads(run_tool("inspect", "--input", str(diagram)).stdout)
            self.assertEqual(len(report["unmanaged_edges"]), 1)
            recovered = report["unmanaged_edges"][0]
            self.assertEqual((recovered["from"], recovered["to"]), ("start", "step"))
            codes = {item["code"] for item in report["validation"]["diagnostics"]}
            self.assertIn("interoperability/unmanaged-edges", codes)

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

    def test_phase_backgrounds_precede_editable_layers_and_lanes_are_transparent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            diagram = self.build_diagram(Path(temp))
            root = ET.parse(diagram).find("./diagram/mxGraphModel/root")
            self.assertIsNotNone(root)
            layer_order = {"phase": 0, "lane": 1, "node": 2, "edge": 3}
            layers = [
                layer_order[cell.attrib["data-kind"]]
                for cell in list(root)
                if cell.attrib.get("data-kind") in layer_order
            ]
            self.assertEqual(layers, sorted(layers))
            lanes = [
                cell for cell in list(root) if cell.attrib.get("data-kind") == "lane"
            ]
            self.assertTrue(lanes)
            self.assertTrue(
                all("swimlaneFillColor=none;" in lane.attrib.get("style", "") for lane in lanes)
            )
            phases = [
                cell for cell in list(root) if cell.attrib.get("data-kind") == "phase"
            ]
            self.assertTrue(phases)
            self.assertTrue(
                all(
                    phase.attrib.get("connectable") == "0"
                    and "pointerEvents=0;" in phase.attrib.get("style", "")
                    for phase in phases
                )
            )

    def test_without_phases_lane_bodies_remain_opaque(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = json.loads((FIXTURES / "neutral-flow.json").read_text(encoding="utf-8"))
            spec.pop("phases", None)
            spec_path = directory / "no-phases.json"
            output = directory / "no-phases.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_tool("build", "--spec", str(spec_path), "--output", str(output))
            lanes = [
                cell
                for cell in ET.parse(output).iter("mxCell")
                if cell.attrib.get("data-kind") == "lane"
            ]
            self.assertTrue(lanes)
            self.assertTrue(
                all(
                    "swimlaneFillColor=#ffffff;" in lane.attrib.get("style", "")
                    for lane in lanes
                )
            )

    def test_strict_validate_rejects_phase_above_editable_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            diagram = self.build_diagram(directory)
            tree = ET.parse(diagram)
            root = tree.find("./diagram/mxGraphModel/root")
            self.assertIsNotNone(root)
            phase = next(
                cell for cell in list(root) if cell.attrib.get("data-kind") == "phase"
            )
            root.remove(phase)
            root.append(phase)
            broken = directory / "phase-above-content.drawio"
            tree.write(broken, encoding="utf-8", xml_declaration=True)

            result = run_tool(
                "validate", "--input", str(broken), "--strict", check=False
            )
            self.assertNotEqual(result.returncode, 0)
            report = json.loads(result.stdout)
            self.assertIn(
                "layout/phase-z-order",
                {item["code"] for item in report["diagnostics"]},
            )

    def test_patch_adds_phase_with_safe_layering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = json.loads((FIXTURES / "neutral-flow.json").read_text(encoding="utf-8"))
            spec.pop("phases", None)
            spec_path = directory / "before.json"
            before = directory / "before.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_tool("build", "--spec", str(spec_path), "--output", str(before))
            changes = directory / "add-phase.json"
            changes.write_text(
                json.dumps(
                    {
                        "phases": [
                            {
                                "id": "phase-new",
                                "label": "New phase",
                                "from_rank": 2,
                                "to_rank": 4,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            after = directory / "after.drawio"
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
            root = ET.parse(after).find("./diagram/mxGraphModel/root")
            self.assertIsNotNone(root)
            kinds = [
                cell.attrib.get("data-kind")
                for cell in list(root)
                if cell.attrib.get("data-kind") in {"phase", "lane", "node", "edge"}
            ]
            self.assertLess(kinds.index("phase"), kinds.index("lane"))
            lanes = [cell for cell in list(root) if cell.attrib.get("data-kind") == "lane"]
            self.assertTrue(
                all("swimlaneFillColor=none;" in lane.attrib.get("style", "") for lane in lanes)
            )

    def test_patch_without_phases_preserves_custom_lane_fill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = json.loads((FIXTURES / "neutral-flow.json").read_text(encoding="utf-8"))
            spec.pop("phases", None)
            spec_path = directory / "custom-lane.json"
            before = directory / "custom-lane.drawio"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_tool("build", "--spec", str(spec_path), "--output", str(before))
            tree = ET.parse(before)
            lane = next(
                cell
                for cell in tree.iter("mxCell")
                if cell.attrib.get("data-kind") == "lane"
            )
            lane.attrib["style"] = lane.attrib["style"].replace(
                "swimlaneFillColor=#ffffff",
                "swimlaneFillColor=#ffeeee",
            )
            tree.write(before, encoding="utf-8", xml_declaration=True)
            changes = directory / "label-only.json"
            changes.write_text(
                json.dumps({"update_nodes": [{"id": "step-a", "label": "Updated"}]}),
                encoding="utf-8",
            )
            after = directory / "custom-lane-updated.drawio"
            run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(changes),
                "--output",
                str(after),
            )
            patched_lane = next(
                cell
                for cell in ET.parse(after).iter("mxCell")
                if cell.attrib.get("data-semantic-id") == lane.attrib["data-semantic-id"]
            )
            self.assertIn("swimlaneFillColor=#ffeeee;", patched_lane.attrib["style"])

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

    def test_cross_lane_adjacent_rank_flow_prefers_center_ports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "center-ports.json"
            output = directory / "center-ports.drawio"
            write_adjacent_decision_spec(spec)

            run_tool("build", "--spec", str(spec), "--output", str(output))
            report = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            edges = {edge["id"]: edge for edge in report["edges"]}

            # These adjacent-rank main-path edges cross lanes vertically.  A
            # clear centered carrier is preferable to 0.1/0.9 endpoint
            # staggering that only reduces the horizontal span locally.
            for edge_id in ("edge-b", "edge-c"):
                self.assertEqual(edges[edge_id]["exit_side"], "bottom")
                self.assertEqual(edges[edge_id]["entry_side"], "top")
                self.assertEqual(edges[edge_id]["exit_offset"], 0.5)
                self.assertEqual(edges[edge_id]["entry_offset"], 0.5)

            pool = next(
                cell
                for cell in ET.parse(output).iter("mxCell")
                if cell.attrib.get("data-kind") == "pool"
            )
            self.assertEqual(float(pool.attrib["data-row-gap"]), 96.0)

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

    def test_same_lane_main_path_stays_vertical_and_retry_uses_side_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "paired-return.json"
            output = directory / "paired-return.drawio"
            spec.write_text(
                json.dumps(
                    {
                        "schema_version": "2",
                        "title": "Paired Return",
                        "lanes": [
                            {"id": "lane-a", "label": "Lane A", "width": 180},
                            {"id": "lane-b", "label": "Lane B", "width": 180},
                        ],
                        "nodes": [
                            {"id": "start", "lane": "lane-a", "rank": 1, "type": "start", "label": ""},
                            {"id": "check", "lane": "lane-a", "rank": 2, "type": "decision", "label": "Check"},
                            {"id": "work", "lane": "lane-a", "rank": 3, "type": "process", "label": "Work"},
                            {"id": "alternate", "lane": "lane-b", "rank": 3, "type": "process", "label": "Alternate"},
                            {"id": "end", "lane": "lane-a", "rank": 4, "type": "end", "label": ""},
                        ],
                        "edges": [
                            {"id": "edge-start", "from": "start", "to": "check"},
                            {"id": "edge-main", "from": "check", "to": "work", "branch": "positive", "label": "Continue"},
                            {"id": "edge-alt", "from": "check", "to": "alternate", "branch": "negative", "label": "Alternate"},
                            {"id": "edge-return", "from": "work", "to": "check", "type": "retry", "label": "Recheck"},
                            {"id": "edge-end", "from": "work", "to": "end"},
                        ],
                        "main_path": ["start", "check", "work", "end"],
                    }
                ),
                encoding="utf-8",
            )
            build = json.loads(
                run_tool("build", "--spec", str(spec), "--output", str(output)).stdout
            )
            self.assertEqual(build["warnings"], [])
            report = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            edges = {edge["id"]: edge for edge in report["edges"]}
            self.assertEqual(edges["edge-main"]["exit_side"], "bottom")
            self.assertEqual(edges["edge-main"]["entry_side"], "top")
            self.assertNotIn("waypoints", edges["edge-main"])
            self.assertTrue(edges["edge-return"].get("waypoints"))
            self.assertEqual(report["validation"]["short_segments"], 0)
            self.assertEqual(report["validation"]["reciprocal_ambiguities"], 0)

    def test_same_rank_long_label_and_phase_crossing_are_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "same-rank.json"
            output = directory / "same-rank.drawio"
            spec.write_text(
                json.dumps(
                    {
                        "schema_version": "2",
                        "title": "Same Rank",
                        "lanes": [
                            {"id": "lane-a", "label": "Lane A", "width": 260},
                            {"id": "lane-b", "label": "Lane B", "width": 260},
                            {"id": "lane-c", "label": "Lane C", "width": 260},
                        ],
                        "nodes": [
                            {"id": "start", "lane": "lane-a", "rank": 1, "type": "start", "label": ""},
                            {"id": "send", "lane": "lane-a", "rank": 2, "type": "process", "label": "Send"},
                            {"id": "receive", "lane": "lane-c", "rank": 2, "type": "process", "label": "Receive"},
                            {"id": "end", "lane": "lane-c", "rank": 3, "type": "end", "label": ""},
                        ],
                        "edges": [
                            {"id": "edge-start", "from": "start", "to": "send"},
                            {
                                "id": "edge-side",
                                "from": "send",
                                "to": "receive",
                                "label": "条件满足后进入下一处理环节",
                            },
                            {"id": "edge-end", "from": "receive", "to": "end"},
                        ],
                        "main_path": ["start", "send", "receive", "end"],
                        "phases": [
                            {"id": "phase-a", "label": "Phase A", "from_rank": 1, "to_rank": 2},
                            {"id": "phase-b", "label": "Phase B", "from_rank": 3, "to_rank": 3},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = json.loads(
                run_tool("build", "--spec", str(spec), "--output", str(output)).stdout
            )
            self.assertEqual(report["warnings"], [])
            self.assertEqual(report["label_conflicts"], 0)
            inspected = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            side = next(edge for edge in inspected["edges"] if edge["id"] == "edge-side")
            self.assertEqual(side["route"], "side")
            self.assertEqual(side["exit_side"], "right")
            self.assertEqual(side["entry_side"], "left")

    def test_explicit_waypoint_quality_issues_are_diagnosed_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "manual-quality.json"
            output = directory / "manual-quality.drawio"
            write_adjacent_decision_spec(spec)
            value = json.loads(spec.read_text(encoding="utf-8"))
            explicit = [
                {"x": 520, "y": 396},
                {"x": 510, "y": 396},
                {"x": 510, "y": 430},
                {"x": 570, "y": 430},
            ]
            next(edge for edge in value["edges"] if edge["id"] == "edge-forward")[
                "waypoints"
            ] = explicit
            spec.write_text(json.dumps(value), encoding="utf-8")

            build = json.loads(
                run_tool("build", "--spec", str(spec), "--output", str(output)).stdout
            )
            codes = {item["code"] for item in build["diagnostics"]}
            self.assertIn("routing/short-segment", codes)
            self.assertIn("routing/hairpin", codes)
            strict = run_tool(
                "validate", "--input", str(output), "--strict", check=False
            )
            self.assertNotEqual(strict.returncode, 0)
            inspected = json.loads(run_tool("inspect", "--input", str(output)).stdout)
            edge = next(item for item in inspected["edges"] if item["id"] == "edge-forward")
            self.assertEqual(edge["waypoints"], [{"x": float(p["x"]), "y": float(p["y"])} for p in explicit])

    def test_near_parallel_reciprocal_and_label_diagnostics_fail_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "diagnostics.json"
            output = directory / "diagnostics.drawio"
            spec.write_text(
                json.dumps(
                    {
                        "title": "Diagnostics",
                        "canvas": {"row_gap": 60},
                        "lanes": [{"id": "lane-a", "label": "Lane A", "width": 220}],
                        "nodes": [
                            {"id": "first", "lane": "lane-a", "rank": 1, "type": "process", "label": "First"},
                            {"id": "second", "lane": "lane-a", "rank": 2, "type": "process", "label": "Second"},
                        ],
                        "edges": [
                            {"id": "forward", "from": "first", "to": "second", "label": "A label that has no clear carrier span"},
                            {
                                "id": "return",
                                "from": "second",
                                "to": "first",
                                "type": "retry",
                                "exit_side": "top",
                                "entry_side": "bottom",
                                "exit_offset": 0.6,
                                "entry_offset": 0.6,
                                "waypoints": [],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            build = json.loads(
                run_tool("build", "--spec", str(spec), "--output", str(output)).stdout
            )
            codes = {item["code"] for item in build["diagnostics"]}
            self.assertIn("routing/near-parallel-conflict", codes)
            self.assertIn("routing/reciprocal-ambiguity", codes)
            self.assertIn("text/edge-label-no-clear-span", codes)
            strict = run_tool(
                "validate", "--input", str(output), "--strict", check=False
            )
            self.assertNotEqual(strict.returncode, 0)

    def test_legacy_v2_without_new_route_metadata_remains_patchable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "legacy-v2.json"
            before = directory / "legacy-v2.drawio"
            after = directory / "legacy-v2-patched.drawio"
            changes = directory / "changes.json"
            write_linear_v2_spec(spec)
            run_tool("build", "--spec", str(spec), "--output", str(before))
            tree = ET.parse(before)
            for cell in tree.iter("mxCell"):
                if cell.attrib.get("data-kind") != "edge":
                    continue
                for key in list(cell.attrib):
                    if key.endswith("-explicit"):
                        cell.attrib.pop(key)
            tree.write(before, encoding="utf-8", xml_declaration=False)
            changes.write_text(
                json.dumps(
                    {
                        "update_edges": [
                            {"id": "edge-a", "label": "Updated", "reroute": True}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            run_tool(
                "patch",
                "--input",
                str(before),
                "--changes",
                str(changes),
                "--output",
                str(after),
            )
            inspected = json.loads(run_tool("inspect", "--input", str(after)).stdout)
            self.assertTrue(inspected["compatible"])
            self.assertEqual(inspected["schema_version"], "2")
            self.assertEqual(inspected["validation"]["warnings"], [])

    def test_zigzag_excessive_bends_and_label_overlaps_are_diagnosed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            spec = directory / "zigzag.json"
            output = directory / "zigzag.drawio"
            write_linear_v2_spec(spec)
            run_tool("build", "--spec", str(spec), "--output", str(output))
            tree = ET.parse(output)
            edges = {
                cell.attrib.get("data-semantic-id"): cell
                for cell in tree.iter("mxCell")
                if cell.attrib.get("data-kind") == "edge"
            }
            zigzag = edges["edge-a"]
            zigzag.attrib["data-waypoints-origin"] = "explicit"
            geometry = zigzag.find("mxGeometry")
            self.assertIsNotNone(geometry)
            assert geometry is not None
            for child in list(geometry):
                geometry.remove(child)
            points = ET.SubElement(geometry, "Array", {"as": "points"})
            for x, y in ((110, 142), (140, 142), (140, 167), (110, 167)):
                ET.SubElement(points, "mxPoint", {"x": str(x), "y": str(y)})

            for edge in (edges["edge-a"], edges["edge-b"]):
                edge.attrib.update(
                    {
                        "value": "Overlap",
                        "data-label-left": "44",
                        "data-label-top": "183",
                        "data-label-width": "60",
                        "data-label-height": "18",
                        "data-label-segment": "1",
                    }
                )
            tree.write(output, encoding="utf-8", xml_declaration=False)

            validate = run_tool(
                "validate", "--input", str(output), "--strict", check=False
            )
            self.assertNotEqual(validate.returncode, 0)
            codes = {
                item["code"]
                for item in json.loads(validate.stdout)["diagnostics"]
            }
            self.assertIn("routing/excessive-bends", codes)
            self.assertIn("layout/main-path-zigzag", codes)
            self.assertIn("text/edge-label-node-overlap", codes)
            self.assertIn("text/edge-label-edge-overlap", codes)


if __name__ == "__main__":
    unittest.main()
