"""Pure source declarations, field snapshots and conservative confirmation history.

No source locator is opened and no actor identity is authenticated here.
"""
from __future__ import annotations

import copy
import re
from urllib.parse import urlsplit

from . import contracts

PROVENANCE_FIELDS = {"node": {"label", "owner", "type"}, "edge": {"label", "from", "to", "outcome"},
                     "outcome": {"label", "decision_label", "owner"}}
PROVENANCE_STATES = ("current", "stale", "missing", "deleted")


def provenance_error(code, message, **evidence):
    raise contracts.DiagramError(message, code=code, subject={"kind": "provenance"}, evidence=evidence)


def provenance_object(value, fields, required=None):
    contracts.require_mapping(value, "provenance object")
    contracts.reject_unknown_fields(value, set(fields), "provenance object")
    absent = sorted(set(fields if required is None else required) - set(value))
    if absent:
        provenance_error("schema/required", "Required provenance fields are missing", fields=absent)
    return value


def provenance_text(value):
    contracts.require_string(value, "provenance string")
    if not value.strip():
        provenance_error("schema/type", "Provenance strings must not be blank")


def provenance_reference(value):
    provenance_text(value)
    if len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        provenance_error("schema/type", "Source reference exceeds its safe text boundary")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,255}", value):
        return
    parsed = urlsplit(value)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or "\\" in value):
        provenance_error("schema/type", "Source reference must be an opaque neutral label or a credential-free HTTP(S) URL without query or fragment")


def provenance_digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        provenance_error("schema/type", "Provenance digest must be lowercase SHA-256")


def provenance_confirmation(value):
    if value is not None:
        provenance_object(value, {"asserted_by", "basis"})
        for text in value.values():
            provenance_text(text)


def provenance_collection(value, validator):
    contracts.require_list(value, "provenance collection")
    if len(value) > 1024:
        provenance_error("context/resource-limit", "Provenance collection exceeds 1024 records")
    ids = []
    for record in value:
        contracts.require_mapping(record, "provenance record")
        if "id" not in record:
            provenance_error("schema/required", "Provenance record requires id")
        ids.append(validator.sid(record["id"], "provenance record id"))
    if len(set(ids)) != len(ids):
        provenance_error("schema/duplicate", "Duplicate provenance record identity")
    return value


