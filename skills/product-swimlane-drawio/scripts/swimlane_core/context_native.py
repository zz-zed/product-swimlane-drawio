"""Read original native facts for opt-in context checks, without repairing them.

The tolerant legacy reader remains unchanged. This read-only boundary checks
identity, ownership and native geometry before exposing a semantic model.
"""
from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from xml.parsers import expat

from . import contracts, document, metadata


def _fail(message, *, unsupported=False, **evidence):
    raise contracts.DiagramError(
        message, code="context/artifact-unsupported" if unsupported else "context/artifact-invalid",
        subject={"kind": "diagram"}, evidence=evidence,
    )


def parse_artifact(data: bytes) -> ET.ElementTree:
    """Reject entity declarations before ElementTree can expand their text."""
    parser = expat.ParserCreate()
    forbidden = set()
    parser.StartDoctypeDeclHandler = lambda *args: forbidden.add("doctype")
    parser.EntityDeclHandler = lambda *args: forbidden.add("entity")
    parser.DefaultHandler = lambda text: None
    parser.ExternalEntityRefHandler = lambda *args: 1
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ET.ParseError(str(exc)) from exc
    if forbidden:
        _fail("Context checks do not support XML document types or entity declarations",
              unsupported=True, payload_kinds=sorted(forbidden))
    return ET.ElementTree(ET.fromstring(data))


