import json
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


class ReleasePackageTests(unittest.TestCase):
    def test_readme_language_navigation_and_structure(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("[简体中文](README.zh-CN.md)", english)
        self.assertIn("[English](README.md)", chinese)
        image_reference = "![product-swimlane-drawio overview](docs/product-swimlane-overview.png)"
        self.assertIn(image_reference, english)
        self.assertIn(image_reference, chinese)
        self.assertEqual(english.count("\n## "), chinese.count("\n## "))
        self.assertEqual(english.count("```"), chinese.count("```"))

    def test_readme_infographic_is_valid_landscape_png(self) -> None:
        image_path = ROOT / "docs" / "product-swimlane-overview.png"
        data = image_path.read_bytes()
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
                "references/schema.md",
                "scripts/drawio_swimlane.py",
            },
        )

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