def provenance_validate_records(component, validator, *, references=True):
    for item in provenance_collection(component["sources"], validator):
        provenance_object(item, {"id", "kind", "version", "digest", "reference"}, {"id", "kind", "version", "digest"})
        provenance_text(item["kind"])
        provenance_text(item["version"])
        provenance_digest(item["digest"])
        if "reference" in item:
            provenance_reference(item["reference"])
    for item in provenance_collection(component["facts"], validator):
        provenance_object(item, {"id", "status", "source_refs", "confirmation"})
        if item["status"] not in ("confirmed", "assumption", "unresolved"):
            provenance_error("schema/type", "Unsupported fact status")
        validator.ids(item["source_refs"], "fact source_refs")
        provenance_confirmation(item["confirmation"])
        if item["status"] == "confirmed" and (item["confirmation"] is None or not item["source_refs"]):
            provenance_error("provenance/confirmation-required", "Confirmed fact requires sources and an assertion basis")
    for item in provenance_collection(component["bindings"], validator):
        provenance_object(item, {"id", "fact_id", "target", "fields", "source_snapshots", "validity"})
        validator.sid(item["fact_id"], "binding fact_id")
        target = contracts.require_mapping(item["target"], "binding target")
        kind = target.get("kind")
        if not isinstance(kind, str) or kind not in PROVENANCE_FIELDS:
            provenance_error("schema/type", "Unsupported binding target kind")
        keys = {"kind", "decision_id", "outcome_id"} if kind == "outcome" else {"kind", "id"}
        provenance_object(target, keys)
        for key in sorted(keys - {"kind"}):
            validator.sid(target[key], "binding target " + key)
        fields = provenance_object(item["fields"], PROVENANCE_FIELDS[kind], ())
        if not fields:
            provenance_error("schema/required", "Binding needs a nonempty field snapshot")
        for key, value in fields.items():
            if not (kind == "edge" and key == "outcome" and value is None):
                contracts.require_string(value, "binding field snapshot", allow_empty=key in {"label", "decision_label"})
            if key in {"owner", "from", "to", "outcome"} and value is not None:
                validator.sid(value, "binding field " + key)
        if item["validity"] not in PROVENANCE_STATES:
            provenance_error("schema/type", "Unsupported binding validity")
        snapshots = contracts.require_list(item["source_snapshots"], "source_snapshots")
        refs = []
        for snapshot in snapshots:
            provenance_object(snapshot, {"source_id", "version", "digest"})
            refs.append(validator.sid(snapshot["source_id"], "source snapshot id"))
            provenance_text(snapshot["version"])
            provenance_digest(snapshot["digest"])
        if len(refs) != len(set(refs)):
            provenance_error("schema/duplicate", "Duplicate source snapshot")
    if references:
        sources = {item["id"] for item in component["sources"]}
        facts = {item["id"]: item for item in component["facts"]}
        for fact in facts.values():
            if set(fact["source_refs"]) - sources:
                provenance_error("provenance/reference-invalid", "Fact references unknown source", fact_id=fact["id"])
        for binding in component["bindings"]:
            fact = facts.get(binding["fact_id"])
            if fact is None:
                provenance_error("provenance/reference-invalid", "Binding references unknown fact", binding_id=binding["id"])
            if {item["source_id"] for item in binding["source_snapshots"]} != set(fact["source_refs"]):
                provenance_error("provenance/reference-invalid", "Source snapshots must cover the binding fact's exact sources", binding_id=binding["id"])


def validate_provenance(component, validator):
    provenance_object(component, {"version", "sources", "facts", "bindings", "history"})
    if type(component["version"]) is not int:
        provenance_error("schema/type", "Provenance version must be a non-boolean integer")
    if component["version"] != 1:
        provenance_error("context/version-unsupported", "Unsupported provenance version")
    provenance_validate_records(component, validator)
    history = contracts.require_list(component["history"], "provenance history")
    if len(history) > 128:
        provenance_error("context/resource-limit", "Provenance history exceeds 128 events")
    for event in history:
        provenance_object(event, {"artifact_sha256", "context_sha256", "records"})
        provenance_digest(event["artifact_sha256"])
        provenance_digest(event["context_sha256"])
        provenance_object(event["records"], {"sources", "facts", "bindings"})
        provenance_validate_records(event["records"], validator, references=False)


def provenance_target_key(target):
    return ((target["kind"], target["decision_id"], target["outcome_id"]) if target["kind"] == "outcome"
            else (target["kind"], target["id"]))


def provenance_projection(model):
    values = {}
    nodes = {node["id"]: node for node in model["nodes"]}
    for sid, node in nodes.items():
        values[("node", sid)] = {"label": node["label"], "owner": node["lane"], "type": node["type"]}
    for edge in model["edges"]:
        values[("edge", edge["id"])] = {key: edge.get(key) for key in PROVENANCE_FIELDS["edge"]}
        if edge.get("outcome") is not None and nodes[edge["from"]]["type"] == "decision":
            decision = nodes[edge["from"]]
            values[("outcome", edge["from"], edge["outcome"])] = {
                "label": edge["outcome"], "decision_label": decision["label"], "owner": decision["lane"]}
    return values


def provenance_binding_validity(binding, component, projection, *, honor_history=True):
    actual = projection.get(provenance_target_key(binding["target"]))
    if actual is None:
        return "deleted" if binding["validity"] == "deleted" else "missing"
    if honor_history and binding["validity"] != "current":
        return binding["validity"]
    if any(actual.get(key) != value for key, value in binding["fields"].items()):
        return "stale"
    sources = {source["id"]: source for source in component["sources"]}
    if not binding["source_snapshots"]:
        return "missing"
    for snapshot in binding["source_snapshots"]:
        source = sources.get(snapshot["source_id"])
        if source is None:
            return "missing"
        if any(snapshot[key] != source[key] for key in ("version", "digest")):
            return "stale"
    return "current"


