"""Versioned, explicitly supplied semantic context for read-only checks.

Context text is data. No embedded path, URL, instruction or exception declaration
causes a filesystem write, network access, metadata repair or automatic lookup.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

from . import contracts, context_native, group_rules, pattern_rules, provenance

MAX_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 32
MAX_SCOPES = 128
MAX_ID_REFERENCES = 20_000
PATTERNS = frozenset({"linear", "approval-loop", "request-response", "fork-join", "fan-in", "lifecycle", "custom"})
DECLARATION_FIELDS = {
    "linear": {"entry", "exit"},
    "approval-loop": {"forward_edges", "retry_edges", "terminal_rejections"},
    "request-response": {"requests"},
    "fork-join": {"fork", "join", "branches"},
    "fan-in": {"target", "inputs"},
    "lifecycle": {"states", "node_states", "allowed_transitions"},
    "custom": set(),
}


def _error(code, message, **evidence):
    raise contracts.DiagramError(message, code=code, subject={"kind": "semantic_context"}, evidence=evidence)


def _object(value, allowed, subject, required=()):
    contracts.require_mapping(value, subject)
    contracts.reject_unknown_fields(value, set(allowed), subject)
    missing = sorted(set(required) - set(value))
    if missing:
        _error("schema/required", "Required context fields are missing", subject=subject, fields=missing)
    return value


def _version(value, subject):
    if type(value) is not int:
        _error("schema/type", "Context component versions must be non-boolean integers", subject=subject)
    if value != 1:
        _error("context/version-unsupported", "Context component version is unsupported", subject=subject, version=value)


def _boolean(value, subject):
    if type(value) is not bool:
        _error("schema/type", "Declaration must be a boolean", subject=subject)


def _unique(values, subject):
    if len(values) != len(set(values)):
        _error("schema/duplicate", "Declaration IDs must be unique", subject=subject)


@dataclass(frozen=True)
class LoadedContext:
    data: dict
    sha256: str


class _InputValidator:
    def __init__(self):
        self.references = 0

    def sid(self, value, subject):
        self.references += 1
        if self.references > MAX_ID_REFERENCES:
            _error("context/resource-limit", "Context ID reference budget exceeded", limit=MAX_ID_REFERENCES)
        return contracts.validate_semantic_id(value, subject)

    def ids(self, value, subject):
        contracts.require_list(value, subject)
        for item in value:
            self.sid(item, subject)
        _unique(value, subject)

    def items(self, declaration, key, allowed, id_field=None):
        values = declaration.get(key, [])
        contracts.require_list(values, key)
        ids = []
        for item in values:
            _object(item, allowed, key + "[]")
            if id_field in item:
                ids.append(self.sid(item[id_field], key + "[]." + id_field))
        if id_field:
            _unique(ids, key + "[]." + id_field)
        return values

    def declarations(self, pattern, declaration):
        _object(declaration, DECLARATION_FIELDS[pattern], "declarations")
        if pattern == "linear":
            for key in ("entry", "exit"):
                if key in declaration:
                    self.sid(declaration[key], key)
        elif pattern == "approval-loop":
            for key in ("forward_edges", "retry_edges"):
                if key in declaration:
                    self.ids(declaration[key], key)
            for item in self.items(declaration, "terminal_rejections", {"rejected_edge", "termination_path"}, "rejected_edge"):
                if "termination_path" in item:
                    self.ids(item["termination_path"], "termination_path")
        elif pattern == "request-response":
            for item in self.items(declaration, "requests", {"request_edge", "response_required", "response_edges", "completion_paths"}, "request_edge"):
                if "response_required" in item:
                    _boolean(item["response_required"], "response_required")
                if "response_edges" in item:
                    self.ids(item["response_edges"], "response_edges")
                for pair in self.items(item, "completion_paths", {"response_edge", "path"}, "response_edge"):
                    if "path" in pair:
                        self.ids(pair["path"], "completion_paths[].path")
        elif pattern == "fork-join":
            for key in ("fork", "join"):
                if key in declaration:
                    self.sid(declaration[key], key)
            entries = []
            for item in self.items(declaration, "branches", {"branch_id", "entry_edge", "required", "join_path", "branch_nodes"}, "branch_id"):
                if "entry_edge" in item:
                    entries.append(self.sid(item["entry_edge"], "entry_edge"))
                if "required" in item:
                    _boolean(item["required"], "required")
                for key in ("join_path", "branch_nodes"):
                    if key in item:
                        self.ids(item[key], key)
            _unique(entries, "branches[].entry_edge")
        elif pattern == "fan-in":
            if "target" in declaration:
                self.sid(declaration["target"], "target")
            for item in self.items(declaration, "inputs", {"input_id", "source", "required", "path"}, "input_id"):
                if "source" in item:
                    self.sid(item["source"], "source")
                if "required" in item:
                    _boolean(item["required"], "required")
                if "path" in item:
                    self.ids(item["path"], "path")
        elif pattern == "lifecycle":
            if "states" in declaration:
                self.ids(declaration["states"], "states")
            if "node_states" in declaration:
                mapping = contracts.require_mapping(declaration["node_states"], "node_states")
                for node in sorted(mapping):
                    self.sid(node, "node_states node")
                    self.sid(mapping[node], "node_states state")
            pairs = []
            for item in self.items(declaration, "allowed_transitions", {"from", "to"}):
                for key in ("from", "to"):
                    if key in item:
                        self.sid(item[key], "allowed_transitions[]." + key)
                if "from" in item and "to" in item:
                    pairs.append((item["from"], item["to"]))
            _unique(pairs, "allowed_transitions")

    def validate(self, data):
        _object(data, {"context_version", "artifact", "patterns", "group_contracts", "provenance"},
                "context", ("context_version", "artifact") if isinstance(data, dict) and "provenance" in data else ("context_version", "artifact", "patterns"))
        _version(data["context_version"], "context_version")
        artifact = _object(data["artifact"], {"sha256", "schema_version", "model_hash_version", "model_hash"},
                           "artifact", ("sha256", "schema_version", "model_hash_version", "model_hash"))
        for key in ("schema_version", "model_hash_version", "sha256", "model_hash"):
            contracts.require_string(artifact[key], "artifact." + key)
        if (artifact["schema_version"] != contracts.V3_SCHEMA_VERSION
                or artifact["model_hash_version"] != contracts.MODEL_HASH_VERSION):
            _error("context/artifact-unsupported", "Context artifact schema or model-hash version is unsupported",
                   schema_version=artifact["schema_version"], model_hash_version=artifact["model_hash_version"])
        for key in ("sha256", "model_hash"):
            if not re.fullmatch(r"[0-9a-f]{64}", artifact[key]):
                _error("schema/type", "Context digests require 64 lowercase hexadecimal characters", field=key)
        if "patterns" in data:
            component = _object(data["patterns"], {"version", "scopes"}, "patterns", ("version", "scopes"))
            _version(component["version"], "patterns.version")
            scopes = contracts.require_list(component["scopes"], "patterns.scopes")
            if not scopes or len(scopes) > MAX_SCOPES:
                _error("context/resource-limit" if scopes else "schema/required",
                       "Patterns require between one and 128 scopes", count=len(scopes), limit=MAX_SCOPES)
            scope_ids = []
            for scope in scopes:
                fields = {"id", "role", "pattern", "nodes", "edges", "excluded_edges", "declarations"}
                _object(scope, fields, "scope", fields)
                scope_ids.append(self.sid(scope["id"], "scope.id"))
                if scope["role"] not in ("primary", "local"):
                    _error("schema/type", "Scope role must be primary or local")
                if not isinstance(scope["pattern"], str) or scope["pattern"] not in PATTERNS:
                    _error("context/version-unsupported", "Pattern is unsupported", pattern=scope["pattern"])
                self.ids(scope["nodes"], "scope.nodes")
                if not scope["nodes"]:
                    _error("schema/required", "Each scope needs at least one node", scope_id=scope["id"])
                self.ids(scope["edges"], "scope.edges")
                exclusions = self.items(scope, "excluded_edges", {"edge_id", "reason"}, "edge_id")
                for item in exclusions:
                    _object(item, {"edge_id", "reason"}, "excluded_edges[]", ("edge_id", "reason"))
                    reason = contracts.require_string(item["reason"], "excluded_edges[].reason")
                    if not reason.strip():
                        _error("schema/type", "An edge exclusion requires a nonblank reason")
                self.declarations(scope["pattern"], scope["declarations"])
            _unique(scope_ids, "scope.id")
            if sum(scope["role"] == "primary" for scope in scopes) != 1:
                _error("context/scope-incomplete", "Exactly one primary scope is required")
        if "group_contracts" in data:
            group_rules.validate_component(data["group_contracts"], self)
        if "provenance" in data:
            provenance.validate_provenance(data["provenance"], self)


def load_context(path: Path) -> LoadedContext:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    return decode_context(raw)


def decode_context(raw: bytes, *, validate=True) -> LoadedContext:
    if len(raw) > MAX_BYTES:
        _error("context/resource-limit", "Context file exceeds its byte budget", limit=MAX_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _error("input/json-invalid", "Context must be UTF-8 JSON", byte_offset=exc.start)
    # Bound nesting before parsing, so adversarial input cannot exhaust Python's
    # parser recursion limit. Brackets inside JSON strings are ordinary text.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                _error("context/resource-limit", "Context nesting exceeds its depth budget", limit=MAX_DEPTH)
        elif char in "]}":
            depth -= 1

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                safe_key = key.encode("unicode_escape").decode("ascii") if any(0xD800 <= ord(char) <= 0xDFFF for char in key) else key
                _error("context/duplicate-key", "Duplicate context JSON object key", key=safe_key)
            result[key] = value
        return result

    def constant(value):
        _error("input/json-invalid", "Context JSON numbers must be finite", value=value)

    data = json.loads(text, object_pairs_hook=unique_pairs, parse_constant=constant)
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, float) and not math.isfinite(value):
            constant(str(value))
        elif isinstance(value, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            _error("input/json-invalid", "Context JSON contains an unpaired Unicode surrogate")
    if validate:
        _InputValidator().validate(data)
    return LoadedContext(data=data, sha256=hashlib.sha256(raw).hexdigest())


def _check_references(scope, model):
    nodes = {node["id"] for node in model["nodes"]}
    edges = {edge["id"]: edge for edge in model["edges"]}
    selected_nodes, selected_edges = set(scope["nodes"]), set(scope["edges"])
    excluded = {item["edge_id"] for item in scope["excluded_edges"]}

    def refs(values, allowed, subject):
        invalid = sorted(set(values) - set(allowed))
        if invalid:
            _error("context/reference-invalid", "Context reference is absent or outside the declared scope",
                   scope_id=scope["id"], field=subject, ids=invalid)

    refs(selected_nodes, nodes, "nodes")
    refs(selected_edges | excluded, edges, "edges/excluded_edges")
    overlap = selected_edges & excluded
    if overlap:
        _error("context/scope-incomplete", "Selected and excluded edges overlap", scope_id=scope["id"], edge_ids=sorted(overlap))
    for edge_id in sorted(selected_edges):
        edge = edges[edge_id]
        refs((edge["from"], edge["to"]), selected_nodes, "edge endpoints")
    incident = {eid for eid, edge in edges.items() if edge["from"] in selected_nodes or edge["to"] in selected_nodes}
    if selected_edges | excluded != incident:
        _error("context/scope-incomplete", "Every incident edge must be selected or explicitly excluded",
               scope_id=scope["id"], missing_edges=sorted(incident - selected_edges - excluded),
               unrelated_exclusions=sorted(excluded - incident))
    declaration, pattern = scope["declarations"], scope["pattern"]

    def field(obj, key, allowed, *, many=False):
        if key in obj:
            refs(obj[key] if many else [obj[key]], allowed, key)

    if pattern == "linear":
        for key in ("entry", "exit"):
            field(declaration, key, selected_nodes)
    elif pattern == "approval-loop":
        for key in ("forward_edges", "retry_edges"):
            field(declaration, key, selected_edges, many=True)
        for item in declaration.get("terminal_rejections", []):
            field(item, "rejected_edge", selected_edges)
            field(item, "termination_path", selected_edges, many=True)
    elif pattern == "request-response":
        for item in declaration.get("requests", []):
            field(item, "request_edge", selected_edges)
            field(item, "response_edges", selected_edges, many=True)
            for pair in item.get("completion_paths", []):
                field(pair, "response_edge", selected_edges)
                field(pair, "path", selected_edges, many=True)
    elif pattern == "fork-join":
        for key in ("fork", "join"):
            field(declaration, key, selected_nodes)
        for item in declaration.get("branches", []):
            field(item, "entry_edge", selected_edges)
            field(item, "join_path", selected_edges, many=True)
            field(item, "branch_nodes", selected_nodes, many=True)
    elif pattern == "fan-in":
        field(declaration, "target", selected_nodes)
        for item in declaration.get("inputs", []):
            field(item, "source", selected_nodes)
            field(item, "path", selected_edges, many=True)
    elif pattern == "lifecycle":
        mapping = declaration.get("node_states", {})
        refs(mapping, selected_nodes, "node_states")
        if "states" in declaration:
            states = declaration["states"]
            refs(mapping.values(), states, "node_states state")
            for item in declaration.get("allowed_transitions", []):
                field(item, "from", states)
                field(item, "to", states)
    if scope["role"] == "primary" and pattern != model["behavior_pattern"]:
        _error("context/pattern-mismatch", "Primary pattern differs from the original diagram declaration",
               scope_id=scope["id"], declared=pattern, artifact=model["behavior_pattern"])


def _coverage(nodes, edges, total_nodes, total_edges, excluded=()):
    return {
        "node_count": len(nodes), "edge_count": len(edges),
        "total_nodes": len(total_nodes), "total_edges": len(total_edges),
        "extent": "whole_diagram" if set(nodes) == total_nodes and set(edges) == total_edges and not excluded else "partial",
    }


def assess_context(loaded: LoadedContext, tree, artifact_sha256: str, validation_result: dict, strict=False) -> dict:
    """Bind an already loaded context and assess only its explicit declarations."""
    model = context_native.original_model(tree)
    data, artifact = loaded.data, loaded.data["artifact"]
    if artifact["sha256"] != artifact_sha256:
        _error("context/artifact-mismatch", "Context artifact SHA-256 is stale", expected=artifact["sha256"], actual=artifact_sha256)
    actual = {"schema_version": model["schema_version"], "model_hash_version": model["model_hash_version"]}
    # This is the existing, verified hash, not a new normalization contract.
    pool = tree.getroot().find("./diagram/mxGraphModel/root/mxCell[@data-kind='pool']")
    actual["model_hash"] = pool.get(contracts.DATA_MODEL_HASH)
    if any(artifact[key] != actual[key] for key in actual):
        _error("context/model-mismatch", "Context model identity differs from the verified diagram", expected={key: artifact[key] for key in actual}, actual=actual)
    scopes = sorted(data.get("patterns", {}).get("scopes", []), key=lambda scope: scope["id"])
    for scope in scopes:
        _check_references(scope, model)
    if "group_contracts" in data:
        group_rules.check_references(data["group_contracts"], scopes, model)
    total_nodes = {node["id"] for node in model["nodes"]}
    total_edges = {edge["id"] for edge in model["edges"]}
    prerequisite_failure = (not validation_result["valid"] or bool(strict and validation_result["warnings"]))
    scope_results = []
    for scope in scopes:
        rules = pattern_rules.assess_scope(scope, model, prerequisites_met=not prerequisite_failure)
        coverage = _coverage(scope["nodes"], scope["edges"], total_nodes, total_edges, scope["excluded_edges"])
        coverage["excluded_edges"] = sorted(scope["excluded_edges"], key=lambda item: item["edge_id"])
        scope_results.append({"scope_id": scope["id"], "role": scope["role"], "pattern": scope["pattern"],
                              "gate": pattern_rules.gate(rules), "coverage": coverage, "rules": rules})
    all_rules = [rule for result in scope_results for rule in result["rules"]]
    selected_nodes = {sid for scope in scopes for sid in scope["nodes"]}
    selected_edges = {sid for scope in scopes for sid in scope["edges"]}
    coverage = _coverage(selected_nodes, selected_edges, total_nodes, total_edges,
                         [item for scope in scopes for item in scope["excluded_edges"]])
    coverage.update({"scope_count": len(scopes), "rule_status_counts": {
        status: sum(rule["status"] == status for rule in all_rules)
        for status in ("passed", "violated", "not_assessed", "not_applicable")}})
    result = {
        "context_version": data["context_version"], "context_sha256": loaded.sha256,
        "artifact_sha256": artifact_sha256, "artifact": actual,
        "components": {"patterns": ({"version": data["patterns"]["version"], "rule_version": pattern_rules.RULE_VERSION,
                                     "status": "provided"} if "patterns" in data else {"status": "not_provided"}),
                       "group_contracts": {"status": "not_provided"}, "provenance": {"status": "not_provided"}},
        "scope_results": scope_results, "gate": pattern_rules.gate(all_rules), "coverage": coverage,
    }
    if "group_contracts" in data:
        component = data["group_contracts"]
        group_results = group_rules.assess_groups(component, scopes, model, prerequisites_met=not prerequisite_failure)
        group_rule_results = [rule for group in group_results for rule in group["rules"]]
        result["components"]["group_contracts"] = {
            "version": component["version"], "rule_version": group_rules.RULE_VERSION,
            "status": "provided" if component["groups"] else "provided_empty",
        }
        result["group_results"] = group_results
        result["group_gate"] = pattern_rules.gate(group_rule_results)
        result["group_coverage"] = group_rules.summarize_coverage(group_results, model)
        result["gate"] = pattern_rules.gate(all_rules + group_rule_results)
    if "provenance" in data:
        result["components"]["provenance"] = {"version": 1, "rule_version": "1",
            "status": "provided" if any(data["provenance"][key] for key in ("sources", "facts", "bindings", "history")) else "provided_empty"}
        result.update(provenance.assess_provenance(data["provenance"], model))
    return result


def read_artifact(path: Path):
    """Read one byte snapshot; check raw identity before legacy validation."""
    raw = path.read_bytes()
    tree = context_native.parse_artifact(raw)
    context_native.original_model(tree)
    return tree, {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
