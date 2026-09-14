import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules


def run_tool(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-B", str(TOOL), *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )


def node(node_id: str, rank: int, node_type: str = "process", lane: str = "lane-a") -> dict:
    return {
        "id": node_id,
        "lane": lane,
        "rank": rank,
        "type": node_type,
        "label": "" if node_type in {"start", "end"} else f"Step {rank}",
    }


def spec(pattern: str, nodes: list[dict], edges: list[dict], main_path: list[str],
         lanes: list[dict] | None = None) -> dict:
    return {
        "schema_version": "3",
        "title": "Neutral context case",
        "behavior_pattern": pattern,
        "layout": {"profile": "review"},
        "lanes": lanes or [{"id": "lane-a", "label": "Lane A", "width": 240}],
        "nodes": nodes,
        "edges": edges,
        "main_path": main_path,
    }


def linear_spec(pattern: str = "linear") -> dict:
    return spec(
        pattern,
        [node("n0", 1, "start"), node("n1", 2), node("n2", 3, "end")],
        [{"id": "e0", "from": "n0", "to": "n1"},
         {"id": "e1", "from": "n1", "to": "n2"}],
        ["n0", "n1", "n2"],
    )


def approval_spec() -> dict:
    value = spec(
        "approval-loop",
        [node("n0", 1, "start"), node("n1", 2), node("n2", 3, "decision"),
         node("n3", 4, "end")],
        [{"id": "e0", "from": "n0", "to": "n1", "flow_role": "main"},
         {"id": "e1", "from": "n1", "to": "n2", "flow_role": "main"},
         {"id": "e2", "from": "n2", "to": "n3", "flow_role": "main",
          "branch": "positive", "outcome": "complete"},
         {"id": "retry", "from": "n2", "to": "n1", "type": "retry",
          "route": "back", "flow_role": "retry", "branch": "negative",
          "outcome": "retry"}],
        ["n0", "n1", "n2", "n3"],
    )
    return value


def request_spec() -> dict:
    lanes = [{"id": "lane-a", "label": "Lane A", "width": 240},
             {"id": "lane-b", "label": "Lane B", "width": 240}]
    return spec(
        "request-response",
        [node("n0", 1, "start"), node("n1", 2), node("n2", 3, lane="lane-b"),
         node("n3", 4, lane="lane-b"), node("n4", 5), node("n5", 6, "end")],
        [{"id": "e0", "from": "n0", "to": "n1"},
         {"id": "request", "from": "n1", "to": "n2", "type": "call"},
         {"id": "work", "from": "n2", "to": "n3"},
         {"id": "response", "from": "n3", "to": "n4", "type": "return",
          "flow_role": "response"},
         {"id": "e4", "from": "n4", "to": "n5"}],
        ["n0", "n1", "n2", "n3", "n4", "n5"], lanes,
    )


def fork_spec(pattern: str = "fork-join") -> dict:
    left = node("left", 3)
    left["slot"] = "left"
    right = node("right", 3)
    right["slot"] = "right"
    return spec(
        pattern,
        [node("n0", 1, "start"), node("fork", 2, "decision"),
         left, right, node("join", 4), node("end", 5, "end")],
        [{"id": "enter", "from": "n0", "to": "fork"},
         {"id": "fork-left", "from": "fork", "to": "left", "branch": "positive",
          "outcome": "left"},
         {"id": "fork-right", "from": "fork", "to": "right", "branch": "negative",
          "outcome": "right"},
         {"id": "left-join", "from": "left", "to": "join"},
         {"id": "right-join", "from": "right", "to": "join"},
         {"id": "leave", "from": "join", "to": "end"}],
        ["n0", "fork", "left", "join", "end"],
    )


def lifecycle_spec() -> dict:
    value = approval_spec()
    value["behavior_pattern"] = "lifecycle"
    return value


class E1SemanticContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e1_semantic_context_tests")
        cls.semantic = cls.loaded.tool.semantic_context
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        source_specs = {
            "linear": linear_spec(),
            "approval-loop": approval_spec(),
            "request-response": request_spec(),
            "fork-join": fork_spec(),
            "fan-in": fork_spec("fan-in"),
            "lifecycle": lifecycle_spec(),
            "custom": linear_spec("custom"),
        }
        cls.paths = {}
        for name, source in source_specs.items():
            path = cls.directory / f"{name}.drawio"
            cls.loaded.document.write_tree(cls.loaded.build.build_tree(source), path)
            cls.paths[name] = path

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def artifact(self, pattern: str, path: Path | None = None) -> dict:
        path = path or self.paths[pattern]
        tree = self.loaded.document.read_tree(path)
        pool = self.loaded.document.find_pool(tree)
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "schema_version": pool.get(self.loaded.contracts.DATA_SCHEMA_VERSION),
            "model_hash_version": pool.get(self.loaded.contracts.DATA_MODEL_HASH_VERSION),
            "model_hash": pool.get(self.loaded.contracts.DATA_MODEL_HASH),
        }

    def base_scope(self, pattern: str) -> dict:
        if pattern in {"linear", "custom"}:
            nodes, edges = ["n0", "n1", "n2"], ["e0", "e1"]
        elif pattern in {"approval-loop", "lifecycle"}:
            nodes, edges = ["n0", "n1", "n2", "n3"], ["e0", "e1", "e2", "retry"]
        elif pattern == "request-response":
            nodes = ["n0", "n1", "n2", "n3", "n4", "n5"]
            edges = ["e0", "request", "work", "response", "e4"]
        else:
            nodes = ["n0", "fork", "left", "right", "join", "end"]
            edges = ["enter", "fork-left", "fork-right", "left-join", "right-join", "leave"]
        declarations = {
            "linear": {"entry": "n0", "exit": "n2"},
            "approval-loop": {
                "forward_edges": ["e0", "e1", "e2"],
                "retry_edges": ["retry"],
                "terminal_rejections": [],
            },
            "request-response": {"requests": [{
                "request_edge": "request", "response_required": True,
                "response_edges": ["response"],
                "completion_paths": [{"response_edge": "response", "path": ["work"]}],
            }]},
            "fork-join": {
                "fork": "fork", "join": "join",
                "branches": [
                    {"branch_id": "left-branch", "entry_edge": "fork-left",
                     "required": True, "join_path": ["left-join"]},
                    {"branch_id": "right-branch", "entry_edge": "fork-right",
                     "required": True, "join_path": ["right-join"]},
                ],
            },
            "fan-in": {
                "target": "join",
                "inputs": [
                    {"input_id": "left-input", "source": "left", "required": True,
                     "path": ["left-join"]},
                    {"input_id": "right-input", "source": "right", "required": True,
                     "path": ["right-join"]},
                ],
            },
            "lifecycle": {
                "states": ["created", "active", "complete"],
                "node_states": {"n0": "created", "n1": "active", "n2": "active",
                                "n3": "complete"},
                "allowed_transitions": [
                    {"from": "created", "to": "active"},
                    {"from": "active", "to": "active"},
                    {"from": "active", "to": "complete"},
                ],
            },
            "custom": {},
        }[pattern]
        return {
            "id": f"{pattern}-primary", "role": "primary", "pattern": pattern,
            "nodes": nodes, "edges": edges, "excluded_edges": [],
            "declarations": declarations,
        }

    def context_data(self, pattern: str, *, scope: dict | None = None,
                     path: Path | None = None) -> dict:
        return {
            "context_version": 1,
            "artifact": self.artifact(pattern, path),
            "patterns": {"version": 1, "scopes": [scope or self.base_scope(pattern)]},
        }

    def write_context(self, data: dict, name: str = "context.json", *, sort_keys=False) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=sort_keys), encoding="utf-8")
        return path

    def assess(self, pattern: str, data: dict, *, path: Path | None = None) -> dict:
        diagram = path or self.paths[pattern]
        context_path = self.write_context(data, f"{pattern}-{hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]}.json")
        loaded = self.semantic.load_context(context_path)
        tree = self.loaded.document.read_tree(diagram)
        validation = self.loaded.validation.validate_tree(tree)
        return self.semantic.assess_context(
            loaded, tree, hashlib.sha256(diagram.read_bytes()).hexdigest(), validation
        )

    @staticmethod
    def rules(result: dict) -> dict[str, dict]:
        return {item["rule_id"]: item for item in result["scope_results"][0]["rules"]}

    def test_all_seven_patterns_have_independent_positive_results(self) -> None:
        expected_rules = {
            "linear": {"linear/degrees", "linear/coverage", "linear/acyclic"},
            "approval-loop": {"approval/declared-roles", "approval/return-reachability",
                              "approval/rejection-termination"},
            "request-response": {"request/coverage", "request/required-response",
                                 "request/participant-pair", "request/completion-path"},
            "fork-join": {"fork/branches", "fork/required-join", "fork/coverage"},
            "fan-in": {"fanin/required-path", "fanin/target", "fanin/coverage"},
            "lifecycle": {"lifecycle/state-coverage", "lifecycle/allowed-transition"},
        }
        for pattern in ("linear", "approval-loop", "request-response", "fork-join",
                        "fan-in", "lifecycle", "custom"):
            with self.subTest(pattern=pattern):
                result = self.assess(pattern, self.context_data(pattern))
                if pattern == "custom":
                    self.assertEqual(result["gate"], "not_applicable")
                    self.assertTrue(all(r["status"] == "not_applicable" for r in self.rules(result).values()))
                else:
                    self.assertEqual(result["gate"], "passed")
                    self.assertEqual(set(self.rules(result)), expected_rules[pattern])
                    self.assertTrue(all(r["status"] == "passed" for r in self.rules(result).values()))
                self.assertEqual(result["components"]["patterns"]["status"], "provided")
                self.assertEqual(result["components"]["group_contracts"]["status"], "not_provided")
                self.assertEqual(result["components"]["provenance"]["status"], "not_provided")

    def test_receipt_shape_gate_priority_and_custom_reference_validation_are_exact(self) -> None:
        result = self.assess("linear", self.context_data("linear"))
        self.assertEqual(set(result), {
            "context_version", "context_sha256", "artifact_sha256", "artifact", "components",
            "scope_results", "gate", "coverage",
        })
        self.assertEqual(set(result["artifact"]), {
            "schema_version", "model_hash_version", "model_hash",
        })
        self.assertEqual(set(result["coverage"]), {
            "scope_count", "node_count", "edge_count", "total_nodes", "total_edges",
            "extent", "rule_status_counts",
        })
        scope_result = result["scope_results"][0]
        self.assertEqual(set(scope_result), {"scope_id", "role", "pattern", "gate", "coverage", "rules"})
        self.assertEqual(set(scope_result["coverage"]), {
            "node_count", "edge_count", "total_nodes", "total_edges", "extent", "excluded_edges",
        })
        for rule in scope_result["rules"]:
            self.assertEqual(set(rule), {
                "scope_id", "rule_id", "status", "code", "subject_ids", "evidence",
                "missing_declarations",
            })
            self.assertEqual(set(rule["subject_ids"]), {"nodes", "edges", "outcomes"})

        mixed = self.context_data("linear")
        mixed["patterns"]["scopes"][0]["declarations"]["exit"] = "n1"
        local = copy.deepcopy(self.base_scope("linear"))
        local.update(id="incomplete-local", role="local", pattern="lifecycle", declarations={})
        mixed["patterns"]["scopes"].append(local)
        assessed = self.assess("linear", mixed)
        self.assertEqual(assessed["gate"], "failed")
        self.assertEqual({scope["gate"] for scope in assessed["scope_results"]}, {"failed", "incomplete"})

        custom = self.context_data("custom")
        custom["patterns"]["scopes"][0]["nodes"][0] = "absent-node"
        self.assert_cli_error_for_diagram(
            custom, self.paths["custom"], "context/reference-invalid", "custom-bad-reference.json"
        )

    def test_each_specific_pattern_distinguishes_missing_declaration_and_bad_reference(self) -> None:
        for pattern in ("linear", "approval-loop", "request-response", "fork-join",
                        "fan-in", "lifecycle"):
            with self.subTest(pattern=pattern, condition="missing"):
                data = self.context_data(pattern)
                data["patterns"]["scopes"][0]["declarations"] = {}
                result = self.assess(pattern, data)
                self.assertEqual(result["gate"], "incomplete")
                self.assertTrue(all(rule["status"] == "not_assessed" for rule in self.rules(result).values()))
                self.assertTrue(all(rule["missing_declarations"] for rule in self.rules(result).values()))
            with self.subTest(pattern=pattern, condition="reference"):
                data = self.context_data(pattern)
                declaration = data["patterns"]["scopes"][0]["declarations"]
                if pattern == "linear":
                    declaration["entry"] = "absent-node"
                elif pattern == "approval-loop":
                    declaration["forward_edges"][0] = "absent-edge"
                elif pattern == "request-response":
                    declaration["requests"][0]["request_edge"] = "absent-edge"
                elif pattern == "fork-join":
                    declaration["fork"] = "absent-node"
                elif pattern == "fan-in":
                    declaration["target"] = "absent-node"
                else:
                    declaration["node_states"]["absent-node"] = declaration["node_states"].pop("n0")
                path = self.write_context(data, f"{pattern}-bad-reference.json")
                result = run_tool("inspect", "--input", str(self.paths[pattern]), "--context", str(path))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["diagnostics"][0]["code"], "context/reference-invalid")

    def test_reordered_legal_input_has_stable_rule_results_and_subject_order(self) -> None:
        data = self.context_data("fork-join")
        first = self.assess("fork-join", data)
        reordered = copy.deepcopy(data)
        scope = reordered["patterns"]["scopes"][0]
        scope["nodes"].reverse()
        scope["edges"].reverse()
        scope["declarations"]["branches"].reverse()
        second = self.assess("fork-join", reordered)
        for result in (first, second):
            for rule in result["scope_results"][0]["rules"]:
                for values in rule["subject_ids"].values():
                    self.assertEqual(values, sorted(values))
        left = copy.deepcopy(first)
        right = copy.deepcopy(second)
        left.pop("context_sha256")
        right.pop("context_sha256")
        self.assertEqual(left, right)

    def test_linear_parallel_edge_counts_as_a_distinct_business_edge(self) -> None:
        tree = self.loaded.document.read_tree(self.paths["linear"])
        root = self.loaded.document.graph_root(tree)
        original = self.loaded.document.edge_records(root)["e1"]
        parallel = copy.deepcopy(original)
        parallel.set("id", "edge-parallel-native")
        parallel.set(self.loaded.contracts.DATA_SEMANTIC_ID, "parallel")
        root.append(parallel)
        self.loaded.metadata.refresh_managed_metadata(tree)
        path = self.directory / "linear-parallel.drawio"
        self.loaded.document.write_tree(tree, path)
        scope = self.base_scope("linear")
        scope["edges"].append("parallel")
        result = self.assess("linear", self.context_data("linear", scope=scope, path=path), path=path)
        self.assertEqual(self.rules(result)["linear/degrees"]["status"], "violated")
        self.assertEqual(result["gate"], "failed")

    def test_linear_isolated_cycle_fails_coverage_and_acyclic_without_rank_inference(self) -> None:
        source = linear_spec()
        source["nodes"].extend([node("n3", 4), node("n4", 5)])
        source["edges"].extend([
            {"id": "cycle-forward", "from": "n3", "to": "n4"},
            {"id": "cycle-back", "from": "n4", "to": "n3", "type": "retry", "route": "back"},
        ])
        path = self.directory / "linear-isolated-cycle.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(source), path)
        scope = self.base_scope("linear")
        scope["nodes"].extend(["n3", "n4"])
        scope["edges"].extend(["cycle-forward", "cycle-back"])
        result = self.assess("linear", self.context_data("linear", scope=scope, path=path), path=path)
        self.assertEqual(self.rules(result)["linear/coverage"]["status"], "violated")
        self.assertEqual(self.rules(result)["linear/acyclic"]["status"], "violated")

    def test_approval_retry_cannot_prove_its_own_forward_reachability(self) -> None:
        data = self.context_data("approval-loop")
        declarations = data["patterns"]["scopes"][0]["declarations"]
        declarations["forward_edges"] = ["e0", "e2"]
        result = self.assess("approval-loop", data)
        self.assertEqual(self.rules(result)["approval/return-reachability"]["status"], "violated")

    def test_approval_allows_terminal_rejection_and_an_explicitly_empty_retry_set(self) -> None:
        source = spec(
            "approval-loop",
            [node("n0", 1, "start"), node("n1", 2), node("n2", 3, "decision"),
             node("done", 4, "end"), node("rejected", 4, "end")],
            [{"id": "e0", "from": "n0", "to": "n1"},
             {"id": "e1", "from": "n1", "to": "n2"},
             {"id": "accepted", "from": "n2", "to": "done", "branch": "positive",
              "outcome": "accepted"},
             {"id": "rejected-edge", "from": "n2", "to": "rejected",
              "branch": "negative", "outcome": "rejected"}],
            ["n0", "n1", "n2", "done"],
        )
        source["nodes"][-1]["slot"] = "right"
        source["nodes"][-2]["slot"] = "left"
        path = self.directory / "approval-terminal.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(source), path)
        scope = {
            "id": "terminal-primary", "role": "primary", "pattern": "approval-loop",
            "nodes": ["n0", "n1", "n2", "done", "rejected"],
            "edges": ["e0", "e1", "accepted", "rejected-edge"], "excluded_edges": [],
            "declarations": {
                "forward_edges": ["e0", "e1", "accepted"], "retry_edges": [],
                "terminal_rejections": [{"rejected_edge": "rejected-edge", "termination_path": []}],
            },
        }
        result = self.assess(
            "approval-loop", self.context_data("approval-loop", scope=scope, path=path), path=path
        )
        self.assertEqual(result["gate"], "passed")
        evidence = self.rules(result)["approval/declared-roles"]["evidence"]
        self.assertEqual(evidence["retry_count"], 0)
        self.assertEqual(evidence["terminal_rejection_count"], 1)

    def test_approval_rejects_ordinary_forward_as_retry_and_retry_as_forward_proof(self) -> None:
        ordinary = self.context_data("approval-loop")
        declarations = ordinary["patterns"]["scopes"][0]["declarations"]
        declarations["forward_edges"] = ["e0", "e2", "retry"]
        declarations["retry_edges"] = ["e1"]
        result = self.assess("approval-loop", ordinary)
        invalid = self.rules(result)["approval/return-reachability"]["evidence"]["invalid_returns"]
        self.assertTrue(any(item["edge_id"] == "e1" and not item["existing_retry_semantics"] for item in invalid))
        self.assertEqual(self.rules(result)["approval/declared-roles"]["status"], "violated")

        tree = self.loaded.document.read_tree(self.paths["approval-loop"])
        root = self.loaded.document.graph_root(tree)
        helper = copy.deepcopy(self.loaded.document.edge_records(root)["e1"])
        helper.set("id", "retry-helper-native")
        helper.set(self.loaded.contracts.DATA_SEMANTIC_ID, "retry-helper")
        helper.set(self.loaded.contracts.DATA_EDGE_TYPE, "retry")
        helper.set(self.loaded.contracts.DATA_FLOW_ROLE, "retry")
        helper.set(self.loaded.contracts.DATA_ROUTE, "back")
        root.insert(list(root).index(self.loaded.document.edge_records(root)["e1"]), helper)
        self.loaded.metadata.refresh_managed_metadata(tree)
        path = self.directory / "retry-as-forward-proof.drawio"
        self.loaded.document.write_tree(tree, path)
        scope = self.base_scope("approval-loop")
        scope["edges"] = ["e0", "e2", "retry", "retry-helper"]
        scope["excluded_edges"] = [{"edge_id": "e1", "reason": "separate ordinary path"}]
        scope["declarations"] = {
            "forward_edges": ["e0", "e2", "retry-helper"],
            "retry_edges": ["retry"], "terminal_rejections": [],
        }
        result = self.assess(
            "approval-loop", self.context_data("approval-loop", scope=scope, path=path), path=path
        )
        self.assertEqual(self.rules(result)["approval/declared-roles"]["status"], "violated")
        self.assertEqual(self.rules(result)["approval/return-reachability"]["status"], "violated")

    def test_request_optional_one_way_is_valid_but_declared_wrong_participant_is_not(self) -> None:
        optional = self.context_data("request-response")
        request = optional["patterns"]["scopes"][0]["declarations"]["requests"][0]
        request.update(response_required=False, response_edges=[], completion_paths=[])
        self.assertEqual(self.assess("request-response", optional)["gate"], "passed")

        wrong = self.context_data("request-response")
        request = wrong["patterns"]["scopes"][0]["declarations"]["requests"][0]
        request.update(response_required=False, response_edges=["e4"],
                       completion_paths=[{"response_edge": "e4", "path": ["work", "response"]}])
        result = self.assess("request-response", wrong)
        self.assertEqual(self.rules(result)["request/participant-pair"]["status"], "violated")

    def test_request_required_empty_response_broken_path_and_missing_request_are_separate(self) -> None:
        required = self.context_data("request-response")
        item = required["patterns"]["scopes"][0]["declarations"]["requests"][0]
        item.update(response_edges=[], completion_paths=[])
        result = self.assess("request-response", required)
        self.assertEqual(self.rules(result)["request/required-response"]["status"], "violated")

        broken = self.context_data("request-response")
        broken["patterns"]["scopes"][0]["declarations"]["requests"][0]["completion_paths"][0]["path"] = ["e4"]
        result = self.assess("request-response", broken)
        self.assertEqual(self.rules(result)["request/completion-path"]["status"], "violated")

        missing = self.context_data("request-response")
        missing["patterns"]["scopes"][0]["declarations"]["requests"] = []
        result = self.assess("request-response", missing)
        self.assertEqual(result["gate"], "incomplete")
        self.assertIn("requests[request]", self.rules(result)["request/coverage"]["missing_declarations"])

    def test_fork_optional_path_may_be_empty_and_extra_declared_scope_edge_is_a_rule_failure(self) -> None:
        optional = self.context_data("fork-join")
        branch = optional["patterns"]["scopes"][0]["declarations"]["branches"][1]
        branch.update(required=False, join_path=[])
        self.assertEqual(self.assess("fork-join", optional)["gate"], "passed")

        extra = self.context_data("fork-join")
        extra["patterns"]["scopes"][0]["declarations"]["branches"].pop()
        result = self.assess("fork-join", extra)
        self.assertEqual(self.rules(result)["fork/coverage"]["status"], "violated")

    def test_fork_branch_nodes_rejects_unproven_extra_members(self) -> None:
        data = self.context_data("fork-join")
        data["patterns"]["scopes"][0]["declarations"]["branches"][0]["branch_nodes"] = [
            "left", "right"
        ]
        result = self.assess("fork-join", data)
        rule = self.rules(result)["fork/branches"]
        self.assertEqual(rule["status"], "violated")
        self.assertEqual(rule["evidence"]["invalid_membership"][0]["unproven_branch_nodes"], ["right"])

    def test_fork_required_branch_and_fanin_required_input_need_continuous_paths(self) -> None:
        fork = self.context_data("fork-join")
        fork["patterns"]["scopes"][0]["declarations"]["branches"][1]["join_path"] = []
        result = self.assess("fork-join", fork)
        self.assertEqual(self.rules(result)["fork/required-join"]["status"], "violated")

        fanin = self.context_data("fan-in")
        fanin["patterns"]["scopes"][0]["declarations"]["inputs"][1]["path"] = ["leave"]
        result = self.assess("fan-in", fanin)
        self.assertEqual(self.rules(result)["fanin/required-path"]["status"], "violated")

    def test_fanin_repeated_source_does_not_count_as_two_inputs(self) -> None:
        data = self.context_data("fan-in")
        data["patterns"]["scopes"][0]["declarations"]["inputs"][1].update(
            source="left", path=["left-join"]
        )
        result = self.assess("fan-in", data)
        self.assertEqual(self.rules(result)["fanin/coverage"]["status"], "violated")

    def test_fanin_optional_path_and_duplicate_input_id_are_distinct(self) -> None:
        optional = self.context_data("fan-in")
        item = optional["patterns"]["scopes"][0]["declarations"]["inputs"][1]
        item.update(required=False, path=[])
        self.assertEqual(self.assess("fan-in", optional)["gate"], "passed")
        duplicate = self.context_data("fan-in")
        duplicate["patterns"]["scopes"][0]["declarations"]["inputs"][1]["input_id"] = "left-input"
        self.assert_cli_error(json.dumps(duplicate).encode(), "schema/duplicate", "duplicate-input-id.json")

    def test_lifecycle_allows_declared_cycle_and_rejects_undeclared_same_state_edge(self) -> None:
        valid = self.assess("lifecycle", self.context_data("lifecycle"))
        self.assertEqual(valid["gate"], "passed")
        invalid = self.context_data("lifecycle")
        invalid["patterns"]["scopes"][0]["declarations"]["allowed_transitions"] = [
            {"from": "created", "to": "active"}, {"from": "active", "to": "complete"}
        ]
        result = self.assess("lifecycle", invalid)
        self.assertEqual(self.rules(result)["lifecycle/allowed-transition"]["status"], "violated")

    def test_lifecycle_missing_node_mapping_is_incomplete_and_phase_is_not_a_state(self) -> None:
        data = self.context_data("lifecycle")
        data["patterns"]["scopes"][0]["declarations"]["node_states"].pop("n2")
        result = self.assess("lifecycle", data)
        self.assertEqual(result["gate"], "incomplete")
        self.assertIn("node_states[n2]", self.rules(result)["lifecycle/state-coverage"]["missing_declarations"])

        phase = linear_spec("lifecycle")
        phase["phases"] = [{"id": "phase-a", "label": "Phase A", "from_rank": 1, "to_rank": 3}]
        phase_path = self.directory / "phase-is-not-state.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(phase), phase_path)
        scope = self.base_scope("linear")
        scope.update(pattern="lifecycle", declarations={})
        result = self.assess(
            "lifecycle", self.context_data("lifecycle", scope=scope, path=phase_path), path=phase_path
        )
        self.assertEqual(result["gate"], "incomplete")

    def test_validate_and_inspect_split_failed_and_incomplete_exit_codes(self) -> None:
        failed = self.context_data("linear")
        failed["patterns"]["scopes"][0]["declarations"]["exit"] = "n1"
        incomplete = self.context_data("linear")
        incomplete["patterns"]["scopes"][0]["declarations"].pop("exit")
        for label, data, gate in (("failed", failed, "failed"),
                                  ("incomplete", incomplete, "incomplete")):
            context_path = self.write_context(data, f"cli-{label}.json")
            validate = run_tool("validate", "--input", str(self.paths["linear"]),
                                "--context", str(context_path))
            inspect = run_tool("inspect", "--input", str(self.paths["linear"]),
                               "--context", str(context_path))
            self.assertEqual(validate.returncode, 1)
            self.assertEqual(inspect.returncode, 0)
            self.assertEqual(json.loads(validate.stdout)["semantic_context"]["gate"], gate)
            self.assertEqual(json.loads(inspect.stdout)["semantic_context"]["gate"], gate)

    def test_general_validation_failure_makes_pattern_rules_not_assessed(self) -> None:
        tree = self.loaded.document.read_tree(self.paths["linear"])
        pool = self.loaded.document.find_pool(tree)
        pool.set(self.loaded.contracts.DATA_MAIN_PATH, json.dumps(["n1", "n2"]))
        self.loaded.metadata.refresh_managed_metadata(tree)
        path = self.directory / "bad-general-validation.drawio"
        self.loaded.document.write_tree(tree, path)
        context = self.write_context(
            self.context_data("linear", path=path), "bad-general-validation.json"
        )
        validate = run_tool("validate", "--input", str(path), "--context", str(context))
        inspect = run_tool("inspect", "--input", str(path), "--context", str(context))
        self.assertEqual(validate.returncode, 1)
        self.assertEqual(inspect.returncode, 0)
        for envelope in (json.loads(validate.stdout), json.loads(inspect.stdout)):
            semantic = envelope["semantic_context"]
            self.assertEqual(semantic["gate"], "incomplete")
            self.assertTrue(all(rule["status"] == "not_assessed"
                                for rule in semantic["scope_results"][0]["rules"]))
            self.assertTrue(any(item["code"] == "semantic/main-path-start"
                                for item in (envelope.get("diagnostics") or envelope["validation"]["diagnostics"])))

    def test_custom_does_not_exempt_an_existing_strict_warning(self) -> None:
        tree = self.loaded.document.read_tree(self.paths["custom"])
        root = self.loaded.document.graph_root(tree)
        _, nodes = self.loaded.document.lane_node_records(root, self.loaded.document.find_pool(tree))
        geometry = nodes["n1"]["cell"].find("mxGeometry")
        geometry.set("x", "500")
        path = self.directory / "custom-warning.drawio"
        self.loaded.document.write_tree(tree, path)
        context = self.write_context(self.context_data("custom", path=path), "custom-warning.json")
        result = run_tool("validate", "--input", str(path), "--context", str(context), "--strict")
        self.assertEqual(result.returncode, 1)
        envelope = json.loads(result.stdout)
        self.assertTrue(envelope["warnings"])
        self.assertEqual(envelope["semantic_context"]["gate"], "incomplete")

    def test_no_context_is_repeatable_and_read_only(self) -> None:
        path = self.paths["linear"]
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        first_validate = run_tool("validate", "--input", str(path))
        first_inspect = run_tool("inspect", "--input", str(path))
        second_validate = run_tool("validate", "--input", str(path))
        second_inspect = run_tool("inspect", "--input", str(path))
        self.assertEqual((first_validate.stdout, first_validate.stderr, first_validate.returncode),
                         (second_validate.stdout, second_validate.stderr, second_validate.returncode))
        self.assertEqual((first_inspect.stdout, first_inspect.stderr, first_inspect.returncode),
                         (second_inspect.stdout, second_inspect.stderr, second_inspect.returncode))
        self.assertNotIn("semantic_context", json.loads(first_validate.stdout))
        self.assertNotIn("semantic_context", json.loads(first_inspect.stdout))
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))

    def test_context_assessment_is_read_only_for_both_files(self) -> None:
        data = self.context_data("linear")
        context_path = self.write_context(data, "read-only-context.json")
        diagram = self.paths["linear"]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in (diagram, context_path)]
        self.assertEqual(run_tool("validate", "--input", str(diagram), "--context", str(context_path)).returncode, 0)
        self.assertEqual(run_tool("inspect", "--input", str(diagram), "--context", str(context_path)).returncode, 0)
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in (diagram, context_path)])

    def test_direct_context_assessment_does_not_call_build_patch_route_or_writer(self) -> None:
        data = self.context_data("linear")
        context_path = self.write_context(data, "dynamic-read-only-context.json")
        tree = self.loaded.document.read_tree(self.paths["linear"])
        loaded = self.semantic.load_context(context_path)
        validation = self.loaded.validation.validate_tree(tree)
        with (
            mock.patch.object(self.loaded.document, "write_tree", side_effect=AssertionError("writer called")),
            mock.patch.object(self.loaded.build, "build_tree", side_effect=AssertionError("build called")),
            mock.patch.object(self.loaded.roundtrip, "patch_tree", side_effect=AssertionError("patch called")),
            mock.patch.object(self.loaded.routing, "plan_route_batch", side_effect=AssertionError("route called")),
        ):
            result = self.semantic.assess_context(
                loaded, tree, hashlib.sha256(self.paths["linear"].read_bytes()).hexdigest(), validation
            )
        self.assertEqual(result["gate"], "passed")

    def assert_cli_error(self, raw: bytes, code: str, name: str) -> None:
        path = self.directory / name
        path.write_bytes(raw)
        result = run_tool("validate", "--input", str(self.paths["linear"]), "--context", str(path))
        self.assertEqual(result.returncode, 2, result.stdout.decode(errors="replace"))
        self.assertEqual(json.loads(result.stdout)["diagnostics"][0]["code"], code)
        self.assertNotIn("semantic_context", json.loads(result.stdout))

    def assert_load_error(self, raw: bytes, code: str, name: str) -> None:
        path = self.directory / name
        path.write_bytes(raw)
        with self.assertRaises(self.loaded.contracts.DiagramError) as raised:
            self.semantic.load_context(path)
        self.assertEqual(raised.exception.code, code)

    def test_duplicate_keys_nonfinite_numbers_boolean_versions_and_unknown_fields_fail_closed(self) -> None:
        valid = self.context_data("linear")
        duplicate = json.dumps(valid).replace('"context_version": 1', '"context_version": 1, "context_version": 1', 1)
        self.assert_cli_error(duplicate.encode(), "context/duplicate-key", "duplicate-key.json")
        for constant in ("NaN", "Infinity", "-Infinity"):
            nonfinite = json.dumps(valid).replace('"context_version": 1', f'"context_version": {constant}', 1)
            self.assert_cli_error(
                nonfinite.encode(), "input/json-invalid", f"nonfinite-{constant.replace('-', 'minus')}.json"
            )
        boolean = copy.deepcopy(valid)
        boolean["context_version"] = True
        self.assert_cli_error(json.dumps(boolean).encode(), "schema/type", "boolean-version.json")
        for field in ("schema_version", "model_hash_version"):
            artifact_boolean = copy.deepcopy(valid)
            artifact_boolean["artifact"][field] = True
            self.assert_cli_error(
                json.dumps(artifact_boolean).encode(), "schema/type", f"boolean-{field}.json"
            )
        unknown = copy.deepcopy(valid)
        unknown["unexpected"] = "data"
        self.assert_cli_error(json.dumps(unknown).encode(), "schema/unknown-field", "unknown-field.json")

        surrogate = json.dumps(valid).replace('"linear-primary"', '"\\ud800"', 1)
        self.assert_cli_error(surrogate.encode(), "input/json-invalid", "surrogate-value.json")
        duplicate_surrogate = b'{"\\ud800":1,"\\ud800":2}'
        self.assert_cli_error(duplicate_surrogate, "context/duplicate-key", "surrogate-duplicate-key.json")

    def test_artifact_digest_model_binding_and_provenance_schema_are_distinct(self) -> None:
        digest = self.context_data("linear")
        digest["artifact"]["sha256"] = "0" * 64
        self.assert_cli_error(json.dumps(digest).encode(), "context/artifact-mismatch", "digest-mismatch.json")
        model = self.context_data("linear")
        model["artifact"]["model_hash"] = "0" * 64
        self.assert_cli_error(json.dumps(model).encode(), "context/model-mismatch", "model-mismatch.json")

        stale = self.context_data("linear")
        tree = self.loaded.document.read_tree(self.paths["linear"])
        root = self.loaded.document.graph_root(tree)
        _, nodes = self.loaded.document.lane_node_records(root, self.loaded.document.find_pool(tree))
        nodes["n1"]["cell"].set("value", "Changed text with stable ID")
        self.loaded.metadata.refresh_managed_metadata(tree)
        stale_path = self.directory / "stale-after-label-change.drawio"
        self.loaded.document.write_tree(tree, stale_path)
        self.assert_cli_error_for_diagram(
            stale, stale_path, "context/artifact-mismatch", "stale-after-label-change.json"
        )
        supported = self.context_data("linear")
        supported["provenance"] = {
            "version": 1, "sources": [], "facts": [], "bindings": [], "history": [],
        }
        loaded = self.semantic.decode_context(json.dumps(supported).encode())
        result = self.assess("linear", loaded.data)
        self.assertEqual(result["components"]["provenance"]["status"], "provided_empty")
        self.assertEqual(result["source_gate"], "incomplete")

    def test_unsupported_context_pattern_and_artifact_versions_are_not_stale_identity(self) -> None:
        for field in ("context_version", "patterns"):
            with self.subTest(field=field):
                data = self.context_data("linear")
                if field == "context_version":
                    data[field] = 2
                else:
                    data[field]["version"] = 2
                self.assert_cli_error(
                    json.dumps(data).encode(), "context/version-unsupported", f"unsupported-{field}.json"
                )
        for field, value in (("schema_version", "4"), ("model_hash_version", "2")):
            with self.subTest(field=field):
                data = self.context_data("linear")
                data["artifact"][field] = value
                self.assert_cli_error(
                    json.dumps(data).encode(), "context/artifact-unsupported",
                    f"unsupported-artifact-{field}.json",
                )

    def test_primary_scope_cardinality_pattern_match_and_scope_closure(self) -> None:
        no_primary = self.context_data("linear")
        no_primary["patterns"]["scopes"][0]["role"] = "local"
        self.assert_cli_error(json.dumps(no_primary).encode(), "context/scope-incomplete", "no-primary.json")
        mismatch = self.context_data("linear")
        mismatch["patterns"]["scopes"][0]["pattern"] = "custom"
        mismatch["patterns"]["scopes"][0]["declarations"] = {}
        self.assert_cli_error(json.dumps(mismatch).encode(), "context/pattern-mismatch", "pattern-mismatch.json")
        omitted = self.context_data("linear")
        omitted["patterns"]["scopes"][0]["edges"].remove("e1")
        self.assert_cli_error(json.dumps(omitted).encode(), "context/scope-incomplete", "scope-not-closed.json")

        overlap = self.context_data("linear")
        overlap["patterns"]["scopes"][0]["excluded_edges"] = [
            {"edge_id": "e1", "reason": "cannot also be selected"}
        ]
        self.assert_cli_error(json.dumps(overlap).encode(), "context/scope-incomplete", "scope-overlap.json")

        two_primary = self.context_data("linear")
        second = copy.deepcopy(two_primary["patterns"]["scopes"][0])
        second["id"] = "second-primary"
        two_primary["patterns"]["scopes"].append(second)
        self.assert_cli_error(json.dumps(two_primary).encode(), "context/scope-incomplete", "two-primary.json")

    def test_scope_and_nested_ids_types_reasons_and_unknown_fields_are_strict(self) -> None:
        cases = []
        duplicate_nodes = self.context_data("linear")
        duplicate_nodes["patterns"]["scopes"][0]["nodes"].append("n0")
        cases.append((duplicate_nodes, "schema/duplicate", "duplicate-node.json"))
        duplicate_scope = self.context_data("linear")
        local = copy.deepcopy(duplicate_scope["patterns"]["scopes"][0])
        local["role"] = "local"
        duplicate_scope["patterns"]["scopes"].append(local)
        cases.append((duplicate_scope, "schema/duplicate", "duplicate-scope.json"))
        empty_id = self.context_data("linear")
        empty_id["patterns"]["scopes"][0]["id"] = ""
        cases.append((empty_id, "schema/type", "empty-id.json"))
        invalid_id = self.context_data("linear")
        invalid_id["patterns"]["scopes"][0]["id"] = "bad id"
        cases.append((invalid_id, "schema/id-format", "invalid-id.json"))
        wrong_type = self.context_data("linear")
        wrong_type["patterns"]["scopes"][0]["nodes"] = "n0"
        cases.append((wrong_type, "schema/type", "wrong-type.json"))
        missing_reason = self.context_data("linear")
        missing_reason["patterns"]["scopes"][0]["edges"].remove("e1")
        missing_reason["patterns"]["scopes"][0]["excluded_edges"] = [{"edge_id": "e1"}]
        cases.append((missing_reason, "schema/required", "missing-reason.json"))
        nested_unknown = self.context_data("linear")
        nested_unknown["patterns"]["scopes"][0]["unexpected"] = True
        cases.append((nested_unknown, "schema/unknown-field", "nested-unknown.json"))
        custom_fields = self.context_data("custom")
        custom_fields["patterns"]["scopes"][0]["declarations"] = {"entry": "n0"}
        cases.append((custom_fields, "schema/unknown-field", "custom-declaration-field.json"))
        endpoint_outside = self.context_data("linear")
        endpoint_outside["patterns"]["scopes"][0]["nodes"].remove("n2")
        cases.append((endpoint_outside, "context/reference-invalid", "edge-endpoint-outside-scope.json"))
        duplicate_exclusion = self.context_data("linear")
        duplicate_exclusion["patterns"]["scopes"][0]["edges"].remove("e1")
        duplicate_exclusion["patterns"]["scopes"][0]["excluded_edges"] = [
            {"edge_id": "e1", "reason": "first"}, {"edge_id": "e1", "reason": "second"}
        ]
        cases.append((duplicate_exclusion, "schema/duplicate", "duplicate-exclusion.json"))
        for data, code, name in cases:
            with self.subTest(name=name):
                self.assert_cli_error(json.dumps(data).encode(), code, name)

    def test_context_byte_and_depth_limits_have_exact_boundaries(self) -> None:
        data = self.context_data("linear")
        compact = json.dumps(data, separators=(",", ":")).encode()
        exact = compact + b" " * (self.semantic.MAX_BYTES - len(compact))
        exact_path = self.directory / "exact-two-mib.json"
        exact_path.write_bytes(exact)
        loaded = self.semantic.load_context(exact_path)
        tree = self.loaded.document.read_tree(self.paths["linear"])
        result = self.semantic.assess_context(
            loaded, tree, hashlib.sha256(self.paths["linear"].read_bytes()).hexdigest(),
            self.loaded.validation.validate_tree(tree),
        )
        self.assertEqual(len(exact), 2 * 1024 * 1024)
        self.assertEqual(result["gate"], "passed")
        self.assert_load_error(exact + b" ", "context/resource-limit", "over-two-mib.json")

        depth_32 = ("[" * 32 + "0" + "]" * 32).encode()
        self.assert_load_error(depth_32, "schema/type", "depth-32.json")
        depth_33 = ("[" * 33 + "0" + "]" * 33).encode()
        self.assert_load_error(depth_33, "context/resource-limit", "depth-33.json")

    def test_scope_limit_accepts_128_closed_scopes_and_rejects_129(self) -> None:
        data = self.context_data("linear")
        primary = data["patterns"]["scopes"][0]
        for index in range(127):
            local = copy.deepcopy(primary)
            local.update(id=f"local-{index:03}", role="local", pattern="custom", declarations={})
            data["patterns"]["scopes"].append(local)
        result = self.assess("linear", data)
        self.assertEqual(result["coverage"]["scope_count"], 128)
        self.assertEqual(result["gate"], "passed")
        over = copy.deepcopy(data)
        extra = copy.deepcopy(primary)
        extra.update(id="local-128", role="local", pattern="custom", declarations={})
        over["patterns"]["scopes"].append(extra)
        self.assert_load_error(json.dumps(over).encode(), "context/resource-limit", "scopes-129.json")

    def test_id_reference_limit_uses_closed_assessable_scopes_at_20000(self) -> None:
        count = 100
        source = spec(
            "linear",
            [node(f"r{i}", i + 1, "start" if i == 0 else "end" if i == count - 1 else "process")
             for i in range(count)],
            [{"id": f"re{i}", "from": f"r{i}", "to": f"r{i + 1}"}
             for i in range(count - 1)],
            [f"r{i}" for i in range(count)],
        )
        diagram = self.directory / "reference-limit.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(source), diagram)

        primary = {
            "id": "primary", "role": "primary", "pattern": "linear",
            "nodes": [f"r{i}" for i in range(count)],
            "edges": [f"re{i}" for i in range(count - 1)], "excluded_edges": [],
            "declarations": {"entry": "r0", "exit": "r99"},
        }

        def local_scope(index: int, selected_count: int, *, fanin=False) -> dict:
            start = 10
            nodes = [f"r{i}" for i in range(start, start + selected_count)]
            edges = [f"re{i}" for i in range(start, start + selected_count - 1)]
            excluded = [
                {"edge_id": f"re{start - 1}", "reason": "left boundary"},
                {"edge_id": f"re{start + selected_count - 1}", "reason": "right boundary"},
            ]
            if fanin:
                pattern = "fan-in"
                declarations = {
                    "target": nodes[2],
                    "inputs": [
                        {"input_id": f"input-{index}-a", "source": nodes[0],
                         "required": False, "path": []},
                        {"input_id": f"input-{index}-b", "source": nodes[1],
                         "required": False, "path": []},
                    ],
                }
            else:
                pattern, declarations = "custom", {}
            return {
                "id": f"local-{index:03}", "role": "local", "pattern": pattern,
                "nodes": nodes, "edges": edges, "excluded_edges": excluded,
                "declarations": declarations,
            }

        exact = {
            "context_version": 1, "artifact": self.artifact("linear", diagram),
            "patterns": {"version": 1, "scopes": [primary]},
        }
        exact["patterns"]["scopes"].extend(local_scope(i, 77) for i in range(126))
        exact["patterns"]["scopes"].append(local_scope(126, 70))
        # 202 primary references + 126 * 156 + 142 = 20,000.
        exact_path = self.write_context(exact, "references-20000.json")
        loaded = self.semantic.load_context(exact_path)
        self.assertEqual(loaded.data["patterns"]["scopes"][-1]["nodes"][-1], "r79")
        tree = self.loaded.document.read_tree(diagram)
        assessed = self.semantic.assess_context(
            loaded, tree, hashlib.sha256(diagram.read_bytes()).hexdigest(),
            self.loaded.validation.validate_tree(tree),
        )
        self.assertEqual(assessed["gate"], "passed")
        self.assertEqual(assessed["coverage"]["scope_count"], 128)

        over = copy.deepcopy(exact)
        over["patterns"]["scopes"][-1] = local_scope(126, 68, fanin=True)
        # 202 + 126 * 156 + (138 structural + 5 declaration) = 20,001.
        self.assert_load_error(
            json.dumps(over).encode(), "context/resource-limit", "references-20001.json"
        )

    def test_overlapping_scopes_are_sorted_and_do_not_overstate_whole_diagram_coverage(self) -> None:
        data = self.context_data("linear")
        local = copy.deepcopy(data["patterns"]["scopes"][0])
        local.update(id="a-local", role="local", pattern="custom", declarations={})
        data["patterns"]["scopes"].insert(0, local)
        result = self.assess("linear", data)
        self.assertEqual([scope["scope_id"] for scope in result["scope_results"]],
                         ["a-local", "linear-primary"])
        self.assertEqual(result["coverage"]["extent"], "whole_diagram")

        tree = self.loaded.document.read_tree(self.paths["linear"])
        root = self.loaded.document.graph_root(tree)
        original = self.loaded.document.edge_records(root)["e1"]
        parallel = copy.deepcopy(original)
        parallel.set("id", "partial-parallel-native")
        parallel.set(self.loaded.contracts.DATA_SEMANTIC_ID, "partial-parallel")
        root.append(parallel)
        self.loaded.metadata.refresh_managed_metadata(tree)
        path = self.directory / "partial-linear.drawio"
        self.loaded.document.write_tree(tree, path)
        scope = self.base_scope("linear")
        scope["excluded_edges"] = [{"edge_id": "partial-parallel", "reason": "notification path"}]
        partial = self.assess("linear", self.context_data("linear", scope=scope, path=path), path=path)
        self.assertEqual(partial["gate"], "passed")
        self.assertEqual(partial["coverage"]["extent"], "partial")
        self.assertEqual(partial["scope_results"][0]["coverage"]["excluded_edges"],
                         [{"edge_id": "partial-parallel", "reason": "notification path"}])

    def test_native_duplicate_geometry_and_endpoint_disagreement_are_rejected_before_rules(self) -> None:
        for case in ("geometry", "endpoint"):
            with self.subTest(case=case):
                tree = self.loaded.document.read_tree(self.paths["linear"])
                if case == "geometry":
                    pool = self.loaded.document.find_pool(tree)
                    ET.SubElement(pool, "mxGeometry", {"as": "geometry", "x": "0", "y": "0",
                                                       "width": "1", "height": "1"})
                else:
                    root = self.loaded.document.graph_root(tree)
                    nodes = self.loaded.document.lane_node_records(root, self.loaded.document.find_pool(tree))[1]
                    edge = self.loaded.document.edge_records(root)["e0"]
                    edge.set("source", nodes["n2"]["cell"].get("id"))
                path = self.directory / f"native-{case}.drawio"
                tree.write(path, encoding="utf-8", xml_declaration=True)
                data = self.context_data("linear", path=path)
                self.assert_cli_error_for_diagram(data, path, "context/artifact-invalid", f"native-{case}.json")

    def assert_cli_error_for_diagram(self, data: dict, diagram: Path, code: str, name: str) -> None:
        context = self.write_context(data, name)
        result = run_tool("validate", "--input", str(diagram), "--context", str(context))
        self.assertEqual(result.returncode, 2, result.stdout.decode(errors="replace"))
        self.assertEqual(json.loads(result.stdout)["diagnostics"][0]["code"], code)

    def test_cross_kind_semantic_id_reuse_remains_valid(self) -> None:
        shared = spec(
            "linear",
            [node("shared", 1, "start", lane="shared"), node("other", 2, "end", lane="shared")],
            [{"id": "shared", "from": "shared", "to": "other"}],
            ["shared", "other"],
            lanes=[{"id": "shared", "label": "Shared lane", "width": 240}],
        )
        path = self.directory / "cross-kind-id.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(shared), path)
        scope = {"id": "shared-scope", "role": "primary", "pattern": "linear",
                 "nodes": ["shared", "other"], "edges": ["shared"], "excluded_edges": [],
                 "declarations": {"entry": "shared", "exit": "other"}}
        result = self.assess("linear", self.context_data("linear", scope=scope, path=path), path=path)
        self.assertEqual(result["gate"], "passed")

    def test_same_kind_duplicate_semantic_id_is_rejected(self) -> None:
        tree = self.loaded.document.read_tree(self.paths["linear"])
        root = self.loaded.document.graph_root(tree)
        _, nodes = self.loaded.document.lane_node_records(root, self.loaded.document.find_pool(tree))
        nodes["n1"]["cell"].set(self.loaded.contracts.DATA_SEMANTIC_ID, "n0")
        path = self.directory / "same-kind-duplicate.drawio"
        tree.write(path, encoding="utf-8", xml_declaration=True)
        data = self.context_data("linear", path=path)
        self.assert_cli_error_for_diagram(
            data, path, "context/artifact-invalid", "same-kind-duplicate.json"
        )

    def test_v1_v2_and_missing_historical_hash_remain_legacy_without_context(self) -> None:
        cases = []
        for version in ("1", "2"):
            source = linear_spec()
            if version == "1":
                source.pop("schema_version")
                source.pop("behavior_pattern")
                source.pop("layout")
            else:
                source["schema_version"] = "2"
                source.pop("behavior_pattern")
                source.pop("layout")
            path = self.directory / f"legacy-v{version}.drawio"
            self.loaded.document.write_tree(self.loaded.build.build_tree(source), path)
            cases.append((f"v{version}", path))

        tree = self.loaded.document.read_tree(self.paths["linear"])
        pool = self.loaded.document.find_pool(tree)
        pool.attrib.pop(self.loaded.contracts.DATA_MODEL_HASH)
        missing = self.directory / "legacy-missing-hash.drawio"
        tree.write(missing, encoding="utf-8", xml_declaration=True)
        cases.append(("missing-hash", missing))

        for name, path in cases:
            with self.subTest(name=name):
                default = run_tool("inspect", "--input", str(path))
                self.assertEqual(default.returncode, 0)
                self.assertNotIn("semantic_context", json.loads(default.stdout))
                artifact = {
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "schema_version": "3", "model_hash_version": "1", "model_hash": "0" * 64,
                }
                data = {"context_version": 1, "artifact": artifact,
                        "patterns": {"version": 1, "scopes": [self.base_scope("linear")]}}
                self.assert_cli_error_for_diagram(
                    data, path, "context/artifact-unsupported", f"legacy-{name}.json"
                )

    def test_only_validate_and_inspect_accept_context_option(self) -> None:
        context = self.write_context(self.context_data("linear"), "option-scope.json")
        self.assertIn(b"--context", run_tool("validate", "--help").stdout)
        self.assertIn(b"--context", run_tool("inspect", "--help").stdout)
        for command in ("validate", "inspect"):
            missing_value = run_tool(command, "--input", str(self.paths["linear"]), "--context")
            self.assertEqual(missing_value.returncode, 2)
            self.assertIn(b"expected one argument", missing_value.stderr)
            missing_file = run_tool(
                command, "--input", str(self.paths["linear"]),
                "--context", str(self.directory / "does-not-exist.json"),
            )
            self.assertEqual(missing_file.returncode, 2)
            self.assertEqual(json.loads(missing_file.stdout)["diagnostics"][0]["code"],
                             "delivery/io-error")
        for command in ("build", "patch", "compare", "migrate"):
            with self.subTest(command=command):
                result = run_tool(command, "--context", str(context))
                self.assertEqual(result.returncode, 2)

    def test_pattern_modules_keep_read_only_dependency_direction(self) -> None:
        core = TOOL.parent / "swimlane_core"
        forbidden = {"build", "migration", "patch_operations", "routing", "routing_adapter"}
        for name in ("pattern_rules.py", "semantic_context.py"):
            text = (core / name).read_text(encoding="utf-8")
            for module in forbidden:
                self.assertNotRegex(text, rf"(?:from \. import .*\b{module}\b|from \.{module} import)")

if __name__ == "__main__":
    unittest.main()