def assess_provenance(component, model):
    projection = provenance_projection(model)
    results, covered, covered_fields = [], set(), set()
    for fact in sorted(component["facts"], key=lambda item: item["id"]):
        bindings = []
        for binding in sorted(component["bindings"], key=lambda item: item["id"]):
            if binding["fact_id"] != fact["id"]:
                continue
            state = provenance_binding_validity(binding, component, projection)
            if binding["validity"] == "current" and (state == "stale" or provenance_target_key(binding["target"]) not in projection):
                provenance_error("provenance/snapshot-mismatch", "Current binding does not match its original target/source snapshot", binding_id=binding["id"])
            bindings.append({"binding_id": binding["id"], "target": copy.deepcopy(binding["target"]),
                             "fields": sorted(binding["fields"]), "validity": state})
            if state == "current" and fact["status"] == "confirmed":
                covered.add(provenance_target_key(binding["target"]))
                covered_fields.update((provenance_target_key(binding["target"]), key) for key in binding["fields"])
        states = {item["validity"] for item in bindings}
        validity = next((state for state in ("deleted", "missing", "stale") if state in states), "current") if states else "missing"
        results.append({"fact_id": fact["id"], "declared_status": fact["status"],
                        "confirmation": copy.deepcopy(fact["confirmation"]), "verification": "declaration_only",
                        "validity": validity, "effective_confirmed": fact["status"] == "confirmed" and validity == "current",
                        "binding_results": bindings})
    total = set(projection)
    total_fields = {(target, key) for target, values in projection.items() for key in values}
    gate = "passed" if results and all(item["effective_confirmed"] for item in results) and covered == total else "incomplete"
    return {"source_gate": gate, "source_results": results,
            "source_coverage": {"source_count": len(component["sources"]), "fact_count": len(results),
                                "binding_count": len(component["bindings"]), "confirmed_current_objects": len(covered),
                                "total_objects": len(total), "extent": "whole_diagram" if covered == total else "partial",
                                "uncovered_objects": [list(key) for key in sorted(total - covered)],
                                "verification": "declaration_only", "gate_scope": "declared_fields_with_object_coverage",
                                "total_fields": len(total_fields), "confirmed_current_fields": len(covered_fields),
                                "field_extent": "all_fields" if covered_fields == total_fields else "partial",
                                "uncovered_fields": [{"target": list(target), "field": key} for target, key in sorted(total_fields - covered_fields)]}}


def provenance_template_check(component, model):
    if component["history"]:
        provenance_error("provenance/change-not-declared", "New provenance templates cannot import history")
    projection = provenance_projection(model)
    for binding in component["bindings"]:
        if provenance_target_key(binding["target"]) not in projection:
            provenance_error("provenance/reference-invalid", "Template binding target is absent", binding_id=binding["id"])
        state = provenance_binding_validity(binding, component, projection, honor_history=False)
        if (binding["validity"] not in {"current", "missing"} or state not in {"current", "missing"}
                or (binding["validity"] == "missing" and binding["source_snapshots"])):
            provenance_error("provenance/snapshot-mismatch", "Template snapshot differs from its actual target/source", binding_id=binding["id"])


