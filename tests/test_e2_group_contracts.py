import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules


def run_tool(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-B", str(TOOL), *args], cwd=ROOT,
        capture_output=True, check=False,
    )


def node(node_id: str, rank: int, node_type: str = "process", *, slot: str | None = None) -> dict:
    value = {
        "id": node_id, "lane": "lane-a", "rank": rank, "type": node_type,
        "label": "" if node_type in {"start", "end"} else f"Neutral {node_id}",
    }
    if slot:
        value["slot"] = slot
    return value


def base_spec(nodes: list[dict], edges: list[dict], main_path: list[str], groups: list[dict]) -> dict:
    return {
        "schema_version": "3", "title": "Neutral group contract",
        "behavior_pattern": "custom", "layout": {"profile": "review"},
        "lanes": [{"id": "lane-a", "label": "Lane A", "width": 260}],
        "nodes": nodes, "edges": edges, "main_path": main_path, "groups": groups,
    }


def parallel_spec() -> dict:
    return base_spec(
        [node("start", 1, "start"), node("fork", 2, "decision"),
         node("left", 3, slot="left"), node("right", 3, slot="right"),
         node("shared", 4), node("join", 5), node("end", 6, "end")],
        [{"id": "enter", "from": "start", "to": "fork"},
         {"id": "fork-left", "from": "fork", "to": "left", "outcome": "left"},
         {"id": "fork-right", "from": "fork", "to": "right", "outcome": "right"},
         {"id": "left-shared", "from": "left", "to": "shared"},
         {"id": "right-shared", "from": "right", "to": "shared"},
         {"id": "shared-join", "from": "shared", "to": "join"},
         {"id": "leave", "from": "join", "to": "end"}],
        ["start", "fork", "left", "shared", "join", "end"],
        [{"id": "parallel-group", "lane": "lane-a", "kind": "parallel",
          "nodes": ["left", "right", "shared"]}],
    )


def branch_spec(*, shared_outcome: bool = True) -> dict:
    left_outcome = "shared-outcome" if shared_outcome else "left-outcome"
    right_outcome = "shared-outcome" if shared_outcome else "right-outcome"
    return base_spec(
        [node("start", 1, "start"), node("decision", 2, "decision"),
         node("left-action", 3, slot="left"), node("right-action", 3, slot="right"),
         node("end", 4, "end")],
        [{"id": "enter", "from": "start", "to": "decision"},
         {"id": "left-result", "from": "decision", "to": "left-action",
          "outcome": left_outcome},
         {"id": "right-result", "from": "decision", "to": "right-action",
          "outcome": right_outcome},
         {"id": "left-end", "from": "left-action", "to": "end"},
         {"id": "right-end", "from": "right-action", "to": "end"}],
        ["start", "decision", "left-action", "end"],
        [{"id": "branch-group", "lane": "lane-a", "kind": "branch",
          "nodes": ["left-action", "right-action"]}],
    )


def merge_spec() -> dict:
    return base_spec(
        [node("start", 1, "start"), node("left-source", 2, slot="left"),
         node("right-source", 2, slot="right"), node("merge-target", 3),
         node("end", 4, "end")],
        [{"id": "start-left", "from": "start", "to": "left-source"},
         {"id": "start-right", "from": "start", "to": "right-source"},
         {"id": "left-merge", "from": "left-source", "to": "merge-target"},
         {"id": "right-merge", "from": "right-source", "to": "merge-target"},
         {"id": "leave", "from": "merge-target", "to": "end"}],
        ["start", "left-source", "merge-target", "end"],
        [{"id": "merge-group", "lane": "lane-a", "kind": "merge",
          "nodes": ["left-source", "right-source", "merge-target"]}],
    )


def reference_only_spec() -> dict:
    return base_spec(
        [node("start", 1, "start"), node("exception-node", 2),
         node("support-node", 3), node("end", 4, "end")],
        [{"id": "e0", "from": "start", "to": "exception-node"},
         {"id": "e1", "from": "exception-node", "to": "support-node"},
         {"id": "e2", "from": "support-node", "to": "end"}],
        ["start", "exception-node", "support-node", "end"],
        [{"id": "exception-group", "lane": "lane-a", "kind": "exception",
          "nodes": ["exception-node"]},
         {"id": "support-group", "lane": "lane-a", "kind": "support",
          "nodes": ["support-node"]}],
    )


def parallel_declaration() -> dict:
    return {
        "fork": "fork", "join": "join",
        "branches": [
            {"branch_id": "left-branch", "entry_edge": "fork-left",
             "members": ["left", "shared"],
             "branch_edges": ["left-shared", "shared-join"], "join_required": True},
            {"branch_id": "right-branch", "entry_edge": "fork-right",
             "members": ["right", "shared"],
             "branch_edges": ["right-shared", "shared-join"], "join_required": True},
        ],
    }


