#!/usr/bin/env python3
"""Build, inspect, patch, validate, and compare editable Draw.io swimlane diagrams."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
import xml.etree.ElementTree as ET


DEFAULTS = {
    "x": 40,
    "y": 40,
    "title_height": 36,
    "lane_header_height": 32,
    "row_gap": 96,
    "top_padding": 40,
    "bottom_padding": 52,
}

NODE_SIZES = {
    "start": (36, 36),
    "end": (36, 36),
    "process": (132, 42),
    "decision": (96, 72),
    "note": (138, 52),
}

NODE_STYLES = {
    "start": "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#ffffff;strokeColor=#333333;strokeWidth=1.5;",
    "end": "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#333333;strokeColor=#333333;strokeWidth=1.5;",
    "process": "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;fontColor=#333333;strokeColor=#666666;fontSize=12;",
    "decision": "rhombus;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontColor=#333333;fontSize=12;",
    "note": "shape=note;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontColor=#333333;fontSize=11;",
}

PORT_OFFSETS = (0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9, 0.275, 0.725)
PORT_SIDES = {"top", "bottom", "left", "right"}
ROUTE_CLASSES = {"auto", "forward", "back", "side"}
BRANCH_CLASSES = {"positive", "negative"}
EDGE_TYPES = {"flow", "call", "return", "retry", "async"}
ROUTING_FIELDS = {
    "from", "to", "type", "route", "branch", "exit_side", "entry_side",
    "exit_offset", "entry_offset", "waypoints", "allow_port_reuse", "reroute",
}
ROUTE_CLEARANCE = 24.0
LANE_BOUNDARY_CLEARANCE = 16.0
POOL_EDGE_MARGIN = 8.0
GEOMETRY_TOLERANCE = 0.75
SCHEMA_VERSION = "2"

TOP_LEVEL_FIELDS = {
    "schema_version", "title", "lanes", "nodes", "edges", "canvas",
    "main_path", "phases",
}
LANE_FIELDS = {"id", "label", "width"}
NODE_FIELDS = {"id", "lane", "rank", "type", "label", "width", "height", "x", "y"}
EDGE_FIELDS = {
    "id", "from", "to", "type", "label", "route", "branch",
    "exit_side", "entry_side", "exit_offset", "entry_offset",
    "allow_port_reuse", "waypoints",
}
CANVAS_FIELDS = {
    "x", "y", "title_height", "lane_header_height", "row_gap",
    "top_padding", "bottom_padding",
}
PHASE_FIELDS = {"id", "label", "from_rank", "to_rank", "fill_color"}
PATCH_FIELDS = {
    "update_nodes", "update_edges", "nodes", "edges", "delete_nodes",
    "delete_edges", "update_phases", "phases", "delete_phases", "main_path",
}
NODE_UPDATE_FIELDS = {"id", "label", "type", "x", "y", "width", "height"}
EDGE_UPDATE_FIELDS = EDGE_FIELDS | {"reroute"}
PHASE_UPDATE_FIELDS = PHASE_FIELDS


class DiagramError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "input/invalid",
        subject: dict | None = None,
        evidence: dict | None = None,
        supported_fixes: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.subject = subject
        self.evidence = evidence or {}
        self.supported_fixes = supported_fixes or []

    def diagnostic(self) -> dict:
        return make_diagnostic(
            self.code,
            "error",
            str(self),
            subject=self.subject,
            evidence=self.evidence,
            supported_fixes=self.supported_fixes,
        )


def make_diagnostic(
    code: str,
    severity: str,
    message: str,
    *,
    subject: dict | None = None,
    evidence: dict | None = None,
    supported_fixes: list[str] | None = None,
) -> dict:
    diagnostic = {
        "code": code,
        "severity": severity,
        "message": message,
        "evidence": evidence or {},
        "supported_fixes": supported_fixes or [],
    }
    if subject:
        diagnostic["subject"] = subject
    return diagnostic


def reject_unknown_fields(value: dict, allowed: set[str], subject: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DiagramError(
            f"Unknown field(s) in {subject}: {', '.join(unknown)}",
            code="schema/unknown-field",
            subject={"kind": subject},
            evidence={"fields": unknown},
            supported_fixes=["remove-unknown-fields"],
        )


def require_mapping(value, subject: str) -> dict:
    if not isinstance(value, dict):
        raise DiagramError(
            f"{subject} must be an object",
            code="schema/type",
            subject={"kind": subject},
            evidence={"expected": "object", "actual": type(value).__name__},
        )
    return value


def require_list(value, subject: str) -> list:
    if not isinstance(value, list):
        raise DiagramError(
            f"{subject} must be an array",
            code="schema/type",
            subject={"kind": subject},
            evidence={"expected": "array", "actual": type(value).__name__},
        )
    return value


def require_string(value, subject: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise DiagramError(
            f"{subject} must be {qualifier}",
            code="schema/type",
            subject={"kind": subject},
            evidence={"expected": qualifier},
        )
    return value


def validate_number(value, subject: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagramError(
            f"{subject} must be a number",
            code="schema/type",
            subject={"kind": subject},
            evidence={"expected": "number"},
        )
    number_value = float(value)
    if minimum is not None and number_value < minimum:
        raise DiagramError(
            f"{subject} must be at least {number(minimum)}",
            code="schema/range",
            subject={"kind": subject},
            evidence={"minimum": minimum, "actual": number_value},
        )
    return number_value


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def clean_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    if not cleaned:
        raise DiagramError(f"Invalid empty semantic id derived from {value!r}")
    return cleaned


def mx_id(kind: str, semantic_id: str) -> str:
    return f"psd-{kind}-{clean_id(semantic_id)}"


def number(value) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def geometry(parent: ET.Element, **attrs) -> ET.Element:
    normalized = {key: number(value) for key, value in attrs.items() if value is not None}
    normalized["as"] = "geometry"
    return ET.SubElement(parent, "mxGeometry", normalized)


def require_unique(items: list[dict], label: str) -> None:
    seen: set[str] = set()
    for item in items:
        semantic_id = item.get("id")
        if not semantic_id:
            raise DiagramError(f"Every {label} must have an id")
        if semantic_id in seen:
            raise DiagramError(f"Duplicate {label} id: {semantic_id}")
        seen.add(semantic_id)


def validate_semantic_id(value, subject: str) -> str:
    value = require_string(value, subject)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise DiagramError(
            f"{subject} must contain only ASCII letters, digits, underscores, or hyphens",
            code="schema/id-format",
            subject={"kind": subject, "id": value},
            supported_fixes=["replace-semantic-id"],
        )
    return value


def validate_node_object(node: dict, subject: str) -> None:
    require_mapping(node, subject)
    reject_unknown_fields(node, NODE_FIELDS, subject)
    for field in ("id", "lane", "rank", "type", "label"):
        if field not in node:
            raise DiagramError(
                f"Missing required field in {subject}: {field}",
                code="schema/required",
                subject={"kind": subject},
                evidence={"field": field},
            )
    validate_semantic_id(node["id"], f"{subject}.id")
    validate_semantic_id(node["lane"], f"{subject}.lane")
    if isinstance(node["rank"], bool) or not isinstance(node["rank"], int) or node["rank"] < 1:
        raise DiagramError(
            f"{subject}.rank must be an integer greater than or equal to 1",
            code="schema/range",
            subject={"kind": "node", "id": node.get("id")},
            evidence={"field": "rank", "actual": node.get("rank")},
        )
    if node["type"] not in NODE_STYLES:
        raise DiagramError(
            f"Unsupported node type: {node['type']}",
            code="schema/enum",
            subject={"kind": "node", "id": node.get("id")},
            evidence={"field": "type", "allowed": sorted(NODE_STYLES)},
        )
    require_string(node["label"], f"{subject}.label", allow_empty=True)
    for field in ("width", "height"):
        if field in node:
            validate_number(node[field], f"{subject}.{field}", minimum=1)
    for field in ("x", "y"):
        if field in node:
            validate_number(node[field], f"{subject}.{field}")


def validate_edge_object(edge: dict, subject: str, *, update: bool = False) -> None:
    require_mapping(edge, subject)
    reject_unknown_fields(edge, EDGE_UPDATE_FIELDS if update else EDGE_FIELDS, subject)
    required = ("id",) if update else ("id", "from", "to")
    for field in required:
        if field not in edge:
            raise DiagramError(
                f"Missing required field in {subject}: {field}",
                code="schema/required",
                subject={"kind": subject},
                evidence={"field": field},
            )
    validate_semantic_id(edge["id"], f"{subject}.id")
    for field in ("from", "to"):
        if field in edge:
            validate_semantic_id(edge[field], f"{subject}.{field}")
    if "type" in edge and edge["type"] not in EDGE_TYPES:
        raise DiagramError(
            f"Unsupported edge type: {edge['type']}",
            code="schema/enum",
            subject={"kind": "edge", "id": edge.get("id")},
            evidence={"field": "type", "allowed": sorted(EDGE_TYPES)},
        )
    if "route" in edge and edge["route"] not in ROUTE_CLASSES:
        raise DiagramError(
            f"Unsupported route class: {edge['route']}",
            code="schema/enum",
            subject={"kind": "edge", "id": edge.get("id")},
            evidence={"field": "route", "allowed": sorted(ROUTE_CLASSES)},
        )
    if "branch" in edge and edge["branch"] not in BRANCH_CLASSES:
        raise DiagramError(
            f"Unsupported branch class: {edge['branch']}",
            code="schema/enum",
            subject={"kind": "edge", "id": edge.get("id")},
            evidence={"field": "branch", "allowed": sorted(BRANCH_CLASSES)},
        )
    for field in ("exit_side", "entry_side"):
        if field in edge:
            validate_side(edge[field], field)
    for field in ("exit_offset", "entry_offset"):
        if field in edge:
            validate_offset(edge[field], field)
    if "label" in edge:
        require_string(edge["label"], f"{subject}.label", allow_empty=True)
    if "allow_port_reuse" in edge and not isinstance(edge["allow_port_reuse"], bool):
        raise DiagramError(
            f"{subject}.allow_port_reuse must be a boolean",
            code="schema/type",
            subject={"kind": "edge", "id": edge.get("id")},
        )
    if "reroute" in edge and not isinstance(edge["reroute"], bool):
        raise DiagramError(
            f"{subject}.reroute must be a boolean",
            code="schema/type",
            subject={"kind": "edge", "id": edge.get("id")},
        )
    if "waypoints" in edge:
        require_list(edge["waypoints"], f"{subject}.waypoints")
        normalize_waypoints(edge["waypoints"])


def validate_phase_object(phase: dict, subject: str, *, update: bool = False) -> None:
    require_mapping(phase, subject)
    reject_unknown_fields(phase, PHASE_UPDATE_FIELDS, subject)
    required = ("id",) if update else ("id", "label", "from_rank", "to_rank")
    for field in required:
        if field not in phase:
            raise DiagramError(
                f"Missing required field in {subject}: {field}",
                code="schema/required",
                subject={"kind": subject},
                evidence={"field": field},
            )
    validate_semantic_id(phase["id"], f"{subject}.id")
    if "label" in phase:
        require_string(phase["label"], f"{subject}.label")
    for field in ("from_rank", "to_rank"):
        if field in phase:
            value = phase[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DiagramError(
                    f"{subject}.{field} must be an integer greater than or equal to 1",
                    code="schema/range",
                    subject={"kind": "phase", "id": phase.get("id")},
                    evidence={"field": field, "actual": value},
                )
    if "from_rank" in phase and "to_rank" in phase and phase["to_rank"] < phase["from_rank"]:
        raise DiagramError(
            f"{subject}.to_rank must not be less than from_rank",
            code="schema/range",
            subject={"kind": "phase", "id": phase.get("id")},
        )
    if "fill_color" in phase:
        color = require_string(phase["fill_color"], f"{subject}.fill_color")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise DiagramError(
                f"{subject}.fill_color must use #RRGGBB format",
                code="schema/format",
                subject={"kind": "phase", "id": phase.get("id")},
            )


def validate_id_list(values, subject: str) -> list[str]:
    values = require_list(values, subject)
    result: list[str] = []
    for index, value in enumerate(values):
        result.append(validate_semantic_id(value, f"{subject}[{index}]"))
    if len(result) != len(set(result)):
        raise DiagramError(
            f"{subject} must not contain duplicate IDs",
            code="schema/duplicate",
            subject={"kind": subject},
        )
    return result


def validate_build_spec(spec: dict) -> str:
    require_mapping(spec, "spec")
    reject_unknown_fields(spec, TOP_LEVEL_FIELDS, "spec")
    for field in ("title", "lanes", "nodes", "edges"):
        if field not in spec:
            raise DiagramError(
                f"Missing required field: {field}",
                code="schema/required",
                subject={"kind": "spec"},
                evidence={"field": field},
            )

    if "schema_version" in spec:
        require_string(spec["schema_version"], "spec.schema_version")
    schema_version = spec.get("schema_version", "1")
    if schema_version not in {"1", SCHEMA_VERSION}:
        raise DiagramError(
            f"Unsupported schema_version: {schema_version}",
            code="schema/version",
            subject={"kind": "spec"},
            evidence={"supported": ["1", SCHEMA_VERSION], "actual": schema_version},
            supported_fixes=["migrate-spec"],
        )
    require_string(spec["title"], "spec.title")
    lanes = require_list(spec["lanes"], "spec.lanes")
    nodes = require_list(spec["nodes"], "spec.nodes")
    edges = require_list(spec["edges"], "spec.edges")
    if not lanes:
        raise DiagramError("spec.lanes must contain at least one lane", code="schema/min-items")
    if schema_version == SCHEMA_VERSION and len(nodes) < 2:
        raise DiagramError("schema version 2 requires at least two nodes", code="schema/min-items")

    for index, lane in enumerate(lanes):
        subject = f"lane[{index}]"
        require_mapping(lane, subject)
        reject_unknown_fields(lane, LANE_FIELDS, subject)
        for field in ("id", "label"):
            if field not in lane:
                raise DiagramError(
                    f"Missing required field in {subject}: {field}",
                    code="schema/required",
                    subject={"kind": subject},
                    evidence={"field": field},
                )
        validate_semantic_id(lane["id"], f"{subject}.id")
        require_string(lane["label"], f"{subject}.label")
        if "width" in lane:
            validate_number(
                lane["width"],
                f"{subject}.width",
                minimum=120 if schema_version == SCHEMA_VERSION else 1,
            )

    for index, node in enumerate(nodes):
        validate_node_object(node, f"node[{index}]")
    for index, edge in enumerate(edges):
        validate_edge_object(edge, f"edge[{index}]")
    require_unique(lanes, "lane")
    require_unique(nodes, "node")
    require_unique(edges, "edge")

    lane_ids = {lane["id"] for lane in lanes}
    node_ids = {node["id"] for node in nodes}
    node_by_id = {node["id"]: node for node in nodes}
    for node in nodes:
        if node["lane"] not in lane_ids:
            raise DiagramError(
                f"Node {node['id']} references an unknown lane",
                code="semantic/unknown-lane",
                subject={"kind": "node", "id": node["id"]},
                evidence={"lane": node["lane"]},
            )
    for edge in edges:
        missing = [field for field in ("from", "to") if edge[field] not in node_ids]
        if missing:
            raise DiagramError(
                f"Edge {edge['id']} references a missing node",
                code="semantic/missing-endpoint",
                subject={"kind": "edge", "id": edge["id"]},
                evidence={field: edge[field] for field in missing},
            )

    if "canvas" in spec:
        canvas = require_mapping(spec["canvas"], "spec.canvas")
        reject_unknown_fields(canvas, CANVAS_FIELDS, "spec.canvas")
        for field, value in canvas.items():
            minimum = 1 if field in {"title_height", "lane_header_height", "row_gap"} else None
            validate_number(value, f"spec.canvas.{field}", minimum=minimum)

    main_path = spec.get("main_path")
    if schema_version == SCHEMA_VERSION and main_path is None:
        raise DiagramError(
            "schema_version 2 requires main_path",
            code="schema/required",
            subject={"kind": "spec"},
            evidence={"field": "main_path"},
        )
    if main_path is not None:
        main_path = validate_id_list(main_path, "spec.main_path")
        if len(main_path) < 2:
            raise DiagramError("spec.main_path must contain at least two nodes", code="schema/min-items")
        missing = [node_id for node_id in main_path if node_id not in node_ids]
        if missing:
            raise DiagramError(
                "main_path references missing nodes",
                code="semantic/main-path-node",
                subject={"kind": "main_path"},
                evidence={"missing": missing},
            )
        edge_pairs = {(edge["from"], edge["to"]) for edge in edges}
        for source_id, target_id in zip(main_path, main_path[1:]):
            if (source_id, target_id) not in edge_pairs:
                raise DiagramError(
                    f"main_path has no edge from {source_id} to {target_id}",
                    code="semantic/main-path-edge",
                    subject={"kind": "main_path"},
                    evidence={"from": source_id, "to": target_id},
                    supported_fixes=["add-main-path-edge", "correct-main-path"],
                )
            if node_by_id[target_id]["rank"] < node_by_id[source_id]["rank"]:
                raise DiagramError(
                    f"main_path moves backward from {source_id} to {target_id}",
                    code="semantic/main-path-rank",
                    subject={"kind": "main_path"},
                    evidence={"from": source_id, "to": target_id},
                    supported_fixes=["correct-rank", "remove-return-from-main-path"],
                )
        if node_by_id[main_path[0]]["type"] != "start":
            raise DiagramError(
                "main_path must begin with a start node",
                code="semantic/main-path-start",
                subject={"kind": "main_path"},
                evidence={"node": main_path[0]},
                supported_fixes=["correct-main-path", "change-node-type"],
            )
        if node_by_id[main_path[-1]]["type"] != "end":
            raise DiagramError(
                "main_path must end with an end node",
                code="semantic/main-path-end",
                subject={"kind": "main_path"},
                evidence={"node": main_path[-1]},
                supported_fixes=["correct-main-path", "change-node-type"],
            )

    phases = require_list(spec.get("phases", []), "spec.phases")
    for index, phase in enumerate(phases):
        validate_phase_object(phase, f"phase[{index}]")
    require_unique(phases, "phase")
    max_rank = max((node["rank"] for node in nodes), default=1)
    for phase in phases:
        if phase["to_rank"] > max_rank:
            raise DiagramError(
                f"Phase {phase['id']} extends beyond the maximum node rank",
                code="semantic/phase-range",
                subject={"kind": "phase", "id": phase["id"]},
                evidence={"to_rank": phase["to_rank"], "max_rank": max_rank},
            )
    return schema_version


def validate_patch_spec(changes: dict) -> None:
    require_mapping(changes, "patch")
    reject_unknown_fields(changes, PATCH_FIELDS, "patch")
    for field in ("update_nodes", "update_edges", "nodes", "edges", "update_phases", "phases"):
        if field in changes:
            require_list(changes[field], f"patch.{field}")
    for index, update in enumerate(changes.get("update_nodes", [])):
        require_mapping(update, f"update_node[{index}]")
        reject_unknown_fields(update, NODE_UPDATE_FIELDS, f"update_node[{index}]")
        validate_semantic_id(update.get("id"), f"update_node[{index}].id")
        if "type" in update and update["type"] not in NODE_STYLES:
            raise DiagramError(f"Unsupported node type: {update['type']}", code="schema/enum")
        if "label" in update:
            require_string(update["label"], f"update_node[{index}].label", allow_empty=True)
        for field in ("x", "y", "width", "height"):
            if field in update:
                validate_number(update[field], f"update_node[{index}].{field}", minimum=1 if field in {"width", "height"} else None)
    for index, update in enumerate(changes.get("update_edges", [])):
        validate_edge_object(update, f"update_edge[{index}]", update=True)
    for index, node in enumerate(changes.get("nodes", [])):
        validate_node_object(node, f"new_node[{index}]")
    for index, edge in enumerate(changes.get("edges", [])):
        validate_edge_object(edge, f"new_edge[{index}]")
    for index, update in enumerate(changes.get("update_phases", [])):
        validate_phase_object(update, f"update_phase[{index}]", update=True)
    for index, phase in enumerate(changes.get("phases", [])):
        validate_phase_object(phase, f"new_phase[{index}]")
    for field in ("delete_nodes", "delete_edges", "delete_phases"):
        if field in changes:
            validate_id_list(changes[field], f"patch.{field}")
    if "main_path" in changes:
        validate_id_list(changes["main_path"], "patch.main_path")
    for field in ("update_nodes", "update_edges", "nodes", "edges", "update_phases", "phases"):
        if field in changes:
            require_unique(changes[field], field)


def canvas_values(spec: dict) -> dict:
    values = dict(DEFAULTS)
    values.update(spec.get("canvas", {}))
    return values


def node_size(node: dict) -> tuple[float, float]:
    kind = node.get("type", "process")
    if kind not in NODE_SIZES:
        raise DiagramError(f"Unsupported node type: {kind}")
    default_width, default_height = NODE_SIZES[kind]
    return float(node.get("width", default_width)), float(node.get("height", default_height))


def lane_height(max_rank: int, values: dict) -> float:
    content = values["top_padding"] + max(0, max_rank - 1) * values["row_gap"]
    return values["lane_header_height"] + content + 40 + values["bottom_padding"]


def node_y(node: dict, values: dict) -> float:
    _, height = node_size(node)
    center = values["lane_header_height"] + values["top_padding"] + (int(node["rank"]) - 1) * values["row_gap"]
    return float(node.get("y", center - height / 2))


def create_node_cell(root: ET.Element, parent: ET.Element, node: dict, lane_width: float, values: dict) -> ET.Element:
    kind = node.get("type", "process")
    width, height = node_size(node)
    x = float(node.get("x", (lane_width - width) / 2))
    y = node_y(node, values)
    cell = ET.SubElement(
        root,
        "mxCell",
        {
            "id": mx_id("node", node["id"]),
            "parent": parent.attrib["id"],
            "style": NODE_STYLES[kind],
            "value": str(node.get("label", "")),
            "vertex": "1",
            "data-kind": "node",
            "data-semantic-id": node["id"],
            "data-node-type": kind,
            "data-lane-id": node["lane"],
            "data-rank": str(node["rank"]),
        },
    )
    geometry(cell, x=x, y=y, width=width, height=height)
    return cell


def phase_geometry_values(phase: dict, values: dict, pool_width: float) -> dict[str, float]:
    first_center = (
        values["title_height"]
        + values["lane_header_height"]
        + values["top_padding"]
        + (int(phase["from_rank"]) - 1) * values["row_gap"]
    )
    last_center = (
        values["title_height"]
        + values["lane_header_height"]
        + values["top_padding"]
        + (int(phase["to_rank"]) - 1) * values["row_gap"]
    )
    top = max(values["title_height"] + values["lane_header_height"], first_center - values["row_gap"] / 2)
    bottom = last_center + values["row_gap"] / 2
    return {"x": 0.0, "y": top, "width": pool_width, "height": max(24.0, bottom - top)}


def create_phase_cell(
    root: ET.Element,
    pool: ET.Element,
    phase: dict,
    values: dict,
    pool_width: float,
) -> ET.Element:
    fill_color = phase.get("fill_color", "#f5f5f5")
    cell = ET.SubElement(
        root,
        "mxCell",
        {
            "id": mx_id("phase", phase["id"]),
            "parent": pool.attrib["id"],
            "vertex": "1",
            "connectable": "0",
            "value": str(phase["label"]),
            "style": (
                "rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;align=left;"
                "spacingTop=4;spacingLeft=6;fontSize=10;fontStyle=1;fontColor=#666666;"
                f"fillColor={fill_color};fillOpacity=12;strokeColor=#b3b3b3;"
                "strokeOpacity=55;dashed=1;pointerEvents=0;"
            ),
            "data-kind": "phase",
            "data-semantic-id": phase["id"],
            "data-from-rank": str(phase["from_rank"]),
            "data-to-rank": str(phase["to_rank"]),
            "data-fill-color": fill_color,
        },
    )
    geometry(cell, **phase_geometry_values(phase, values, pool_width))
    return cell


def parse_geometry(cell: ET.Element) -> dict[str, float]:
    geom = cell.find("mxGeometry")
    if geom is None:
        raise DiagramError(f"Cell {cell.attrib.get('id')} has no geometry")
    result: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        result[key] = float(geom.attrib.get(key, "0"))
    return result


def lane_node_records(root: ET.Element, pool: ET.Element) -> tuple[dict[str, dict], dict[str, dict]]:
    lanes: dict[str, dict] = {}
    nodes: dict[str, dict] = {}
    for child in list(root):
        if (
            child.tag != "mxCell"
            or child.attrib.get("data-kind") != "lane"
            or child.attrib.get("parent") != pool.attrib["id"]
        ):
            continue
        semantic_id = child.attrib["data-semantic-id"]
        lanes[semantic_id] = {"cell": child, "geometry": parse_geometry(child)}
    lane_by_cell_id = {record["cell"].attrib["id"]: semantic_id for semantic_id, record in lanes.items()}
    for child in list(root):
        if child.tag != "mxCell" or child.attrib.get("data-kind") != "node":
            continue
        lane_semantic_id = lane_by_cell_id.get(child.attrib.get("parent"))
        if lane_semantic_id:
            nodes[child.attrib["data-semantic-id"]] = {
                "cell": child,
                "geometry": parse_geometry(child),
                "lane": lane_semantic_id,
            }
    return lanes, nodes


def phase_records(root: ET.Element, pool: ET.Element) -> dict[str, ET.Element]:
    return {
        child.attrib["data-semantic-id"]: child
        for child in list(root)
        if child.tag == "mxCell"
        and child.attrib.get("data-kind") == "phase"
        and child.attrib.get("parent") == pool.attrib["id"]
        and child.attrib.get("data-semantic-id")
    }


def node_center_in_pool(node_record: dict, lane_record: dict) -> tuple[float, float]:
    node_geom = node_record["geometry"]
    lane_geom = lane_record["geometry"]
    return (
        lane_geom["x"] + node_geom["x"] + node_geom["width"] / 2,
        lane_geom["y"] + node_geom["y"] + node_geom["height"] / 2,
    )


def node_bounds_in_pool(node_record: dict, lane_record: dict) -> dict[str, float]:
    node_geom = node_record["geometry"]
    lane_geom = lane_record["geometry"]
    left = lane_geom["x"] + node_geom["x"]
    top = lane_geom["y"] + node_geom["y"]
    return {
        "left": left,
        "top": top,
        "right": left + node_geom["width"],
        "bottom": top + node_geom["height"],
        "width": node_geom["width"],
        "height": node_geom["height"],
    }


def validate_side(side: str, field: str) -> str:
    if side not in PORT_SIDES:
        raise DiagramError(f"Unsupported {field}: {side}")
    return side


def validate_offset(offset, field: str) -> float:
    value = float(offset)
    if not 0.05 <= value <= 0.95:
        raise DiagramError(f"{field} must be between 0.05 and 0.95")
    return value


def port_xy(side: str, offset: float) -> tuple[float, float]:
    if side == "top":
        return offset, 0.0
    if side == "bottom":
        return offset, 1.0
    if side == "left":
        return 0.0, offset
    if side == "right":
        return 1.0, offset
    raise DiagramError(f"Unsupported port side: {side}")


def port_point(bounds: dict[str, float], side: str, offset: float) -> tuple[float, float]:
    if side == "top":
        return bounds["left"] + bounds["width"] * offset, bounds["top"]
    if side == "bottom":
        return bounds["left"] + bounds["width"] * offset, bounds["bottom"]
    if side == "left":
        return bounds["left"], bounds["top"] + bounds["height"] * offset
    if side == "right":
        return bounds["right"], bounds["top"] + bounds["height"] * offset
    raise DiagramError(f"Unsupported port side: {side}")


class PortAllocator:
    def __init__(self) -> None:
        self.occupied: dict[tuple[str, str, float], list[str]] = {}

    @staticmethod
    def key(node_id: str, side: str, offset: float) -> tuple[str, str, float]:
        return node_id, side, round(float(offset), 4)

    def reserve(
        self,
        node_id: str,
        side: str,
        offset: float,
        edge_id: str,
        *,
        allow_reuse: bool = False,
        fail_on_conflict: bool = True,
    ) -> float:
        side = validate_side(side, "port side")
        offset = validate_offset(offset, "port offset")
        key = self.key(node_id, side, offset)
        if key in self.occupied and not allow_reuse and fail_on_conflict:
            used_by = ", ".join(self.occupied[key])
            raise DiagramError(
                f"Port {node_id}:{side}@{number(offset)} is already used by {used_by}; "
                "choose another port or set allow_port_reuse"
            )
        self.occupied.setdefault(key, []).append(edge_id)
        return offset

    def choose(
        self,
        node_id: str,
        side: str,
        edge_id: str,
        requested=None,
        *,
        allow_reuse: bool = False,
    ) -> float:
        side = validate_side(side, "port side")
        if requested is not None:
            return self.reserve(
                node_id, side, requested, edge_id, allow_reuse=allow_reuse
            )
        for offset in PORT_OFFSETS:
            if allow_reuse or self.key(node_id, side, offset) not in self.occupied:
                return self.reserve(
                    node_id, side, offset, edge_id, allow_reuse=allow_reuse
                )
        raise DiagramError(f"No free {side} port remains on node {node_id}")


def edge_style(
    edge_type: str,
    exit_side: str,
    entry_side: str,
    exit_offset: float,
    entry_offset: float,
) -> str:
    exit_x, exit_y = port_xy(exit_side, exit_offset)
    entry_x, entry_y = port_xy(entry_side, entry_offset)
    extra = "dashed=1;" if edge_type == "async" else ""
    return (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
        "endArrow=block;endFill=1;labelBackgroundColor=#ffffff;fontSize=11;"
        f"exitX={number(exit_x)};exitY={number(exit_y)};exitDx=0;exitDy=0;"
        f"entryX={number(entry_x)};entryY={number(entry_y)};entryDx=0;entryDy=0;{extra}"
    )


def infer_route_class(edge: dict, source: dict, target: dict) -> str:
    requested = edge.get("route", "auto")
    if requested not in ROUTE_CLASSES:
        raise DiagramError(f"Unsupported route class: {requested}")
    if requested != "auto":
        return requested
    source_rank = int(source["cell"].attrib.get("data-rank", "0"))
    target_rank = int(target["cell"].attrib.get("data-rank", "0"))
    if edge.get("type") == "retry" or target_rank < source_rank:
        return "back"
    if target_rank > source_rank:
        return "forward"
    return "side"


def preferred_sides(edge: dict, route_class: str, source: dict, target: dict, lanes: dict[str, dict]) -> tuple[str, str]:
    branch = edge.get("branch")
    if branch is not None and branch not in BRANCH_CLASSES:
        raise DiagramError(f"Unsupported branch class: {branch}")
    source_type = source["cell"].attrib.get("data-node-type", "process")

    if route_class == "back":
        default_exit = "left"
        default_entry = "left"
    elif route_class == "forward":
        if source_type == "decision" and branch == "positive":
            default_exit = "right"
        elif source_type == "decision" and branch == "negative":
            default_exit = "left"
        else:
            default_exit = "bottom"
        default_entry = "top"
    else:
        source_index = list(lanes).index(source["lane"])
        target_index = list(lanes).index(target["lane"])
        if target_index >= source_index:
            default_exit, default_entry = "right", "left"
        else:
            default_exit, default_entry = "left", "right"

    exit_side = validate_side(edge.get("exit_side", default_exit), "exit_side")
    entry_side = validate_side(edge.get("entry_side", default_entry), "entry_side")
    return exit_side, entry_side


def normalize_waypoints(values) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in values or []:
        if isinstance(item, dict):
            if "x" not in item or "y" not in item:
                raise DiagramError("Every waypoint object must contain x and y")
            x, y = item["x"], item["y"]
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            x, y = item
        else:
            raise DiagramError("Waypoints must be {x, y} objects or [x, y] pairs")
        points.append((float(x), float(y)))
    return points


def compact_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    compacted: list[tuple[float, float]] = []
    for point in points:
        if not compacted or point != compacted[-1]:
            compacted.append(point)
    return compacted


def internal_lane_boundaries(lanes: dict[str, dict]) -> list[float]:
    right_edges = {
        round(record["geometry"]["x"] + record["geometry"]["width"], 4)
        for record in lanes.values()
    }
    if not right_edges:
        return []
    pool_right = max(right_edges)
    return sorted(edge for edge in right_edges if edge < pool_right - GEOMETRY_TOLERANCE)


def safe_vertical_corridor(
    candidate: float,
    boundaries: list[float],
    direction: str,
    pool_width: float,
) -> float:
    """Move an automatic vertical corridor away from internal lane boundaries."""
    if direction not in {"left", "right"}:
        raise DiagramError(f"Unsupported corridor direction: {direction}")

    lower = POOL_EDGE_MARGIN
    upper = max(lower, pool_width - POOL_EDGE_MARGIN)
    candidate = min(max(candidate, lower), upper)
    safe_gap = LANE_BOUNDARY_CLEARANCE + GEOMETRY_TOLERANCE

    for _ in range(len(boundaries) + 1):
        conflict = next(
            (
                boundary
                for boundary in boundaries
                if abs(candidate - boundary) < LANE_BOUNDARY_CLEARANCE
            ),
            None,
        )
        if conflict is None:
            break
        shifted = conflict - safe_gap if direction == "left" else conflict + safe_gap
        shifted = min(max(shifted, lower), upper)
        if abs(shifted - candidate) < GEOMETRY_TOLERANCE:
            break
        candidate = shifted

    return candidate


def automatic_waypoints(
    route_class: str,
    source_bounds: dict[str, float],
    target_bounds: dict[str, float],
    source_point: tuple[float, float],
    target_point: tuple[float, float],
    exit_side: str,
    entry_side: str,
    pool_width: float,
    lane_boundaries: list[float],
) -> list[tuple[float, float]]:
    sx, sy = source_point
    tx, ty = target_point

    if route_class == "forward":
        if exit_side == "bottom" and entry_side == "top" and abs(sx - tx) < GEOMETRY_TOLERANCE:
            return []
        corridor_y = (sy + ty) / 2
        if exit_side == "bottom":
            return compact_points([(sx, corridor_y), (tx, corridor_y)])
        if exit_side in {"left", "right"}:
            escape_x = sx + (ROUTE_CLEARANCE if exit_side == "right" else -ROUTE_CLEARANCE)
            escape_x = safe_vertical_corridor(
                escape_x, lane_boundaries, exit_side, pool_width
            )
            return compact_points([(escape_x, sy), (escape_x, corridor_y), (tx, corridor_y)])
        return compact_points([(sx, corridor_y), (tx, corridor_y)])

    if route_class == "back":
        if exit_side == entry_side == "left":
            route_x = safe_vertical_corridor(
                min(source_bounds["left"], target_bounds["left"]) - ROUTE_CLEARANCE,
                lane_boundaries,
                "left",
                pool_width,
            )
            return compact_points([(route_x, sy), (route_x, ty)])
        if exit_side == entry_side == "right":
            route_x = safe_vertical_corridor(
                max(source_bounds["right"], target_bounds["right"]) + ROUTE_CLEARANCE,
                lane_boundaries,
                "right",
                pool_width,
            )
            return compact_points([(route_x, sy), (route_x, ty)])
        corridor_y = (sy + ty) / 2
        return compact_points([(sx, corridor_y), (tx, corridor_y)])

    if abs(sy - ty) < GEOMETRY_TOLERANCE:
        return []
    if exit_side == "right":
        route_x = safe_vertical_corridor(
            source_bounds["right"] + ROUTE_CLEARANCE,
            lane_boundaries,
            "right",
            pool_width,
        )
    elif exit_side == "left":
        route_x = safe_vertical_corridor(
            source_bounds["left"] - ROUTE_CLEARANCE,
            lane_boundaries,
            "left",
            pool_width,
        )
    else:
        route_x = (sx + tx) / 2
    return compact_points([(route_x, sy), (route_x, ty)])


def route_edge(
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: PortAllocator,
) -> dict:
    if edge["from"] not in nodes or edge["to"] not in nodes:
        raise DiagramError(f"Edge {edge.get('id')} references a missing node")
    source = nodes[edge["from"]]
    target = nodes[edge["to"]]
    source_lane = lanes[source["lane"]]
    target_lane = lanes[target["lane"]]
    route_class = infer_route_class(edge, source, target)
    exit_side, entry_side = preferred_sides(edge, route_class, source, target, lanes)
    allow_reuse = bool(edge.get("allow_port_reuse", False))
    exit_offset = allocator.choose(
        edge["from"], exit_side, edge["id"], edge.get("exit_offset"), allow_reuse=allow_reuse
    )
    entry_offset = allocator.choose(
        edge["to"], entry_side, edge["id"], edge.get("entry_offset"), allow_reuse=allow_reuse
    )
    source_bounds = node_bounds_in_pool(source, source_lane)
    target_bounds = node_bounds_in_pool(target, target_lane)
    source_point = port_point(source_bounds, exit_side, exit_offset)
    target_point = port_point(target_bounds, entry_side, entry_offset)
    pool_width = max(record["geometry"]["x"] + record["geometry"]["width"] for record in lanes.values())
    lane_boundaries = internal_lane_boundaries(lanes)
    points = (
        normalize_waypoints(edge["waypoints"])
        if "waypoints" in edge
        else automatic_waypoints(
            route_class,
            source_bounds,
            target_bounds,
            source_point,
            target_point,
            exit_side,
            entry_side,
            pool_width,
            lane_boundaries,
        )
    )
    return {
        "style": edge_style(
            edge.get("type", "flow"), exit_side, entry_side, exit_offset, entry_offset
        ),
        "points": points,
        "route": route_class,
        "exit_side": exit_side,
        "entry_side": entry_side,
        "exit_offset": exit_offset,
        "entry_offset": entry_offset,
    }


def set_edge_points(cell: ET.Element, points: list[tuple[float, float]]) -> None:
    geom = cell.find("mxGeometry")
    if geom is None:
        geom = geometry(cell, relative=1)
    else:
        geom.attrib.clear()
        geom.attrib.update({"relative": "1", "as": "geometry"})
        for child in list(geom):
            geom.remove(child)
    if points:
        array = ET.SubElement(geom, "Array", {"as": "points"})
        for x, y in points:
            ET.SubElement(array, "mxPoint", {"x": number(x), "y": number(y)})


def apply_edge_route(
    cell: ET.Element,
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: PortAllocator,
) -> ET.Element:
    routed = route_edge(edge, lanes, nodes, allocator)
    cell.attrib.update(
        {
            "source": nodes[edge["from"]]["cell"].attrib["id"],
            "target": nodes[edge["to"]]["cell"].attrib["id"],
            "style": routed["style"],
            "value": str(edge.get("label", "")),
            "data-edge-type": edge.get("type", "flow"),
            "data-from": edge["from"],
            "data-to": edge["to"],
            "data-route": routed["route"],
            "data-exit-side": routed["exit_side"],
            "data-entry-side": routed["entry_side"],
            "data-exit-offset": number(routed["exit_offset"]),
            "data-entry-offset": number(routed["entry_offset"]),
            "data-allow-port-reuse": "1" if edge.get("allow_port_reuse") else "0",
        }
    )
    if edge.get("branch"):
        cell.attrib["data-branch"] = edge["branch"]
    else:
        cell.attrib.pop("data-branch", None)
    set_edge_points(cell, routed["points"])
    return cell


def create_edge_cell(
    root: ET.Element,
    pool: ET.Element,
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: PortAllocator,
) -> ET.Element:
    cell = ET.SubElement(
        root,
        "mxCell",
        {
            "id": mx_id("edge", edge["id"]),
            "parent": pool.attrib["id"],
            "edge": "1",
            "data-kind": "edge",
            "data-semantic-id": edge["id"],
        },
    )
    geometry(cell, relative=1)
    return apply_edge_route(cell, edge, lanes, nodes, allocator)


def build_tree(spec: dict) -> ET.ElementTree:
    schema_version = validate_build_spec(spec)

    values = canvas_values(spec)
    max_rank = max((int(node["rank"]) for node in spec["nodes"]), default=1)
    current_lane_height = lane_height(max_rank, values)
    pool_width = sum(float(lane.get("width", 200)) for lane in spec["lanes"])
    pool_height = values["title_height"] + current_lane_height

    mxfile = ET.Element("mxfile", {"host": "Electron", "modified": "product-swimlane-drawio"})
    diagram = ET.SubElement(mxfile, "diagram", {"name": "Main Flow", "id": "product-swimlane-main"})
    model = ET.SubElement(
        diagram,
        "mxGraphModel",
        {
            "dx": "1200", "dy": "900", "grid": "1", "gridSize": "10", "guides": "1",
            "tooltips": "1", "connect": "1", "arrows": "1", "fold": "1", "page": "1",
            "pageScale": "1", "pageWidth": "1169", "pageHeight": "1654", "math": "0", "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    pool = ET.SubElement(
        root,
        "mxCell",
        {
            "id": "psd-pool-main", "parent": "1", "vertex": "1", "value": spec["title"],
            "style": "swimlane;html=1;startSize=36;horizontal=1;rounded=0;shadow=0;strokeWidth=1.5;fontSize=15;fontStyle=1;fillColor=#dae8fc;swimlaneFillColor=#ffffff;",
            "data-kind": "pool", "data-semantic-id": "main", "data-title-height": number(values["title_height"]),
            "data-lane-header-height": number(values["lane_header_height"]), "data-row-gap": number(values["row_gap"]),
            "data-top-padding": number(values["top_padding"]), "data-bottom-padding": number(values["bottom_padding"]),
            "data-max-rank": str(max_rank),
            "data-schema-version": schema_version,
            "data-main-path": json.dumps(spec.get("main_path", []), ensure_ascii=True, separators=(",", ":")),
        },
    )
    geometry(pool, x=values["x"], y=values["y"], width=pool_width, height=pool_height)

    lane_cells: dict[str, ET.Element] = {}
    offset_x = 0.0
    for lane in spec["lanes"]:
        width = float(lane.get("width", 200))
        lane_cell = ET.SubElement(
            root,
            "mxCell",
            {
                "id": mx_id("lane", lane["id"]), "parent": pool.attrib["id"], "vertex": "1",
                "value": lane["label"],
                "style": f"swimlane;html=1;startSize={number(values['lane_header_height'])};horizontal=1;rounded=0;strokeWidth=1;fontSize=13;fontStyle=1;fillColor=#dae8fc;swimlaneFillColor=#ffffff;",
                "data-kind": "lane", "data-semantic-id": lane["id"],
            },
        )
        geometry(lane_cell, x=offset_x, y=values["title_height"], width=width, height=current_lane_height)
        lane_cells[lane["id"]] = lane_cell
        offset_x += width

    for phase in spec.get("phases", []):
        create_phase_cell(root, pool, phase, values, pool_width)

    for node in spec["nodes"]:
        lane_cell = lane_cells[node["lane"]]
        lane_width = parse_geometry(lane_cell)["width"]
        create_node_cell(root, lane_cell, node, lane_width, values)

    lanes, nodes = lane_node_records(root, pool)
    allocator = PortAllocator()
    for edge in spec["edges"]:
        create_edge_cell(root, pool, edge, lanes, nodes, allocator)
    return ET.ElementTree(mxfile)


def graph_root(tree: ET.ElementTree) -> ET.Element:
    root = tree.find("./diagram/mxGraphModel/root")
    if root is None:
        raise DiagramError("Not a supported uncompressed Draw.io document")
    return root


def find_pool(tree: ET.ElementTree) -> ET.Element:
    root = graph_root(tree)
    for cell in list(root):
        if cell.attrib.get("data-kind") == "pool":
            return cell
    raise DiagramError("Diagram is missing compatible swimlane semantic metadata")


def values_from_pool(pool: ET.Element) -> dict:
    return {
        "title_height": float(pool.attrib.get("data-title-height", DEFAULTS["title_height"])),
        "lane_header_height": float(pool.attrib.get("data-lane-header-height", DEFAULTS["lane_header_height"])),
        "row_gap": float(pool.attrib.get("data-row-gap", DEFAULTS["row_gap"])),
        "top_padding": float(pool.attrib.get("data-top-padding", DEFAULTS["top_padding"])),
        "bottom_padding": float(pool.attrib.get("data-bottom-padding", DEFAULTS["bottom_padding"])),
    }


def style_values(style: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in style.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key] = value
    return result


def port_from_style(cell: ET.Element, prefix: str) -> tuple[str, float] | None:
    values = style_values(cell.attrib.get("style", ""))
    try:
        x = float(values[f"{prefix}X"])
        y = float(values[f"{prefix}Y"])
    except (KeyError, ValueError):
        return None
    if abs(y) < GEOMETRY_TOLERANCE / 10:
        return "top", x
    if abs(y - 1.0) < GEOMETRY_TOLERANCE / 10:
        return "bottom", x
    if abs(x) < GEOMETRY_TOLERANCE / 10:
        return "left", y
    if abs(x - 1.0) < GEOMETRY_TOLERANCE / 10:
        return "right", y
    return None


def edge_records(root: ET.Element) -> dict[str, ET.Element]:
    return {
        child.attrib["data-semantic-id"]: child
        for child in list(root)
        if child.tag == "mxCell"
        and child.attrib.get("data-kind") == "edge"
        and child.attrib.get("data-semantic-id")
    }


def reserve_existing_ports(
    root: ET.Element,
    allocator: PortAllocator,
    *,
    exclude: set[str] | None = None,
) -> None:
    excluded = exclude or set()
    for edge_id, cell in edge_records(root).items():
        if edge_id in excluded:
            continue
        allow_reuse = cell.attrib.get("data-allow-port-reuse") == "1"
        exit_port = port_from_style(cell, "exit")
        entry_port = port_from_style(cell, "entry")
        if exit_port and cell.attrib.get("data-from"):
            allocator.reserve(
                cell.attrib["data-from"],
                exit_port[0],
                exit_port[1],
                edge_id,
                allow_reuse=allow_reuse,
                fail_on_conflict=False,
            )
        if entry_port and cell.attrib.get("data-to"):
            allocator.reserve(
                cell.attrib["data-to"],
                entry_port[0],
                entry_port[1],
                edge_id,
                allow_reuse=allow_reuse,
                fail_on_conflict=False,
            )


def existing_edge_spec(cell: ET.Element) -> dict:
    spec = {
        "id": cell.attrib["data-semantic-id"],
        "from": cell.attrib.get("data-from"),
        "to": cell.attrib.get("data-to"),
        "type": cell.attrib.get("data-edge-type", "flow"),
        "label": cell.attrib.get("value", ""),
        "route": cell.attrib.get("data-route", "auto"),
        "allow_port_reuse": cell.attrib.get("data-allow-port-reuse") == "1",
    }
    if cell.attrib.get("data-branch"):
        spec["branch"] = cell.attrib["data-branch"]
    exit_port = port_from_style(cell, "exit")
    entry_port = port_from_style(cell, "entry")
    if exit_port:
        spec["exit_side"], spec["exit_offset"] = exit_port
    if entry_port:
        spec["entry_side"], spec["entry_offset"] = entry_port
    return spec


def phase_cell_spec(cell: ET.Element) -> dict:
    return {
        "id": cell.attrib["data-semantic-id"],
        "label": cell.attrib.get("value", ""),
        "from_rank": int(cell.attrib.get("data-from-rank", "1")),
        "to_rank": int(cell.attrib.get("data-to-rank", "1")),
        "fill_color": cell.attrib.get("data-fill-color", "#f5f5f5"),
    }


def apply_phase_update(
    cell: ET.Element,
    phase: dict,
    values: dict,
    pool_width: float,
) -> None:
    current = phase_cell_spec(cell)
    current.update(phase)
    validate_phase_object(current, f"phase[{current['id']}]")
    cell.attrib["value"] = str(current["label"])
    cell.attrib["data-from-rank"] = str(current["from_rank"])
    cell.attrib["data-to-rank"] = str(current["to_rank"])
    cell.attrib["data-fill-color"] = current["fill_color"]
    style = cell.attrib.get("style", "")
    style = re.sub(r"fillColor=#[0-9A-Fa-f]{6}", f"fillColor={current['fill_color']}", style)
    cell.attrib["style"] = style
    geom = cell.find("mxGeometry")
    if geom is None:
        geom = geometry(cell)
    geom.attrib.update(
        {key: number(value) for key, value in phase_geometry_values(current, values, pool_width).items()}
    )


def edge_route_is_locally_valid(
    cell: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
) -> bool:
    points = edge_polyline(cell, lanes, nodes)
    if len(points) < 2:
        return False
    boundaries = internal_lane_boundaries(lanes)
    node_bounds = {
        semantic_id: node_bounds_in_pool(record, lanes[record["lane"]])
        for semantic_id, record in nodes.items()
    }
    for segment in zip(points, points[1:]):
        axis = segment_axis(segment)
        if axis == "diagonal":
            return False
        if axis == "vertical" and any(
            abs(segment[0][0] - boundary) < LANE_BOUNDARY_CLEARANCE
            for boundary in boundaries
        ):
            return False
        for node_id, bounds in node_bounds.items():
            if node_id in {cell.attrib.get("data-from"), cell.attrib.get("data-to")}:
                continue
            if segment_crosses_bounds(segment, bounds):
                return False
    return True


def read_main_path(pool: ET.Element) -> list[str]:
    raw = pool.attrib.get("data-main-path", "[]")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def patch_tree(tree: ET.ElementTree, changes: dict, allow_geometry_updates: bool) -> dict:
    validate_patch_spec(changes)
    pool = find_pool(tree)
    root = graph_root(tree)
    values = values_from_pool(pool)
    lanes, nodes = lane_node_records(root, pool)
    existing_edges = edge_records(root)
    phases = phase_records(root, pool)
    pool_width = max(
        record["geometry"]["x"] + record["geometry"]["width"]
        for record in lanes.values()
    )

    deleted_edge_ids = set(changes.get("delete_edges", []))
    deleted_node_ids = set(changes.get("delete_nodes", []))
    deleted_phase_ids = set(changes.get("delete_phases", []))
    for edge_id in deleted_edge_ids:
        if edge_id not in existing_edges:
            raise DiagramError(f"Cannot delete missing edge: {edge_id}", code="patch/missing-edge")
    for node_id in deleted_node_ids:
        if node_id not in nodes:
            raise DiagramError(f"Cannot delete missing node: {node_id}", code="patch/missing-node")
    for phase_id in deleted_phase_ids:
        if phase_id not in phases:
            raise DiagramError(f"Cannot delete missing phase: {phase_id}", code="patch/missing-phase")

    undeclared_incident = sorted(
        edge_id
        for edge_id, cell in existing_edges.items()
        if edge_id not in deleted_edge_ids
        and (
            cell.attrib.get("data-from") in deleted_node_ids
            or cell.attrib.get("data-to") in deleted_node_ids
        )
    )
    if undeclared_incident:
        raise DiagramError(
            "Deleting a node requires explicitly deleting every incident edge",
            code="patch/incident-edge",
            evidence={"edges": undeclared_incident},
            supported_fixes=["add-delete-edges"],
        )

    for edge_id in deleted_edge_ids:
        root.remove(existing_edges[edge_id])
    for node_id in deleted_node_ids:
        root.remove(nodes[node_id]["cell"])
    for phase_id in deleted_phase_ids:
        root.remove(phases[phase_id])

    lanes, nodes = lane_node_records(root, pool)
    existing_edges = edge_records(root)
    phases = phase_records(root, pool)
    moved_node_ids: set[str] = set()

    for update in changes.get("update_nodes", []):
        semantic_id = update.get("id")
        if semantic_id not in nodes:
            raise DiagramError(f"Cannot update missing node: {semantic_id}")
        cell = nodes[semantic_id]["cell"]
        if "label" in update:
            cell.attrib["value"] = str(update["label"])
        if "type" in update:
            kind = update["type"]
            if kind not in NODE_STYLES:
                raise DiagramError(f"Unsupported node type: {kind}")
            cell.attrib["style"] = NODE_STYLES[kind]
            cell.attrib["data-node-type"] = kind
        requested_geometry = any(key in update for key in ("x", "y", "width", "height"))
        if requested_geometry and not allow_geometry_updates:
            raise DiagramError("Existing geometry update requires --allow-geometry-updates")
        if requested_geometry:
            moved_node_ids.add(semantic_id)
            geom = cell.find("mxGeometry")
            assert geom is not None
            for key in ("x", "y", "width", "height"):
                if key in update:
                    geom.attrib[key] = number(update[key])
            nodes[semantic_id]["geometry"] = parse_geometry(cell)

    new_nodes = changes.get("nodes", [])
    for node in new_nodes:
        if node["id"] in nodes:
            raise DiagramError(f"Node already exists: {node['id']}")
        if node.get("lane") not in lanes:
            raise DiagramError(f"Unknown lane for node {node.get('id')}: {node.get('lane')}")
        lane_cell = lanes[node["lane"]]["cell"]
        lane_width = lanes[node["lane"]]["geometry"]["width"]
        create_node_cell(root, lane_cell, node, lane_width, values)

    lanes, nodes = lane_node_records(root, pool)
    existing_edges = edge_records(root)
    edge_updates = changes.get("update_edges", [])
    for update in edge_updates:
        if update["id"] not in existing_edges:
            raise DiagramError(f"Cannot update missing edge: {update['id']}", code="patch/missing-edge")
        if "label" in update:
            existing_edges[update["id"]].attrib["value"] = str(update["label"])

    explicit_reroute_ids = {
        update["id"]
        for update in edge_updates
        if update.get("reroute") or any(
            key in update for key in ROUTING_FIELDS if key != "reroute"
        )
    }
    auto_reroute_ids = {
        edge_id
        for edge_id, cell in existing_edges.items()
        if (
            cell.attrib.get("data-from") in moved_node_ids
            or cell.attrib.get("data-to") in moved_node_ids
        )
        and not edge_route_is_locally_valid(cell, lanes, nodes)
    }
    reroute_ids = explicit_reroute_ids | auto_reroute_ids
    allocator = PortAllocator()
    reserve_existing_ports(root, allocator, exclude=reroute_ids)
    update_by_id = {update["id"]: update for update in edge_updates}
    for semantic_id in sorted(reroute_ids):
        cell = existing_edges[semantic_id]
        edge = existing_edge_spec(cell)
        edge.update(
            {
                key: value
                for key, value in update_by_id.get(semantic_id, {}).items()
                if key != "reroute"
            }
        )
        apply_edge_route(cell, edge, lanes, nodes, allocator)

    new_edges = changes.get("edges", [])
    for edge in new_edges:
        if edge["id"] in existing_edges:
            raise DiagramError(f"Edge already exists: {edge['id']}")
        create_edge_cell(root, pool, edge, lanes, nodes, allocator)

    phases = phase_records(root, pool)
    for update in changes.get("update_phases", []):
        phase_id = update["id"]
        if phase_id not in phases:
            raise DiagramError(f"Cannot update missing phase: {phase_id}", code="patch/missing-phase")
        apply_phase_update(phases[phase_id], update, values, pool_width)
    for phase in changes.get("phases", []):
        if phase["id"] in phases:
            raise DiagramError(f"Phase already exists: {phase['id']}", code="patch/duplicate-phase")
        create_phase_cell(root, pool, phase, values, pool_width)

    if "main_path" in changes:
        pool.attrib["data-main-path"] = json.dumps(
            changes["main_path"], ensure_ascii=True, separators=(",", ":")
        )
        pool.attrib["data-schema-version"] = SCHEMA_VERSION
    elif deleted_node_ids.intersection(read_main_path(pool)):
        raise DiagramError(
            "Deleting a main_path node requires supplying the replacement main_path",
            code="patch/main-path",
            evidence={"deleted_nodes": sorted(deleted_node_ids.intersection(read_main_path(pool)))},
            supported_fixes=["supply-main-path"],
        )

    requested_max_rank = max(
        [int(pool.attrib.get("data-max-rank", "1"))]
        + [int(node["rank"]) for node in new_nodes]
    )
    if requested_max_rank > int(pool.attrib.get("data-max-rank", "1")):
        new_lane_height = lane_height(requested_max_rank, values)
        for lane in lanes.values():
            lane["cell"].find("mxGeometry").attrib["height"] = number(new_lane_height)
        pool_geom = pool.find("mxGeometry")
        assert pool_geom is not None
        pool_geom.attrib["height"] = number(values["title_height"] + new_lane_height)
        pool.attrib["data-max-rank"] = str(requested_max_rank)

    phases = phase_records(root, pool)
    for cell in phases.values():
        apply_phase_update(cell, phase_cell_spec(cell), values, pool_width)

    return {
        "updated_nodes": sorted(update["id"] for update in changes.get("update_nodes", [])),
        "updated_edges": sorted(update["id"] for update in edge_updates),
        "auto_rerouted_edges": sorted(auto_reroute_ids - explicit_reroute_ids),
        "added_nodes": sorted(node["id"] for node in new_nodes),
        "added_edges": sorted(edge["id"] for edge in new_edges),
        "deleted_nodes": sorted(deleted_node_ids),
        "deleted_edges": sorted(deleted_edge_ids),
        "added_phases": sorted(phase["id"] for phase in changes.get("phases", [])),
        "updated_phases": sorted(phase["id"] for phase in changes.get("update_phases", [])),
        "deleted_phases": sorted(deleted_phase_ids),
        "main_path_updated": "main_path" in changes,
    }


def edge_waypoints(cell: ET.Element) -> list[tuple[float, float]]:
    geom = cell.find("mxGeometry")
    if geom is None:
        return []
    array = geom.find("./Array[@as='points']")
    if array is None:
        return []
    points: list[tuple[float, float]] = []
    for point in array.findall("mxPoint"):
        try:
            points.append((float(point.attrib["x"]), float(point.attrib["y"])))
        except (KeyError, ValueError):
            continue
    return points


def edge_polyline(
    cell: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
) -> list[tuple[float, float]]:
    source_id = cell.attrib.get("data-from")
    target_id = cell.attrib.get("data-to")
    if source_id not in nodes or target_id not in nodes:
        return []
    exit_port = port_from_style(cell, "exit")
    entry_port = port_from_style(cell, "entry")
    if exit_port is None or entry_port is None:
        return []
    source = nodes[source_id]
    target = nodes[target_id]
    source_bounds = node_bounds_in_pool(source, lanes[source["lane"]])
    target_bounds = node_bounds_in_pool(target, lanes[target["lane"]])
    return compact_points(
        [
            port_point(source_bounds, exit_port[0], exit_port[1]),
            *edge_waypoints(cell),
            port_point(target_bounds, entry_port[0], entry_port[1]),
        ]
    )


def segment_axis(segment: tuple[tuple[float, float], tuple[float, float]]) -> str:
    (x1, y1), (x2, y2) = segment
    if abs(x1 - x2) < GEOMETRY_TOLERANCE:
        return "vertical"
    if abs(y1 - y2) < GEOMETRY_TOLERANCE:
        return "horizontal"
    return "diagonal"


def value_between(value: float, start: float, end: float, *, strict: bool = False) -> bool:
    low, high = sorted((start, end))
    margin = GEOMETRY_TOLERANCE if strict else -GEOMETRY_TOLERANCE
    return low + margin < value < high - margin if strict else low + margin <= value <= high - margin


def segment_crosses_bounds(
    segment: tuple[tuple[float, float], tuple[float, float]],
    bounds: dict[str, float],
) -> bool:
    (x1, y1), (x2, y2) = segment
    axis = segment_axis(segment)
    if axis == "vertical":
        return (
            bounds["left"] + GEOMETRY_TOLERANCE < x1 < bounds["right"] - GEOMETRY_TOLERANCE
            and max(min(y1, y2), bounds["top"]) < min(max(y1, y2), bounds["bottom"])
        )
    if axis == "horizontal":
        return (
            bounds["top"] + GEOMETRY_TOLERANCE < y1 < bounds["bottom"] - GEOMETRY_TOLERANCE
            and max(min(x1, x2), bounds["left"]) < min(max(x1, x2), bounds["right"])
        )
    return False


def segments_conflict(
    first: tuple[tuple[float, float], tuple[float, float]],
    second: tuple[tuple[float, float], tuple[float, float]],
) -> bool:
    first_axis = segment_axis(first)
    second_axis = segment_axis(second)
    if "diagonal" in {first_axis, second_axis}:
        return False

    (ax1, ay1), (ax2, ay2) = first
    (bx1, by1), (bx2, by2) = second
    if first_axis != second_axis:
        vertical = first if first_axis == "vertical" else second
        horizontal = second if first_axis == "vertical" else first
        vx = vertical[0][0]
        hy = horizontal[0][1]
        return value_between(vx, horizontal[0][0], horizontal[1][0], strict=True) and value_between(
            hy, vertical[0][1], vertical[1][1], strict=True
        )

    if first_axis == "vertical" and abs(ax1 - bx1) < GEOMETRY_TOLERANCE:
        overlap = min(max(ay1, ay2), max(by1, by2)) - max(min(ay1, ay2), min(by1, by2))
        return overlap > GEOMETRY_TOLERANCE
    if first_axis == "horizontal" and abs(ay1 - by1) < GEOMETRY_TOLERANCE:
        overlap = min(max(ax1, ax2), max(bx1, bx2)) - max(min(ax1, ax2), min(bx1, bx2))
        return overlap > GEOMETRY_TOLERANCE
    return False


def estimated_text_lines(text: str, width: float, *, diamond: bool = False) -> int:
    usable_width = max(12.0, width - (28.0 if diamond else 16.0))
    capacity = max(1, int(usable_width / 7.0))
    lines = 0
    for logical_line in (text.splitlines() or [""]):
        units = sum(
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            for character in logical_line
        )
        lines += max(1, (units + capacity - 1) // capacity)
    return lines


def validate_tree(tree: ET.ElementTree) -> dict:
    diagnostics: list[dict] = []

    def add(
        code: str,
        severity: str,
        message: str,
        *,
        subject: dict | None = None,
        evidence: dict | None = None,
        supported_fixes: list[str] | None = None,
    ) -> None:
        diagnostics.append(
            make_diagnostic(
                code,
                severity,
                message,
                subject=subject,
                evidence=evidence,
                supported_fixes=supported_fixes,
            )
        )

    try:
        pool = find_pool(tree)
        root = graph_root(tree)
        lanes, nodes = lane_node_records(root, pool)
    except DiagramError as exc:
        diagnostic = exc.diagnostic()
        return {
            "valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "diagnostics": [diagnostic],
        }

    schema_version = pool.attrib.get("data-schema-version", "1")

    semantic_ids: set[str] = set()
    for cell in root.iter("mxCell"):
        semantic_id = cell.attrib.get("data-semantic-id")
        kind = cell.attrib.get("data-kind")
        if semantic_id and kind:
            composite = f"{kind}:{semantic_id}"
            if composite in semantic_ids:
                add(
                    "structure/duplicate-semantic-id",
                    "error",
                    f"Duplicate semantic cell: {composite}",
                    subject={"kind": kind, "id": semantic_id},
                    supported_fixes=["replace-semantic-id"],
                )
            semantic_ids.add(composite)

    cell_ids = {cell.attrib.get("id") for cell in tree.iter("mxCell")}
    edge_cells = [
        cell for cell in root.iter("mxCell") if cell.attrib.get("data-kind") == "edge"
    ]
    for cell in edge_cells:
        if cell.attrib.get("source") not in cell_ids or cell.attrib.get("target") not in cell_ids:
            edge_id = cell.attrib.get("data-semantic-id")
            add(
                "structure/broken-endpoint",
                "error",
                f"Broken edge endpoints: {edge_id}",
                subject={"kind": "edge", "id": edge_id},
                supported_fixes=["repair-edge-endpoints"],
            )

    for semantic_id, record in nodes.items():
        lane = lanes[record["lane"]]
        node_geom = record["geometry"]
        lane_geom = lane["geometry"]
        if node_geom["x"] < 0 or node_geom["x"] + node_geom["width"] > lane_geom["width"]:
            add(
                "layout/node-outside-lane-horizontal",
                "warning",
                f"Node outside lane horizontally: {semantic_id}",
                subject={"kind": "node", "id": semantic_id},
                supported_fixes=["move-node-inside-lane"],
            )
        if node_geom["y"] < 0 or node_geom["y"] + node_geom["height"] > lane_geom["height"]:
            add(
                "layout/node-outside-lane-vertical",
                "warning",
                f"Node outside lane vertically: {semantic_id}",
                subject={"kind": "node", "id": semantic_id},
                supported_fixes=["move-node-inside-lane", "increase-lane-height"],
            )
        if schema_version == SCHEMA_VERSION:
            label = record["cell"].attrib.get("value", "")
            node_type = record["cell"].attrib.get("data-node-type", "process")
            required_lines = estimated_text_lines(
                label,
                node_geom["width"],
                diamond=node_type == "decision",
            )
            available_lines = max(1, int(max(0.0, node_geom["height"] - 8.0) / 16.0))
            if label and required_lines > available_lines:
                add(
                    "text/node-overflow-risk",
                    "warning",
                    f"Node label may not fit: {semantic_id}",
                    subject={"kind": "node", "id": semantic_id},
                    evidence={"estimated_lines": required_lines, "available_lines": available_lines},
                    supported_fixes=["increase-node-height", "shorten-label"],
                )

    port_usage: dict[tuple[str, str, float], list[str]] = {}
    for cell in edge_cells:
        if cell.attrib.get("data-allow-port-reuse") == "1":
            continue
        edge_id = cell.attrib.get("data-semantic-id", cell.attrib.get("id", "unknown"))
        for prefix, endpoint_field in (("exit", "data-from"), ("entry", "data-to")):
            endpoint = cell.attrib.get(endpoint_field)
            port = port_from_style(cell, prefix)
            if endpoint and port:
                key = endpoint, port[0], round(port[1], 4)
                port_usage.setdefault(key, []).append(edge_id)
    for (node_id, side, offset), used_by in sorted(port_usage.items()):
        if len(used_by) > 1:
            add(
                "routing/port-reuse",
                "warning",
                f"Port reused at node {node_id} ({side}@{number(offset)}): {', '.join(sorted(used_by))}",
                subject={"kind": "node", "id": node_id},
                evidence={"side": side, "offset": offset, "edges": sorted(used_by)},
                supported_fixes=["allocate-distinct-port"],
            )

    internal_boundaries = internal_lane_boundaries(lanes)
    node_bounds = {
        semantic_id: node_bounds_in_pool(record, lanes[record["lane"]])
        for semantic_id, record in nodes.items()
    }
    edge_segments: dict[str, list[tuple[tuple[float, float], tuple[float, float]]]] = {}
    for cell in edge_cells:
        edge_id = cell.attrib.get("data-semantic-id", cell.attrib.get("id", "unknown"))
        points = edge_polyline(cell, lanes, nodes)
        segments = list(zip(points, points[1:]))
        edge_segments[edge_id] = segments
        for segment in segments:
            axis = segment_axis(segment)
            if axis == "diagonal":
                add(
                    "routing/non-orthogonal",
                    "warning",
                    f"Non-orthogonal connector segment: {edge_id}",
                    subject={"kind": "edge", "id": edge_id},
                    supported_fixes=["reroute-edge"],
                )
                continue
            if axis == "vertical":
                x = segment[0][0]
                if any(abs(x - boundary) < GEOMETRY_TOLERANCE for boundary in internal_boundaries):
                    add(
                        "routing/lane-boundary-overlap",
                        "warning",
                        f"Connector overlaps a lane boundary: {edge_id}",
                        subject={"kind": "edge", "id": edge_id},
                        evidence={"x": x},
                        supported_fixes=["reroute-edge", "change-routing-zone"],
                    )
                elif any(
                    abs(x - boundary) < LANE_BOUNDARY_CLEARANCE
                    for boundary in internal_boundaries
                ):
                    nearest = min(abs(x - boundary) for boundary in internal_boundaries)
                    add(
                        "routing/lane-boundary-clearance",
                        "warning",
                        "Connector is too close to a lane boundary "
                        f"(< {number(LANE_BOUNDARY_CLEARANCE)} px): {edge_id}",
                        subject={"kind": "edge", "id": edge_id},
                        evidence={"distance": nearest, "minimum": LANE_BOUNDARY_CLEARANCE},
                        supported_fixes=["reroute-edge", "change-routing-zone"],
                    )
            for node_id, bounds in node_bounds.items():
                if node_id in {cell.attrib.get("data-from"), cell.attrib.get("data-to")}:
                    continue
                if segment_crosses_bounds(segment, bounds):
                    add(
                        "routing/node-crossing",
                        "warning",
                        f"Connector crosses node: {edge_id} -> {node_id}",
                        subject={"kind": "edge", "id": edge_id},
                        evidence={"node": node_id},
                        supported_fixes=["reroute-edge"],
                    )

    edge_ids = sorted(edge_segments)
    for index, first_id in enumerate(edge_ids):
        for second_id in edge_ids[index + 1 :]:
            if any(
                segments_conflict(first_segment, second_segment)
                for first_segment in edge_segments[first_id]
                for second_segment in edge_segments[second_id]
            ):
                add(
                    "routing/edge-conflict",
                    "warning",
                    f"Connector segments cross or overlap: {first_id} / {second_id}",
                    subject={"kind": "edge", "id": first_id},
                    evidence={"other_edge": second_id},
                    supported_fixes=["reroute-edge"],
                )

    if schema_version == SCHEMA_VERSION:
        node_ranks = {
            node_id: int(record["cell"].attrib.get("data-rank", "0"))
            for node_id, record in nodes.items()
        }
        main_path = read_main_path(pool)
        if len(main_path) < 2:
            add(
                "semantic/main-path-missing",
                "error",
                "Schema version 2 requires a main path with at least two nodes",
                subject={"kind": "main_path"},
                supported_fixes=["supply-main-path"],
            )
        elif len(main_path) != len(set(main_path)):
            add(
                "semantic/main-path-duplicate",
                "error",
                "Main path contains duplicate nodes",
                subject={"kind": "main_path"},
                supported_fixes=["correct-main-path"],
            )
        if main_path and main_path[0] in nodes and nodes[main_path[0]]["cell"].attrib.get("data-node-type") != "start":
            add(
                "semantic/main-path-start",
                "error",
                "Main path must begin with a start node",
                subject={"kind": "main_path"},
                evidence={"node": main_path[0]},
                supported_fixes=["correct-main-path", "change-node-type"],
            )
        if main_path and main_path[-1] in nodes and nodes[main_path[-1]]["cell"].attrib.get("data-node-type") != "end":
            add(
                "semantic/main-path-end",
                "error",
                "Main path must end with an end node",
                subject={"kind": "main_path"},
                evidence={"node": main_path[-1]},
                supported_fixes=["correct-main-path", "change-node-type"],
            )
        edge_pairs = {
            (cell.attrib.get("data-from"), cell.attrib.get("data-to")): cell
            for cell in edge_cells
        }
        for source_id, target_id in zip(main_path, main_path[1:]):
            if source_id not in nodes or target_id not in nodes:
                add(
                    "semantic/main-path-node",
                    "error",
                    f"Main path references a missing node: {source_id} -> {target_id}",
                    subject={"kind": "main_path"},
                    evidence={"from": source_id, "to": target_id},
                    supported_fixes=["correct-main-path"],
                )
                continue
            edge = edge_pairs.get((source_id, target_id))
            if edge is None:
                add(
                    "semantic/main-path-edge",
                    "error",
                    f"Main path has no edge from {source_id} to {target_id}",
                    subject={"kind": "main_path"},
                    evidence={"from": source_id, "to": target_id},
                    supported_fixes=["add-main-path-edge", "correct-main-path"],
                )
            elif edge.attrib.get("data-route") == "back" or node_ranks[target_id] < node_ranks[source_id]:
                add(
                    "semantic/main-path-rank",
                    "error",
                    f"Main path moves backward from {source_id} to {target_id}",
                    subject={"kind": "main_path"},
                    evidence={"from": source_id, "to": target_id},
                    supported_fixes=["correct-rank", "remove-return-from-main-path"],
                )

        outgoing: dict[str, list[ET.Element]] = {}
        for cell in edge_cells:
            outgoing.setdefault(cell.attrib.get("data-from", ""), []).append(cell)
            if cell.attrib.get("data-edge-type") == "retry":
                source_id = cell.attrib.get("data-from")
                target_id = cell.attrib.get("data-to")
                if source_id in node_ranks and target_id in node_ranks and node_ranks[target_id] >= node_ranks[source_id]:
                    edge_id = cell.attrib.get("data-semantic-id")
                    add(
                        "semantic/retry-direction",
                        "warning",
                        f"Retry edge does not return to an earlier rank: {edge_id}",
                        subject={"kind": "edge", "id": edge_id},
                        supported_fixes=["correct-rank", "change-edge-type"],
                    )

        for node_id, record in nodes.items():
            if record["cell"].attrib.get("data-node-type") != "decision":
                continue
            decision_edges = outgoing.get(node_id, [])
            if len(decision_edges) < 2:
                add(
                    "semantic/decision-branches",
                    "warning",
                    f"Decision node has fewer than two outgoing branches: {node_id}",
                    subject={"kind": "node", "id": node_id},
                    supported_fixes=["add-decision-branch"],
                )
                continue
            branches = [edge.attrib.get("data-branch") for edge in decision_edges]
            if any(branch not in BRANCH_CLASSES for branch in branches) or len(set(branches)) != len(branches):
                add(
                    "semantic/decision-outcome",
                    "warning",
                    f"Decision branches must use distinct positive/negative outcomes: {node_id}",
                    subject={"kind": "node", "id": node_id},
                    evidence={"branches": branches},
                    supported_fixes=["label-decision-branches"],
                )

        starts = [
            node_id
            for node_id, record in nodes.items()
            if record["cell"].attrib.get("data-node-type") == "start"
        ]
        if not starts:
            add(
                "semantic/start-missing",
                "warning",
                "No start node is defined",
                supported_fixes=["add-start-node"],
            )
        else:
            reachable = set(starts)
            frontier = list(starts)
            adjacency: dict[str, list[str]] = {}
            for cell in edge_cells:
                adjacency.setdefault(cell.attrib.get("data-from", ""), []).append(cell.attrib.get("data-to", ""))
            while frontier:
                current = frontier.pop()
                for target in adjacency.get(current, []):
                    if target in nodes and target not in reachable:
                        reachable.add(target)
                        frontier.append(target)
            for node_id, record in nodes.items():
                if node_id not in reachable and record["cell"].attrib.get("data-node-type") != "note":
                    add(
                        "semantic/unreachable-node",
                        "warning",
                        f"Node is unreachable from a start node: {node_id}",
                        subject={"kind": "node", "id": node_id},
                        supported_fixes=["connect-node", "remove-node"],
                    )

        max_rank = max(node_ranks.values(), default=1)
        for phase_id, cell in phase_records(root, pool).items():
            try:
                from_rank = int(cell.attrib.get("data-from-rank", "0"))
                to_rank = int(cell.attrib.get("data-to-rank", "0"))
            except ValueError:
                from_rank = to_rank = 0
            if from_rank < 1 or to_rank < from_rank or to_rank > max_rank:
                add(
                    "semantic/phase-range",
                    "warning",
                    f"Phase has an invalid rank range: {phase_id}",
                    subject={"kind": "phase", "id": phase_id},
                    evidence={"from_rank": from_rank, "to_rank": to_rank, "max_rank": max_rank},
                    supported_fixes=["correct-phase-range"],
                )

    unique_diagnostics: list[dict] = []
    seen_diagnostics: set[str] = set()
    for diagnostic in diagnostics:
        key = json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
        if key not in seen_diagnostics:
            seen_diagnostics.add(key)
            unique_diagnostics.append(diagnostic)
    diagnostics = sorted(
        unique_diagnostics,
        key=lambda item: (
            0 if item["severity"] == "error" else 1,
            item["code"],
            item["message"],
        ),
    )
    errors = [item["message"] for item in diagnostics if item["severity"] == "error"]
    warnings = [item["message"] for item in diagnostics if item["severity"] == "warning"]

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "diagnostics": diagnostics,
        "schema_version": schema_version,
        "lanes": len(lanes),
        "nodes": len(nodes),
        "edges": len(edge_cells),
    }


def semantic_cells(tree: ET.ElementTree) -> dict[str, ET.Element]:
    cells: dict[str, ET.Element] = {}
    for cell in graph_root(tree).iter("mxCell"):
        kind = cell.attrib.get("data-kind")
        semantic_id = cell.attrib.get("data-semantic-id")
        if kind and semantic_id:
            cells[f"{kind}:{semantic_id}"] = cell
    return cells


def element_signature(element: ET.Element | None):
    if element is None:
        return None
    return (
        element.tag,
        tuple(sorted(element.attrib.items())),
        (element.text or "").strip(),
        tuple(element_signature(child) for child in list(element)),
    )


def allowed_changes_from_patch(changes: dict | None, before: ET.ElementTree, after: ET.ElementTree) -> set[str]:
    if not changes:
        return set()
    allowed = {
        f"node:{item['id']}" for item in changes.get("update_nodes", []) if item.get("id")
    }
    allowed.update(
        f"edge:{item['id']}" for item in changes.get("update_edges", []) if item.get("id")
    )
    allowed.update(
        f"phase:{item['id']}" for item in changes.get("update_phases", []) if item.get("id")
    )
    geometry_nodes = {
        item["id"]
        for item in changes.get("update_nodes", [])
        if item.get("id") and any(field in item for field in ("x", "y", "width", "height"))
    }
    if geometry_nodes:
        for edge_id, cell in edge_records(graph_root(before)).items():
            if cell.attrib.get("data-from") in geometry_nodes or cell.attrib.get("data-to") in geometry_nodes:
                allowed.add(f"edge:{edge_id}")
    if "main_path" in changes:
        allowed.add("pool:main")
    if changes.get("nodes"):
        before_pool = find_pool(before)
        after_pool = find_pool(after)
        if before_pool.attrib.get("data-max-rank") != after_pool.attrib.get("data-max-rank"):
            allowed.add("pool:main")
            after_root = graph_root(after)
            after_lanes, _ = lane_node_records(after_root, after_pool)
            allowed.update(f"lane:{lane_id}" for lane_id in after_lanes)
    return allowed


def allowed_missing_from_patch(changes: dict | None) -> set[str]:
    if not changes:
        return set()
    allowed = {f"node:{semantic_id}" for semantic_id in changes.get("delete_nodes", [])}
    allowed.update(f"edge:{semantic_id}" for semantic_id in changes.get("delete_edges", []))
    allowed.update(f"phase:{semantic_id}" for semantic_id in changes.get("delete_phases", []))
    return allowed


def compare_trees(before: ET.ElementTree, after: ET.ElementTree, changes: dict | None = None) -> dict:
    before_cells = semantic_cells(before)
    after_cells = semantic_cells(after)
    missing = sorted(set(before_cells) - set(after_cells))
    added = sorted(set(after_cells) - set(before_cells))
    changed_geometry: list[str] = []
    changed_attributes: list[str] = []

    for key in sorted(set(before_cells) & set(after_cells)):
        before_cell = before_cells[key]
        after_cell = after_cells[key]
        if element_signature(before_cell.find("mxGeometry")) != element_signature(after_cell.find("mxGeometry")):
            changed_geometry.append(key)
        before_attributes = tuple(sorted(before_cell.attrib.items()))
        after_attributes = tuple(sorted(after_cell.attrib.items()))
        if before_attributes != after_attributes:
            changed_attributes.append(key)

    allowed = allowed_changes_from_patch(changes, before, after)
    allowed_missing = allowed_missing_from_patch(changes)
    unexpected_missing = sorted(set(missing) - allowed_missing)
    unexpected_geometry = sorted(set(changed_geometry) - allowed)
    unexpected_attributes = sorted(set(changed_attributes) - allowed)
    preserved = not unexpected_missing and not unexpected_geometry and not unexpected_attributes
    return {
        "preserved": preserved,
        "existing_cells_checked": len(set(before_cells) & set(after_cells)),
        "added_cells": added,
        "missing_cells": missing,
        "changed_geometry": changed_geometry,
        "changed_attributes": changed_attributes,
        "allowed_changes": sorted(allowed),
        "allowed_missing": sorted(allowed_missing),
        "unexpected_missing": unexpected_missing,
        "unexpected_geometry": unexpected_geometry,
        "unexpected_attributes": unexpected_attributes,
    }


def inspect_tree(tree: ET.ElementTree) -> dict:
    pool = find_pool(tree)
    root = graph_root(tree)
    lanes, nodes = lane_node_records(root, pool)
    phases = phase_records(root, pool)
    lane_items = sorted(lanes.items(), key=lambda item: item[1]["geometry"]["x"])
    lane_index = {lane_id: index for index, (lane_id, _) in enumerate(lane_items)}

    lane_specs = [
        {
            "id": lane_id,
            "label": record["cell"].attrib.get("value", ""),
            "x": record["geometry"]["x"],
            "width": record["geometry"]["width"],
        }
        for lane_id, record in lane_items
    ]
    node_specs = []
    for node_id, record in sorted(
        nodes.items(),
        key=lambda item: (
            int(item[1]["cell"].attrib.get("data-rank", "0")),
            lane_index.get(item[1]["lane"], 999),
            item[0],
        ),
    ):
        node_specs.append(
            {
                "id": node_id,
                "lane": record["lane"],
                "rank": int(record["cell"].attrib.get("data-rank", "0")),
                "type": record["cell"].attrib.get("data-node-type", "process"),
                "label": record["cell"].attrib.get("value", ""),
                **record["geometry"],
            }
        )

    edge_specs = []
    for edge_id, cell in sorted(edge_records(root).items()):
        edge = existing_edge_spec(cell)
        points = edge_waypoints(cell)
        if points:
            edge["waypoints"] = [{"x": x, "y": y} for x, y in points]
        edge_specs.append(edge)

    phase_specs = [phase_cell_spec(cell) for _, cell in sorted(phases.items())]
    validation = validate_tree(tree)
    return {
        "compatible": True,
        "schema_version": pool.attrib.get("data-schema-version", "1"),
        "title": pool.attrib.get("value", ""),
        "main_path": read_main_path(pool),
        "lanes": lane_specs,
        "phases": phase_specs,
        "nodes": node_specs,
        "edges": edge_specs,
        "validation": validation,
    }


def ensure_output_available(output: Path, force: bool) -> None:
    if output.exists() and not force:
        raise DiagramError(
            f"Output already exists: {output}; use --force to replace it",
            code="delivery/output-exists",
            evidence={"output": str(output)},
            supported_fixes=["choose-new-output", "use-force"],
        )


def write_tree(tree: ET.ElementTree, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="  ")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".candidate", dir=output.parent
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        tree.write(
            temporary_path,
            encoding="utf-8",
            xml_declaration=False,
            short_empty_elements=True,
        )
        with temporary_path.open("ab") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def file_receipt(path: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": str(path), "bytes": size, "sha256": digest.hexdigest()}


def ensure_different(input_path: Path, output_path: Path) -> None:
    if input_path.resolve() == output_path.resolve():
        raise DiagramError("Input and output must differ; review the new file before replacing the original")


def command_build(args: argparse.Namespace) -> None:
    ensure_output_available(args.output, args.force)
    spec = load_json(args.spec)
    tree = build_tree(spec)
    result = validate_tree(tree)
    if not result["valid"]:
        raise DiagramError(
            "Generated diagram failed validation",
            code="delivery/validation-failed",
            evidence={"diagnostics": result["diagnostics"]},
        )
    write_tree(tree, args.output)
    result.update({"operation": "build", "output": file_receipt(args.output)})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_patch(args: argparse.Namespace) -> None:
    ensure_different(args.input, args.output)
    ensure_output_available(args.output, args.force)
    tree = ET.parse(args.input)
    patch_receipt = patch_tree(tree, load_json(args.changes), args.allow_geometry_updates)
    result = validate_tree(tree)
    if not result["valid"]:
        raise DiagramError(
            "Patched diagram failed validation",
            code="delivery/validation-failed",
            evidence={"diagnostics": result["diagnostics"]},
        )
    write_tree(tree, args.output)
    result.update(
        {
            "operation": "patch",
            "patch_receipt": patch_receipt,
            "output": file_receipt(args.output),
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_validate(args: argparse.Namespace) -> None:
    result = validate_tree(ET.parse(args.input))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"] or (args.strict and result["warnings"]):
        raise SystemExit(1)


def command_compare(args: argparse.Namespace) -> None:
    changes = load_json(args.changes) if args.changes else None
    result = compare_trees(ET.parse(args.before), ET.parse(args.after), changes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["preserved"]:
        raise SystemExit(1)


def command_inspect(args: argparse.Namespace) -> None:
    print(json.dumps(inspect_tree(ET.parse(args.input)), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a new editable Draw.io file")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--force", action="store_true", help="Replace an existing output file")
    build.set_defaults(func=command_build)

    patch = subparsers.add_parser("patch", help="Incrementally patch an existing generated Draw.io file")
    patch.add_argument("--input", type=Path, required=True)
    patch.add_argument("--changes", type=Path, required=True)
    patch.add_argument("--output", type=Path, required=True)
    patch.add_argument("--allow-geometry-updates", action="store_true")
    patch.add_argument("--force", action="store_true", help="Replace an existing output file")
    patch.set_defaults(func=command_patch)

    validate = subparsers.add_parser("validate", help="Validate structure and visual routing heuristics")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--strict", action="store_true", help="Fail when quality warnings are present")
    validate.set_defaults(func=command_validate)

    compare = subparsers.add_parser("compare", help="Prove that all existing semantic cells were preserved")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--changes", type=Path, help="Allow cells named in a patch file to change")
    compare.set_defaults(func=command_compare)

    inspect = subparsers.add_parser("inspect", help="Inspect compatible semantic metadata and geometry")
    inspect.add_argument("--input", type=Path, required=True)
    inspect.set_defaults(func=command_inspect)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except DiagramError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": [str(exc)],
                    "warnings": [],
                    "diagnostics": [exc.diagnostic()],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except (json.JSONDecodeError, ET.ParseError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if isinstance(exc, json.JSONDecodeError):
            code = "input/json-invalid"
        elif isinstance(exc, ET.ParseError):
            code = "input/drawio-xml-invalid"
        elif isinstance(exc, OSError):
            code = "delivery/io-error"
        else:
            code = "input/invalid"
        diagnostic = make_diagnostic(code, "error", str(exc))
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": [str(exc)],
                    "warnings": [],
                    "diagnostics": [diagnostic],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
