import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"
sys.path.insert(0, str(ROOT / "tools"))
from swimlane_loader import load_skill_modules


ZERO = "0" * 64
ONE = "1" * 64


def neutral_spec() -> dict:
    return {
        "schema_version": "3",
        "title": "Neutral provenance case",
        "behavior_pattern": "custom",
        "layout": {"profile": "review"},
        "lanes": [
            {"id": "lane-a", "label": "Lane A", "width": 240},
            {"id": "lane-b", "label": "Lane B", "width": 240},
        ],
        "nodes": [
            {"id": "start", "lane": "lane-a", "rank": 1, "type": "start", "label": ""},
            {"id": "decision", "lane": "lane-a", "rank": 2, "type": "decision", "label": "Choose"},
            {"id": "action", "lane": "lane-b", "rank": 3, "type": "process", "label": "Act"},
            {"id": "end", "lane": "lane-a", "rank": 4, "type": "end", "label": ""},
        ],
        "edges": [
            {"id": "enter", "from": "start", "to": "decision"},
            {"id": "choose", "from": "decision", "to": "action", "outcome": "go"},
            {"id": "finish", "from": "action", "to": "end"},
        ],
        "main_path": ["start", "decision", "action", "end"],
    }


class E3ProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loaded = load_skill_modules(TOOL, module_name="e3_provenance_tests")
        cls.semantic = cls.loaded.tool.semantic_context
        cls.provenance = cls.semantic.provenance
        cls.validator_type = cls.semantic._InputValidator
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.diagram = cls.directory / "neutral.drawio"
        tree = cls.loaded.build.build_tree(neutral_spec())
        cls.loaded.document.write_tree(tree, cls.diagram)
        cls.tree = cls.loaded.document.read_tree(cls.diagram)
        cls.model = cls.semantic.context_native.original_model(cls.tree)
        pool = cls.loaded.document.find_pool(cls.tree)
        cls.artifact = {
            "sha256": hashlib.sha256(cls.diagram.read_bytes()).hexdigest(),
            "schema_version": pool.get(cls.loaded.contracts.DATA_SCHEMA_VERSION),
            "model_hash_version": pool.get(cls.loaded.contracts.DATA_MODEL_HASH_VERSION),
            "model_hash": pool.get(cls.loaded.contracts.DATA_MODEL_HASH),
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def source(self, source_id: str = "source-a", *, version: str = "v1", digest: str = ZERO,
               reference: str | None = None) -> dict:
        value = {"id": source_id, "kind": "record", "version": version, "digest": digest}
        if reference is not None:
            value["reference"] = reference
        return value

    def fact(self, fact_id: str = "fact-a", *, status: str = "confirmed",
             source_refs: list[str] | None = None) -> dict:
        refs = ["source-a"] if source_refs is None else source_refs
        return {
            "id": fact_id,
            "status": status,
            "source_refs": refs,
            "confirmation": ({"asserted_by": "reviewer", "basis": "declared evidence"}
                             if status == "confirmed" else None),
        }

    def binding(self, binding_id: str, target: dict, fields: dict, *, fact_id: str = "fact-a",
                validity: str = "current", snapshots: list[dict] | None = None) -> dict:
        return {
            "id": binding_id,
            "fact_id": fact_id,
            "target": target,
            "fields": fields,
            "source_snapshots": ([{"source_id": "source-a", "version": "v1", "digest": ZERO}]
                                 if snapshots is None else snapshots),
            "validity": validity,
        }

    def complete_component(self) -> dict:
        projection = self.provenance.provenance_projection(self.model)
        bindings = []
        for index, (target_key, values) in enumerate(sorted(projection.items())):
            if target_key[0] == "outcome":
                target = {"kind": "outcome", "decision_id": target_key[1], "outcome_id": target_key[2]}
            else:
                target = {"kind": target_key[0], "id": target_key[1]}
            field = "type" if target_key[0] == "node" else ("from" if target_key[0] == "edge" else "label")
            bindings.append(self.binding(f"binding-{index}", target, {field: values[field]}))
        return {"version": 1, "sources": [self.source()], "facts": [self.fact()],
                "bindings": bindings, "history": []}

    def validate(self, component: dict) -> None:
        self.provenance.validate_provenance(component, self.validator_type())

    def assert_error(self, code: str, callback) -> None:
        with self.assertRaises(self.loaded.contracts.DiagramError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code, caught.exception.diagnostic())

    def bound_context(self, component: dict) -> dict:
        return {"context_version": 1, "artifact": copy.deepcopy(self.artifact),
                "provenance": copy.deepcopy(component)}

    def changes(self, *, provenance: dict | None = None, mode: str = "semantic",
                actions: list[dict] | None = None) -> dict:
        value = {
            "context_changes_version": 1,
            "expected_input_sha256": self.artifact["sha256"],
            "expected_context_sha256": ONE,
            "mode": mode,
        }
        if provenance is not None:
            value["provenance"] = provenance
        if actions is not None:
            value["confirmation_actions"] = actions
        return value

    def transition(self, component: dict, changes: dict, after_model: dict | None = None) -> dict:
        return self.provenance.transition_context(
            self.bound_context(component), changes, self.model,
            self.model if after_model is None else after_model,
            context_sha256=ONE, artifact={**self.artifact, "sha256": "2" * 64},
        )

    def test_confirmed_current_coverage_distinguishes_objects_from_fields(self) -> None:
        component = self.complete_component()
        self.validate(component)
        result = self.provenance.assess_provenance(component, self.model)
        projection = self.provenance.provenance_projection(self.model)
        self.assertEqual(result["source_gate"], "passed")
        self.assertEqual(result["source_coverage"]["confirmed_current_objects"], len(projection))
        self.assertEqual(result["source_coverage"]["total_objects"], len(projection))
        self.assertEqual(result["source_coverage"]["extent"], "whole_diagram")
        self.assertEqual(result["source_coverage"]["confirmed_current_fields"], len(projection))
        self.assertGreater(result["source_coverage"]["total_fields"], len(projection))
        self.assertEqual(result["source_coverage"]["field_extent"], "partial")
        self.assertTrue(result["source_coverage"]["uncovered_fields"])
        self.assertEqual(result["source_results"][0]["verification"], "declaration_only")

    def test_empty_original_node_and_edge_labels_are_valid_exact_snapshots(self) -> None:
        component = {
            "version": 1, "sources": [self.source()], "facts": [self.fact()],
            "bindings": [
                self.binding("start-label", {"kind": "node", "id": "start"}, {"label": ""}),
                self.binding("edge-label", {"kind": "edge", "id": "enter"}, {"label": ""}),
            ], "history": [],
        }
        self.validate(component)
        result = self.provenance.assess_provenance(component, self.model)
        self.assertEqual(
            [item["validity"] for item in result["source_results"][0]["binding_results"]],
            ["current", "current"],
        )

    def test_empty_component_and_unbound_fact_are_incomplete(self) -> None:
        empty = {"version": 1, "sources": [], "facts": [], "bindings": [], "history": []}
        result = self.provenance.assess_provenance(empty, self.model)
        self.assertEqual(result["source_gate"], "incomplete")
        self.assertEqual(result["source_coverage"]["confirmed_current_objects"], 0)
        component = {"version": 1, "sources": [self.source()], "facts": [self.fact()],
                     "bindings": [], "history": []}
        result = self.provenance.assess_provenance(component, self.model)
        self.assertFalse(result["source_results"][0]["effective_confirmed"])
        self.assertEqual(result["source_results"][0]["validity"], "missing")

    def test_source_less_assumption_and_unresolved_bindings_remain_missing(self) -> None:
        for status, declared_validity in (("assumption", "current"), ("unresolved", "missing")):
            with self.subTest(status=status, declared_validity=declared_validity):
                component = {
                    "version": 1, "sources": [], "facts": [self.fact(status=status, source_refs=[])],
                    "bindings": [self.binding(
                        "binding-a", {"kind": "node", "id": "action"}, {"label": "Act"},
                        validity=declared_validity, snapshots=[],
                    )], "history": [],
                }
                self.validate(component)
                self.provenance.provenance_template_check(component, self.model)
                result = self.provenance.assess_provenance(component, self.model)
                fact = result["source_results"][0]
                self.assertEqual(fact["declared_status"], status)
                self.assertEqual(fact["validity"], "missing")
                self.assertFalse(fact["effective_confirmed"])

    def test_source_less_missing_binding_still_requires_exact_target_fields(self) -> None:
        component = {
            "version": 1, "sources": [],
            "facts": [self.fact(status="assumption", source_refs=[])],
            "bindings": [self.binding(
                "binding-a", {"kind": "node", "id": "action"}, {"label": "Wrong label"},
                validity="missing", snapshots=[],
            )], "history": [],
        }
        self.validate(component)
        self.assert_error(
            "provenance/snapshot-mismatch",
            lambda: self.provenance.provenance_template_check(component, self.model),
        )

    def test_template_missing_is_only_for_source_less_support_and_history_is_not_importable(self) -> None:
        sourced_missing = self.complete_component()
        sourced_missing["bindings"][0]["validity"] = "missing"
        self.assert_error(
            "provenance/snapshot-mismatch",
            lambda: self.provenance.provenance_template_check(sourced_missing, self.model),
        )
        history = self.complete_component()
        history["history"] = [{
            "artifact_sha256": ZERO, "context_sha256": ONE,
            "records": {"sources": [], "facts": [], "bindings": []},
        }]
        self.validate(history)
        self.assert_error(
            "provenance/change-not-declared",
            lambda: self.provenance.provenance_template_check(history, self.model),
        )

    def test_raw_current_target_and_source_snapshot_drift_are_input_errors(self) -> None:
        for mutate in ("label", "owner", "source-version", "source-digest", "deleted"):
            with self.subTest(mutate=mutate):
                component = self.complete_component()
                if mutate in {"label", "owner", "deleted"}:
                    target = next(item for item in component["bindings"] if item["target"] == {"kind": "node", "id": "action"})
                    if mutate == "label":
                        target["fields"] = {"label": "Different"}
                    elif mutate == "owner":
                        target["fields"] = {"owner": "lane-a"}
                    else:
                        target["target"]["id"] = "absent"
                elif mutate == "source-version":
                    component["sources"][0]["version"] = "v2"
                else:
                    component["sources"][0]["digest"] = ONE
                self.assert_error(
                    "provenance/snapshot-mismatch",
                    lambda c=component: self.provenance.assess_provenance(c, self.model),
                )

    def test_stale_deleted_and_reverted_bindings_do_not_auto_promote(self) -> None:
        component = self.complete_component()
        action = next(item for item in component["bindings"] if item["target"] == {"kind": "node", "id": "action"})
        action["validity"] = "stale"
        result = self.provenance.assess_provenance(component, self.model)
        binding = next(item for item in result["source_results"][0]["binding_results"] if item["binding_id"] == action["id"])
        self.assertEqual(binding["validity"], "stale")
        self.assertEqual(result["source_gate"], "incomplete")
        action["validity"] = "deleted"
        result = self.provenance.assess_provenance(component, self.model)
        binding = next(item for item in result["source_results"][0]["binding_results"] if item["binding_id"] == action["id"])
        self.assertEqual(binding["validity"], "deleted")

    def test_typed_target_domains_and_decision_scoped_outcome_are_exact(self) -> None:
        component = self.complete_component()
        component["sources"][0]["id"] = "shared"
        component["facts"][0]["id"] = "shared"
        component["facts"][0]["source_refs"] = ["shared"]
        for binding in component["bindings"]:
            binding["fact_id"] = "shared"
            binding["source_snapshots"][0]["source_id"] = "shared"
        component["bindings"][0]["id"] = "shared"
        self.validate(component)

        wrong = copy.deepcopy(component)
        outcome = next(item for item in wrong["bindings"] if item["target"]["kind"] == "outcome")
        outcome["target"]["decision_id"] = "action"
        self.assert_error(
            "provenance/reference-invalid",
            lambda: self.provenance.provenance_template_check(wrong, self.model),
        )

    def test_schema_versions_duplicates_and_exact_collection_limit(self) -> None:
        component = self.complete_component()
        for value, code in ((True, "schema/type"), (2, "context/version-unsupported")):
            changed = copy.deepcopy(component)
            changed["version"] = value
            self.assert_error(code, lambda c=changed: self.validate(c))
        unknown = copy.deepcopy(component)
        unknown["sources"][0]["extra"] = "x"
        self.assert_error("schema/unknown-field", lambda: self.validate(unknown))
        duplicate = copy.deepcopy(component)
        duplicate["facts"].append(copy.deepcopy(duplicate["facts"][0]))
        self.assert_error("schema/duplicate", lambda: self.validate(duplicate))

        exact = {"version": 1, "sources": [], "facts": [], "bindings": [], "history": []}
        for index in range(1024):
            exact["sources"].append(self.source(f"s{index}"))
        self.validate(exact)
        over = copy.deepcopy(exact)
        over["sources"].append(self.source("overflow"))
        self.assert_error("context/resource-limit", lambda: self.validate(over))

    def test_source_snapshot_refs_require_exact_typed_fact_source_set(self) -> None:
        component = self.complete_component()
        binding = component["bindings"][0]
        cases = []
        missing = copy.deepcopy(component)
        missing["bindings"][0]["source_snapshots"] = []
        cases.append((missing, "provenance/reference-invalid"))
        wrong = copy.deepcopy(component)
        wrong["bindings"][0]["source_snapshots"][0]["source_id"] = "missing"
        cases.append((wrong, "provenance/reference-invalid"))
        duplicate = copy.deepcopy(component)
        duplicate["bindings"][0]["source_snapshots"].append(copy.deepcopy(binding["source_snapshots"][0]))
        cases.append((duplicate, "schema/duplicate"))
        bad_fact = copy.deepcopy(component)
        bad_fact["facts"][0]["source_refs"] = ["missing"]
        cases.append((bad_fact, "provenance/reference-invalid"))
        for changed, code in cases:
            with self.subTest(code=code):
                self.assert_error(code, lambda c=changed: self.validate(c))

    def test_safe_references_are_inert_and_unsafe_reference_shapes_reject(self) -> None:
        accepted = ["Evidence 2026-09", "https://example.test/evidence/item"]
        with mock.patch("builtins.open", side_effect=AssertionError("source opened")):
            for reference in accepted:
                self.validate({"version": 1, "sources": [self.source(reference=reference)],
                               "facts": [], "bindings": [], "history": []})
        rejected = [
            "/private/tmp/evidence", "../evidence", "file:///tmp/evidence", "ftp://example.test/a",
            "https://user:secret@example.test/a", "https://example.test/a?token=secret",
            "https://example.test/a#fragment", "C:\\evidence\\item", "\\\\server\\share",
        ]
        for reference in rejected:
            with self.subTest(reference=reference):
                component = {"version": 1, "sources": [self.source(reference=reference)],
                             "facts": [], "bindings": [], "history": []}
                self.assert_error("schema/type", lambda c=component: self.validate(c))

    def test_transition_invalidates_only_changed_field_and_preserves_records(self) -> None:
        component = self.complete_component()
        action = next(item for item in component["bindings"] if item["target"] == {"kind": "node", "id": "action"})
        action["fields"] = {"label": "Act", "owner": "lane-b"}
        after_model = copy.deepcopy(self.model)
        next(item for item in after_model["nodes"] if item["id"] == "action")["label"] = "Changed"
        result = self.transition(component, self.changes(provenance=copy.deepcopy(component)), after_model)
        output = result["provenance"]
        changed = next(item for item in output["bindings"] if item["id"] == action["id"])
        self.assertEqual(changed["validity"], "stale")
        self.assertEqual(len(output["history"]), 1)
        self.assertIn(action, output["history"][0]["records"]["bindings"])
        unaffected = [item for item in component["bindings"] if item["id"] != action["id"]]
        self.assertEqual([item for item in output["bindings"] if item["id"] != action["id"]], unaffected)

    def test_same_id_field_change_matrix_invalidates_exact_related_bindings(self) -> None:
        cases = [
            (("node", "action"), "label", "Changed"),
            (("node", "action"), "owner", "lane-a"),
            (("edge", "choose"), "from", "start"),
            (("edge", "choose"), "to", "end"),
            (("edge", "choose"), "outcome", "other"),
            (("outcome", "decision", "go"), "decision_label", "Changed decision"),
        ]
        for key, field, value in cases:
            with self.subTest(target=key, field=field):
                component = self.complete_component()
                target = ({"kind": "outcome", "decision_id": key[1], "outcome_id": key[2]}
                          if key[0] == "outcome" else {"kind": key[0], "id": key[1]})
                bound = next(item for item in component["bindings"] if item["target"] == target)
                before_projection = self.provenance.provenance_projection(self.model)
                bound["fields"] = {field: before_projection[key][field]}
                after_model = copy.deepcopy(self.model)
                if key[0] == "node":
                    next(item for item in after_model["nodes"] if item["id"] == key[1])[field if field != "owner" else "lane"] = value
                elif key[0] == "edge":
                    next(item for item in after_model["edges"] if item["id"] == key[1])[field] = value
                else:
                    next(item for item in after_model["nodes"] if item["id"] == key[1])["label"] = value
                output = self.transition(component, self.changes(provenance=copy.deepcopy(component)), after_model)["provenance"]
                self.assertEqual(next(item for item in output["bindings"] if item["id"] == bound["id"])["validity"],
                                 "deleted" if key[0] == "outcome" and field != "decision_label" else "stale")
                self.assertTrue(any(item["validity"] == "current" for item in output["bindings"]
                                    if item["id"] != bound["id"]))

    def test_transition_deletion_and_source_change_invalidate_without_record_loss(self) -> None:
        component = self.complete_component()
        after_model = copy.deepcopy(self.model)
        after_model["edges"] = [edge for edge in after_model["edges"] if edge["id"] != "finish"]
        output = self.transition(component, self.changes(provenance=copy.deepcopy(component)), after_model)["provenance"]
        deleted = next(item for item in output["bindings"] if item["target"] == {"kind": "edge", "id": "finish"})
        self.assertEqual(deleted["validity"], "deleted")

        changed_component = copy.deepcopy(component)
        changed_component["sources"][0]["version"] = "v2"
        output = self.transition(component, self.changes(provenance=changed_component))["provenance"]
        self.assertTrue(all(item["validity"] == "stale" for item in output["bindings"]))
        self.assertEqual({item["id"] for item in output["bindings"]}, {item["id"] for item in component["bindings"]})

    def test_node_and_decision_scoped_outcome_deletion_are_retained_as_deleted(self) -> None:
        for kind in ("node", "outcome"):
            with self.subTest(kind=kind):
                component = self.complete_component()
                after_model = copy.deepcopy(self.model)
                if kind == "node":
                    after_model["nodes"] = [item for item in after_model["nodes"] if item["id"] != "action"]
                    target = {"kind": "node", "id": "action"}
                else:
                    next(item for item in after_model["edges"] if item["id"] == "choose").pop("outcome")
                    target = {"kind": "outcome", "decision_id": "decision", "outcome_id": "go"}
                output = self.transition(component, self.changes(provenance=copy.deepcopy(component)), after_model)["provenance"]
                self.assertEqual(next(item for item in output["bindings"] if item["target"] == target)["validity"], "deleted")

    def test_confirmation_upgrade_refresh_and_record_removal_are_guarded(self) -> None:
        assumption = self.complete_component()
        assumption["facts"][0]["status"] = "assumption"
        assumption["facts"][0]["confirmation"] = None
        candidate = copy.deepcopy(assumption)
        candidate["facts"][0] = self.fact()
        self.assert_error(
            "provenance/confirmation-required",
            lambda: self.transition(assumption, self.changes(provenance=candidate)),
        )
        action = {"fact_id": "fact-a", "asserted_by": "reviewer", "basis": "declared evidence"}
        refreshed = self.transition(assumption, self.changes(provenance=candidate, actions=[action]))
        self.assertEqual(refreshed["provenance"]["facts"][0]["status"], "confirmed")

        removed = copy.deepcopy(self.complete_component())
        removed["bindings"].pop()
        self.assert_error(
            "provenance/record-loss",
            lambda: self.transition(self.complete_component(), self.changes(provenance=removed)),
        )

    def test_explicit_downgrade_clears_confirmation_without_action_and_keeps_history(self) -> None:
        before = self.complete_component()
        for status in ("assumption", "unresolved"):
            with self.subTest(status=status):
                candidate = copy.deepcopy(before)
                candidate["facts"][0]["status"] = status
                candidate["facts"][0]["confirmation"] = None
                output = self.transition(before, self.changes(provenance=candidate))["provenance"]
                self.assertEqual(output["facts"][0]["status"], status)
                self.assertIsNone(output["facts"][0]["confirmation"])
                self.assertIn(before["facts"][0], output["history"][-1]["records"]["facts"])

    def test_downgrade_may_refresh_unconfirmed_snapshot_but_confirmed_refresh_needs_action(self) -> None:
        before = self.complete_component()
        original = next(item for item in before["bindings"] if item["target"] == {"kind": "node", "id": "action"})
        original["fields"] = {"label": "Act"}
        after_model = copy.deepcopy(self.model)
        next(item for item in after_model["nodes"] if item["id"] == "action")["label"] = "Changed"

        downgrade = copy.deepcopy(before)
        downgrade["facts"][0]["status"] = "unresolved"
        downgrade["facts"][0]["confirmation"] = None
        refreshed = next(item for item in downgrade["bindings"] if item["id"] == original["id"])
        refreshed["fields"] = {"label": "Changed"}
        output = self.transition(before, self.changes(provenance=downgrade), after_model)["provenance"]
        self.assertEqual(output["facts"][0]["status"], "unresolved")
        self.assertEqual(next(item for item in output["bindings"] if item["id"] == original["id"])["validity"], "current")
        self.assertEqual(self.provenance.audit_context_preservation(
            self.bound_context(before), {**self.bound_context(output), "artifact": {**self.artifact, "sha256": "2" * 64}},
            self.changes(provenance=downgrade),
        ), [])

        confirmed_refresh = copy.deepcopy(before)
        next(item for item in confirmed_refresh["bindings"] if item["id"] == original["id"])["fields"] = {"label": "Changed"}
        self.assert_error(
            "provenance/confirmation-required",
            lambda: self.transition(before, self.changes(provenance=confirmed_refresh), after_model),
        )

    def test_geometry_only_rebind_preserves_provenance_bytes_semantics_and_history(self) -> None:
        component = self.complete_component()
        before = copy.deepcopy(component)
        output = self.transition(component, self.changes(mode="geometry-only"))["provenance"]
        self.assertEqual(output, before)
        bad = copy.deepcopy(self.model)
        bad["nodes"][1]["label"] = "Changed"
        self.assert_error(
            "provenance/geometry-not-pure",
            lambda: self.transition(component, self.changes(mode="geometry-only"), bad),
        )

    def test_source_and_result_order_are_stable_under_input_permutation(self) -> None:
        component = self.complete_component()
        component["facts"].append(self.fact("fact-z", status="unresolved", source_refs=[]))
        component["bindings"].append(self.binding(
            "binding-z", {"kind": "node", "id": "action"}, {"type": "process"},
            fact_id="fact-z", validity="missing", snapshots=[],
        ))
        first = self.provenance.assess_provenance(component, self.model)
        permuted = copy.deepcopy(component)
        permuted["facts"].reverse()
        permuted["bindings"].reverse()
        second = self.provenance.assess_provenance(permuted, self.model)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
