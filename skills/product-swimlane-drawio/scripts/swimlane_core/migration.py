"""Conservative, same-schema metadata plans for native managed drawings.

This private entry point reads original XML facts before using the tolerant
legacy readers. It never infers identity or invokes build/patch/routing.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import xml.etree.ElementTree as ET
from xml.parsers import expat

from . import contracts, document, metadata, routing_policy, spec_validation, validation


MIGRATION_RULE_VERSION = "1"
REPAIR_ATTRIBUTES = (
    contracts.DATA_LANE_ORDER, contracts.DATA_MODEL_HASH_VERSION,
    contracts.DATA_MODEL_HASH, contracts.DATA_TOOL_VERSION,
)
ELIGIBLE_CLASSES = frozenset({"not-needed", "automatic", "confirmation-required"})
V3_POOL_ATTRIBUTES = (
    contracts.DATA_BEHAVIOR_PATTERN, contracts.DATA_LAYOUT_PROFILE,
    contracts.DATA_PHASE_PRESENTATION, contracts.DATA_GROUPS,
)
V3_CELL_ATTRIBUTES = (
    contracts.DATA_SLOT, contracts.DATA_ANCHOR, contracts.DATA_GROUP_ID,
    contracts.DATA_FLOW_ROLE, contracts.DATA_OUTCOME, contracts.DATA_PRESENTATION,
)


def _diagnostic(code, message, *, cell=None, evidence=None, severity="error"):
    subject = ({"kind": cell.get(contracts.DATA_KIND, "cell"),
                "id": cell.get(contracts.DATA_SEMANTIC_ID, cell.get("id"))}
               if cell is not None else {"kind": "diagram"})
    return contracts.make_diagnostic(code, severity, message, subject=subject,
                                     evidence=evidence)


def _receipt(input_receipt=None):
    return {
        "operation": "migrate", "migration_rule_version": MIGRATION_RULE_VERSION,
        "producing_tool_version": contracts.TOOL_VERSION, "input": input_receipt,
        "source_managed_state": None, "classification": None, "can_write": False,
        "reasons": [], "semantic_summary": None, "planned_changes": [],
        "baseline_acceptance_required": False, "baseline_accepted": False,
        "written": False, "output": None,
        "validation": {"source": None, "projected": None, "serialized": None},
        "preservation": None, "delivery": None,
    }


def bytes_receipt(data: bytes, path: Path) -> dict:
    return {"path": str(path), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def parse_native_bytes(data: bytes) -> tuple[ET.ElementTree | None, list[dict]]:
    """Reject parser-loss payload before the legacy ElementTree conversion.

    Expat checks complete XML syntax, including prolog and epilog. Its default
    handler prevents expansion of custom text entities; external entities are
    never loaded. The migration parser rejects their declarations altogether.
    """
    payload = set()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.CommentHandler = lambda text: payload.add("comment")
    parser.ProcessingInstructionHandler = lambda target, text: payload.add("processing-instruction")
    parser.StartDoctypeDeclHandler = lambda *args: payload.add("doctype")
    parser.EntityDeclHandler = lambda *args: payload.add("entity-declaration")
    parser.StartNamespaceDeclHandler = lambda *args: payload.add("namespace-declaration")
    parser.DefaultHandler = lambda text: None
    parser.ExternalEntityRefHandler = lambda *args: 1
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ET.ParseError(str(exc)) from exc
    if payload:
        return None, [_diagnostic(
            "migration/unprotected-xml-payload",
            "Original XML contains payload that migration cannot preserve",
            evidence={"payload_kinds": sorted(payload)},
        )]
    return ET.ElementTree(ET.fromstring(data)), []


def _json_value(cell, attribute, expected_type):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON object key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")

    value = json.loads(cell.attrib[attribute], object_pairs_hook=unique_pairs,
                       parse_constant=reject_constant)
    if not isinstance(value, expected_type):
        raise ValueError(f"{attribute} must contain {expected_type.__name__}")
    return value


def _raw_document(tree):
    """Return original structure plus blockers, before dictionary readers."""
    reasons = []
    top = tree.getroot()
    diagrams = top.findall("diagram")
    models = diagrams[0].findall("mxGraphModel") if len(diagrams) == 1 else []
    roots = models[0].findall("root") if len(models) == 1 else []
    if (top.tag != "mxfile" or len(diagrams) != 1 or len(models) != 1
            or len(roots) != 1 or (diagrams[0].text or "").strip()):
        return None, None, [_diagnostic(
            "migration/unsupported-document",
            "Migration requires one uncompressed Draw.io page and graph root",
        )]
    root = roots[0]
    pools = [cell for cell in root if cell.tag == "mxCell"
             and cell.get(contracts.DATA_KIND) == "pool"]
    if not pools:
        return root, None, [_diagnostic(
            "migration/unsupported-document", "No compatible managed pool exists",
        )]
    pool = pools[0]

    def unsafe(message, cell=None, **evidence):
        reasons.append(_diagnostic("migration/unsafe-metadata", message,
                                   cell=cell, evidence=evidence))

    if len(pools) != 1:
        unsafe("Exactly one managed pool is required", count=len(pools))
    direct = set(root)
    native_ids = set()
    semantic_ids = set()
    all_cells = list(top.iter("mxCell"))
    for cell in all_cells:
        native_id = cell.get("id")
        kind, semantic_id = cell.get(contracts.DATA_KIND), cell.get(contracts.DATA_SEMANTIC_ID)
        if not native_id or native_id in native_ids:
            unsafe("Native cell IDs must exist and be unique", cell, cell_id=native_id)
        native_ids.add(native_id)
        if kind in contracts.MANAGED_KINDS:
            if cell not in direct:
                unsafe("Managed cells must be direct drawing-root entries", cell)
            try:
                contracts.validate_semantic_id(semantic_id, "managed semantic ID")
            except contracts.DiagramError as exc:
                unsafe("Managed semantic identity is missing or invalid", cell, cause=exc.diagnostic())
            key = kind, semantic_id
            if key in semantic_ids:
                unsafe("Same-kind semantic IDs must be unique", cell)
            semantic_ids.add(key)
            if not cell.get("parent"):
                unsafe("Managed cells require an original parent", cell)
            flag = "edge" if kind == "edge" else "vertex"
            other_flag = "vertex" if flag == "edge" else "edge"
            if cell.get(flag) != "1" or cell.get(other_flag) == "1":
                unsafe("Native cell flags contradict the managed kind", cell)
        elif kind is not None or semantic_id is not None or cell.get("vertex") == "1" or cell.get("edge") == "1":
            reasons.append(_diagnostic(
                "migration/unsupported-drawing-content",
                "Drawing content needs semantic adoption outside migration", cell=cell,
                evidence={"cell_id": native_id},
            ))
    # A wrapper's identity is native even when its child mxCell has no ID.
    if any(entry.tag in {"object", "UserObject"} and entry.find("mxCell") is not None for entry in root):
        reasons.append(_diagnostic("migration/unsupported-document",
                                   "Managed native wrappers are outside migration support"))
    by_native_id = {cell.get("id"): cell for cell in all_cells}
    current, seen = pool, set()
    while current.get("parent"):
        parent_id = current.get("parent")
        parent = by_native_id.get(parent_id)
        if (parent is None or parent_id in seen or parent not in direct
                or parent.get(contracts.DATA_KIND) is not None
                or parent.get("vertex") == "1" or parent.get("edge") == "1"):
            unsafe("Pool parent chain is missing, cyclic or not a native layer", pool)
            break
        seen.add(parent_id)
        current = parent
    return root, pool, reasons


def _raw_metadata(root, pool):
    reasons = []

    def unsafe(message, cell=None, **evidence):
        reasons.append(_diagnostic("migration/unsafe-metadata", message,
                                   cell=cell, evidence=evidence))

    schema = pool.get(contracts.DATA_SCHEMA_VERSION, "1")
    if schema not in {"1", *contracts.STRUCTURED_SCHEMA_VERSIONS}:
        reasons.append(_diagnostic("migration/schema-unsupported",
                                   "Schema version is unsupported", cell=pool,
                                   evidence={"schema_version": schema}))
    hash_version = pool.get(contracts.DATA_MODEL_HASH_VERSION)
    if hash_version not in {None, contracts.MODEL_HASH_VERSION}:
        reasons.append(_diagnostic("migration/hash-version-unsupported",
                                   "Model hash version is unsupported", cell=pool,
                                   evidence={"model_hash_version": hash_version}))
    if contracts.DATA_MODEL_HASH in pool.attrib and not pool.get(contracts.DATA_MODEL_HASH):
        unsafe("An empty stored model hash is not an absent historical hash", pool)
    managed = [cell for cell in root if cell.tag == "mxCell"
               and cell.get(contracts.DATA_KIND) in contracts.MANAGED_KINDS]
    lanes = {cell.get(contracts.DATA_SEMANTIC_ID): cell for cell in managed
             if cell.get(contracts.DATA_KIND) == "lane"}
    nodes = {cell.get(contracts.DATA_SEMANTIC_ID): cell for cell in managed
             if cell.get(contracts.DATA_KIND) == "node"}
    if not lanes:
        unsafe("A managed diagram requires at least one lane", pool)
    required = {
        "node": (contracts.DATA_LANE_ID, contracts.DATA_RANK, contracts.DATA_NODE_TYPE),
        "edge": ("source", "target", contracts.DATA_FROM, contracts.DATA_TO,
                 contracts.DATA_EDGE_TYPE, contracts.DATA_ROUTE),
        "phase": (contracts.DATA_FROM_RANK, contracts.DATA_TO_RANK),
    }
    enums = {
        contracts.DATA_NODE_TYPE: contracts.NODE_TYPES,
        contracts.DATA_EDGE_TYPE: spec_validation.EDGE_TYPES,
        contracts.DATA_ROUTE: routing_policy.ROUTE_CLASSES,
        contracts.DATA_BRANCH: routing_policy.BRANCH_CLASSES,
        contracts.DATA_SLOT: spec_validation.SLOT_CLASSES,
        contracts.DATA_FLOW_ROLE: spec_validation.FLOW_ROLES,
        contracts.DATA_BEHAVIOR_PATTERN: spec_validation.BEHAVIOR_PATTERNS,
        contracts.DATA_LAYOUT_PROFILE: spec_validation.LAYOUT_PROFILES,
        contracts.DATA_PHASE_PRESENTATION: spec_validation.PHASE_PRESENTATIONS,
        contracts.DATA_PRESENTATION: spec_validation.PHASE_PRESENTATIONS,
    }
    for cell in managed:
        kind = cell.get(contracts.DATA_KIND)
        missing = [name for name in required.get(kind, ()) if not cell.get(name)]
        if missing:
            unsafe("Original semantic fields are missing or empty", cell, attributes=missing)
        for name, values in enums.items():
            if name in cell.attrib and cell.get(name) not in values:
                unsafe("Original metadata has an unsupported enum value", cell,
                       attribute=name, value=cell.get(name), allowed=sorted(values))
        for name in (contracts.DATA_RANK, contracts.DATA_FROM_RANK, contracts.DATA_TO_RANK):
            if name in cell.attrib:
                try:
                    value = int(cell.attrib[name])
                    if value < 1:
                        raise ValueError("rank below one")
                except ValueError:
                    unsafe("Original rank must be an integer greater than zero", cell,
                           attribute=name, value=cell.get(name))
        if kind in {"lane", "phase", "edge"} and cell.get("parent") != pool.get("id"):
            unsafe("Managed parent does not match the pool", cell)
        if kind == "node":
            lane = lanes.get(cell.get(contracts.DATA_LANE_ID))
            if lane is None or cell.get("parent") != lane.get("id"):
                unsafe("Original node lane ID and native parent disagree", cell,
                       lane_id=cell.get(contracts.DATA_LANE_ID), parent=cell.get("parent"))
        if kind == "edge":
            for semantic, native in ((contracts.DATA_FROM, "source"), (contracts.DATA_TO, "target")):
                node = nodes.get(cell.get(semantic))
                if node is None or cell.get(native) != node.get("id"):
                    unsafe("Original semantic and native edge endpoints disagree", cell,
                           attribute=semantic, value=cell.get(semantic), native_value=cell.get(native))
        geometries = cell.findall("mxGeometry")
        geometry_bindings = [child for child in cell if child.get("as") == "geometry"]
        if (len(geometries) != 1 or geometries[0].get("as") != "geometry"
                or len(geometry_bindings) != 1 or geometry_bindings[0] is not geometries[0]):
            unsafe("Native geometry must have one unambiguous geometry binding", cell,
                   geometry_count=len(geometries),
                   geometry_as=[geom.get("as") for geom in geometries],
                   binding_tags=[child.tag for child in geometry_bindings])
        else:
            geom = geometries[0]
            native_geometry = [geom, *geom.findall("./Array[@as='points']/mxPoint")]
            native_geometry.extend(child for child in geom if
                                   (child.tag == "mxPoint" and child.get("as") in
                                    {"offset", "sourcePoint", "targetPoint"})
                                   or (child.tag == "mxRectangle" and child.get("as") == "alternateBounds"))
            for element in native_geometry:
                for name in ("x", "y", "width", "height"):
                    if name not in element.attrib:
                        continue
                    try:
                        if not math.isfinite(float(element.get(name))):
                            raise ValueError("nonfinite")
                    except ValueError:
                        unsafe("Native geometry requires finite numeric coordinates", cell,
                               element=element.tag, attribute=name, value=element.get(name))
            if kind != "edge":
                for name in ("width", "height"):
                    try:
                        if float(geom.get(name, "0")) <= 0:
                            raise ValueError("nonpositive")
                    except ValueError:
                        unsafe("Native vertex dimensions must be present and positive", cell, attribute=name)
            if len(geom.findall("./Array[@as='points']")) > 1 or len(geom.findall("./mxPoint[@as='offset']")) > 1:
                unsafe("Native points or label offsets are ambiguous", cell)
            for point in geom.findall("./Array[@as='points']/mxPoint"):
                if "x" not in point.attrib or "y" not in point.attrib:
                    unsafe("Native route point coordinates are missing", cell)
        style = document.style_values(cell.get("style", ""))
        for name in ("exitX", "exitY", "exitDx", "exitDy", "entryX", "entryY", "entryDx", "entryDy"):
            if name in style:
                try:
                    if not math.isfinite(float(style[name])):
                        raise ValueError("nonfinite")
                except ValueError:
                    unsafe("Native port coordinates must be finite", cell, style_key=name, value=style[name])
        if schema != contracts.V3_SCHEMA_VERSION:
            unexpected = [name for name in (*V3_POOL_ATTRIBUTES, *V3_CELL_ATTRIBUTES)
                          if name in cell.attrib]
            if unexpected:
                unsafe("Newer-schema metadata cannot be interpreted under this schema", cell,
                       attributes=unexpected, schema_version=schema)
    if schema in contracts.STRUCTURED_SCHEMA_VERSIONS and contracts.DATA_MAIN_PATH not in pool.attrib:
        unsafe("Structured schemas require an original main path", pool)
    if schema == contracts.V3_SCHEMA_VERSION:
        missing = [name for name in V3_POOL_ATTRIBUTES if name not in pool.attrib]
        if missing:
            unsafe("Schema v3 requires original pool metadata", pool, attributes=missing)
    for cell in managed:
        for name, expected_type in ((contracts.DATA_LANE_ORDER, list), (contracts.DATA_MAIN_PATH, list),
                                    (contracts.DATA_GROUPS, list), (contracts.DATA_ANCHOR, dict)):
            if name not in cell.attrib:
                continue
            try:
                value = _json_value(cell, name, expected_type)
                if name in {contracts.DATA_LANE_ORDER, contracts.DATA_MAIN_PATH}:
                    contracts.validate_id_list(value, name)
                if name == contracts.DATA_LANE_ORDER and set(value) != set(lanes):
                    raise ValueError("lane order does not match all original lanes")
                if name == contracts.DATA_MAIN_PATH and any(node not in nodes for node in value):
                    raise ValueError("main path references a missing original node")
            except (ValueError, TypeError) as exc:
                unsafe("Original JSON metadata is invalid", cell, attribute=name, cause=str(exc))
        if contracts.DATA_OUTCOME in cell.attrib:
            try:
                contracts.validate_semantic_id(cell.get(contracts.DATA_OUTCOME), "edge outcome")
            except contracts.DiagramError as exc:
                unsafe("Original outcome identity is invalid", cell, cause=exc.diagnostic())
    return reasons


def _semantic_checks(tree, root, pool, model):
    """Check mirrored populations and optional intent without compiling a spec."""
    lanes, nodes = document.lane_node_records(root, pool)
    edge_records = document.edge_records(root)
    phase_records = document.phase_records(root, pool)
    for kind, records in (("lane", lanes), ("node", nodes), ("edge", edge_records), ("phase", phase_records)):
        original = [cell for cell in tree.getroot().iter("mxCell")
                    if cell.get(contracts.DATA_KIND) == kind]
        if len(original) != len(records) or {cell.get(contracts.DATA_SEMANTIC_ID) for cell in original} != set(records):
            raise ValueError(f"Original {kind} population differs from the semantic reader")
    if model["schema_version"] == contracts.V3_SCHEMA_VERSION:
        metadata.managed_groups_attribute(pool, lanes, nodes)
        by_node = {node["id"]: node for node in model["nodes"]}
        for node in model["nodes"]:
            # Only validate optional intent. Native geometry stays authoritative.
            if "slot" in node or "anchor" in node:
                spec_validation.validate_node_object(node, "managed node")
            anchor = node.get("anchor")
            if anchor is not None:
                target = by_node.get(anchor["node"])
                if (target is None or target["id"] == node["id"]
                        or (target["lane"], target["rank"]) != (node["lane"], node["rank"])
                        or ("slot" in node and node["slot"] != anchor["side"])):
                    raise ValueError("Original note anchor contradicts its node/lane/rank/slot")


def _apply_exact_changes(tree, changes):
    candidate = copy.deepcopy(tree)
    pool = document.find_pool(candidate)
    seen = set()
    for change in changes:
        name = change["attribute"]
        if (name not in REPAIR_ATTRIBUTES or name in seen or change["cell_id"] != pool.get("id")
                or change["kind"] != "pool" or change["semantic_id"] != pool.get(contracts.DATA_SEMANTIC_ID)
                or change["old_present"] != (name in pool.attrib)
                or change["old_value"] != pool.get(name)):
            raise contracts.DiagramError("Migration plan no longer matches the original pool",
                                         code="delivery/candidate-preservation-failed")
        seen.add(name)
        pool.set(name, change["new_value"])
    return candidate


def _plan_tree(tree, report, accept_unverified_baseline):
    root, pool, raw_reasons = _raw_document(tree)
    report["reasons"].extend(raw_reasons)
    if pool is None:
        report["classification"] = "unsupported"
        return report
    report["reasons"].extend(_raw_metadata(root, pool))
    unsafe_codes = {"migration/unsafe-metadata", "migration/schema-unsupported", "migration/hash-version-unsupported"}
    unsafe = any(reason["code"] in unsafe_codes for reason in report["reasons"])
    # Unsafe raw facts cannot be certified by the tolerant reader. Reporting a
    # null legacy state is more accurate than inventing its validator result.
    if unsafe:
        report["classification"] = "unsafe"
        return report
    try:
        model = metadata.semantic_model_document(tree)
        _semantic_checks(tree, root, pool, model)
    except (contracts.DiagramError, KeyError, TypeError, ValueError) as exc:
        report["classification"] = "unsafe"
        report["reasons"].append(_diagnostic("migration/unsafe-metadata",
                                            "Original semantic model is inconsistent",
                                            evidence={"cause": str(exc)}))
        return report
    report["semantic_summary"] = model
    source_validation = validation.validate_tree(tree)
    report["validation"]["source"] = source_validation
    report["source_managed_state"] = source_validation.get("managed_state")
    integrity_errors = [item for item in source_validation["diagnostics"]
                        if item["severity"] == "error"
                        and (item["code"].startswith("integrity/") or item["code"].startswith("semantic/main-path"))]
    if integrity_errors:
        report["reasons"].append(_diagnostic("migration/unsafe-metadata",
                                            "Original semantic structure is invalid",
                                            evidence={"diagnostics": integrity_errors}))
    stored_hash = pool.get(contracts.DATA_MODEL_HASH)
    computed_hash = metadata.semantic_model_hash(tree)
    if stored_hash is not None and stored_hash != computed_hash:
        report["reasons"].append(_diagnostic("migration/model-hash-mismatch",
                                            "Stored model hash differs from original semantics",
                                            evidence={"stored": stored_hash, "computed": computed_hash}))
    if integrity_errors or (stored_hash is not None and stored_hash != computed_hash):
        report["classification"] = "unsafe"
        return report
    if report["reasons"]:
        report["classification"] = "unsupported"
        return report
    targets = {}
    if contracts.DATA_LANE_ORDER not in pool.attrib:
        targets[contracts.DATA_LANE_ORDER] = json.dumps([lane["id"] for lane in model["lanes"]],
                                                        ensure_ascii=True, separators=(",", ":"))
    if contracts.DATA_MODEL_HASH_VERSION not in pool.attrib:
        targets[contracts.DATA_MODEL_HASH_VERSION] = contracts.MODEL_HASH_VERSION
    if stored_hash is None:
        targets[contracts.DATA_MODEL_HASH] = computed_hash
    if not targets:
        report["classification"] = "not-needed"
        report["reasons"].append(_diagnostic("migration/not-needed", "No metadata repair is needed", severity="info"))
        return report
    if pool.get(contracts.DATA_TOOL_VERSION) != contracts.TOOL_VERSION:
        targets[contracts.DATA_TOOL_VERSION] = contracts.TOOL_VERSION
    needs_acceptance = stored_hash is None
    report["classification"] = "confirmation-required" if needs_acceptance else "automatic"
    report["baseline_acceptance_required"] = needs_acceptance
    report["baseline_accepted"] = bool(needs_acceptance and accept_unverified_baseline)
    for attribute in REPAIR_ATTRIBUTES:
        if attribute in targets:
            report["planned_changes"].append({
                "cell_id": pool.get("id"), "kind": "pool",
                "semantic_id": pool.get(contracts.DATA_SEMANTIC_ID), "attribute": attribute,
                "old_present": attribute in pool.attrib, "old_value": pool.get(attribute),
                "new_value": targets[attribute], "reason": "migration/metadata-repair",
            })
    report["reasons"].append(_diagnostic(
        "migration/metadata-repair", "Only the listed original pool attributes need repair",
        cell=pool, evidence={"attributes": list(targets)}, severity="info"))
    if needs_acceptance:
        report["reasons"].append(_diagnostic(
            "migration/baseline-unverified", "A missing historical hash cannot prove that past semantics were unchanged",
            cell=pool, severity="warning"))
    candidate = _apply_exact_changes(tree, report["planned_changes"])
    if metadata.semantic_model_document(candidate) != model:
        raise contracts.DiagramError("Metadata repair changed the semantic snapshot",
                                     code="delivery/candidate-preservation-failed")
    projected = validation.validate_tree(candidate)
    report["validation"]["projected"] = projected
    report["can_write"] = bool(projected["quality_gate_passed"]
                                and (not needs_acceptance or accept_unverified_baseline))
    return report


def plan_migration(data: bytes, *, input_receipt=None, accept_unverified_baseline=False) -> dict:
    """Plan from original bytes without writing XML or modifying any input."""
    report = _receipt(input_receipt)
    tree, reasons = parse_native_bytes(data)
    if reasons:
        report.update(classification="unsupported", reasons=reasons)
        return report
    return _plan_tree(tree, report, accept_unverified_baseline)


def _read_source(path):
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError("Migration input must be a regular file")
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise OSError("Migration input must be a regular file")
        data = handle.read()
        after = os.fstat(handle.fileno())
    identity = (before.st_dev, before.st_ino)
    if (identity != (after.st_dev, after.st_ino)
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise contracts.DiagramError("Input changed while being read", code="delivery/input-changed")
    return data, bytes_receipt(data, path), identity


def _check_sha(receipt, expected, *, required):
    if not expected and required:
        raise contracts.DiagramError("Migration write requires a reviewed input SHA-256",
                                     code="delivery/input-sha256-required")
    if expected is not None and receipt["sha256"] != expected:
        raise contracts.DiagramError("Input differs from the reviewed SHA-256 baseline",
                                     code="delivery/input-sha256-mismatch",
                                     evidence={"expected": expected, "actual": receipt["sha256"]})


def _migration_failure(report, exc, *, exit_code=2):
    if isinstance(exc, contracts.DiagramError):
        diagnostic = exc.diagnostic()
    elif isinstance(exc, ET.ParseError):
        diagnostic = _diagnostic("input/drawio-xml-invalid", str(exc))
    elif isinstance(exc, OSError):
        diagnostic = _diagnostic("delivery/io-error", str(exc))
    else:
        diagnostic = _diagnostic("internal/unexpected", "Unexpected internal migration error",
                                 evidence={"exception_type": type(exc).__name__})
        exit_code = 3
    report["reasons"].append(diagnostic)
    report["can_write"] = False
    if isinstance(exc, contracts.DiagramError) and "delivery" in exc.evidence:
        report["delivery"] = exc.evidence["delivery"]
    elif getattr(exc, "_migration_delivery", None) is not None:
        report["delivery"] = exc._migration_delivery
    return report, exit_code


def dry_run(input_path: Path, *, expected_input_sha256=None, accept_unverified_baseline=False):
    """File adapter for a read-only plan; errors retain available input identity."""
    report = _receipt()
    try:
        data, input_receipt, _ = _read_source(input_path)
        report["input"] = input_receipt
        _check_sha(input_receipt, expected_input_sha256, required=False)
        report = plan_migration(data, input_receipt=input_receipt,
                                accept_unverified_baseline=accept_unverified_baseline)
        return report, 0
    except Exception as exc:
        return _migration_failure(report, exc)


def _resolved_migration_path(path):
    try:
        return path.resolve()
    except RuntimeError as exc:
        raise contracts.DiagramError("Unable to resolve migration path",
                                     code="delivery/io-error", evidence={"path": str(path)}) from exc


def _check_new_output(input_path, output_path):
    same = _resolved_migration_path(input_path) == _resolved_migration_path(output_path)
    if not same and input_path.exists() and output_path.exists():
        same = os.path.samefile(input_path, output_path)
    if same:
        raise contracts.DiagramError("Migration input and output refer to the same file",
                                     code="delivery/input-output-alias",
                                     evidence={"input": str(input_path), "output": str(output_path)})
    if os.path.lexists(output_path):
        raise contracts.DiagramError("Migration output already exists",
                                     code="delivery/output-exists", evidence={"output": str(output_path)},
                                     supported_fixes=["choose-new-output"])


def _verify_source_current(input_path, original_receipt, original_identity):
    try:
        _, current, identity = _read_source(input_path)
    except (OSError, contracts.DiagramError) as exc:
        raise contracts.DiagramError("Migration input changed before publication",
                                     code="delivery/input-changed", evidence={"cause": str(exc)}) from exc
    if current["sha256"] != original_receipt["sha256"] or identity != original_identity:
        raise contracts.DiagramError(
            "Migration input changed before publication", code="delivery/input-changed",
            evidence={"expected_sha256": original_receipt["sha256"], "actual_sha256": current["sha256"],
                      "object_identity_matches": identity == original_identity},
        )


def _signature_differences(expected, actual, path="/"):
    """Explain full-tree differences using the established formatting boundary."""
    differences = []
    if expected[0] != actual[0]:
        differences.append({"path": path, "field": "tag", "expected": expected[0], "actual": actual[0]})
    left, right = dict(expected[1]), dict(actual[1])
    for name in sorted(left.keys() | right.keys()):
        if (name in left) != (name in right) or left.get(name) != right.get(name):
            differences.append({"path": path, "field": "attribute", "attribute": name,
                                "expected_present": name in left, "expected": left.get(name),
                                "actual_present": name in right, "actual": right.get(name)})
    for index, field in ((2, "text"), (3, "tail")):
        if expected[index] != actual[index]:
            differences.append({"path": path, "field": field,
                                "expected": expected[index], "actual": actual[index]})
    left_children, right_children = expected[4], actual[4]
    for index in range(max(len(left_children), len(right_children))):
        child_path = path.rstrip("/") + f"/child[{index}]"
        if index >= len(left_children):
            differences.append({"path": child_path, "field": "child", "change": "unexpected",
                                "actual_tag": right_children[index][0]})
        elif index >= len(right_children):
            differences.append({"path": child_path, "field": "child", "change": "missing",
                                "expected_tag": left_children[index][0]})
        else:
            differences.extend(_signature_differences(left_children[index], right_children[index], child_path))
    return differences


def _compare_migration_tree(before_data, after):
    # Recompute from the original bytes, never from a caller's receipt. Keep
    # expected construction independent of the migration writer's apply helper.
    plan = plan_migration(before_data)
    if plan["classification"] not in ELIGIBLE_CLASSES:
        raise contracts.DiagramError("Before input is not eligible for migration comparison",
                                     code="migration/input-unsafe" if plan["classification"] == "unsafe"
                                     else "migration/input-unsupported",
                                     evidence={"classification": plan["classification"], "reasons": plan["reasons"]})
    original, _ = parse_native_bytes(before_data)
    expected = copy.deepcopy(original)
    pool = document.find_pool(expected)
    for change in plan["planned_changes"]:
        pool.set(change["attribute"], change["new_value"])
    differences = _signature_differences(document.serialization_signature(expected),
                                         document.serialization_signature(after))
    return {"preserved": not differences, "differences": differences,
            "classification": plan["classification"], "planned_changes": plan["planned_changes"]}


def compare_migration_files(before_path: Path, after_path: Path):
    """Compare actual bytes to a recomputed plan, without accepting a baseline.

    A legal comparison proves exact preservation only. Quality blockers in the
    source or projected metadata do not change that comparison's meaning.
    """
    report = {
        "operation": "compare-migration", "migration_rule_version": MIGRATION_RULE_VERSION,
        "producing_tool_version": contracts.TOOL_VERSION, "before": None, "after": None,
        "classification": None, "planned_changes": [], "preserved": False,
        "differences": [], "reasons": [],
    }
    try:
        before_data, before_receipt, _ = _read_source(before_path)
        report["before"] = before_receipt
        plan = plan_migration(before_data)
        report["classification"] = plan["classification"]
        report["planned_changes"] = plan["planned_changes"]
        report["reasons"].extend(plan["reasons"])
        after_data, after_receipt, _ = _read_source(after_path)
        report["after"] = after_receipt
        if plan["classification"] not in ELIGIBLE_CLASSES:
            raise contracts.DiagramError(
                "Before input is not eligible for migration comparison",
                code="migration/input-unsafe" if plan["classification"] == "unsafe"
                else "migration/input-unsupported",
                evidence={"classification": plan["classification"]},
            )
        after, problems = parse_native_bytes(after_data)
        if problems:
            report["reasons"].extend(problems)
            report["differences"] = [{"field": "xml-payload", "diagnostic": item} for item in problems]
            return report, 1
        report.update(_compare_migration_tree(before_data, after))
        return report, 0 if report["preserved"] else 1
    except Exception as exc:
        if isinstance(exc, contracts.DiagramError):
            diagnostic = exc.diagnostic()
        elif isinstance(exc, ET.ParseError):
            diagnostic = _diagnostic("input/drawio-xml-invalid", str(exc))
        elif isinstance(exc, OSError):
            diagnostic = _diagnostic("delivery/io-error", str(exc))
        else:
            diagnostic = _diagnostic("internal/unexpected", "Unexpected internal migration comparison error",
                                     evidence={"exception_type": type(exc).__name__})
            report["reasons"].append(diagnostic)
            return report, 3
        report["reasons"].append(diagnostic)
        return report, 2


def _validate_migration_candidate(before_data, accepted, candidate, semantic_snapshot):
    signature_matches = document.serialization_signature(accepted) == document.serialization_signature(candidate)
    comparison = _compare_migration_tree(before_data, candidate)
    if not signature_matches or not comparison["preserved"]:
        raise contracts.DiagramError(
            "Candidate differs from the exact migration plan", code="delivery/candidate-preservation-failed",
            evidence={"accepted_signature_matches": signature_matches, "comparison": comparison},
        )
    if metadata.semantic_model_document(candidate) != semantic_snapshot:
        raise contracts.DiagramError("Candidate changed the same-schema semantic snapshot",
                                     code="delivery/candidate-preservation-failed")
    result = validation.validate_tree(candidate)
    if not result["quality_gate_passed"]:
        raise contracts.DiagramError("Migration candidate failed strict validation",
                                     code="delivery/strict-validation-failed",
                                     evidence={"validation": result, "comparison": comparison})
    return result, comparison


def migrate_file(input_path: Path, output_path: Path, *, expected_input_sha256=None,
                 accept_unverified_baseline=False):
    """Deliver only an exact, strict, current-input migration to a new path."""
    report = _receipt()
    validation_stage = "projected"
    try:
        _check_new_output(input_path, output_path)
        resolved_parent = _resolved_migration_path(output_path.parent)
        target = resolved_parent / output_path.name
        data, input_receipt, input_identity = _read_source(input_path)
        report["input"] = input_receipt
        _check_sha(input_receipt, expected_input_sha256, required=True)
        report = plan_migration(data, input_receipt=input_receipt,
                                accept_unverified_baseline=accept_unverified_baseline)
        classification = report["classification"]
        if classification not in ELIGIBLE_CLASSES:
            raise contracts.DiagramError(
                "Input cannot be migrated under the current rules",
                code="migration/input-unsafe" if classification == "unsafe" else "migration/input-unsupported",
                evidence={"classification": classification},
            )
        if report["baseline_acceptance_required"] and not report["baseline_accepted"]:
            raise contracts.DiagramError(
                "Explicit acceptance of the current unverified baseline is required",
                code="migration/baseline-acceptance-required",
            )
        if classification == "not-needed":
            return report, 0
        tree, _ = parse_native_bytes(data)
        accepted = _apply_exact_changes(tree, report["planned_changes"])
        projected, preservation = _validate_migration_candidate(data, accepted, accepted, report["semantic_summary"])
        report["validation"]["projected"] = projected
        report["preservation"] = preservation
        candidate_identity = {}

        def read_candidate(path):
            raw = path.read_bytes()
            try:
                candidate, problems = parse_native_bytes(raw)
            except ET.ParseError as exc:
                raise contracts.DiagramError(
                    "Serialized migration candidate is malformed XML",
                    code="delivery/candidate-preservation-failed", evidence={"cause": str(exc)},
                ) from exc
            if problems:
                raise contracts.DiagramError(
                    "Serialized migration candidate contains unprotected XML payload",
                    code="delivery/candidate-preservation-failed", evidence={"diagnostics": problems},
                )
            candidate_identity.update(bytes_receipt(raw, target))
            return candidate

        def check_candidate(candidate):
            result, comparison = _validate_migration_candidate(data, accepted, candidate, report["semantic_summary"])
            report["validation"]["serialized"] = result
            report["preservation"] = comparison

        def check_before_commit(path, output_receipt):
            current_candidate = bytes_receipt(path.read_bytes(), target)
            if output_receipt != candidate_identity or current_candidate != candidate_identity:
                raise contracts.DiagramError(
                    "Serialized candidate bytes changed after validation",
                    code="delivery/candidate-preservation-failed",
                )
            if _resolved_migration_path(output_path.parent) != resolved_parent:
                raise contracts.DiagramError("Output parent changed before publication",
                                             code="delivery/io-error",
                                             evidence={"reason": "output-parent-changed", "output": str(output_path)})
            _check_new_output(input_path, output_path)
            # This is the last source observation, not a cross-file editor lock.
            _verify_source_current(input_path, input_receipt, input_identity)

        validation_stage = "serialized"
        delivery = document.write_tree(
            accepted, target, candidate_reader=read_candidate, candidate_check=check_candidate,
            before_commit=check_before_commit, overwrite=False,
        )
        report["written"] = delivery["committed"]
        report["output"] = {**delivery["output"], "path": str(output_path)}
        report["delivery"] = delivery
        report["reasons"].extend(delivery["diagnostics"])
        return report, 0
    except Exception as exc:
        code = 1 if isinstance(exc, contracts.DiagramError) and exc.code in {
            "delivery/candidate-preservation-failed", "delivery/strict-validation-failed",
        } else 2
        if isinstance(exc, contracts.DiagramError):
            if "comparison" in exc.evidence:
                report["preservation"] = exc.evidence["comparison"]
            elif exc.code == "delivery/candidate-preservation-failed":
                report["preservation"] = {"preserved": False, "differences": [exc.evidence]}
            if "validation" in exc.evidence:
                report["validation"][validation_stage] = exc.evidence["validation"]
        return _migration_failure(report, exc, exit_code=code)