def _native_json(cell, name, expected_type):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite JSON constant")

    try:
        value = json.loads(cell.attrib[name], object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, expected_type):
            raise ValueError("wrong JSON type")
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("nonfinite JSON number")
            elif isinstance(item, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise ValueError("unpaired Unicode surrogate")
        return value
    except (ValueError, KeyError, RecursionError) as exc:
        _fail("Original JSON metadata is absent or invalid", attribute=name,
              native_id=cell.get("id"), cause=str(exc))


def original_model(tree: ET.ElementTree) -> dict:
    """Require complete managed v3 identity and unambiguous original geometry."""
    top = tree.getroot()
    pages = top.findall("diagram")
    models = pages[0].findall("mxGraphModel") if len(pages) == 1 else []
    roots = models[0].findall("root") if len(models) == 1 else []
    if (top.tag != "mxfile" or len(pages) != 1 or len(models) != 1 or len(roots) != 1
            or (pages[0].text or "").strip()):
        _fail("Context checks require one uncompressed managed page", unsupported=True)
    root = roots[0]
    pools = [c for c in root if c.tag == "mxCell" and c.get(contracts.DATA_KIND) == "pool"]
    if len(pools) != 1:
        _fail("Context checks require exactly one managed pool", count=len(pools))
    pool = pools[0]
    if pool.get(contracts.DATA_SCHEMA_VERSION) != contracts.V3_SCHEMA_VERSION:
        _fail("Pattern context checks support only schema v3", unsupported=True,
              schema_version=pool.get(contracts.DATA_SCHEMA_VERSION))
    if (contracts.DATA_MODEL_HASH not in pool.attrib
            or pool.get(contracts.DATA_MODEL_HASH_VERSION) != contracts.MODEL_HASH_VERSION):
        _fail("Context checks require an existing supported model hash", unsupported=True,
              model_hash_version=pool.get(contracts.DATA_MODEL_HASH_VERSION))
    if not re.fullmatch(r"[0-9a-f]{64}", pool.get(contracts.DATA_MODEL_HASH, "")):
        _fail("Original model hash must be a lowercase SHA-256 digest")
    required_pool = (
        contracts.DATA_TOOL_VERSION, contracts.DATA_LANE_ORDER, contracts.DATA_MAIN_PATH,
        contracts.DATA_BEHAVIOR_PATTERN, contracts.DATA_LAYOUT_PROFILE,
        contracts.DATA_PHASE_PRESENTATION, contracts.DATA_GROUPS,
    )
    missing = [name for name in required_pool if name not in pool.attrib or not pool.get(name)]
    if missing:
        _fail("Context checks require complete original pool metadata", attributes=missing)
    cells = list(top.iter("mxCell"))
    direct = set(root)
    native_ids, semantic_ids = set(), set()
    managed = []
    for cell in cells:
        native_id = cell.get("id")
        if not native_id or native_id in native_ids:
            _fail("Original native cell IDs must exist and be unique", native_id=native_id)
        native_ids.add(native_id)
        kind, sid = cell.get(contracts.DATA_KIND), cell.get(contracts.DATA_SEMANTIC_ID)
        if kind not in contracts.MANAGED_KINDS:
            if kind is not None or sid is not None or cell.get("vertex") == "1" or cell.get("edge") == "1":
                _fail("Context checks cannot adopt unmanaged drawing content", unsupported=True,
                      native_id=native_id)
            continue
        try:
            contracts.validate_semantic_id(sid, "original semantic ID")
        except contracts.DiagramError as exc:
            _fail("Original semantic ID is missing or invalid", cause=exc.diagnostic())
        if (kind, sid) in semantic_ids:
            _fail("Original semantic IDs must be unique", semantic_id=sid)
        semantic_ids.add((kind, sid))
        if cell not in direct or not cell.get("parent"):
            _fail("Managed cells require original root membership and a parent", semantic_id=sid)
        flag, other = ("edge", "vertex") if kind == "edge" else ("vertex", "edge")
        if cell.get(flag) != "1" or cell.get(other) == "1":
            _fail("Original native flags contradict the managed kind", semantic_id=sid)
        managed.append(cell)
    if any(c.tag in {"object", "UserObject"} and c.find("mxCell") is not None for c in root):
        _fail("Native wrapper semantics are unsupported", unsupported=True)
    by_native = {c.get("id"): c for c in cells}
    current, seen = pool, set()
    while current.get("parent"):
        parent_id = current.get("parent")
        parent = by_native.get(parent_id)
        if (parent is None or parent_id in seen or parent not in direct
                or parent.get(contracts.DATA_KIND) is not None
                or parent.get("vertex") == "1" or parent.get("edge") == "1"):
            _fail("Original pool parent chain is missing, cyclic or not a native layer")
        seen.add(parent_id)
        current = parent
    lanes = {c.get(contracts.DATA_SEMANTIC_ID): c for c in managed if c.get(contracts.DATA_KIND) == "lane"}
    nodes = {c.get(contracts.DATA_SEMANTIC_ID): c for c in managed if c.get(contracts.DATA_KIND) == "node"}
    if not lanes or not nodes:
        _fail("Context checks require original lanes and nodes")
    required = {
        "node": (contracts.DATA_LANE_ID, contracts.DATA_RANK, contracts.DATA_NODE_TYPE),
        "edge": ("source", "target", contracts.DATA_FROM, contracts.DATA_TO,
                 contracts.DATA_EDGE_TYPE, contracts.DATA_ROUTE),
        "phase": (contracts.DATA_FROM_RANK, contracts.DATA_TO_RANK),
    }
    enums = {
        contracts.DATA_NODE_TYPE: contracts.NODE_TYPES,
        contracts.DATA_EDGE_TYPE: {"flow", "call", "return", "retry", "async"},
        contracts.DATA_ROUTE: {"auto", "forward", "back", "side"},
        contracts.DATA_BRANCH: {"positive", "negative"},
        contracts.DATA_SLOT: {"left", "main", "right"},
        contracts.DATA_FLOW_ROLE: {"main", "branch", "fork", "join", "return", "retry", "exception", "response"},
        contracts.DATA_BEHAVIOR_PATTERN: {"linear", "approval-loop", "request-response", "fork-join", "fan-in", "lifecycle", "custom"},
        contracts.DATA_LAYOUT_PROFILE: {"compact", "review", "long-form"},
        contracts.DATA_PHASE_PRESENTATION: {"bands", "rail"},
        contracts.DATA_PRESENTATION: {"bands", "rail"},
    }
    for cell in managed:
        kind, sid = cell.get(contracts.DATA_KIND), cell.get(contracts.DATA_SEMANTIC_ID)
        for attribute, values in enums.items():
            if attribute in cell.attrib and cell.get(attribute) not in values:
                _fail("Original metadata contains an invalid enum", semantic_id=sid, attribute=attribute)
        if contracts.DATA_OUTCOME in cell.attrib:
            try:
                contracts.validate_semantic_id(cell.get(contracts.DATA_OUTCOME), "original outcome")
            except contracts.DiagramError as exc:
                _fail("Original outcome identity is invalid", cause=exc.diagnostic())
        missing = [name for name in required.get(kind, ()) if not cell.get(name)]
        if missing:
            _fail("Original semantic fields are missing or empty", semantic_id=sid, attributes=missing)
        if kind in {"lane", "phase", "edge"} and cell.get("parent") != pool.get("id"):
            _fail("Original managed parent disagrees with the pool", semantic_id=sid)
        if kind == "node":
            lane = lanes.get(cell.get(contracts.DATA_LANE_ID))
            if lane is None or cell.get("parent") != lane.get("id"):
                _fail("Original node owner and native parent disagree", semantic_id=sid)
        if kind == "edge":
            for semantic, native in ((contracts.DATA_FROM, "source"), (contracts.DATA_TO, "target")):
                node = nodes.get(cell.get(semantic))
                if node is None or cell.get(native) != node.get("id"):
                    _fail("Original semantic and native edge endpoints disagree", semantic_id=sid,
                          attribute=semantic)
        for name in (contracts.DATA_RANK, contracts.DATA_FROM_RANK, contracts.DATA_TO_RANK):
            if name in cell.attrib:
                try:
                    if int(cell.attrib[name]) < 1:
                        raise ValueError("rank below one")
                except ValueError:
                    _fail("Original ranks must be positive integers", semantic_id=sid, attribute=name)
        geometries = cell.findall("mxGeometry")
        bindings = [c for c in cell if c.get("as") == "geometry"]
        if (len(geometries) != 1 or geometries[0].get("as") != "geometry"
                or len(bindings) != 1 or bindings[0] is not geometries[0]):
            _fail("Native geometry must have one unambiguous original binding", semantic_id=sid)
        geom = geometries[0]
        coordinates = [geom, *geom.findall("./Array[@as='points']/mxPoint")]
        coordinates.extend(c for c in geom if
                           (c.tag == "mxPoint" and c.get("as") in {"offset", "sourcePoint", "targetPoint"})
                           or (c.tag == "mxRectangle" and c.get("as") == "alternateBounds"))
        for element in coordinates:
            for name in ("x", "y", "width", "height"):
                if name in element.attrib:
                    try:
                        if not math.isfinite(float(element.get(name))):
                            raise ValueError("nonfinite")
                    except ValueError:
                        _fail("Native geometry requires finite coordinates", semantic_id=sid,
                              attribute=name)
        if kind != "edge":
            for name in ("width", "height"):
                if float(geom.get(name, "0")) <= 0:
                    _fail("Native vertex dimensions must be present and positive", semantic_id=sid,
                          attribute=name)
        if len(geom.findall("./Array[@as='points']")) > 1 or len(geom.findall("./mxPoint[@as='offset']")) > 1:
            _fail("Native points or label offsets are ambiguous", semantic_id=sid)
        if any("x" not in p.attrib or "y" not in p.attrib for p in geom.findall("./Array[@as='points']/mxPoint")):
            _fail("Native route point coordinates are missing", semantic_id=sid)
        for name, value in document.style_values(cell.get("style", "")).items():
            if name in {"exitX", "exitY", "exitDx", "exitDy", "entryX", "entryY", "entryDx", "entryDy"}:
                try:
                    if not math.isfinite(float(value)):
                        raise ValueError("nonfinite")
                except ValueError:
                    _fail("Original native port coordinates must be finite", semantic_id=sid, style_key=name)
        for name, expected in ((contracts.DATA_ANCHOR, dict), (contracts.DATA_GROUPS, list),
                               (contracts.DATA_MAIN_PATH, list), (contracts.DATA_LANE_ORDER, list)):
            if name in cell.attrib:
                parsed = _native_json(cell, name, expected)
                if name in {contracts.DATA_LANE_ORDER, contracts.DATA_MAIN_PATH}:
                    try:
                        contracts.validate_id_list(parsed, name)
                    except contracts.DiagramError as exc:
                        _fail("Original ID metadata is invalid", cause=exc.diagnostic())
    lane_order = _native_json(pool, contracts.DATA_LANE_ORDER, list)
    if set(lane_order) != set(lanes):
        _fail("Original lane order does not cover the original lanes")
    try:
        model = metadata.semantic_model_document(tree)
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        _fail("Original semantic model cannot be read safely", cause=str(exc))
    if metadata.semantic_model_hash(tree) != pool.get(contracts.DATA_MODEL_HASH):
        _fail("Original stored semantic hash does not match the current model")
    return model