def validate_context_changes(changes, validator):
    provenance_object(changes, {"context_changes_version", "expected_input_sha256", "expected_context_sha256", "mode",
                                "patterns", "group_contracts", "provenance", "confirmation_actions"},
                      {"context_changes_version", "expected_input_sha256", "expected_context_sha256", "mode"})
    if type(changes["context_changes_version"]) is not int:
        provenance_error("schema/type", "Context change version must be integer")
    if changes["context_changes_version"] != 1:
        provenance_error("context/version-unsupported", "Unsupported context changes version")
    for key in ("expected_input_sha256", "expected_context_sha256"):
        provenance_digest(changes[key])
    if not isinstance(changes["mode"], str) or changes["mode"] not in {"semantic", "geometry-only", "migration"}:
        provenance_error("schema/type", "Unsupported context change mode")
    actions = contracts.require_list(changes.get("confirmation_actions", []), "confirmation_actions")
    ids = []
    for action in actions:
        provenance_object(action, {"fact_id", "asserted_by", "basis"})
        ids.append(validator.sid(action["fact_id"], "confirmation action fact"))
        provenance_confirmation({key: action[key] for key in ("asserted_by", "basis")})
    if len(ids) != len(set(ids)):
        provenance_error("schema/duplicate", "Duplicate confirmation action")
    if "provenance" in changes:
        validate_provenance(changes["provenance"], validator)


def transition_context(before, changes, before_model, after_model, *, context_sha256, artifact):
    if changes["expected_input_sha256"] != before["artifact"]["sha256"]:
        provenance_error("delivery/input-sha256-mismatch", "Context change graph precondition does not match")
    if changes["expected_context_sha256"] != context_sha256:
        provenance_error("delivery/context-sha256-mismatch", "Context change raw-context precondition does not match")
    result = copy.deepcopy(before)
    result["artifact"] = copy.deepcopy(artifact)
    if changes["mode"] in {"geometry-only", "migration"}:
        if before_model != after_model:
            provenance_error("provenance/geometry-not-pure", "Rebinding requires the exact unchanged semantic model")
        if any(key in changes for key in ("patterns", "group_contracts", "provenance")) or changes.get("confirmation_actions"):
            provenance_error("provenance/change-not-declared", "Pure rebind cannot modify context declarations")
        if provenance_projection(before_model) != provenance_projection(after_model):
            provenance_error("provenance/geometry-not-pure", "Rebinding changed field projections")
        return result
    for key in ("patterns", "group_contracts"):
        if key in changes:
            result[key] = copy.deepcopy(changes[key])
    old = before.get("provenance", {"version": 1, "sources": [], "facts": [], "bindings": [], "history": []})
    if "provenance" not in before and "provenance" not in changes:
        if changes.get("confirmation_actions"):
            provenance_error("provenance/reference-invalid", "Confirmation references absent provenance")
        return result
    candidate = copy.deepcopy(changes.get("provenance", old))
    if candidate["history"] != old["history"]:
        provenance_error("provenance/record-loss", "Declared context cannot edit prior history")
    for kind in ("sources", "facts", "bindings"):
        if {item["id"] for item in old[kind]} - {item["id"] for item in candidate[kind]}:
            provenance_error("provenance/record-loss", "Source, fact and binding records must be retained", collection=kind)
    actions = {item["fact_id"]: {key: item[key] for key in ("asserted_by", "basis")}
               for item in changes.get("confirmation_actions", [])}
    old_facts = {item["id"]: item for item in old["facts"]}
    new_facts = {item["id"]: item for item in candidate["facts"]}
    if set(actions) - set(new_facts):
        provenance_error("provenance/reference-invalid", "Confirmation action references unknown fact")
    for sid, fact in new_facts.items():
        previous = old_facts.get(sid)
        if sid in actions and (fact["status"] != "confirmed" or fact["confirmation"] != actions[sid]):
            provenance_error("provenance/confirmation-required", "Confirmation action must match the declared confirmed fact", fact_id=sid)
        if (fact["status"] == "confirmed" and previous != fact) or (previous and previous["confirmation"] != fact["confirmation"] and fact["confirmation"] is not None):
            if sid not in actions:
                provenance_error("provenance/confirmation-required", "Changing a confirmation requires an explicit assertion action", fact_id=sid)
    old_bindings = {item["id"]: item for item in old["bindings"]}
    before_projection, after_projection = provenance_projection(before_model), provenance_projection(after_model)
    for binding in candidate["bindings"]:
        previous = old_bindings.get(binding["id"])
        fact_id = binding["fact_id"]
        if previous and previous["fact_id"] != fact_id:
            provenance_error("provenance/change-not-declared", "A stable binding ID cannot change its fact")
        if fact_id in actions:
            if binding["validity"] != "current" or provenance_binding_validity(binding, candidate, after_projection, honor_history=False) != "current":
                provenance_error("provenance/snapshot-mismatch", "New confirmation must bind exact current fields and source versions", binding_id=binding["id"])
        else:
            if previous and previous != binding and not (new_facts[fact_id]["status"] in {"assumption", "unresolved"} and new_facts[fact_id]["confirmation"] is None):
                provenance_error("provenance/confirmation-required", "Refreshing an existing binding needs an explicit confirmation action", binding_id=binding["id"])
            if previous is None:
                if new_facts[fact_id]["status"] == "confirmed":
                    provenance_error("provenance/confirmation-required", "Adding confirmed bindings needs an explicit confirmation action", binding_id=binding["id"])
                if provenance_target_key(binding["target"]) not in after_projection:
                    provenance_error("provenance/reference-invalid", "New binding target does not exist")
            if previous and provenance_target_key(previous["target"]) in before_projection and provenance_target_key(previous["target"]) not in after_projection:
                binding["validity"] = "deleted"
            else:
                binding["validity"] = provenance_binding_validity(binding, candidate, after_projection)
    historical = {kind: [copy.deepcopy(item) for item in old[kind]
                          if item != next((record for record in candidate[kind] if record["id"] == item["id"]), None)]
                  for kind in ("sources", "facts", "bindings")}
    if any(historical.values()):
        candidate["history"].append({"artifact_sha256": before["artifact"]["sha256"], "context_sha256": context_sha256,
                                     "records": historical})
    result["provenance"] = candidate
    return result


