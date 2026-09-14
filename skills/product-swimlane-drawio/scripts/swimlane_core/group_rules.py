"""Versioned group declarations and read-only topology predicates.

Existing groups retain their lane-local ownership and mirrored membership.
Explicit scopes and declarations provide every additional relationship. This
module never reads labels, geometry or ranks and has no mutation capability.
"""
from __future__ import annotations

from . import contracts, pattern_rules

RULE_VERSION = "1"
MAX_GROUPS = 128
FIELDS = {
    "parallel": {"fork", "join", "branches"},
    "branch": {"decision", "branches"},
    "merge": {"target", "inputs"},
    "exception": set(),
    "support": set(),
}
BRANCH_FIELDS = {
    "parallel": {"branch_id", "entry_edge", "members", "branch_edges", "join_required"},
    "branch": {"entry_edge", "outcome", "member", "path"},
}
RULE_IDS = {
    "parallel": ("group/parallel/entries", "group/parallel/members", "group/parallel/required-join", "group/parallel/coverage"),
    "branch": ("group/branch/outcomes", "group/branch/member-paths", "group/branch/coverage"),
    "merge": ("group/merge/target", "group/merge/required-paths", "group/merge/coverage"),
    "exception": ("group/exception/reference-only",),
    "support": ("group/support/reference-only",),
}


def _group_error(code, message, **evidence):
    raise contracts.DiagramError(message, code=code, subject={"kind": "group_contracts"}, evidence=evidence)


def _group_object(value, allowed, subject, required=()):
    contracts.require_mapping(value, subject)
    contracts.reject_unknown_fields(value, set(allowed), subject)
    absent = sorted(set(required) - set(value))
    if absent:
        _group_error("schema/required", "Required group context fields are missing", field=subject, fields=absent)
    return value


def _group_unique(values, subject):
    if len(values) != len(set(values)):
        _group_error("schema/duplicate", "Group declaration identities must be unique", field=subject)


def validate_component(component, validator):
    """Check JSON shape and count all IDs in the existing shared input budget."""
    _group_object(component, {"version", "groups"}, "group_contracts", ("version", "groups"))
    version = component["version"]
    if type(version) is not int:
        _group_error("schema/type", "Group component version must be a non-boolean integer")
    if version != 1:
        _group_error("context/version-unsupported", "Group component version is unsupported", version=version)
    entries = contracts.require_list(component["groups"], "group_contracts.groups")
    if len(entries) > MAX_GROUPS:
        _group_error("context/resource-limit", "Group contract count exceeds its budget", limit=MAX_GROUPS)
    group_ids = []
    for entry in entries:
        _group_object(entry, {"group_id", "scope_id", "declarations"}, "group contract", ("group_id", "scope_id", "declarations"))
        group_ids.append(validator.sid(entry["group_id"], "group_id"))
        validator.sid(entry["scope_id"], "scope_id")
        declaration = _group_object(entry["declarations"], set().union(*FIELDS.values()), "group declarations")
        for field in ("fork", "join", "decision", "target"):
            if field in declaration:
                validator.sid(declaration[field], field)
        branches = validator.items(declaration, "branches", set().union(*BRANCH_FIELDS.values()))
        branch_ids, pairs = [], []
        for branch in branches:
            for field in ("branch_id", "entry_edge", "outcome", "member"):
                if field in branch:
                    validator.sid(branch[field], field)
            for field in ("members", "branch_edges", "path"):
                if field in branch:
                    validator.ids(branch[field], field)
            if "join_required" in branch and type(branch["join_required"]) is not bool:
                _group_error("schema/type", "join_required must be a boolean")
            if "branch_id" in branch:
                branch_ids.append(branch["branch_id"])
            if "entry_edge" in branch and "member" in branch:
                pairs.append((branch["entry_edge"], branch["member"]))
        _group_unique(branch_ids, "branches[].branch_id")
        _group_unique(pairs, "branches[].(entry_edge,member)")
        for item in validator.items(declaration, "inputs", {"input_id", "source", "required", "path"}, "input_id"):
            if "source" in item:
                validator.sid(item["source"], "source")
            if "required" in item and type(item["required"]) is not bool:
                _group_error("schema/type", "required must be a boolean")
            if "path" in item:
                validator.ids(item["path"], "path")
    _group_unique(group_ids, "group_contracts.groups[].group_id")


