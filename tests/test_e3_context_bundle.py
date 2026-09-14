import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules


SOURCE_DIGEST = "3" * 64


def run_tool(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-B", str(TOOL), *args], cwd=ROOT,
        capture_output=True, check=False,
    )


def neutral_spec() -> dict:
    return {
        "schema_version": "3", "title": "Neutral bundle",
        "behavior_pattern": "custom", "layout": {"profile": "review"},
        "lanes": [{"id": "lane", "label": "Lane", "width": 260}],
        "nodes": [
            {"id": "start", "lane": "lane", "rank": 1, "type": "start", "label": ""},
            {"id": "step", "lane": "lane", "rank": 2, "type": "process", "label": "Review"},
            {"id": "end", "lane": "lane", "rank": 3, "type": "end", "label": ""},
        ],
        "edges": [
            {"id": "enter", "from": "start", "to": "step"},
            {"id": "finish", "from": "step", "to": "end"},
        ],
        "main_path": ["start", "step", "end"],
    }


class E3ContextBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e3_context_bundle_tests")
        cls.workflow = cls.loaded.tool.context_workflow
        cls.bundle = cls.workflow.context_bundle
        cls.provenance = cls.workflow.provenance
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name).resolve()
        cls.spec_data = neutral_spec()
        cls.spec_path = cls.directory / "spec.json"
        cls.spec_path.write_text(json.dumps(cls.spec_data), encoding="utf-8")
        tree = cls.loaded.build.build_tree(cls.spec_data)
        cls.model = cls.workflow.context_native.original_model(tree)
        cls.template_data = {
            "context_template_version": 1,
            "provenance": cls.complete_provenance(cls.model),
        }
        cls.template_path = cls.directory / "template.json"
        cls.template_path.write_text(json.dumps(cls.template_data), encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    @staticmethod
    def complete_provenance(model: dict) -> dict:
        source = {"id": "source", "kind": "review", "version": "v1", "digest": SOURCE_DIGEST,
                  "reference": "https://example.test/evidence"}
        fact = {"id": "fact", "status": "confirmed", "source_refs": ["source"],
                "confirmation": {"asserted_by": "reviewer", "basis": "declared review"}}
        projection = E3ContextBundleTests.projection_for(model)
        bindings = []
        for index, (key, values) in enumerate(sorted(projection.items())):
            target = ({"kind": "outcome", "decision_id": key[1], "outcome_id": key[2]}
                      if key[0] == "outcome" else {"kind": key[0], "id": key[1]})
            field = "label" if key == ("node", "step") else ("type" if key[0] == "node" else "from")
            bindings.append({
                "id": f"binding-{index}", "fact_id": "fact", "target": target,
                "fields": {field: values[field]},
                "source_snapshots": [{"source_id": "source", "version": "v1", "digest": SOURCE_DIGEST}],
                "validity": "current",
            })
        return {"version": 1, "sources": [source], "facts": [fact],
                "bindings": bindings, "history": []}

    @staticmethod
    def projection_for(model: dict) -> dict:
        values = {}
        nodes = {item["id"]: item for item in model["nodes"]}
        for sid, node in nodes.items():
            values[("node", sid)] = {"label": node["label"], "owner": node["lane"], "type": node["type"]}
        for edge in model["edges"]:
            values[("edge", edge["id"])] = {name: edge.get(name) for name in ("label", "from", "to", "outcome")}
            if edge.get("outcome") is not None and nodes[edge["from"]]["type"] == "decision":
                decision = nodes[edge["from"]]
                values[("outcome", edge["from"], edge["outcome"])] = {
                    "label": edge["outcome"], "decision_label": decision["label"], "owner": decision["lane"],
                }
        return values

    def bundle_paths(self, name: str) -> tuple[Path, Path, Path]:
        directory = self.directory / name
        return directory / "diagram.drawio", directory / "context.json", directory / "completion.json"

    def build_bundle(self, name: str, *, template: Path | None = None) -> tuple[subprocess.CompletedProcess[bytes], tuple[Path, Path, Path]]:
        paths = self.bundle_paths(name)
        result = run_tool(
            "build", "--spec", str(self.spec_path), "--context", str(template or self.template_path),
            "--output", str(paths[0]), "--context-output", str(paths[1]),
            "--completion-manifest", str(paths[2]), "--strict",
        )
        return result, paths

    def assert_code(self, result: subprocess.CompletedProcess[bytes], code: str, exit_code: int = 2) -> dict:
        self.assertEqual(result.returncode, exit_code, result.stdout.decode(errors="replace"))
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["diagnostics"][0]["code"], code, envelope)
        return envelope

    def manifest(self, graph: Path, context: Path) -> dict:
        return {"bundle_version": 1, "members": [
            {"role": "diagram", "name": "diagram.drawio", "sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
             "bytes": len(graph.read_bytes())},
            {"role": "context", "name": "context.json", "sha256": hashlib.sha256(context.read_bytes()).hexdigest(),
             "bytes": len(context.read_bytes())},
        ]}

    def write_manifest(self, graph: Path, context: Path, completion: Path) -> None:
        completion.write_bytes(self.bundle.bundle_json(self.manifest(graph, context)))

    def direct_inputs(self, name: str) -> list:
        first = self.directory / f"{name}-a"
        second = self.directory / f"{name}-b"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        return [self.bundle.bundle_read_input(first), self.bundle.bundle_read_input(second)]

    def test_build_commits_exact_three_member_bundle_and_initialization_receipt(self) -> None:
        result, paths = self.build_bundle("build-success")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["provenance_operation"], "initialize")
        self.assertFalse(receipt["prior_context_preserved"])
        self.assertTrue(receipt["delivery"]["committed"])
        self.assertTrue(receipt["delivery"]["complete"])
        self.assertEqual({path.name for path in paths[0].parent.iterdir()},
                         {"diagram.drawio", "context.json", "completion.json"})
        self.assertEqual(json.loads(paths[2].read_text()), self.manifest(paths[0], paths[1]))
        context = json.loads(paths[1].read_text())
        self.assertEqual(context["artifact"]["sha256"], hashlib.sha256(paths[0].read_bytes()).hexdigest())
        self.assertEqual(receipt["semantic_context"]["source_gate"], "passed")
        self.assertEqual(receipt["semantic_context"]["gate"], "not_applicable")

    def test_same_build_inputs_are_byte_deterministic_across_new_directories(self) -> None:
        first, a = self.build_bundle("deterministic-a")
        second, b = self.build_bundle("deterministic-b")
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        self.assertEqual([path.read_bytes() for path in a], [path.read_bytes() for path in b])

    def test_default_build_remains_single_file_and_context_flags_are_all_or_nothing(self) -> None:
        output = self.directory / "default-single.drawio"
        result = run_tool("build", "--spec", str(self.spec_path), "--output", str(output), "--strict")
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        body = json.loads(result.stdout)
        self.assertNotIn("semantic_context", body)
        self.assertNotIn("context_output", body)
        self.assertTrue(output.is_file())
        self.assertEqual(set(path.name for path in self.directory.glob("default-single*")), {"default-single.drawio"})

        incomplete = self.directory / "incomplete-flags" / "diagram.drawio"
        rejected = run_tool(
            "build", "--spec", str(self.spec_path), "--context", str(self.template_path),
            "--output", str(incomplete),
        )
        self.assert_code(rejected, "delivery/bundle-required")
        self.assertFalse(incomplete.parent.exists())

    def test_malformed_context_json_keeps_input_code_and_uncommitted_delivery_receipt(self) -> None:
        malformed = self.directory / "malformed-template.json"
        malformed.write_bytes(b'{"context_template_version":')
        output = self.bundle_paths("malformed-output")
        result = run_tool(
            "build", "--spec", str(self.spec_path), "--context", str(malformed),
            "--output", str(output[0]), "--context-output", str(output[1]),
            "--completion-manifest", str(output[2]),
        )
        body = self.assert_code(result, "input/json-invalid")
        self.assertEqual(body["delivery"], {
            "stage": "validation", "committed": False, "complete": False, "members_written": [],
        })
        self.assertFalse(output[0].parent.exists())

    def test_inspect_and_validate_require_and_verify_explicit_manifest(self) -> None:
        result, paths = self.build_bundle("read-bundle")
        self.assertEqual(result.returncode, 0)
        for command in ("inspect", "validate"):
            checked = run_tool(command, "--input", str(paths[0]), "--context", str(paths[1]),
                               "--completion-manifest", str(paths[2]))
            self.assertEqual(checked.returncode, 0, checked.stdout.decode(errors="replace"))
            body = json.loads(checked.stdout)
            self.assertTrue(body["bundle"]["verified"])
            self.assertEqual(body["semantic_context"]["source_gate"], "passed")
            absent = run_tool(command, "--input", str(paths[0]), "--context", str(paths[1]))
            self.assert_code(absent, "delivery/bundle-required")

    def test_empty_provenance_bundle_is_readable_but_validate_is_incomplete(self) -> None:
        template = self.directory / "empty-template.json"
        template.write_text(json.dumps({"context_template_version": 1, "provenance": {
            "version": 1, "sources": [], "facts": [], "bindings": [], "history": [],
        }}), encoding="utf-8")
        built, paths = self.build_bundle("empty-provenance", template=template)
        self.assertEqual(built.returncode, 0, built.stdout.decode(errors="replace"))
        inspect = run_tool("inspect", "--input", str(paths[0]), "--context", str(paths[1]),
                           "--completion-manifest", str(paths[2]))
        validate = run_tool("validate", "--input", str(paths[0]), "--context", str(paths[1]),
                            "--completion-manifest", str(paths[2]))
        self.assertEqual(inspect.returncode, 0)
        self.assertEqual(validate.returncode, 1)
        self.assertEqual(json.loads(inspect.stdout)["semantic_context"]["source_gate"], "incomplete")
        self.assertEqual(json.loads(validate.stdout)["semantic_context"]["components"]["provenance"]["status"], "provided_empty")

    def test_source_less_assumption_template_builds_without_inventing_support(self) -> None:
        component = {
            "version": 1, "sources": [],
            "facts": [{"id": "open", "status": "assumption", "source_refs": [], "confirmation": None}],
            "bindings": [{
                "id": "open-binding", "fact_id": "open", "target": {"kind": "node", "id": "step"},
                "fields": {"label": "Review"}, "source_snapshots": [], "validity": "current",
            }], "history": [],
        }
        template = self.directory / "source-less-template.json"
        template.write_text(json.dumps({"context_template_version": 1, "provenance": component}), encoding="utf-8")
        built, paths = self.build_bundle("source-less-output", template=template)
        self.assertEqual(built.returncode, 0, built.stdout.decode(errors="replace"))
        receipt = json.loads(built.stdout)["semantic_context"]
        self.assertEqual(receipt["source_results"][0]["declared_status"], "assumption")
        self.assertEqual(receipt["source_results"][0]["validity"], "missing")
        self.assertFalse(receipt["source_results"][0]["effective_confirmed"])
        self.assertEqual(receipt["source_coverage"]["source_count"], 0)
        validated = run_tool("validate", "--input", str(paths[0]), "--context", str(paths[1]),
                             "--completion-manifest", str(paths[2]))
        self.assertEqual(validated.returncode, 1)

    def test_raw_current_field_and_source_drift_reject_instead_of_auto_stale(self) -> None:
        built, source = self.build_bundle("raw-drift-source")
        self.assertEqual(built.returncode, 0)
        original_context = json.loads(source[1].read_text())
        for case in ("field", "source-version", "source-digest"):
            with self.subTest(case=case):
                target_dir = self.directory / f"raw-drift-{case}"
                target_dir.mkdir()
                graph, context, completion = (target_dir / "diagram.drawio", target_dir / "context.json", target_dir / "completion.json")
                data = copy.deepcopy(original_context)
                if case == "field":
                    tree = self.loaded.document.read_tree(source[0])
                    _, nodes = self.loaded.document.lane_node_records(
                        self.loaded.document.graph_root(tree), self.loaded.document.find_pool(tree))
                    nodes["step"]["cell"].set("value", "Changed outside workflow")
                    self.loaded.metadata.refresh_managed_metadata(tree)
                    self.loaded.document.write_tree(tree, graph)
                    pool = self.loaded.document.find_pool(tree)
                    data["artifact"].update(
                        sha256=hashlib.sha256(graph.read_bytes()).hexdigest(),
                        model_hash=pool.get(self.loaded.contracts.DATA_MODEL_HASH),
                    )
                else:
                    shutil.copyfile(source[0], graph)
                    data["provenance"]["sources"][0]["version" if case == "source-version" else "digest"] = (
                        "v2" if case == "source-version" else "4" * 64)
                context.write_bytes(self.bundle.bundle_json(data))
                self.write_manifest(graph, context, completion)
                result = run_tool("inspect", "--input", str(graph), "--context", str(context),
                                  "--completion-manifest", str(completion))
                self.assert_code(result, "provenance/snapshot-mismatch")

    def test_manifest_and_member_tampering_are_rejected_without_sidecar_discovery(self) -> None:
        built, paths = self.build_bundle("tampered-member")
        self.assertEqual(built.returncode, 0)
        paths[1].chmod(0o600)
        paths[1].write_bytes(paths[1].read_bytes() + b" ")
        result = run_tool("inspect", "--input", str(paths[0]), "--context", str(paths[1]),
                          "--completion-manifest", str(paths[2]))
        self.assert_code(result, "delivery/bundle-incomplete")

    def test_explicit_missing_manifest_with_two_members_is_incomplete(self) -> None:
        built, paths = self.build_bundle("missing-completion")
        self.assertEqual(built.returncode, 0)
        paths[2].unlink()
        result = run_tool("inspect", "--input", str(paths[0]), "--context", str(paths[1]),
                          "--completion-manifest", str(paths[2]))
        self.assert_code(result, "delivery/bundle-incomplete")

        built, paths = self.build_bundle("tampered-manifest")
        self.assertEqual(built.returncode, 0)
        paths[2].chmod(0o600)
        data = json.loads(paths[2].read_text())
        data["members"].reverse()
        paths[2].write_text(json.dumps(data), encoding="utf-8")
        result = run_tool("validate", "--input", str(paths[0]), "--context", str(paths[1]),
                          "--completion-manifest", str(paths[2]))
        self.assert_code(result, "delivery/bundle-incomplete")

    def test_manifest_shape_member_set_and_budget_fail_closed(self) -> None:
        built, source = self.build_bundle("manifest-source")
        self.assertEqual(built.returncode, 0)
        mutations = {
            "unknown-top": lambda data: data.update(extra="x"),
            "missing-member": lambda data: data["members"].pop(),
            "duplicate-member": lambda data: data["members"].append(copy.deepcopy(data["members"][0])),
            "wrong-role": lambda data: data["members"][0].update(role="context"),
            "traversal-name": lambda data: data["members"][0].update(name="../diagram.drawio"),
            "boolean-bytes": lambda data: data["members"][0].update(bytes=True),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                target = self.directory / f"manifest-{name}"
                target.mkdir()
                graph, context, completion = (target / "diagram.drawio", target / "context.json", target / "completion.json")
                shutil.copyfile(source[0], graph)
                shutil.copyfile(source[1], context)
                data = self.manifest(graph, context)
                mutate(data)
                completion.write_bytes(self.bundle.bundle_json(data))
                result = run_tool("inspect", "--input", str(graph), "--context", str(context),
                                  "--completion-manifest", str(completion))
                self.assert_code(result, "delivery/bundle-invalid" if name == "unknown-top" else "delivery/bundle-incomplete")

        for extra, expected in ((0, 0), (1, 2)):
            with self.subTest(manifest_size=self.workflow.semantic_context.MAX_BYTES + extra):
                target = self.directory / f"manifest-budget-{extra}"
                target.mkdir()
                graph, context, completion = (target / "diagram.drawio", target / "context.json", target / "completion.json")
                shutil.copyfile(source[0], graph)
                shutil.copyfile(source[1], context)
                raw = self.bundle.bundle_json(self.manifest(graph, context))
                completion.write_bytes(b" " * (self.workflow.semantic_context.MAX_BYTES + extra - len(raw)) + raw)
                result = run_tool("inspect", "--input", str(graph), "--context", str(context),
                                  "--completion-manifest", str(completion))
                self.assertEqual(result.returncode, expected, result.stdout.decode(errors="replace"))
                if extra:
                    self.assertEqual(json.loads(result.stdout)["diagnostics"][0]["code"], "context/resource-limit")

    def test_output_directory_names_and_no_clobber_are_preflighted(self) -> None:
        existing = self.directory / "existing"
        existing.mkdir()
        sentinel = existing / "sentinel"
        sentinel.write_bytes(b"unchanged")
        result = run_tool(
            "build", "--spec", str(self.spec_path), "--context", str(self.template_path),
            "--output", str(existing / "diagram.drawio"), "--context-output", str(existing / "context.json"),
            "--completion-manifest", str(existing / "completion.json"),
        )
        self.assert_code(result, "delivery/output-exists")
        self.assertEqual(sentinel.read_bytes(), b"unchanged")
        self.assertEqual(list(existing.iterdir()), [sentinel])

        graph, context, completion = self.bundle_paths("wrong-names")
        result = run_tool(
            "build", "--spec", str(self.spec_path), "--context", str(self.template_path),
            "--output", str(graph.with_name("other.drawio")), "--context-output", str(context),
            "--completion-manifest", str(completion),
        )
        self.assert_code(result, "delivery/path-unsafe")
        self.assertFalse(graph.parent.exists())

    def test_symlink_hardlink_and_traversal_inputs_are_rejected(self) -> None:
        plain = self.directory / "plain-input"
        plain.write_bytes(b"plain")
        hard = self.directory / "hard-input"
        os.link(plain, hard)
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            self.bundle.bundle_read_input(plain)
        self.assertEqual(caught.exception.code, "delivery/path-unsafe")
        hard.unlink()
        link = self.directory / "soft-input"
        link.symlink_to(plain)
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            self.bundle.bundle_read_input(link)
        self.assertEqual(caught.exception.code, "delivery/path-unsafe")
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            self.bundle.bundle_read_input(self.directory / "child" / ".." / "plain-input")
        self.assertEqual(caught.exception.code, "delivery/path-unsafe")

        actual_parent = self.directory / "actual-parent"
        actual_parent.mkdir()
        alias_parent = self.directory / "alias-parent"
        alias_parent.symlink_to(actual_parent, target_is_directory=True)
        result = run_tool(
            "build", "--spec", str(self.spec_path), "--context", str(self.template_path),
            "--output", str(alias_parent / "new" / "diagram.drawio"),
            "--context-output", str(alias_parent / "new" / "context.json"),
            "--completion-manifest", str(alias_parent / "new" / "completion.json"),
        )
        self.assert_code(result, "delivery/path-unsafe")
        self.assertFalse((actual_parent / "new").exists())

    def test_input_change_before_commit_leaves_incomplete_residue(self) -> None:
        first = self.directory / "race-a"
        second = self.directory / "race-b"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        inputs = [self.bundle.bundle_read_input(first), self.bundle.bundle_read_input(second)]
        paths = self.bundle_paths("race-output")
        original = self.bundle.bundle_write_member

        def mutate_after_graph(descriptor, name, data):
            original(descriptor, name, data)
            if name == "diagram.drawio":
                first.write_bytes(b"changed")

        with mock.patch.object(self.bundle, "bundle_write_member", side_effect=mutate_after_graph):
            with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                self.bundle.deliver_bundle(*paths, b"<graph/>", b"{}\n", inputs)
        self.assertEqual(caught.exception.code, "delivery/input-changed")
        delivery = caught.exception.evidence["delivery"]
        self.assertFalse(delivery["committed"])
        self.assertFalse(paths[2].exists())
        self.assertTrue(paths[0].exists())

    def test_member_and_manifest_commit_failures_never_publish_success(self) -> None:
        for stage in ("context", "manifest"):
            with self.subTest(stage=stage):
                first = self.directory / f"failure-{stage}-a"
                second = self.directory / f"failure-{stage}-b"
                first.write_bytes(b"first")
                second.write_bytes(b"second")
                inputs = [self.bundle.bundle_read_input(first), self.bundle.bundle_read_input(second)]
                paths = self.bundle_paths(f"failure-{stage}-output")
                original = self.bundle.bundle_write_member

                def fail(descriptor, name, data):
                    if (stage == "context" and name == "context.json") or (stage == "manifest" and name == ".completion.pending"):
                        raise OSError("injected")
                    return original(descriptor, name, data)

                with mock.patch.object(self.bundle, "bundle_write_member", side_effect=fail):
                    with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                        self.bundle.deliver_bundle(*paths, b"<graph/>", b"{}\n", inputs)
                self.assertEqual(caught.exception.code, "delivery/io-error")
                self.assertFalse(caught.exception.evidence["delivery"]["committed"])
                self.assertFalse(paths[2].exists())

    def test_partial_member_failure_reports_exact_residue_without_manifest(self) -> None:
        inputs = self.direct_inputs("partial")
        paths = self.bundle_paths("partial-output")

        def partial_write(descriptor, name, data):
            target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o400, dir_fd=descriptor)
            try:
                os.write(target, data[:max(1, len(data) // 2)])
            finally:
                os.close(target)
            raise OSError("injected partial write")

        with mock.patch.object(self.bundle, "bundle_write_member", side_effect=partial_write):
            with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                self.bundle.deliver_bundle(*paths, b"<graph/>", b"{}\n", inputs)
        delivery = caught.exception.evidence["delivery"]
        self.assertEqual(delivery["stage"], "write-diagram")
        self.assertEqual(delivery["failed_member"], "diagram.drawio")
        self.assertEqual(delivery["members_written"], [])
        self.assertTrue(paths[0].is_file())
        self.assertFalse(paths[2].exists())

    def test_post_link_failures_report_committed_marker_and_truthful_residue(self) -> None:
        inputs = self.direct_inputs("post-link")
        paths = self.bundle_paths("post-link-output")
        original_unlink = self.bundle.os.unlink

        def fail_pending_unlink(path, *args, **kwargs):
            if path == ".completion.pending":
                raise OSError("injected unlink failure")
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(self.bundle.os, "unlink", side_effect=fail_pending_unlink):
            with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                self.bundle.deliver_bundle(*paths, b"<graph/>", b"{}\n", inputs)
        delivery = caught.exception.evidence["delivery"]
        self.assertTrue(delivery["committed"])
        self.assertFalse(delivery["complete"])
        self.assertEqual(delivery["stage"], "commit-manifest")
        pending = paths[2].parent / ".completion.pending"
        self.assertTrue(paths[2].exists())
        self.assertTrue(pending.exists())
        self.assertEqual(paths[2].stat().st_ino, pending.stat().st_ino)

        inputs = self.direct_inputs("post-verify")
        paths = self.bundle_paths("post-verify-output")
        with mock.patch.object(
            self.bundle, "verify_bundle",
            side_effect=self.loaded.contracts.DiagramError("injected", code="delivery/bundle-incomplete"),
        ):
            with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                self.bundle.deliver_bundle(*paths, b"<graph/>", b"{}\n", inputs)
        self.assertTrue(caught.exception.evidence["delivery"]["committed"])
        self.assertFalse(caught.exception.evidence["delivery"]["complete"])
        self.assertEqual(caught.exception.evidence["delivery"]["stage"], "verify-completion")
        self.assertTrue(paths[2].exists())
        self.assertFalse((paths[2].parent / ".completion.pending").exists())

    def test_final_link_collision_and_fsync_failure_leave_no_false_completion(self) -> None:
        inputs = self.direct_inputs("link-collision")
        paths = self.bundle_paths("link-collision-output")
        original_write = self.bundle.bundle_write_member

        def create_collision(descriptor, name, data):
            original_write(descriptor, name, data)
            if name == ".completion.pending":
                fd = os.open("completion.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o400, dir_fd=descriptor)
                try:
                    os.write(fd, b"collision")
                finally:
                    os.close(fd)

        with mock.patch.object(self.bundle, "bundle_write_member", side_effect=create_collision):
            with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                self.bundle.deliver_bundle(*paths, b"<graph/>", b"{}\n", inputs)
        self.assertEqual(caught.exception.code, "delivery/io-error")
        self.assertEqual(caught.exception.evidence["delivery"]["stage"], "commit-manifest")
        self.assertFalse(caught.exception.evidence["delivery"]["committed"])
        self.assertEqual(paths[2].read_bytes(), b"collision")

        inputs = self.direct_inputs("fsync")
        paths = self.bundle_paths("fsync-output")
        with mock.patch.object(self.bundle.os, "fsync", side_effect=OSError("injected fsync")):
            with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                self.bundle.deliver_bundle(*paths, b"<graph/>", b"{}\n", inputs)
        self.assertEqual(caught.exception.code, "delivery/io-error")
        self.assertFalse(caught.exception.evidence["delivery"]["committed"])
        self.assertFalse(paths[2].exists())

    def test_read_verification_rechecks_member_identity_at_end(self) -> None:
        built, paths = self.build_bundle("reader-final-recheck")
        self.assertEqual(built.returncode, 0)
        graph = self.bundle.bundle_read_input(paths[0])
        context = self.bundle.bundle_read_input(paths[1], max_bytes=self.workflow.semantic_context.MAX_BYTES)
        original_verify = self.bundle.BundleInput.verify
        changed = False

        def mutate_before_verify(item):
            nonlocal changed
            if not changed and item.path == paths[0]:
                changed = True
                item.path.chmod(0o600)
                item.path.write_bytes(item.raw + b" ")
            return original_verify(item)

        with mock.patch.object(self.bundle.BundleInput, "verify", autospec=True, side_effect=mutate_before_verify):
            with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
                self.bundle.verify_bundle(paths[2], graph, context)
        self.assertEqual(caught.exception.code, "delivery/input-changed")

    def test_concurrent_build_writers_complete_at_most_once(self) -> None:
        paths = self.bundle_paths("concurrent")
        command = [sys.executable, "-B", str(TOOL), "build", "--spec", str(self.spec_path),
                   "--context", str(self.template_path), "--output", str(paths[0]),
                   "--context-output", str(paths[1]), "--completion-manifest", str(paths[2])]
        first = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        outputs = [first.communicate(), second.communicate()]
        codes = sorted((first.returncode, second.returncode))
        self.assertEqual(codes, [0, 2], outputs)
        self.assertTrue(paths[2].is_file())
        graph = self.bundle.bundle_read_input(paths[0])
        context = self.bundle.bundle_read_input(paths[1], max_bytes=self.workflow.semantic_context.MAX_BYTES)
        self.bundle.verify_bundle(paths[2], graph, context)

    def test_semantic_patch_invalidates_bound_label_and_compare_accepts_exact_transition(self) -> None:
        built, before = self.build_bundle("patch-before")
        self.assertEqual(built.returncode, 0)
        before_context = json.loads(before[1].read_text())
        graph_sha = hashlib.sha256(before[0].read_bytes()).hexdigest()
        context_sha = hashlib.sha256(before[1].read_bytes()).hexdigest()
        graph_changes = self.directory / "label-changes.json"
        graph_changes.write_text(json.dumps({"update_nodes": [{"id": "step", "label": "Reviewed"}]}), encoding="utf-8")
        context_changes = self.directory / "label-context-changes.json"
        context_changes.write_text(json.dumps({
            "context_changes_version": 1, "expected_input_sha256": graph_sha,
            "expected_context_sha256": context_sha, "mode": "semantic",
            "provenance": before_context["provenance"],
        }), encoding="utf-8")
        after = self.bundle_paths("patch-after")
        patched = run_tool(
            "patch", "--input", str(before[0]), "--changes", str(graph_changes),
            "--expected-input-sha256", graph_sha, "--context", str(before[1]),
            "--expected-context-sha256", context_sha, "--context-changes", str(context_changes),
            "--input-completion-manifest", str(before[2]), "--output", str(after[0]),
            "--context-output", str(after[1]), "--completion-manifest", str(after[2]),
        )
        self.assertEqual(patched.returncode, 0, patched.stdout.decode(errors="replace"))
        receipt = json.loads(patched.stdout)
        self.assertTrue(receipt["prior_context_preserved"])
        output_context = json.loads(after[1].read_text())
        label_binding = next(item for item in output_context["provenance"]["bindings"]
                             if item["target"] == {"kind": "node", "id": "step"})
        self.assertEqual(label_binding["validity"], "stale")
        self.assertEqual(len(output_context["provenance"]["history"]), 1)
        inspected = run_tool("inspect", "--input", str(after[0]), "--context", str(after[1]),
                             "--completion-manifest", str(after[2]))
        validated = run_tool("validate", "--input", str(after[0]), "--context", str(after[1]),
                             "--completion-manifest", str(after[2]))
        self.assertEqual(inspected.returncode, 0)
        self.assertEqual(validated.returncode, 1)
        compared = run_tool(
            "compare", "--before", str(before[0]), "--after", str(after[0]),
            "--changes", str(graph_changes), "--before-context", str(before[1]),
            "--after-context", str(after[1]), "--context-changes", str(context_changes),
            "--before-completion-manifest", str(before[2]),
            "--after-completion-manifest", str(after[2]),
        )
        self.assertEqual(compared.returncode, 0, compared.stdout.decode(errors="replace"))
        self.assertTrue(json.loads(compared.stdout)["context_comparison"]["preserved"])

    def test_geometry_only_patch_rebinds_artifact_without_history_or_confirmation(self) -> None:
        built, before = self.build_bundle("geometry-before")
        self.assertEqual(built.returncode, 0)
        before_data = json.loads(before[1].read_text())
        graph_sha = hashlib.sha256(before[0].read_bytes()).hexdigest()
        context_sha = hashlib.sha256(before[1].read_bytes()).hexdigest()
        graph_changes = self.directory / "geometry-graph-changes.json"
        graph_changes.write_text(json.dumps({
            "update_nodes": [{"id": "step", "y": 148}],
            "update_edges": [{"id": "enter", "reroute": True}, {"id": "finish", "reroute": True}],
        }), encoding="utf-8")
        context_changes = self.directory / "geometry-context-changes.json"
        context_changes.write_text(json.dumps({
            "context_changes_version": 1, "expected_input_sha256": graph_sha,
            "expected_context_sha256": context_sha, "mode": "geometry-only",
        }), encoding="utf-8")
        after = self.bundle_paths("geometry-after")
        patched = run_tool(
            "patch", "--input", str(before[0]), "--changes", str(graph_changes),
            "--allow-geometry-updates", "--expected-input-sha256", graph_sha,
            "--context", str(before[1]), "--expected-context-sha256", context_sha,
            "--context-changes", str(context_changes), "--input-completion-manifest", str(before[2]),
            "--output", str(after[0]), "--context-output", str(after[1]),
            "--completion-manifest", str(after[2]),
        )
        self.assertEqual(patched.returncode, 0, patched.stdout.decode(errors="replace"))
        after_data = json.loads(after[1].read_text())
        self.assertNotEqual(before_data["artifact"]["sha256"], after_data["artifact"]["sha256"])
        self.assertEqual(before_data["provenance"], after_data["provenance"])
        self.assertEqual(after_data["provenance"]["history"], [])
        self.assertTrue(json.loads(patched.stdout)["prior_context_preserved"])
        compared = run_tool(
            "compare", "--before", str(before[0]), "--after", str(after[0]),
            "--changes", str(graph_changes), "--before-context", str(before[1]),
            "--after-context", str(after[1]), "--context-changes", str(context_changes),
            "--before-completion-manifest", str(before[2]),
            "--after-completion-manifest", str(after[2]),
        )
        self.assertEqual(compared.returncode, 0, compared.stdout.decode(errors="replace"))

    def test_template_noop_patch_initializes_without_claiming_prior_context_preservation(self) -> None:
        graph = self.directory / "initialize-source.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(self.spec_data), graph)
        graph_sha = hashlib.sha256(graph.read_bytes()).hexdigest()
        template_sha = hashlib.sha256(self.template_path.read_bytes()).hexdigest()
        graph_changes = self.directory / "initialize-graph-changes.json"
        graph_changes.write_text("{}\n", encoding="utf-8")
        changes = self.directory / "initialize-context-changes.json"
        changes.write_text(json.dumps({
            "context_changes_version": 1, "expected_input_sha256": graph_sha,
            "expected_context_sha256": template_sha, "mode": "semantic",
            "provenance": self.template_data["provenance"],
            "confirmation_actions": [{"fact_id": "fact", "asserted_by": "reviewer", "basis": "declared review"}],
        }), encoding="utf-8")
        output = self.bundle_paths("initialize-output")
        result = run_tool(
            "patch", "--input", str(graph), "--changes", str(graph_changes),
            "--expected-input-sha256", graph_sha, "--context", str(self.template_path),
            "--expected-context-sha256", template_sha, "--context-changes", str(changes),
            "--output", str(output[0]), "--context-output", str(output[1]),
            "--completion-manifest", str(output[2]),
        )
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["provenance_operation"], "initialize")
        self.assertFalse(receipt["prior_context_preserved"])
        self.assertEqual(json.loads(output[1].read_text())["provenance"]["history"], [])
        compared = run_tool(
            "compare", "--before", str(graph), "--after", str(output[0]), "--changes", str(graph_changes),
            "--before-context", str(self.template_path), "--after-context", str(output[1]),
            "--context-changes", str(changes), "--after-completion-manifest", str(output[2]),
        )
        self.assertEqual(compared.returncode, 0, compared.stdout.decode(errors="replace"))
        self.assertFalse(json.loads(compared.stdout)["context_comparison"]["prior_context_preserved"])

        without_action = json.loads(changes.read_text())
        without_action.pop("confirmation_actions")
        bad_changes = self.directory / "initialize-no-action.json"
        bad_changes.write_text(json.dumps(without_action), encoding="utf-8")
        bad_output = self.bundle_paths("initialize-no-action-output")
        rejected = run_tool(
            "patch", "--input", str(graph), "--changes", str(graph_changes),
            "--expected-input-sha256", graph_sha, "--context", str(self.template_path),
            "--expected-context-sha256", template_sha, "--context-changes", str(bad_changes),
            "--output", str(bad_output[0]), "--context-output", str(bad_output[1]),
            "--completion-manifest", str(bad_output[2]),
        )
        self.assert_code(rejected, "provenance/confirmation-required")
        self.assertFalse(bad_output[0].parent.exists())

    def test_patch_requires_both_raw_input_preconditions_and_bound_input_manifest(self) -> None:
        built, before = self.build_bundle("precondition-before")
        self.assertEqual(built.returncode, 0)
        graph_sha = hashlib.sha256(before[0].read_bytes()).hexdigest()
        context_sha = hashlib.sha256(before[1].read_bytes()).hexdigest()
        graph_changes = self.directory / "precondition-graph.json"
        graph_changes.write_text("{}\n", encoding="utf-8")
        changes = self.directory / "precondition-context.json"
        changes.write_text(json.dumps({
            "context_changes_version": 1, "expected_input_sha256": graph_sha,
            "expected_context_sha256": context_sha, "mode": "semantic",
            "provenance": json.loads(before[1].read_text())["provenance"],
        }), encoding="utf-8")
        cases = [
            ([], "delivery/bundle-required"),
            (["--input-completion-manifest", str(before[2])], "delivery/input-sha256-required"),
            (["--input-completion-manifest", str(before[2]), "--expected-input-sha256", graph_sha],
             "delivery/context-sha256-required"),
            (["--input-completion-manifest", str(before[2]), "--expected-input-sha256", "0" * 64,
              "--expected-context-sha256", context_sha], "delivery/input-sha256-mismatch"),
            (["--input-completion-manifest", str(before[2]), "--expected-input-sha256", graph_sha,
              "--expected-context-sha256", "0" * 64], "delivery/context-sha256-mismatch"),
        ]
        for index, (extra, code) in enumerate(cases):
            with self.subTest(code=code):
                output = self.bundle_paths(f"precondition-output-{index}")
                result = run_tool(
                    "patch", "--input", str(before[0]), "--changes", str(graph_changes),
                    "--context", str(before[1]), "--context-changes", str(changes), *extra,
                    "--output", str(output[0]), "--context-output", str(output[1]),
                    "--completion-manifest", str(output[2]),
                )
                self.assert_code(result, code)
                self.assertFalse(output[0].parent.exists())

    def test_compare_independently_rejects_record_loss_with_valid_bundle_identity(self) -> None:
        built, before = self.build_bundle("record-loss-before")
        self.assertEqual(built.returncode, 0)
        graph_sha = hashlib.sha256(before[0].read_bytes()).hexdigest()
        context_sha = hashlib.sha256(before[1].read_bytes()).hexdigest()
        changes = self.directory / "record-loss-context-changes.json"
        before_data = json.loads(before[1].read_text())
        changes.write_text(json.dumps({
            "context_changes_version": 1, "expected_input_sha256": graph_sha,
            "expected_context_sha256": context_sha, "mode": "semantic",
            "provenance": before_data["provenance"],
        }), encoding="utf-8")
        after_dir = self.directory / "record-loss-after"
        after_dir.mkdir()
        after = (after_dir / "diagram.drawio", after_dir / "context.json", after_dir / "completion.json")
        shutil.copyfile(before[0], after[0])
        lost = copy.deepcopy(before_data)
        lost["provenance"]["bindings"].pop()
        after[1].write_bytes(self.bundle.bundle_json(lost))
        self.write_manifest(after[0], after[1], after[2])
        compared = run_tool(
            "compare", "--before", str(before[0]), "--after", str(after[0]),
            "--before-context", str(before[1]), "--after-context", str(after[1]),
            "--context-changes", str(changes), "--before-completion-manifest", str(before[2]),
            "--after-completion-manifest", str(after[2]),
        )
        self.assertEqual(compared.returncode, 1, compared.stdout.decode(errors="replace"))
        body = json.loads(compared.stdout)
        self.assertFalse(body["context_comparison"]["preserved"])
        self.assertIn("provenance/record-loss", {item["code"] for item in body["context_comparison"]["differences"]})

    def test_compare_rejects_assumption_to_confirmed_upgrade_without_action(self) -> None:
        assumption_component = {
            "version": 1, "sources": [],
            "facts": [{"id": "open", "status": "assumption", "source_refs": [], "confirmation": None}],
            "bindings": [{
                "id": "open-binding", "fact_id": "open", "target": {"kind": "node", "id": "step"},
                "fields": {"label": "Review"}, "source_snapshots": [], "validity": "current",
            }], "history": [],
        }
        template = self.directory / "upgrade-before-template.json"
        template.write_text(json.dumps({"context_template_version": 1, "provenance": assumption_component}), encoding="utf-8")
        built, before = self.build_bundle("upgrade-before", template=template)
        self.assertEqual(built.returncode, 0)
        before_data = json.loads(before[1].read_text())
        graph_sha = hashlib.sha256(before[0].read_bytes()).hexdigest()
        context_sha = hashlib.sha256(before[1].read_bytes()).hexdigest()
        candidate = copy.deepcopy(before_data["provenance"])
        candidate["sources"] = [{"id": "source", "kind": "review", "version": "v1", "digest": SOURCE_DIGEST}]
        candidate["facts"][0].update(
            status="confirmed", source_refs=["source"],
            confirmation={"asserted_by": "reviewer", "basis": "new assertion"},
        )
        candidate["bindings"][0].update(
            source_snapshots=[{"source_id": "source", "version": "v1", "digest": SOURCE_DIGEST}],
            validity="current",
        )
        after_data = copy.deepcopy(before_data)
        after_data["provenance"] = candidate
        after_dir = self.directory / "upgrade-after"
        after_dir.mkdir()
        after = (after_dir / "diagram.drawio", after_dir / "context.json", after_dir / "completion.json")
        shutil.copyfile(before[0], after[0])
        after[1].write_bytes(self.bundle.bundle_json(after_data))
        self.write_manifest(after[0], after[1], after[2])
        changes = self.directory / "upgrade-without-action.json"
        changes.write_text(json.dumps({
            "context_changes_version": 1, "expected_input_sha256": graph_sha,
            "expected_context_sha256": context_sha, "mode": "semantic", "provenance": candidate,
        }), encoding="utf-8")
        compared = run_tool(
            "compare", "--before", str(before[0]), "--after", str(after[0]),
            "--before-context", str(before[1]), "--after-context", str(after[1]),
            "--context-changes", str(changes), "--before-completion-manifest", str(before[2]),
            "--after-completion-manifest", str(after[2]),
        )
        self.assert_code(compared, "provenance/confirmation-required", exit_code=1)

    def test_context_migrate_dry_run_write_and_compare_keep_exact_old_boundary(self) -> None:
        tree = self.loaded.build.build_tree(self.spec_data)
        pool = self.loaded.document.find_pool(tree)
        pool.attrib.pop(self.loaded.contracts.DATA_MODEL_HASH_VERSION)
        pool.set(self.loaded.contracts.DATA_TOOL_VERSION, "0.1.0")
        graph_raw = self.workflow.ET.tostring(tree.getroot(), encoding="utf-8", short_empty_elements=True)
        plan = self.loaded.migration.plan_migration(graph_raw)
        self.assertEqual(plan["classification"], "automatic")
        artifact = {
            "sha256": hashlib.sha256(graph_raw).hexdigest(), "schema_version": "3",
            "model_hash_version": "1", "model_hash": pool.get(self.loaded.contracts.DATA_MODEL_HASH),
        }
        context_data = {"context_version": 1, "artifact": artifact,
                        "provenance": copy.deepcopy(self.template_data["provenance"])}
        context_raw = self.bundle.bundle_json(context_data)
        before = self.bundle_paths("migration-before")
        delivery = self.bundle.deliver_bundle(*before, graph_raw, context_raw, self.direct_inputs("migration-source"))
        self.assertTrue(delivery["complete"])
        graph_sha = hashlib.sha256(before[0].read_bytes()).hexdigest()
        context_sha = hashlib.sha256(before[1].read_bytes()).hexdigest()

        dry = run_tool(
            "migrate", "--input", str(before[0]), "--dry-run", "--context", str(before[1]),
            "--input-completion-manifest", str(before[2]), "--expected-input-sha256", graph_sha,
            "--expected-context-sha256", context_sha,
        )
        self.assertEqual(dry.returncode, 0, dry.stdout.decode(errors="replace"))
        self.assertEqual(json.loads(dry.stdout)["classification"], "automatic")

        context_changes = self.directory / "migration-context-changes.json"
        context_changes.write_text(json.dumps({
            "context_changes_version": 1, "expected_input_sha256": graph_sha,
            "expected_context_sha256": context_sha, "mode": "migration",
        }), encoding="utf-8")
        after = self.bundle_paths("migration-after")
        migrated = run_tool(
            "migrate", "--input", str(before[0]), "--output", str(after[0]),
            "--expected-input-sha256", graph_sha, "--context", str(before[1]),
            "--expected-context-sha256", context_sha, "--context-changes", str(context_changes),
            "--input-completion-manifest", str(before[2]), "--context-output", str(after[1]),
            "--completion-manifest", str(after[2]),
        )
        self.assertEqual(migrated.returncode, 0, migrated.stdout.decode(errors="replace"))
        after_data = json.loads(after[1].read_text())
        self.assertEqual(after_data["provenance"], context_data["provenance"])
        compared = run_tool(
            "compare", "--before", str(before[0]), "--after", str(after[0]), "--migration",
            "--before-context", str(before[1]), "--after-context", str(after[1]),
            "--context-changes", str(context_changes), "--before-completion-manifest", str(before[2]),
            "--after-completion-manifest", str(after[2]),
        )
        self.assertEqual(compared.returncode, 0, compared.stdout.decode(errors="replace"))

        # A no-op plan still validates the declared transition; it cannot skip a
        # contradictory context_changes precondition merely because no write is needed.
        after_graph_sha = hashlib.sha256(after[0].read_bytes()).hexdigest()
        after_context_sha = hashlib.sha256(after[1].read_bytes()).hexdigest()
        contradictory = self.directory / "migration-not-needed-contradictory.json"
        contradictory.write_text(json.dumps({
            "context_changes_version": 1, "expected_input_sha256": "0" * 64,
            "expected_context_sha256": after_context_sha, "mode": "migration",
        }), encoding="utf-8")
        no_write = self.bundle_paths("migration-not-needed-output")
        rejected = run_tool(
            "migrate", "--input", str(after[0]), "--output", str(no_write[0]),
            "--expected-input-sha256", after_graph_sha, "--context", str(after[1]),
            "--expected-context-sha256", after_context_sha, "--context-changes", str(contradictory),
            "--input-completion-manifest", str(after[2]), "--context-output", str(no_write[1]),
            "--completion-manifest", str(no_write[2]),
        )
        self.assert_code(rejected, "delivery/input-sha256-mismatch")
        self.assertFalse(no_write[0].parent.exists())


if __name__ == "__main__":
    unittest.main()