def audit_context_preservation(before, after, changes):
    """Independent direct invariants, in addition to exact transition comparison."""
    differences = []
    old, new = before.get("provenance"), after.get("provenance")
    actions = {item["fact_id"]: {key: item[key] for key in ("asserted_by", "basis")}
               for item in changes.get("confirmation_actions", [])}
    if old is None:
        old = {"sources": [], "facts": [], "bindings": [], "history": []}
    if new is None:
        if any(old[key] for key in ("sources", "facts", "bindings", "history")):
            differences.append({"code": "provenance/record-loss", "field": "provenance"})
        return differences
    if new["history"][:len(old["history"])] != old["history"]:
        differences.append({"code": "provenance/record-loss", "field": "history"})
    for kind in ("sources", "facts", "bindings"):
        actual = {item["id"]: item for item in new[kind]}
        for record in old[kind]:
            other = actual.get(record["id"])
            if other is None:
                differences.append({"code": "provenance/record-loss", "field": kind, "id": record["id"]})
            elif other != record and not any(record in event["records"][kind] for event in new["history"][len(old["history"]):]):
                differences.append({"code": "provenance/record-loss", "field": "history." + kind, "id": record["id"]})
    old_facts = {item["id"]: item for item in old["facts"]}
    for fact in new["facts"]:
        previous = old_facts.get(fact["id"])
        if (fact["status"] == "confirmed" and fact != previous) or (previous and fact["confirmation"] != previous["confirmation"] and fact["confirmation"] is not None):
            if actions.get(fact["id"]) != fact["confirmation"]:
                differences.append({"code": "provenance/confirmation-required", "field": "facts", "id": fact["id"]})
    old_bindings = {item["id"]: item for item in old["bindings"]}
    for binding in new["bindings"]:
        previous = old_bindings.get(binding["id"])
        if binding["validity"] == "current" and previous != binding and binding["fact_id"] not in actions:
            if next(item for item in new["facts"] if item["id"] == binding["fact_id"])["status"] == "confirmed":
                differences.append({"code": "provenance/confirmation-required", "field": "bindings", "id": binding["id"]})
    return differences
