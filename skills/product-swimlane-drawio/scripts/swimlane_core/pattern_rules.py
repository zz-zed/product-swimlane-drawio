"""Pure read-only predicates over declared semantic scopes.

No layout, label, rank, writer, parser, routing or network capability is used.
Reachability and ordered paths use only the selected edges and stable IDs.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque

RULE_VERSION = "1"
RULE_IDS = {
    "linear": ("linear/degrees", "linear/coverage", "linear/acyclic"),
    "approval-loop": ("approval/declared-roles", "approval/return-reachability", "approval/rejection-termination"),
    "request-response": ("request/coverage", "request/required-response", "request/participant-pair", "request/completion-path"),
    "fork-join": ("fork/branches", "fork/required-join", "fork/coverage"),
    "fan-in": ("fanin/required-path", "fanin/target", "fanin/coverage"),
    "lifecycle": ("lifecycle/state-coverage", "lifecycle/allowed-transition"),
    "custom": ("custom/no-pattern-rules",),
}
REQUIRED = {
    "linear": ("entry", "exit"),
    "approval-loop": ("forward_edges", "retry_edges", "terminal_rejections"),
    "request-response": ("requests",),
    "fork-join": ("fork", "join", "branches"),
    "fan-in": ("target", "inputs"),
    "lifecycle": ("states", "node_states", "allowed_transitions"),
    "custom": (),
}


def gate(rules):
    statuses = {rule["status"] for rule in rules}
    if "violated" in statuses:
        return "failed"
    if "not_assessed" in statuses:
        return "incomplete"
    return "passed" if "passed" in statuses else "not_applicable"


def _visited(source, edges):
    adjacency = defaultdict(list)
    for edge in edges:
        adjacency[edge["from"]].append(edge["to"])
    visited, pending = set(), [source]
    while pending:
        node = pending.pop()
        if node not in visited:
            visited.add(node)
            pending.extend(adjacency[node])
    return visited


def _reachable(source, target, edges):
    return target in _visited(source, edges)


def _path(source, target, path, edges):
    current, visited = source, [source]
    for edge_id in path:
        edge = edges[edge_id]
        if edge["from"] != current:
            return False, visited
        current = edge["to"]
        visited.append(current)
    return current == target, visited


def _missing(scope, edges):
    declaration, pattern = scope["declarations"], scope["pattern"]
    missing = {field for field in REQUIRED[pattern] if field not in declaration}

    def members(items, root, keys, identity):
        for item in items:
            name = str(item.get(identity, "?"))
            for key in keys:
                if key not in item:
                    missing.add(f"{root}[{name}].{key}")

    if pattern == "approval-loop":
        members(declaration.get("terminal_rejections", []), "terminal_rejections",
                ("rejected_edge", "termination_path"), "rejected_edge")
    elif pattern == "request-response":
        requests = declaration.get("requests", [])
        members(requests, "requests", ("request_edge", "response_required", "response_edges", "completion_paths"), "request_edge")
        for item in requests:
            root = f"requests[{item.get('request_edge', '?')}].completion_paths"
            members(item.get("completion_paths", []), root, ("response_edge", "path"), "response_edge")
            if "response_edges" in item and "completion_paths" in item:
                declared = {pair.get("response_edge") for pair in item["completion_paths"]}
                missing.update(f"{root}[{eid}]" for eid in set(item["response_edges"]) - declared)
        if "requests" in declaration:
            requested = {item.get("request_edge") for item in requests}
            missing.update(f"requests[{eid}]" for eid, edge in edges.items()
                           if edge["type"] in {"call", "async"} and eid not in requested)
    elif pattern == "fork-join":
        branches = declaration.get("branches", [])
        members(branches, "branches", ("branch_id", "entry_edge", "required"), "branch_id")
        members([item for item in branches if item.get("required") is True], "branches", ("join_path",), "branch_id")
    elif pattern == "fan-in":
        inputs = declaration.get("inputs", [])
        members(inputs, "inputs", ("input_id", "source", "required"), "input_id")
        members([item for item in inputs if item.get("required") is True], "inputs", ("path",), "input_id")
    elif pattern == "lifecycle":
        members(declaration.get("allowed_transitions", []), "allowed_transitions", ("from", "to"), "from")
        if "node_states" in declaration:
            missing.update(f"node_states[{nid}]" for nid in set(scope["nodes"]) - set(declaration["node_states"]))
    return sorted(missing)


def _linear(declaration, nodes, edges):
    entry, exit_node = declaration["entry"], declaration["exit"]
    incoming = Counter(edge["to"] for edge in edges.values())
    outgoing = Counter(edge["from"] for edge in edges.values())
    degree_errors = []
    for node in sorted(nodes):
        expected = (0, 1) if node == entry else (1, 0) if node == exit_node else (1, 1)
        actual = (incoming[node], outgoing[node])
        if actual != expected:
            degree_errors.append({"node_id": node, "incoming": actual[0], "outgoing": actual[1],
                                  "expected_incoming": expected[0], "expected_outgoing": expected[1]})
    reachable = _visited(entry, edges.values())
    backwards = _visited(exit_node, [{"from": edge["to"], "to": edge["from"]} for edge in edges.values()])
    unreachable = sorted(set(nodes) - reachable)
    no_exit_path = sorted(set(nodes) - backwards)
    degree = dict(incoming)
    queue = deque(sorted(node for node in nodes if not incoming[node]))
    adjacency = defaultdict(list)
    for edge in edges.values():
        adjacency[edge["from"]].append(edge["to"])
    removed = set()
    while queue:
        node = queue.popleft()
        removed.add(node)
        for target in adjacency[node]:
            degree[target] -= 1
            if not degree[target]:
                queue.append(target)
    cyclic = sorted(set(nodes) - removed)
    return {
        "linear/degrees": (entry != exit_node and not degree_errors,
                           {"entry": entry, "exit": exit_node, "degree_mismatches": degree_errors}),
        "linear/coverage": (not unreachable and not no_exit_path, {"unreachable_from_entry": unreachable, "cannot_reach_exit": no_exit_path}),
        "linear/acyclic": (not cyclic, {"cycle_or_cycle_dependent_nodes": cyclic}),
    }


def _approval(declaration, nodes, edges):
    forward = set(declaration["forward_edges"])
    retry = set(declaration["retry_edges"])
    terminals = sorted(declaration["terminal_rejections"], key=lambda item: item["rejected_edge"])
    rejected = {item["rejected_edge"] for item in terminals}
    terminal_paths = {eid for item in terminals for eid in item["termination_path"]}
    covered = forward | retry | rejected | terminal_paths
    overlaps = (forward & retry) | (retry & rejected) | (forward & rejected) | (retry & terminal_paths)
    original_retry = {eid for eid, edge in edges.items()
                      if edge["type"] == "retry" or edge.get("flow_role") == "retry" or edge["route"] == "back"}
    invalid_forward = forward & original_retry
    proof_edges = [edges[eid] for eid in sorted(forward - original_retry - retry)]
    invalid_retry = []
    for edge_id in sorted(retry):
        edge = edges[edge_id]
        existing_retry = edge_id in original_retry
        reachable = _reachable(edge["to"], edge["from"], proof_edges)
        if edge["from"] == edge["to"] or not existing_retry or not reachable:
            invalid_retry.append({"edge_id": edge_id, "existing_retry_semantics": existing_retry,
                                  "distinct_endpoints": edge["from"] != edge["to"], "forward_return_path_exists": reachable})
    invalid_terminal = []
    for item in terminals:
        edge, path = edges[item["rejected_edge"]], item["termination_path"]
        end = edges[path[-1]]["to"] if path else edge["to"]
        continuous, _ = _path(edge["to"], end, path, edges)
        if not continuous or nodes[end]["type"] != "end":
            invalid_terminal.append({"rejected_edge": item["rejected_edge"], "termination_path": path,
                                     "continuous": continuous, "target_is_end": nodes[end]["type"] == "end"})
    return {
        "approval/declared-roles": (not overlaps and not invalid_forward and covered == set(edges), {"unexplained_edges": sorted(set(edges) - covered), "role_overlap_edges": sorted(overlaps), "retry_edges_declared_forward": sorted(invalid_forward), "retry_count": len(retry), "terminal_rejection_count": len(terminals)}),
        "approval/return-reachability": (not invalid_retry, {"invalid_returns": invalid_retry, "forward_edges": sorted(forward)}),
        "approval/rejection-termination": (not invalid_terminal, {"invalid_terminations": invalid_terminal, "terminal_rejection_count": len(terminals)}),
    }


def _request(declaration, nodes, edges):
    requests = sorted(declaration["requests"], key=lambda item: item["request_edge"])
    declared = {item["request_edge"] for item in requests}
    actual = {eid for eid, edge in edges.items() if edge["type"] in {"call", "async"}}
    required_errors, participant_errors, path_errors = [], [], []
    for item in requests:
        request = edges[item["request_edge"]]
        responses = set(item["response_edges"])
        if item["response_required"] and not responses:
            required_errors.append(item["request_edge"])
        paths = {pair["response_edge"]: pair["path"] for pair in item["completion_paths"]}
        for response_id in sorted(responses):
            response = edges[response_id]
            response_semantics = response["type"] == "return" or response.get("flow_role") in {"response", "return"}
            owners_match = (nodes[response["from"]]["lane"] == nodes[request["to"]]["lane"]
                            and nodes[response["to"]]["lane"] == nodes[request["from"]]["lane"])
            if not response_semantics or not owners_match:
                participant_errors.append({"request_edge": item["request_edge"], "response_edge": response_id,
                                           "response_semantics": response_semantics, "owners_match": owners_match})
            good, _ = _path(request["to"], response["from"], paths[response_id], edges)
            if not good:
                path_errors.append({"request_edge": item["request_edge"], "response_edge": response_id,
                                    "path": paths[response_id], "reason": "discontinuous-or-wrong-target"})
        for response_id in sorted(set(paths) - responses):
            path_errors.append({"request_edge": item["request_edge"], "response_edge": response_id,
                                "reason": "path-without-declared-response"})
    return {
        "request/coverage": (declared == actual, {"undeclared_requests": sorted(actual - declared), "non_request_edges": sorted(declared - actual), "request_count": len(actual)}),
        "request/required-response": (not required_errors, {"required_without_response": required_errors}),
        "request/participant-pair": (not participant_errors, {"invalid_pairs": participant_errors}),
        "request/completion-path": (not path_errors, {"invalid_paths": path_errors}),
    }


def _fork(declaration, nodes, edges):
    fork, join = declaration["fork"], declaration["join"]
    branches = sorted(declaration["branches"], key=lambda item: item["branch_id"])
    invalid_entries, invalid_paths, invalid_members = [], [], []
    declared = {item["entry_edge"] for item in branches}
    outgoing = {eid for eid, edge in edges.items() if edge["from"] == fork}
    for branch in branches:
        entry, path = edges[branch["entry_edge"]], branch.get("join_path", [])
        if entry["from"] != fork:
            invalid_entries.append(branch["branch_id"])
        good, visited = _path(entry["to"], join, path, edges)
        if (branch["required"] or path) and not good:
            invalid_paths.append({"branch_id": branch["branch_id"], "join_path": path})
        if "branch_nodes" in branch:
            expected = set(visited) - {fork, join}
            declared_members = set(branch["branch_nodes"])
            absent, extra = expected - declared_members, declared_members - expected
            if absent or extra:
                invalid_members.append({"branch_id": branch["branch_id"], "missing_branch_nodes": sorted(absent),
                                        "unproven_branch_nodes": sorted(extra)})
    return {
        "fork/branches": (fork != join and len(branches) >= 2 and not invalid_entries and not invalid_members,
                          {"fork": fork, "join": join, "branch_count": len(branches), "invalid_entry_branches": invalid_entries, "invalid_membership": invalid_members}),
        "fork/required-join": (not invalid_paths, {"invalid_join_paths": invalid_paths}),
        "fork/coverage": (declared == outgoing, {"undeclared_fork_edges": sorted(outgoing - declared), "non_fork_entries": sorted(declared - outgoing)}),
    }


def _fanin(declaration, nodes, edges):
    target = declaration["target"]
    inputs = sorted(declaration["inputs"], key=lambda item: item["input_id"])
    sources = {item["source"] for item in inputs}
    invalid = []
    for item in inputs:
        path = item.get("path", [])
        good, _ = _path(item["source"], target, path, edges)
        if (item["required"] or path) and not good:
            invalid.append({"input_id": item["input_id"], "source": item["source"], "path": path})
    return {
        "fanin/required-path": (not invalid, {"invalid_input_paths": invalid}),
        "fanin/target": (len(sources) >= 2, {"target": target, "distinct_sources": sorted(sources)}),
        "fanin/coverage": (len(sources) == len(inputs), {"input_count": len(inputs), "distinct_source_count": len(sources),
                                                     "required_inputs": sorted(item["input_id"] for item in inputs if item["required"]),
                                                     "optional_inputs": sorted(item["input_id"] for item in inputs if not item["required"])}),
    }


def _lifecycle(declaration, nodes, edges):
    states, mapping = set(declaration["states"]), declaration["node_states"]
    allowed = {(item["from"], item["to"]) for item in declaration["allowed_transitions"]}
    invalid = [{"edge_id": eid, "from_state": mapping[edge["from"]], "to_state": mapping[edge["to"]]}
               for eid, edge in sorted(edges.items()) if (mapping[edge["from"]], mapping[edge["to"]]) not in allowed]
    return {
        "lifecycle/state-coverage": (bool(states) and set(mapping) == set(nodes), {"state_count": len(states), "mapped_node_count": len(mapping)}),
        "lifecycle/allowed-transition": (not invalid, {"undeclared_transitions": invalid}),
    }


PREDICATES = {"linear": _linear, "approval-loop": _approval, "request-response": _request,
              "fork-join": _fork, "fan-in": _fanin, "lifecycle": _lifecycle}


def assess_scope(scope, model, *, prerequisites_met=True):
    selected_nodes, selected_edges = set(scope["nodes"]), set(scope["edges"])
    nodes = {node["id"]: node for node in model["nodes"] if node["id"] in selected_nodes}
    edges = {edge["id"]: edge for edge in model["edges"] if edge["id"] in selected_edges}
    missing = _missing(scope, edges)
    if not prerequisites_met:
        status, results = "not_assessed", {}
    elif missing:
        status, results = "not_assessed", {}
    elif scope["pattern"] == "custom":
        status, results = "not_applicable", {}
    else:
        status, results = None, PREDICATES[scope["pattern"]](scope["declarations"], nodes, edges)
    rules = []
    for rule_id in sorted(RULE_IDS[scope["pattern"]]):
        good, evidence = results.get(rule_id, (False, {}))
        rule_status = status or ("passed" if good else "violated")
        if not prerequisites_met:
            evidence = {"reason": "general-validation-prerequisite-failed"}
        elif status == "not_applicable":
            evidence = {"reason": "custom-has-no-specific-pattern-rules"}
        code = ("semantic/pattern-violation" if rule_status == "violated"
                else "semantic/pattern-incomplete" if rule_status == "not_assessed" else None)
        rules.append({"scope_id": scope["id"], "rule_id": rule_id, "status": rule_status, "code": code,
                      "subject_ids": {"nodes": sorted(nodes), "edges": sorted(edges),
                                      "outcomes": sorted({edge["outcome"] for edge in edges.values() if edge.get("outcome")})},
                      "evidence": evidence, "missing_declarations": missing})
    return rules