def check_references(component, scopes, model):
    """Resolve typed objects and exact kind-specific fields before any rules."""
    groups = {group["id"]: group for group in model["groups"]}
    by_scope = {scope["id"]: scope for scope in scopes}
    all_edges = {edge["id"]: edge for edge in model["edges"]}
    for entry in sorted(component["groups"], key=lambda item: item["group_id"]):
        group_id, scope_id = entry["group_id"], entry["scope_id"]

        def refs(values, allowed, field):
            missing = sorted(set(values) - set(allowed))
            if missing:
                _group_error("context/reference-invalid", "Group reference is missing or outside its typed scope",
                       group_id=group_id, scope_id=scope_id, field=field, ids=missing)

        refs([group_id], groups, "group_id")
        refs([scope_id], by_scope, "scope_id")
        group, scope = groups[group_id], by_scope[scope_id]
        kind, declaration = group["kind"], entry["declarations"]
        node_ids, edge_ids = set(scope["nodes"]), set(scope["edges"])
        refs(group["nodes"], node_ids, "group members in scope")
        contracts.reject_unknown_fields(declaration, FIELDS[kind], "group declarations for " + kind)

        def field(value, key, allowed, many=False):
            if key in value:
                refs(value[key] if many else [value[key]], allowed, key)

        for key in ("fork", "join", "decision", "target"):
            field(declaration, key, node_ids)
        if kind in {"parallel", "branch"}:
            entries = []
            for branch in declaration.get("branches", []):
                contracts.reject_unknown_fields(branch, BRANCH_FIELDS[kind], "group " + kind + " branch")
                field(branch, "entry_edge", edge_ids)
                field(branch, "members", node_ids, True)
                field(branch, "member", node_ids)
                field(branch, "branch_edges", edge_ids, True)
                field(branch, "path", edge_ids, True)
                if kind == "parallel" and "entry_edge" in branch:
                    entries.append(branch["entry_edge"])
                if kind == "branch" and "outcome" in branch and "decision" in declaration:
                    outcomes = {edge.get("outcome") for edge in all_edges.values()
                                if edge["from"] == declaration["decision"] and edge.get("outcome")}
                    field(branch, "outcome", outcomes)
            if kind == "parallel":
                _group_unique(entries, "parallel branches[].entry_edge")
        elif kind == "merge":
            for item in declaration.get("inputs", []):
                field(item, "source", node_ids)
                field(item, "path", edge_ids, True)


def _declared_members(group, declaration, edges):
    """Return membership claims, without treating a claim as a proved path."""
    claimed = set()
    kind = group["kind"]
    if kind == "parallel":
        claimed.update(declaration[key] for key in ("fork", "join") if key in declaration)
        for branch in declaration.get("branches", []):
            claimed.update(branch.get("members", []))
    elif kind == "branch":
        if "decision" in declaration:
            claimed.add(declaration["decision"])
        claimed.update(branch["member"] for branch in declaration.get("branches", []) if "member" in branch)
    elif kind == "merge":
        if "target" in declaration:
            claimed.add(declaration["target"])
        for item in declaration.get("inputs", []):
            if "source" in item:
                claimed.add(item["source"])
            for edge_id in item.get("path", []):
                claimed.update((edges[edge_id]["from"], edges[edge_id]["to"]))
    return claimed & set(group["nodes"])


def _missing_group_declarations(group, declaration, claimed):
    kind = group["kind"]
    required = {"parallel": ("fork", "branches"), "branch": ("decision", "branches"),
                "merge": ("target", "inputs"), "exception": (), "support": ()}[kind]
    missing = {key for key in required if key not in declaration}
    if kind in {"parallel", "branch"}:
        for branch in declaration.get("branches", []):
            identity = branch.get("branch_id", branch.get("entry_edge", "?"))
            for key in BRANCH_FIELDS[kind]:
                if key not in branch:
                    missing.add(f"branches[{identity}].{key}")
        if kind == "parallel" and any(branch.get("join_required") is True for branch in declaration.get("branches", [])) and "join" not in declaration:
            missing.add("join")
    elif kind == "merge":
        for item in declaration.get("inputs", []):
            keys = ("input_id", "source", "required", "path") if item.get("required") is True else ("input_id", "source", "required")
            for key in keys:
                if key not in item:
                    missing.add(f"inputs[{item.get('input_id', '?')}].{key}")
    if kind in {"parallel", "branch", "merge"}:
        missing.update(f"members[{node}]" for node in set(group["nodes"]) - claimed)
    return sorted(missing)


