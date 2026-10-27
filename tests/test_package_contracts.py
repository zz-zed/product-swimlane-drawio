"""Public package, documentation, example and schema contract checks."""

import ast
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest
from urllib.parse import urlsplit

from cli_test_support import (
    FIXTURES, ROOT, SKILL, TOOL, TOOL_VERSION, load_tool_module, run_tool,
)
from release_check import check_release, skill_inventory, EXPECTED_SKILL_FILES
from swimlane_loader import load_skill_modules


def load_spec_validation_module():
    return load_skill_modules(
        TOOL, module_name="spec_validation_under_test"
    ).spec_validation



def schema_matches(value, rule: dict, root: dict) -> bool:
    if "$ref" in rule:
        target = root
        for part in rule["$ref"].removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return schema_matches(value, target, root)
    if "allOf" in rule and not all(schema_matches(value, item, root) for item in rule["allOf"]):
        return False
    if "oneOf" in rule and sum(schema_matches(value, item, root) for item in rule["oneOf"]) != 1:
        return False
    if "anyOf" in rule and not any(schema_matches(value, item, root) for item in rule["anyOf"]):
        return False
    if "not" in rule and schema_matches(value, rule["not"], root):
        return False
    if "if" in rule:
        branch = rule.get("then") if schema_matches(value, rule["if"], root) else rule.get("else")
        if branch is not None and not schema_matches(value, branch, root):
            return False
    if "const" in rule and value != rule["const"]:
        return False
    if "enum" in rule and value not in rule["enum"]:
        return False

    expected_type = rule.get("type")
    if expected_type == "object" and not isinstance(value, dict):
        return False
    if expected_type == "array" and not isinstance(value, list):
        return False
    if expected_type == "string" and not isinstance(value, str):
        return False
    if expected_type == "boolean" and not isinstance(value, bool):
        return False
    if expected_type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return False
    if expected_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return False

    if isinstance(value, dict):
        required = set(rule.get("required", []))
        if not required.issubset(value):
            return False
        properties = rule.get("properties", {})
        if rule.get("additionalProperties") is False and set(value) - set(properties):
            return False
        for key, child in properties.items():
            if key in value and not schema_matches(value[key], child, root):
                return False
    if isinstance(value, list):
        if len(value) < rule.get("minItems", 0) or len(value) > rule.get("maxItems", float("inf")):
            return False
        if rule.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                return False
        for index, child in enumerate(rule.get("prefixItems", [])):
            if index < len(value) and not schema_matches(value[index], child, root):
                return False
        if "items" in rule and not all(schema_matches(item, rule["items"], root) for item in value):
            return False
    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0):
            return False
        if "pattern" in rule and re.fullmatch(rule["pattern"], value) is None:
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in rule and value < rule["minimum"]:
            return False
        if "exclusiveMinimum" in rule and value <= rule["exclusiveMinimum"]:
            return False
        if "maximum" in rule and value > rule["maximum"]:
            return False
    return True