def branch_declaration(*, shared_outcome: bool = True) -> dict:
    return {
        "decision": "decision",
        "branches": [
            {"entry_edge": "left-result",
             "outcome": "shared-outcome" if shared_outcome else "left-outcome",
             "member": "left-action", "path": []},
            {"entry_edge": "right-result",
             "outcome": "shared-outcome" if shared_outcome else "right-outcome",
             "member": "right-action", "path": []},
        ],
    }


def merge_declaration() -> dict:
    return {
        "target": "merge-target",
        "inputs": [
            {"input_id": "left-input", "source": "left-source", "required": True,
             "path": ["left-merge"]},
            {"input_id": "right-input", "source": "right-source", "required": True,
             "path": ["right-merge"]},
        ],
    }


class E2GroupContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e2_group_contract_tests")
        cls.semantic = cls.loaded.tool.semantic_context
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.specs = {
            "parallel": parallel_spec(), "branch": branch_spec(),
            "branch-distinct": branch_spec(shared_outcome=False),
            "merge": merge_spec(), "reference": reference_only_spec(),
        }
        cls.paths = {}
        for name, source in cls.specs.items():
            path = cls.directory / f"{name}.drawio"
            cls.loaded.document.write_tree(cls.loaded.build.build_tree(source), path)
            cls.paths[name] = path

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def artifact(self, path: Path) -> dict:
        tree = self.loaded.document.read_tree(path)
        pool = self.loaded.document.find_pool(tree)
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "schema_version": pool.get(self.loaded.contracts.DATA_SCHEMA_VERSION),
            "model_hash_version": pool.get(self.loaded.contracts.DATA_MODEL_HASH_VERSION),
            "model_hash": pool.get(self.loaded.contracts.DATA_MODEL_HASH),
        }

    def scope(self, source: dict) -> dict:
        return {
            "id": "primary", "role": "primary", "pattern": "custom",
            "nodes": [item["id"] for item in source["nodes"]],
            "edges": [item["id"] for item in source["edges"]],
            "excluded_edges": [], "declarations": {},
        }

    def context(self, name: str, groups: list[dict] | None) -> dict:
        data = {
            "context_version": 1, "artifact": self.artifact(self.paths[name]),
            "patterns": {"version": 1, "scopes": [self.scope(self.specs[name])]},
        }
        if groups is not None:
            data["group_contracts"] = {"version": 1, "groups": groups}
        return data

    def contract(self, group_id: str, declarations: dict) -> dict:
        return {"group_id": group_id, "scope_id": "primary", "declarations": declarations}

    def assess(self, name: str, data: dict) -> dict:
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
        context_path = self.directory / f"context-{hashlib.sha256(raw).hexdigest()}.json"
        context_path.write_bytes(raw)
        loaded = self.semantic.load_context(context_path)
        tree = self.loaded.document.read_tree(self.paths[name])
        return self.semantic.assess_context(
            loaded, tree, hashlib.sha256(self.paths[name].read_bytes()).hexdigest(),
            self.loaded.validation.validate_tree(tree),
        )

    @staticmethod
    def group_rules(result: dict, group_id: str) -> dict[str, dict]:
        group = next(item for item in result["group_results"] if item["group_id"] == group_id)
        return {item["rule_id"]: item for item in group["rules"]}

    def write_context(self, data: dict, name: str) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def assert_cli_error(self, name: str, data: dict, code: str, case: str) -> None:
        context = self.write_context(data, case)
        for command in ("validate", "inspect"):
            result = run_tool(command, "--input", str(self.paths[name]), "--context", str(context))
            self.assertEqual(result.returncode, 2, result.stdout.decode(errors="replace"))
            envelope = json.loads(result.stdout)
            self.assertEqual(envelope["diagnostics"][0]["code"], code)
            self.assertNotIn("semantic_context", envelope)

    def test_absent_and_empty_components_have_distinct_stable_receipts(self) -> None:
        absent = self.assess("reference", self.context("reference", None))
        self.assertEqual(absent["components"]["group_contracts"], {"status": "not_provided"})
        self.assertNotIn("group_results", absent)
        self.assertNotIn("group_coverage", absent)

        empty = self.assess("reference", self.context("reference", []))
        self.assertEqual(empty["components"]["group_contracts"], {
            "version": 1, "rule_version": "1", "status": "provided_empty",
        })
        self.assertEqual(empty["group_results"], [])
        self.assertEqual(empty["group_coverage"], {
            "group_count": 0, "total_groups": 2,
            "topology_group_count": 0,
            "unrequested_groups": ["exception-group", "support-group"],
            "extent": "partial",
            "rule_status_counts": {"passed": 0, "violated": 0,
                                   "not_assessed": 0, "not_applicable": 0},
        })
        self.assertEqual(empty["group_gate"], "not_applicable")
        self.assertEqual(empty["gate"], "not_applicable")

        no_group_spec = branch_spec()
        no_group_spec["groups"] = []
        no_group_path = self.directory / "no-groups.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(no_group_spec), no_group_path)
        self.paths["no-groups"], self.specs["no-groups"] = no_group_path, no_group_spec
        no_groups = self.assess("no-groups", self.context("no-groups", []))
        self.assertEqual(no_groups["group_coverage"]["total_groups"], 0)
        self.assertEqual(no_groups["group_coverage"]["extent"], "all_groups")

    def test_parallel_branch_merge_and_reference_only_kinds_have_exact_rule_sets(self) -> None:
        cases = [
            ("parallel", "parallel-group", parallel_declaration(), "passed",
             {"group/parallel/entries", "group/parallel/members",
              "group/parallel/required-join", "group/parallel/coverage"}),
            ("branch", "branch-group", branch_declaration(), "passed",
             {"group/branch/outcomes", "group/branch/member-paths", "group/branch/coverage"}),
            ("merge", "merge-group", merge_declaration(), "passed",
             {"group/merge/target", "group/merge/required-paths", "group/merge/coverage"}),
            ("reference", "exception-group", {}, "not_applicable",
             {"group/exception/reference-only"}),
            ("reference", "support-group", {}, "not_applicable",
             {"group/support/reference-only"}),
        ]
        for name, group_id, declaration, gate, rule_ids in cases:
            with self.subTest(kind=name, group=group_id):
                result = self.assess(name, self.context(name, [self.contract(group_id, declaration)]))
                group = result["group_results"][0]
                self.assertEqual(group["gate"], gate)
                self.assertEqual(set(self.group_rules(result, group_id)), rule_ids)
                expected = "passed" if gate == "passed" else "not_applicable"
                self.assertTrue(all(rule["status"] == expected for rule in group["rules"]))

    def test_group_receipt_shape_codes_subjects_and_coverage_are_exact(self) -> None:
        result = self.assess(
            "branch", self.context("branch", [self.contract("branch-group", branch_declaration())])
        )
        self.assertEqual(set(result), {
            "context_version", "context_sha256", "artifact_sha256", "artifact", "components",
            "scope_results", "gate", "coverage", "group_results", "group_gate",
            "group_coverage",
        })
        group = result["group_results"][0]
        self.assertEqual(set(group), {"group_id", "kind", "scope_id", "gate", "coverage", "rules"})
        self.assertEqual(set(group["coverage"]), {
            "member_count", "declared_member_count", "reference_member_count", "total_members",
            "extent", "scope_id", "excluded_edges", "topology_assessed", "assessment",
        })
        for rule in group["rules"]:
            self.assertEqual(set(rule), {
                "group_id", "scope_id", "rule_id", "status", "code", "subject_ids",
                "evidence", "missing_declarations",
            })
            self.assertEqual(set(rule["subject_ids"]), {"groups", "nodes", "edges", "outcomes"})
            self.assertEqual(rule["code"], None)
            self.assertEqual(rule["subject_ids"]["groups"], ["branch-group"])
            self.assertEqual(rule["subject_ids"]["nodes"],
                             sorted(rule["subject_ids"]["nodes"]))
            self.assertEqual(rule["subject_ids"]["edges"],
                             sorted(rule["subject_ids"]["edges"]))
            self.assertEqual(rule["subject_ids"]["outcomes"], [
                {"decision_id": "decision", "outcome_id": "shared-outcome"}
            ])

    def test_default_group_structure_remains_repeatable_and_read_only(self) -> None:
        for name in ("parallel", "branch", "merge", "reference"):
            with self.subTest(name=name):
                path = self.paths[name]
                before = (path.read_bytes(), path.stat().st_mtime_ns)
                for command in ("inspect", "validate"):
                    first = run_tool(command, "--input", str(path))
                    second = run_tool(command, "--input", str(path))
                    self.assertEqual(first.returncode, 0)
                    self.assertEqual((first.stdout, first.stderr, first.returncode),
                                     (second.stdout, second.stderr, second.returncode))
                    self.assertNotIn("semantic_context", json.loads(first.stdout))
                self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))

    def test_parallel_same_rank_never_supplies_missing_declarations(self) -> None:
        result = self.assess(
            "parallel", self.context("parallel", [self.contract("parallel-group", {})])
        )
        group = result["group_results"][0]
        self.assertEqual(group["gate"], "incomplete")
        self.assertTrue(all(rule["status"] == "not_assessed" for rule in group["rules"]))
        self.assertTrue(all(rule["missing_declarations"] for rule in group["rules"]))
        self.assertFalse(group["coverage"]["topology_assessed"])

    def test_parallel_cannot_borrow_another_entry_but_can_share_a_proven_node(self) -> None:
        valid = self.assess(
            "parallel",
            self.context("parallel", [self.contract("parallel-group", parallel_declaration())]),
        )
        self.assertEqual(valid["group_results"][0]["gate"], "passed")
        self.assertEqual(valid["group_results"][0]["coverage"]["member_count"], 3)
        self.assertTrue(valid["group_results"][0]["coverage"]["topology_assessed"])

        invalid_declaration = parallel_declaration()
        invalid_declaration["branches"][0]["branch_edges"].append("fork-right")
        invalid = self.assess(
            "parallel",
            self.context("parallel", [self.contract("parallel-group", invalid_declaration)]),
        )
        self.assertEqual(invalid["group_results"][0]["gate"], "failed")
        self.assertTrue(any(rule["status"] == "violated"
                            for rule in invalid["group_results"][0]["rules"]))

    def test_parallel_missing_branch_edges_empty_zero_step_and_optional_join_are_distinct(self) -> None:
        missing = parallel_declaration()
        missing["branches"][0].pop("branch_edges")
        result = self.assess(
            "parallel", self.context("parallel", [self.contract("parallel-group", missing)])
        )
        self.assertEqual(result["group_results"][0]["gate"], "incomplete")

        empty = parallel_declaration()
        empty["branches"][0]["branch_edges"] = []
        result = self.assess(
            "parallel", self.context("parallel", [self.contract("parallel-group", empty)])
        )
        self.assertEqual(result["group_results"][0]["gate"], "failed")

        optional = parallel_declaration()
        optional["branches"][1].update(
            members=["right"], branch_edges=[], join_required=False
        )
        result = self.assess(
            "parallel", self.context("parallel", [self.contract("parallel-group", optional)])
        )
        self.assertEqual(result["group_results"][0]["gate"], "passed")

        unclaimed = parallel_declaration()
        for branch in unclaimed["branches"]:
            branch["members"] = [item for item in branch["members"] if item != "shared"]
        result = self.assess(
            "parallel", self.context("parallel", [self.contract("parallel-group", unclaimed)])
        )
        self.assertEqual(result["group_results"][0]["gate"], "incomplete")
        self.assertIn("members[shared]", result["group_results"][0]["rules"][0][
            "missing_declarations"
        ])

    def test_branch_allows_one_outcome_to_trigger_multiple_actions(self) -> None:
        result = self.assess(
            "branch", self.context("branch", [self.contract("branch-group", branch_declaration())])
        )
        self.assertEqual(result["group_results"][0]["gate"], "passed")
        outcomes = self.group_rules(result, "branch-group")["group/branch/outcomes"]["subject_ids"]["outcomes"]
        self.assertEqual(outcomes, [{"decision_id": "decision", "outcome_id": "shared-outcome"}])

    def test_branch_missing_fact_mismatched_existing_outcome_and_deleted_outcome_are_distinct(self) -> None:
        missing = branch_declaration()
        missing["branches"][0].pop("path")
        result = self.assess(
            "branch", self.context("branch", [self.contract("branch-group", missing)])
        )
        self.assertEqual(result["group_results"][0]["gate"], "incomplete")

        mismatched = branch_declaration(shared_outcome=False)
        mismatched["branches"][0]["outcome"] = "right-outcome"
        result = self.assess(
            "branch-distinct",
            self.context("branch-distinct", [self.contract("branch-group", mismatched)]),
        )
        self.assertEqual(self.group_rules(result, "branch-group")["group/branch/outcomes"]["status"],
                         "violated")

        removed_spec = branch_spec()
        for edge in removed_spec["edges"]:
            if edge.get("outcome") == "shared-outcome":
                edge["outcome"] = "renamed-outcome"
        path = self.directory / "branch-outcome-removed.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(removed_spec), path)
        self.paths["branch-outcome-removed"] = path
        self.specs["branch-outcome-removed"] = removed_spec
        data = self.context(
            "branch-outcome-removed", [self.contract("branch-group", branch_declaration())]
        )
        self.assert_cli_error(
            "branch-outcome-removed", data, "context/reference-invalid", "removed-outcome.json"
        )

    def test_branch_outcome_identity_is_decision_scoped_but_not_selected_edge_scoped(self) -> None:
        source = branch_spec(shared_outcome=False)
        source["groups"][0]["nodes"] = ["left-action"]
        path = self.directory / "branch-excluded-outcome.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(source), path)
        self.paths["branch-excluded-outcome"] = path
        self.specs["branch-excluded-outcome"] = source
        declaration = {
            "decision": "decision",
            "branches": [{"entry_edge": "left-result", "outcome": "right-outcome",
                          "member": "left-action", "path": []}],
        }
        data = self.context(
            "branch-excluded-outcome", [self.contract("branch-group", declaration)]
        )
        scope = data["patterns"]["scopes"][0]
        scope["edges"].remove("right-result")
        scope["excluded_edges"] = [
            {"edge_id": "right-result", "reason": "different declared result"}
        ]
        result = self.assess("branch-excluded-outcome", data)
        rule = self.group_rules(result, "branch-group")["group/branch/outcomes"]
        self.assertEqual(rule["status"], "violated")
        self.assertEqual(rule["code"], "semantic/group-violation")

        other = copy.deepcopy(source)
        other["nodes"].extend([
            node("other-decision", 5, "decision"), node("other-target", 6),
        ])
        other["edges"].append(
            {"id": "other-result", "from": "other-decision", "to": "other-target",
             "outcome": "other-only"}
        )
        other_path = self.directory / "branch-other-decision-outcome.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(other), other_path)
        self.paths["branch-other-decision-outcome"] = other_path
        self.specs["branch-other-decision-outcome"] = other
        other_declaration = copy.deepcopy(declaration)
        other_declaration["branches"][0]["outcome"] = "other-only"
        invalid = self.context(
            "branch-other-decision-outcome",
            [self.contract("branch-group", other_declaration)],
        )
        self.assert_cli_error(
            "branch-other-decision-outcome", invalid, "context/reference-invalid",
            "outcome-belongs-to-other-decision.json",
        )

    def test_merge_required_optional_empty_and_repeated_source_are_distinct(self) -> None:
        optional = merge_declaration()
        optional["inputs"][1].pop("path")
        optional["inputs"][1]["required"] = False
        result = self.assess(
            "merge", self.context("merge", [self.contract("merge-group", optional)])
        )
        self.assertEqual(result["group_results"][0]["gate"], "passed")

        missing = merge_declaration()
        missing["inputs"][0].pop("path")
        result = self.assess(
            "merge", self.context("merge", [self.contract("merge-group", missing)])
        )
        self.assertEqual(result["group_results"][0]["gate"], "incomplete")

        empty = merge_declaration()
        empty["inputs"][0]["path"] = []
        result = self.assess(
            "merge", self.context("merge", [self.contract("merge-group", empty)])
        )
        self.assertEqual(result["group_results"][0]["gate"], "failed")

        repeated_spec = merge_spec()
        repeated_spec["groups"][0]["nodes"] = ["left-source", "merge-target"]
        repeated_path = self.directory / "merge-repeated-source.drawio"
        self.loaded.document.write_tree(
            self.loaded.build.build_tree(repeated_spec), repeated_path
        )
        self.paths["merge-repeated"] = repeated_path
        self.specs["merge-repeated"] = repeated_spec
        repeated = merge_declaration()
        repeated["inputs"][1].update(source="left-source", path=["left-merge"])
        result = self.assess(
            "merge-repeated",
            self.context("merge-repeated", [self.contract("merge-group", repeated)]),
        )
        self.assertEqual(self.group_rules(result, "merge-group")["group/merge/target"]["status"],
                         "violated")

    def test_merge_same_rank_and_indegree_do_not_supply_declarations(self) -> None:
        result = self.assess(
            "merge", self.context("merge", [self.contract("merge-group", {})])
        )
        self.assertEqual(result["group_results"][0]["gate"], "incomplete")
        self.assertTrue(all(rule["status"] == "not_assessed"
                            for rule in result["group_results"][0]["rules"]))

    def test_exception_and_support_are_reference_only_with_partial_topology_coverage(self) -> None:
        groups = [self.contract("support-group", {}), self.contract("exception-group", {})]
        result = self.assess("reference", self.context("reference", groups))
        self.assertEqual([item["group_id"] for item in result["group_results"]],
                         ["exception-group", "support-group"])
        self.assertEqual(result["group_coverage"]["extent"], "all_groups")
        for item in result["group_results"]:
            self.assertEqual(item["gate"], "not_applicable")
            self.assertEqual(item["coverage"]["member_count"], 0)
            self.assertEqual(item["coverage"]["declared_member_count"], 0)
            self.assertEqual(item["coverage"]["reference_member_count"], 1)
            self.assertEqual(item["coverage"]["extent"], "partial")
            self.assertFalse(item["coverage"]["topology_assessed"])
            self.assertEqual(item["coverage"]["assessment"], "reference_only")

        partial = self.assess(
            "reference",
            self.context("reference", [self.contract("support-group", {})]),
        )
        self.assertEqual(partial["group_coverage"]["extent"], "partial")
        self.assertEqual(partial["group_coverage"]["unrequested_groups"], ["exception-group"])

    def test_typed_group_references_allow_cross_kind_ids_but_reject_wrong_kind(self) -> None:
        source = branch_spec()
        source["groups"][0]["id"] = "left-action"
        for edge in source["edges"]:
            if edge["id"] == "left-result":
                edge["id"] = "left-action"
            if "outcome" in edge:
                edge["outcome"] = "left-action"
        path = self.directory / "typed-cross-kind.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(source), path)
        self.paths["typed"] = path
        self.specs["typed"] = source
        declaration = branch_declaration()
        declaration["branches"][0]["entry_edge"] = "left-action"
        for branch in declaration["branches"]:
            branch["outcome"] = "left-action"
        good = self.context("typed", [self.contract("left-action", declaration)])
        self.assertEqual(self.assess("typed", good)["group_results"][0]["gate"], "passed")

        wrong = self.context("typed", [self.contract("right-action", declaration)])
        self.assert_cli_error("typed", wrong, "context/reference-invalid", "wrong-kind-group.json")

        wrong_node = copy.deepcopy(declaration)
        wrong_node["decision"] = "right-result"
        self.assert_cli_error(
            "typed", self.context("typed", [self.contract("left-action", wrong_node)]),
            "context/reference-invalid", "edge-used-as-node.json",
        )

        wrong_edge = copy.deepcopy(declaration)
        wrong_edge["branches"][0]["entry_edge"] = "decision"
        self.assert_cli_error(
            "typed", self.context("typed", [self.contract("left-action", wrong_edge)]),
            "context/reference-invalid", "node-used-as-edge.json",
        )

    def test_group_component_schema_versions_duplicates_and_kind_fields_fail_closed(self) -> None:
        valid = self.context(
            "parallel", [self.contract("parallel-group", parallel_declaration())]
        )
        cases = []
        for value in (True, 2):
            data = copy.deepcopy(valid)
            data["group_contracts"]["version"] = value
            cases.append((data, "schema/type" if value is True else "context/version-unsupported",
                          f"version-{value}.json"))
        for field in ("version", "groups"):
            data = copy.deepcopy(valid)
            data["group_contracts"].pop(field)
            cases.append((data, "schema/required", f"missing-{field}.json"))
        unknown = copy.deepcopy(valid)
        unknown["group_contracts"]["unknown"] = 1
        cases.append((unknown, "schema/unknown-field", "unknown-component-field.json"))
        duplicate = copy.deepcopy(valid)
        duplicate["group_contracts"]["groups"].append(
            copy.deepcopy(duplicate["group_contracts"]["groups"][0])
        )
        cases.append((duplicate, "schema/duplicate", "duplicate-group-id.json"))
        duplicate_branch = copy.deepcopy(valid)
        duplicate_branch["group_contracts"]["groups"][0]["declarations"]["branches"][1][
            "branch_id"
        ] = "left-branch"
        cases.append((duplicate_branch, "schema/duplicate", "duplicate-branch-id.json"))
        unknown_entry = copy.deepcopy(valid)
        unknown_entry["group_contracts"]["groups"][0]["unknown"] = 1
        cases.append((unknown_entry, "schema/unknown-field", "unknown-group-entry-field.json"))
        unknown_branch = copy.deepcopy(valid)
        unknown_branch["group_contracts"]["groups"][0]["declarations"]["branches"][0][
            "unknown"
        ] = 1
        cases.append((unknown_branch, "schema/unknown-field", "unknown-parallel-branch-field.json"))
        nonboolean = copy.deepcopy(valid)
        nonboolean["group_contracts"]["groups"][0]["declarations"]["branches"][0][
            "join_required"
        ] = 1
        cases.append((nonboolean, "schema/type", "nonboolean-required.json"))
        wrong_kind = self.context("reference", [self.contract("support-group", {"fork": "start"})])
        cases.append((wrong_kind, "schema/unknown-field", "wrong-kind-field.json"))
        duplicate_input = self.context(
            "merge", [self.contract("merge-group", merge_declaration())]
        )
        duplicate_input["group_contracts"]["groups"][0]["declarations"]["inputs"][1][
            "input_id"
        ] = "left-input"
        cases.append((duplicate_input, "schema/duplicate", "duplicate-input-id.json"))
        for data, code, case in cases:
            with self.subTest(case=case):
                fixture = ("reference" if case == "wrong-kind-field.json" else
                           "merge" if case == "duplicate-input-id.json" else "parallel")
                self.assert_cli_error(fixture, data, code, case)

    def test_group_member_edge_group_and_outcome_deletions_leave_no_silent_rebinding(self) -> None:
        old = self.context(
            "branch", [self.contract("branch-group", branch_declaration())]
        )
        variants = {}

        group_deleted = branch_spec()
        group_deleted["groups"] = []
        variants["group"] = group_deleted

        member_deleted = branch_spec()
        member_deleted["nodes"] = [item for item in member_deleted["nodes"]
                                   if item["id"] != "right-action"]
        member_deleted["edges"] = [item for item in member_deleted["edges"]
                                   if "right-action" not in (item["from"], item["to"])]
        member_deleted["groups"][0]["nodes"].remove("right-action")
        variants["member"] = member_deleted

        edge_deleted = branch_spec()
        edge_deleted["edges"] = [item for item in edge_deleted["edges"]
                                 if item["id"] != "right-result"]
        variants["edge"] = edge_deleted

        outcome_deleted = branch_spec()
        for edge in outcome_deleted["edges"]:
            if edge.get("outcome") == "shared-outcome":
                edge["outcome"] = "renamed-outcome"
        variants["outcome"] = outcome_deleted

        for kind, source in variants.items():
            with self.subTest(kind=kind):
                path = self.directory / f"deleted-{kind}.drawio"
                self.loaded.document.write_tree(self.loaded.build.build_tree(source), path)
                name = f"deleted-{kind}"
                self.paths[name], self.specs[name] = path, source

                stale = copy.deepcopy(old)
                stale_context = self.write_context(stale, f"stale-{kind}.json")
                stale_result = run_tool(
                    "validate", "--input", str(path), "--context", str(stale_context)
                )
                self.assertEqual(stale_result.returncode, 2)
                self.assertEqual(json.loads(stale_result.stdout)["diagnostics"][0]["code"],
                                 "context/artifact-mismatch")

                rebound = self.context(
                    name, [self.contract("branch-group", branch_declaration())]
                )
                self.assert_cli_error(
                    name, rebound, "context/reference-invalid", f"rebound-{kind}.json"
                )

    def test_group_count_limit_accepts_128_real_groups_and_rejects_129(self) -> None:
        count = 128
        nodes = [node("start", 1, "start")]
        nodes.extend(node(f"member-{index:03}", index + 2) for index in range(count))
        nodes.append(node("end", count + 2, "end"))
        path_nodes = [item["id"] for item in nodes]
        edges = [{"id": f"edge-{index:03}", "from": path_nodes[index],
                  "to": path_nodes[index + 1]}
                 for index in range(len(path_nodes) - 1)]
        groups = [{"id": f"group-{index:03}", "lane": "lane-a", "kind": "support",
                   "nodes": [f"member-{index:03}"]}
                  for index in range(count)]
        source = base_spec(nodes, edges, path_nodes, groups)
        source["layout"] = {"profile": "long-form"}
        path = self.directory / "groups-128.drawio"
        self.loaded.document.write_tree(self.loaded.build.build_tree(source), path)
        self.paths["groups-128"], self.specs["groups-128"] = path, source
        contracts = [self.contract(group["id"], {}) for group in groups]
        exact = self.context("groups-128", contracts)
        result = self.assess("groups-128", exact)
        self.assertEqual(result["group_coverage"]["group_count"], 128)
        self.assertEqual(result["group_coverage"]["extent"], "all_groups")
        self.assertEqual(result["group_gate"], "not_applicable")

        over = copy.deepcopy(exact)
        over["group_contracts"]["groups"].append(
            self.contract("group-128", {})
        )
        over_path = self.write_context(over, "groups-129.json")
        with self.assertRaises(self.loaded.contracts.DiagramError) as raised:
            self.semantic.load_context(over_path)
        self.assertEqual(raised.exception.code, "context/resource-limit")

    def test_group_ids_share_the_existing_twenty_thousand_reference_budget(self) -> None:
        validator = self.semantic._InputValidator()
        for _ in range(self.semantic.MAX_ID_REFERENCES - 2):
            validator.sid("existing-reference", "existing")
        self.loaded.tool.semantic_context.group_rules.validate_component(
            {"version": 1, "groups": [
                {"group_id": "support-group", "scope_id": "primary", "declarations": {}}
            ]},
            validator,
        )
        self.assertEqual(validator.references, self.semantic.MAX_ID_REFERENCES)
        with self.assertRaises(self.loaded.contracts.DiagramError) as raised:
            validator.sid("one-more-reference", "over")
        self.assertEqual(raised.exception.code, "context/resource-limit")

    def test_general_validation_failure_makes_requested_group_rules_not_assessed(self) -> None:
        tree = self.loaded.document.read_tree(self.paths["parallel"])
        pool = self.loaded.document.find_pool(tree)
        pool.set(self.loaded.contracts.DATA_MAIN_PATH,
                 json.dumps(["fork", "left", "shared", "join", "end"]))
        self.loaded.metadata.refresh_managed_metadata(tree)
        path = self.directory / "group-prerequisite-invalid.drawio"
        self.loaded.document.write_tree(tree, path)
        self.paths["prerequisite"], self.specs["prerequisite"] = path, parallel_spec()
        data = self.context(
            "prerequisite", [self.contract("parallel-group", parallel_declaration())]
        )
        context = self.write_context(data, "group-prerequisite-invalid.json")
        result = run_tool("inspect", "--input", str(path), "--context", str(context))
        self.assertEqual(result.returncode, 0)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["semantic_context"]["group_gate"], "incomplete")
        self.assertTrue(all(rule["status"] == "not_assessed"
                            for rule in envelope["semantic_context"]["group_results"][0]["rules"]))

    def test_group_and_scope_results_are_sorted_and_top_gate_keeps_failure_priority(self) -> None:
        groups = [self.contract("support-group", {}), self.contract("exception-group", {})]
        first = self.assess("reference", self.context("reference", groups))
        groups.reverse()
        second = self.assess("reference", self.context("reference", groups))
        for value in (first, second):
            self.assertEqual([item["group_id"] for item in value["group_results"]],
                             ["exception-group", "support-group"])
        left, right = copy.deepcopy(first), copy.deepcopy(second)
        left.pop("context_sha256")
        right.pop("context_sha256")
        self.assertEqual(left, right)

        failed = parallel_declaration()
        failed["branches"][0]["branch_edges"] = []
        data = self.context("parallel", [self.contract("parallel-group", failed)])
        result = self.assess("parallel", data)
        self.assertEqual(result["scope_results"][0]["gate"], "not_applicable")
        self.assertEqual(result["group_results"][0]["gate"], "failed")
        self.assertEqual(result["gate"], "failed")

    def test_validate_and_inspect_keep_failed_incomplete_and_input_exit_boundaries(self) -> None:
        cases = []
        failed = parallel_declaration()
        failed["branches"][0]["branch_edges"] = []
        cases.append(("failed", self.context(
            "parallel", [self.contract("parallel-group", failed)]
        )))
        cases.append(("incomplete", self.context(
            "parallel", [self.contract("parallel-group", {})]
        )))
        for gate, data in cases:
            context = self.write_context(data, f"cli-{gate}.json")
            validate = run_tool("validate", "--input", str(self.paths["parallel"]),
                                "--context", str(context))
            inspect = run_tool("inspect", "--input", str(self.paths["parallel"]),
                               "--context", str(context))
            self.assertEqual(validate.returncode, 1)
            self.assertEqual(inspect.returncode, 0)
            self.assertEqual(json.loads(validate.stdout)["semantic_context"]["gate"], gate)
            self.assertEqual(json.loads(inspect.stdout)["semantic_context"]["gate"], gate)

        invalid = self.context(
            "parallel", [self.contract("missing-group", parallel_declaration())]
        )
        self.assert_cli_error(
            "parallel", invalid, "context/reference-invalid", "cli-reference-error.json"
        )

    def test_group_assessment_preserves_inputs_and_does_not_call_mutating_modules(self) -> None:
        data = self.context(
            "parallel", [self.contract("parallel-group", parallel_declaration())]
        )
        context_path = self.write_context(data, "read-only-group-context.json")
        diagram = self.paths["parallel"]
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in (diagram, context_path)]
        loaded = self.semantic.load_context(context_path)
        tree = self.loaded.document.read_tree(diagram)
        validation = self.loaded.validation.validate_tree(tree)
        with (
            mock.patch.object(self.loaded.document, "write_tree", side_effect=AssertionError("writer")),
            mock.patch.object(self.loaded.build, "build_tree", side_effect=AssertionError("build")),
            mock.patch.object(self.loaded.roundtrip, "patch_tree", side_effect=AssertionError("patch")),
            mock.patch.object(self.loaded.routing, "plan_route_batch", side_effect=AssertionError("route")),
        ):
            result = self.semantic.assess_context(
                loaded, tree, hashlib.sha256(diagram.read_bytes()).hexdigest(), validation
            )
        self.assertEqual(result["group_results"][0]["gate"], "passed")
        self.assertEqual(before, [(path.read_bytes(), path.stat().st_mtime_ns)
                                  for path in (diagram, context_path)])

    def test_empty_provenance_and_group_components_keep_independent_gates(self) -> None:
        data = self.context("reference", [])
        data["provenance"] = {
            "version": 1, "sources": [], "facts": [], "bindings": [], "history": [],
        }
        result = self.assess("reference", data)
        self.assertEqual(result["components"]["provenance"]["status"], "provided_empty")
        self.assertEqual(result["group_gate"], "not_applicable")
        self.assertEqual(result["source_gate"], "incomplete")


if __name__ == "__main__":
    unittest.main()