def _parallel(group, declaration, nodes, edges):
    fork, join = declaration["fork"], declaration.get("join")
    members = set(group["nodes"])
    branches = sorted(declaration["branches"], key=lambda item: item["branch_id"])
    entry_ids = {branch["entry_edge"] for branch in branches}
    outgoing = {eid for eid, edge in edges.items() if edge["from"] == fork}
    entry_errors, member_errors, join_errors = [], [], []
    proved = {node for node in (fork, join) if node in members}
    for branch in branches:
        branch_id = branch["branch_id"]
        entry = edges[branch["entry_edge"]]
        if entry["from"] != fork:
            entry_errors.append(branch_id)
        selected = {eid: edges[eid] for eid in branch["branch_edges"]}
        forbidden = {eid for eid, edge in selected.items() if eid in entry_ids
                     or fork in (edge["from"], edge["to"]) or (join is not None and edge["from"] == join)}
        proof = {eid: edge for eid, edge in selected.items() if eid not in forbidden}
        reachable = pattern_rules._visited(entry["to"], proof.values())
        disconnected = {eid for eid, edge in proof.items() if edge["from"] not in reachable}
        invalid_members = set(branch["members"]) - members
        boundary_members = set(branch["members"]) & {fork, join}
        unreachable = set(branch["members"]) - reachable
        if (not branch["members"] or forbidden or disconnected or invalid_members or boundary_members or unreachable):
            member_errors.append({"branch_id": branch_id, "empty_members": not branch["members"],
                                  "forbidden_proof_edges": sorted(forbidden), "disconnected_proof_edges": sorted(disconnected),
                                  "non_group_members": sorted(invalid_members), "boundary_members": sorted(boundary_members),
                                  "unreachable_members": sorted(unreachable)})
        if entry["from"] == fork and not forbidden:
            proved.update((set(branch["members"]) & members & reachable) - {fork, join})
        if branch["join_required"]:
            reverse = [{"from": edge["to"], "to": edge["from"]} for edge in proof.values()]
            reach_join = pattern_rules._visited(join, reverse)
            cannot_join = ({entry["to"]} | set(branch["members"])) - reach_join
            if cannot_join:
                join_errors.append({"branch_id": branch_id, "cannot_reach_join": sorted(cannot_join)})
    return {
        "group/parallel/entries": (len(branches) >= 2 and fork != join and not entry_errors and entry_ids == outgoing,
                                   {"fork": fork, "join": join, "branch_count": len(branches), "invalid_entry_branches": entry_errors,
                                    "undeclared_entry_edges": sorted(outgoing - entry_ids), "non_fork_entries": sorted(entry_ids - outgoing)}),
        "group/parallel/members": (not member_errors, {"invalid_memberships": member_errors}),
        "group/parallel/required-join": (not join_errors, {"invalid_joins": join_errors,
                                         "required_branch_count": sum(branch["join_required"] for branch in branches),
                                         "optional_branch_count": sum(not branch["join_required"] for branch in branches)}),
        "group/parallel/coverage": (proved == members, {"unproved_members": sorted(members - proved)}),
    }, proved


def _branch(group, declaration, nodes, edges):
    decision = declaration["decision"]
    members = set(group["nodes"])
    branches = sorted(declaration["branches"], key=lambda item: (item["entry_edge"], item["member"], item["outcome"]))
    outcomes, paths = [], []
    proved = {decision} & members if nodes[decision]["type"] == "decision" else set()
    for item in branches:
        entry = edges[item["entry_edge"]]
        pair_matches = entry["from"] == decision and entry.get("outcome") == item["outcome"]
        if not pair_matches:
            outcomes.append({"entry_edge": item["entry_edge"], "outcome": item["outcome"], "actual_source": entry["from"], "actual_outcome": entry.get("outcome")})
        valid_path, _ = pattern_rules._path(entry["to"], item["member"], item["path"], edges)
        is_member = item["member"] in members
        if not valid_path or not is_member:
            paths.append({"entry_edge": item["entry_edge"], "member": item["member"], "path": item["path"],
                          "continuous_to_member": valid_path, "group_member": is_member})
        if valid_path and is_member and pair_matches and nodes[decision]["type"] == "decision":
            proved.add(item["member"])
    return {
        "group/branch/outcomes": (nodes[decision]["type"] == "decision" and not outcomes,
                                  {"decision": decision, "decision_type": nodes[decision]["type"], "invalid_outcome_pairs": outcomes}),
        "group/branch/member-paths": (not paths, {"invalid_member_paths": paths}),
        "group/branch/coverage": (proved == members, {"unproved_members": sorted(members - proved)}),
    }, proved