class PackageContractTests(unittest.TestCase):
    def test_readme_language_navigation_and_structure(self) -> None:
        shared_links = {
            "docs/illustrations/product-swimlane-readme/create-update.png",
            "docs/illustrations/product-swimlane-readme/quality-gate.png",
            "examples/request-review/preview.png",
        }
        for name, other, language in (("README.md", "README.zh-CN.md", "en"),
                                      ("README.zh-CN.md", "README.md", "zh")):
            with self.subTest(readme=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                # Installation and command contracts survive editorial changes
                # to headings, slogans, image captions and translated structure.
                for command in (
                    "npx skills add zz-zed/product-swimlane-drawio",
                    "npx skills list -g",
                    "codex plugin marketplace add zz-zed/product-swimlane-drawio",
                    "codex plugin add product-swimlane-drawio@product-swimlane-drawio",
                    "/plugin marketplace add zz-zed/product-swimlane-drawio",
                    "/plugin install product-swimlane-drawio@product-swimlane-drawio",
                    "build --spec process.json --output process.drawio --strict",
                ):
                    self.assertIn(command, text)
                self.assertNotIn("--skill product-swimlane-drawio", text)
                self.assertIn("github.com/zz-zed/product-swimlane-drawio", text)
                self.assertRegex(
                    text,
                    r'patch --input process\.drawio --expected-input-sha256 "<[^">]+>" '
                    r'--changes changes\.json --output process-updated\.drawio --strict',
                )
                links = set(re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text))
                self.assertTrue((shared_links | {
                    other,
                    f"docs/illustrations/product-swimlane-readme/overview-{language}.png",
                }).issubset(links))
                for link in links:
                    target = urlsplit(link)
                    if target.scheme or target.netloc or not target.path:
                        continue
                    local = (ROOT / target.path).resolve()
                    self.assertTrue(local.is_relative_to(ROOT), link)
                    self.assertTrue(local.exists(), f"Broken local link in {name}: {link}")

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
        self.assertEqual(codex_plugin["version"], TOOL_VERSION)
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

    def test_readme_illustration_directory_contains_final_images_only(self) -> None:
        image_dir = ROOT / "docs" / "illustrations" / "product-swimlane-readme"
        relative_files = {
            path.relative_to(image_dir).as_posix()
            for path in image_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            relative_files,
            {
                "overview-en.png",
                "overview-zh.png",
                "create-update.png",
                "quality-gate.png",
            },
        )

    def test_complete_example_rebuilds_deterministically_and_strictly_validates(self) -> None:
        example = ROOT / "examples" / "request-review"
        preview = example / "preview.png"
        self.assertTrue((example / "prompt.md").is_file())
        self.assertTrue((example / "README.md").is_file())
        self.assertEqual(preview.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

        with tempfile.TemporaryDirectory() as temp:
            rebuilt = Path(temp) / "process.drawio"
            rebuilt_again = Path(temp) / "process-again.drawio"
            report = json.loads(
                run_tool(
                    "build",
                    "--spec",
                    str(example / "process.json"),
                    "--output",
                    str(rebuilt),
                ).stdout
            )
            self.assertTrue(report["valid"])
            self.assertEqual(report["warnings"], [])
            run_tool(
                "build",
                "--spec",
                str(example / "process.json"),
                "--output",
                str(rebuilt_again),
            )
            self.assertEqual(rebuilt.read_bytes(), rebuilt_again.read_bytes())
            validation = json.loads(
                run_tool("validate", "--input", str(rebuilt), "--strict").stdout
            )
            self.assertTrue(validation["valid"])
            self.assertEqual(validation["warnings"], [])

            inspected = json.loads(
                run_tool("inspect", "--input", str(rebuilt)).stdout
            )
            self.assertEqual(inspected["schema_version"], "3")
            self.assertEqual(inspected["behavior_pattern"], "approval-loop")
            self.assertEqual(inspected["layout"]["profile"], "long-form")
            self.assertEqual(inspected["layout"]["phase_presentation"], "rail")
            lanes = {lane["id"]: lane for lane in inspected["lanes"]}
            nodes = {node["id"]: node for node in inspected["nodes"]}
            retry = next(
                edge for edge in inspected["edges"] if edge["id"] == "approved-review"
            )
            self.assertEqual((retry["from"], retry["to"]), ("approved", "review"))
            self.assertEqual(nodes[retry["from"]]["lane"], "reviewer")
            self.assertEqual(nodes[retry["to"]]["lane"], "reviewer")
            corridor_x = {point["x"] for point in retry["waypoints"]}
            self.assertEqual(len(corridor_x), 1)
            corridor_x = corridor_x.pop()
            reviewer = lanes["reviewer"]
            review_left = reviewer["x"] + nodes["review"]["x"]
            self.assertGreaterEqual(corridor_x - reviewer["x"], 16.0)
            self.assertLess(corridor_x, review_left)

    def test_architecture_documents_capture_product_boundaries(self) -> None:
        architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        principles = (ROOT / "docs" / "design-principles.md").read_text(
            encoding="utf-8"
        )
        # Check documented guarantees without freezing entire prose sentences.
        for contract in (
            r"semantic JSON",
            r"inspect[^\n]*patch[^\n]*compare",
            r"patch[^.\n]*(?:bound|requires?)[^.\n]*(?:inspected|input)[^.\n]*SHA-256",
            r"strict validation[^.\n]*visual review[^.\n]*(?:independent|separate)",
            r"bounded[^.\n]*(?:compilation|pipeline)",
            r"complete Skill[^.\n]*distribution",
            r"CLI[^.\n]*(?:dispatch|argument)",
        ):
            self.assertRegex(architecture, re.compile(contract, re.IGNORECASE))
        for command in ("build", "inspect", "patch", "validate", "compare", "migrate"):
            self.assertRegex(architecture, rf"\b{command}\b")
        tool_tree = ast.parse(TOOL.read_text(encoding="utf-8"))
        core_imports = [
            node for node in tool_tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "swimlane_core"
        ]
        self.assertEqual(len(core_imports), 1)
        self.assertEqual(
            {alias.name for alias in core_imports[0].names},
            {
                "build", "context_workflow", "contracts", "document", "migration", "review_cycle", "review_workflow",
                "roundtrip", "semantic_context", "validation",
            },
        )
        self.assertEqual(
            {alias.name: alias.asname for alias in core_imports[0].names}["validation"],
            "core_validation",
        )
        modules = {
            Path(name).stem for name in EXPECTED_SKILL_FILES
            if name.startswith("scripts/swimlane_core/") and name.endswith(".py")
            and Path(name).stem != "__init__"
        }
        for module in sorted(modules):
            self.assertTrue((SKILL / "scripts" / "swimlane_core" / f"{module}.py").is_file())
            self.assertRegex(architecture, rf"(?m)^\|\s*`{re.escape(module)}`\s*\|")
        self.assertRegex(architecture, r"single[- ]page")
        self.assertRegex(principles, r"(?i)does not[^.\n]*general-purpose generator")

    def test_integrity_workflow_is_documented(self) -> None:
        schema = (SKILL / "references" / "schema.md").read_text(encoding="utf-8")
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        round_trip = (ROOT / "docs" / "ROUND_TRIP_CONTRACT.md").read_text(
            encoding="utf-8"
        )
        for document in (schema, skill, round_trip):
            self.assertIn("managed", document)
            self.assertIn("recoverable", document)
            self.assertIn("unsafe", document)
            self.assertIn("--expected-input-sha256", document)
            self.assertIn("--accept-model-drift", document)

    def test_readme_discloses_multimodal_review_reliability(self) -> None:
        # These disclosures are product guarantees, not presentation copy.
        # Ignore Markdown emphasis and permit alternate wording around them.
        contracts = {
            "README.md": (
                r"\b(?:not|no)\b[^.]*\b(?:claim|provide|publish)\b[^.]*\baccuracy\b",
                r"(?:export|render)[^.]*\bnot\b[^.]*\b(?:proof|mean|confirm)[^.]*\b(?:inspect|review|view)",
                r"(?:review|inspection)[^.]*\b(?:miss|overlook)[^.]*\b(?:defects|issues|errors)",
                r"validation[^.]*visual review[^.]*(?:different|independent|separate)",
                r"human review",
            ),
            "README.zh-CN.md": (
                r"(?:不声明|不声称|不承诺|未提供)[^。]*准确率",
                r"(?:导出|渲染)[^。；;]*(?:不代表|不等于|不能证明)[^。；;]*(?:检查|看图|审阅)",
                r"(?:检查|审阅|看图)[^。]*(?:漏检|遗漏)",
                r"(?:校验|验证)[^。]*视觉(?:检查|审阅)[^。]*(?:不同|独立|分别)",
                r"人工(?:复核|审阅|检查)",
            ),
        }
        for name, patterns in contracts.items():
            text = " ".join((ROOT / name).read_text(encoding="utf-8").replace("*", "").split())
            with self.subTest(readme=name):
                self.assertIn("not_available", text)
                for pattern in patterns:
                    self.assertRegex(text, re.compile(pattern, re.IGNORECASE))

    def test_expected_skill_files_only(self) -> None:
        self.assertEqual(skill_inventory(ROOT), EXPECTED_SKILL_FILES)

    def test_v2_json_schema_is_strict_and_valid_json(self) -> None:
        schema = json.loads((SKILL / "references" / "schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("schema_version", schema["required"])
        self.assertIn("main_path", schema["required"])
        self.assertFalse(schema["$defs"]["edge"]["additionalProperties"])

    def test_json_schema_fields_match_runtime_build_contract(self) -> None:
        schema = json.loads((SKILL / "references" / "schema.json").read_text(encoding="utf-8"))
        spec_validation = load_spec_validation_module()
        self.assertEqual(set(schema["properties"]), spec_validation.TOP_LEVEL_FIELDS)
        self.assertEqual(set(schema["$defs"]["lane"]["properties"]), spec_validation.LANE_FIELDS)
        self.assertEqual(set(schema["$defs"]["node"]["properties"]), spec_validation.NODE_FIELDS)
        self.assertEqual(set(schema["$defs"]["edge"]["properties"]), spec_validation.EDGE_FIELDS)
        self.assertEqual(set(schema["$defs"]["phase"]["properties"]), spec_validation.PHASE_FIELDS)
        self.assertEqual(set(schema["$defs"]["group"]["properties"]), spec_validation.contracts.GROUP_FIELDS)
        self.assertEqual(set(schema["$defs"]["anchor"]["properties"]), spec_validation.ANCHOR_FIELDS)
        self.assertEqual(set(schema["$defs"]["layout"]["properties"]), spec_validation.LAYOUT_FIELDS)
        self.assertEqual(set(schema["$defs"]["canvas"]["properties"]), spec_validation.CANVAS_FIELDS)

    def test_loading_tool_module_does_not_write_into_skill_directory(self) -> None:
        cache_directory = TOOL.parent / "__pycache__"
        before = set(cache_directory.iterdir()) if cache_directory.exists() else set()
        load_tool_module()
        after = set(cache_directory.iterdir()) if cache_directory.exists() else set()
        self.assertEqual(after, before)

    def test_json_schema_and_runtime_agree_on_representative_v2_v3_specs(self) -> None:
        schema = json.loads((SKILL / "references" / "schema.json").read_text(encoding="utf-8"))
        valid_v2 = json.loads((FIXTURES / "neutral-flow.json").read_text(encoding="utf-8"))
        valid_v3 = json.loads(
            (ROOT / "examples" / "request-review" / "process.json").read_text(encoding="utf-8")
        )

        invalid_specs = []
        missing_pattern = json.loads(json.dumps(valid_v3))
        missing_pattern.pop("behavior_pattern")
        invalid_specs.append(missing_pattern)

        v2_layout = json.loads(json.dumps(valid_v2))
        v2_layout["layout"] = {"profile": "review"}
        invalid_specs.append(v2_layout)

        v2_slot = json.loads(json.dumps(valid_v2))
        v2_slot["nodes"][1]["slot"] = "main"
        invalid_specs.append(v2_slot)

        v2_flow_role = json.loads(json.dumps(valid_v2))
        v2_flow_role["edges"][0]["flow_role"] = "main"
        invalid_specs.append(v2_flow_role)

        labeled_end = json.loads(json.dumps(valid_v2))
        end_node = next(node for node in labeled_end["nodes"] if node["type"] == "end")
        end_node["label"] = "Done"
        invalid_specs.append(labeled_end)

        unknown_edge_field = json.loads(json.dumps(valid_v2))
        unknown_edge_field["edges"][0]["unexpected"] = True
        invalid_specs.append(unknown_edge_field)

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for index, candidate in enumerate((valid_v2, valid_v3)):
                self.assertTrue(schema_matches(candidate, schema, schema))
                spec_path = directory / f"valid-{index}.json"
                output = directory / f"valid-{index}.drawio"
                spec_path.write_text(json.dumps(candidate), encoding="utf-8")
                result = run_tool(
                    "build",
                    "--spec",
                    str(spec_path),
                    "--output",
                    str(output),
                    "--strict",
                )
                self.assertTrue(json.loads(result.stdout)["quality_gate_passed"])

            for index, candidate in enumerate(invalid_specs):
                self.assertFalse(schema_matches(candidate, schema, schema))
                spec_path = directory / f"invalid-{index}.json"
                output = directory / f"invalid-{index}.drawio"
                spec_path.write_text(json.dumps(candidate), encoding="utf-8")
                result = run_tool(
                    "build",
                    "--spec",
                    str(spec_path),
                    "--output",
                    str(output),
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

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
        result = check_release(ROOT)
        self.assertTrue(result["valid"], result["errors"])



if __name__ == "__main__":
    unittest.main()