def _merge(group, declaration, nodes, edges):
    target = declaration["target"]
    members = set(group["nodes"])
    inputs = sorted(declaration["inputs"], key=lambda item: item["input_id"])
    sources = {item["source"] for item in inputs}
    invalid = []
    proved = {target} & members
    for item in inputs:
        path = item.get("path", [])
        valid_path, visited = pattern_rules._path(item["source"], target, path, edges)
        if item["required"] or path:
            if not valid_path:
                invalid.append({"input_id": item["input_id"], "source": item["source"], "path": path})
            elif target in members:
                proved.update(set(visited) & members)
        elif item["source"] in members:
            # This explicitly optional input has no claimed path to prove.
            proved.add(item["source"])
    return {
        "group/merge/target": (target in members and len(sources) >= 2 and len(sources) == len(inputs),
                               {"target": target, "target_is_group_member": target in members,
                                "input_count": len(inputs), "distinct_sources": sorted(sources)}),
        "group/merge/required-paths": (not invalid, {"invalid_input_paths": invalid,
                                      "required_inputs": sorted(item["input_id"] for item in inputs if item["required"]),
                                      "optional_inputs": sorted(item["input_id"] for item in inputs if not item["required"])}),
        "group/merge/coverage": (proved == members, {"unproved_members": sorted(members - proved)}),
    }, proved


PREDICATES = {"parallel": _parallel, "branch": _branch, "merge": _merge}


def assess_groups(component, scopes, model, *, prerequisites_met=True):
    groups = {group["id"]: group for group in model["groups"]}
    by_scope = {scope["id"]: scope for scope in scopes}
    all_nodes = {node["id"]: node for node in model["nodes"]}
    all_edges = {edge["id"]: edge for edge in model["edges"]}
    results = []
    for item in sorted(component["groups"], key=lambda value: value["group_id"]):
        group, scope = groups[item["group_id"]], by_scope[item["scope_id"]]
        declaration, kind = item["declarations"], group["kind"]
        edges = {eid: all_edges[eid] for eid in scope["edges"]}
        nodes = {nid: all_nodes[nid] for nid in scope["nodes"]}
        claimed = _declared_members(group, declaration, edges)
        missing = _missing_group_declarations(group, declaration, claimed)
        reference_only = kind in {"exception", "support"}
        assessed = prerequisites_met and not missing and not reference_only
        outcomes = sorted({(declaration["decision"], branch["outcome"]) for branch in declaration.get("branches", [])
                           if "decision" in declaration and "outcome" in branch})
        subjects = {"groups": [group["id"]], "nodes": sorted(scope["nodes"]), "edges": sorted(scope["edges"]),
                    "outcomes": [{"decision_id": decision, "outcome_id": outcome} for decision, outcome in outcomes]}
        predicate_results, proved = PREDICATES[kind](group, declaration, nodes, edges) if assessed else ({}, set())
        rules = []
        for rule_id in sorted(RULE_IDS[kind]):
            if not prerequisites_met:
                status, evidence = "not_assessed", {"reason": "general-validation-prerequisite-failed"}
            elif missing:
                status, evidence = "not_assessed", {}
            elif reference_only:
                status, evidence = "not_applicable", {"reason": "only-existing-group-references-checked", "assessment": "reference_only"}
            else:
                passed, evidence = predicate_results[rule_id]
                status = "passed" if passed else "violated"
            code = "semantic/group-violation" if status == "violated" else "semantic/group-incomplete" if status == "not_assessed" else None
            rules.append({"group_id": group["id"], "scope_id": scope["id"], "rule_id": rule_id, "status": status,
                          "code": code, "subject_ids": subjects, "evidence": evidence, "missing_declarations": missing})
        coverage = {"member_count": len(proved), "declared_member_count": len(claimed), "reference_member_count": len(group["nodes"]),
                    "total_members": len(group["nodes"]), "extent": "whole_group" if proved == set(group["nodes"]) else "partial",
                    "scope_id": scope["id"], "excluded_edges": sorted(scope["excluded_edges"], key=lambda value: value["edge_id"]),
                    "topology_assessed": assessed, "assessment": "reference_only" if reference_only else "topology"}
        results.append({"group_id": group["id"], "kind": kind, "scope_id": scope["id"],
                        "gate": pattern_rules.gate(rules), "coverage": coverage, "rules": rules})
    return results


def summarize_coverage(results, model):
    selected = {result["group_id"] for result in results}
    all_groups = {group["id"] for group in model["groups"]}
    rules = [rule for result in results for rule in result["rules"]]
    return {"group_count": len(selected), "total_groups": len(all_groups),
            "topology_group_count": sum(result["coverage"]["topology_assessed"] for result in results),
            "unrequested_groups": sorted(all_groups - selected), "extent": "all_groups" if selected == all_groups else "partial",
            "rule_status_counts": {status: sum(rule["status"] == status for rule in rules)
                                   for status in ("passed", "violated", "not_assessed", "not_applicable")}}
