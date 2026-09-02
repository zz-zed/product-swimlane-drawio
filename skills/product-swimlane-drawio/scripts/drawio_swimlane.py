#!/usr/bin/env python3
"""Build, inspect, patch, validate, and compare editable Draw.io swimlane diagrams."""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
import xml.etree.ElementTree as ET

from swimlane_core import contracts, document, geometry as core_geometry, metadata


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

FIXED_ASPECT_NODE_TYPES = {"start", "end"}
LABELED_FIXED_NODE_MIN_SIZE = 48.0
PROCESS_TEXT_LINE_HEIGHT = 14.0
PROCESS_VERTICAL_PADDING = 10.0
MAX_AUTOMATIC_PROCESS_HEIGHT = 66.0
EXCESSIVE_HEIGHT_TOLERANCE = 8.0
MIN_INTERNAL_SEGMENT = 16.0
NEAR_PARALLEL_CLEARANCE = 16.0
EDGE_LABEL_FONT_SIZE = 11.0
EDGE_LABEL_HEIGHT = 18.0
EDGE_LABEL_PADDING = 8.0
EDGE_LABEL_VERTICAL_PADDING = 2.0
EDGE_LABEL_GAP = 5.0
ROUTE_BEND_PENALTY = 32.0
ROUTE_CONFLICT_PENALTY = 4000.0
ROUTE_LABEL_CONFLICT_PENALTY = 2500.0

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

SLOT_CLASSES = {"left", "main", "right"}
SLOT_ORDER = {"left": 0, "main": 1, "right": 2}
SLOT_GAP = 20.0
SLOT_SIDE_PADDING = 20.0
PROFILE_SLOT_GAPS = {"compact": 20.0, "review": 32.0, "long-form": 40.0}
PROFILE_SIDE_PADDING = {"compact": 20.0, "review": 24.0, "long-form": 32.0}
PROFILE_ROW_GAPS = {"compact": 80.0, "review": 96.0, "long-form": 104.0}
ANCHOR_SIDES = {"left", "right"}
BEHAVIOR_PATTERNS = {
    "linear",
    "approval-loop",
    "request-response",
    "fork-join",
    "fan-in",
    "lifecycle",
    "custom",
}
LAYOUT_PROFILES = {"compact", "review", "long-form"}
PHASE_PRESENTATIONS = {"bands", "rail"}
PHASE_RAIL_WIDTH = 76.0
FLOW_ROLES = {
    "main", "branch", "fork", "join", "return", "retry", "exception", "response",
}

TOP_LEVEL_FIELDS = {
    "schema_version", "title", "lanes", "nodes", "edges", "canvas",
    "main_path", "phases", "behavior_pattern", "groups", "layout",
}
LANE_FIELDS = {"id", "label", "width"}
NODE_FIELDS = {
    "id", "lane", "rank", "type", "label", "width", "height", "x", "y",
    "slot", "anchor",
}
EDGE_FIELDS = {
    "id", "from", "to", "type", "label", "route", "branch",
    "exit_side", "entry_side", "exit_offset", "entry_offset",
    "allow_port_reuse", "waypoints", "flow_role", "outcome",
}
CANVAS_FIELDS = {
    "x", "y", "title_height", "lane_header_height", "row_gap",
    "top_padding", "bottom_padding",
}
PHASE_FIELDS = {"id", "label", "from_rank", "to_rank", "fill_color"}
ANCHOR_FIELDS = {"node", "side"}
LAYOUT_FIELDS = {"profile", "phase_presentation"}
PATCH_FIELDS = {
    "update_lanes", "lanes", "delete_lanes",
    "update_nodes", "update_edges", "nodes", "edges", "delete_nodes",
    "delete_edges", "update_phases", "phases", "delete_phases",
    "update_groups", "groups", "delete_groups", "main_path",
}
LANE_PATCH_FIELDS = LANE_FIELDS | {"before", "after"}
LANE_UPDATE_FIELDS = LANE_FIELDS
NODE_UPDATE_FIELDS = {"id", "label", "type", "x", "y", "width", "height"}
EDGE_UPDATE_FIELDS = EDGE_FIELDS | {"reroute"}
PHASE_UPDATE_FIELDS = PHASE_FIELDS


def validate_number(value, subject: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise contracts.DiagramError(
            f"{subject} must be a number",
            code="schema/type",
            subject={"kind": subject},
            evidence={"expected": "number"},
        )
    number_value = float(value)
    if minimum is not None and number_value < minimum:
        raise contracts.DiagramError(
            f"{subject} must be at least {document.number(minimum)}",
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
        raise contracts.DiagramError(f"Invalid empty semantic id derived from {value!r}")
    return cleaned


def mx_id(kind: str, semantic_id: str) -> str:
    return f"psd-{kind}-{clean_id(semantic_id)}"


def require_unique(items: list[dict], label: str) -> None:
    seen: set[str] = set()
    for item in items:
        semantic_id = item.get("id")
        if not semantic_id:
            raise contracts.DiagramError(f"Every {label} must have an id")
        if semantic_id in seen:
            raise contracts.DiagramError(f"Duplicate {label} id: {semantic_id}")
        seen.add(semantic_id)


def validate_lane_object(
    lane: dict,
    subject: str,
    *,
    patch_addition: bool = False,
    update: bool = False,
    minimum_width: float = 120,
) -> None:
    contracts.require_mapping(lane, subject)
    allowed = (
        LANE_UPDATE_FIELDS
        if update
        else LANE_PATCH_FIELDS if patch_addition else LANE_FIELDS
    )
    contracts.reject_unknown_fields(lane, allowed, subject)
    required = ("id",) if update else ("id", "label")
    for field in required:
        if field not in lane:
            raise contracts.DiagramError(
                f"Missing required field in {subject}: {field}",
                code="schema/required",
                subject={"kind": subject},
                evidence={"field": field},
            )
    contracts.validate_semantic_id(lane["id"], f"{subject}.id")
    if "label" in lane:
        contracts.require_string(lane["label"], f"{subject}.label")
    if "width" in lane:
        validate_number(lane["width"], f"{subject}.width", minimum=minimum_width)
    if patch_addition:
        placements = [field for field in ("before", "after") if field in lane]
        if len(placements) != 1:
            raise contracts.DiagramError(
                f"{subject} must specify exactly one of before or after",
                code="patch/lane-placement",
                subject={"kind": "lane", "id": lane.get("id")},
                supported_fixes=["set-before", "set-after"],
            )
        contracts.validate_semantic_id(lane[placements[0]], f"{subject}.{placements[0]}")


def validate_node_object(node: dict, subject: str) -> None:
    contracts.require_mapping(node, subject)
    contracts.reject_unknown_fields(node, NODE_FIELDS, subject)
    for field in ("id", "lane", "rank", "type", "label"):
        if field not in node:
            raise contracts.DiagramError(
                f"Missing required field in {subject}: {field}",
                code="schema/required",
                subject={"kind": subject},
                evidence={"field": field},
            )
    contracts.validate_semantic_id(node["id"], f"{subject}.id")
    contracts.validate_semantic_id(node["lane"], f"{subject}.lane")
    if isinstance(node["rank"], bool) or not isinstance(node["rank"], int) or node["rank"] < 1:
        raise contracts.DiagramError(
            f"{subject}.rank must be an integer greater than or equal to 1",
            code="schema/range",
            subject={"kind": "node", "id": node.get("id")},
            evidence={"field": "rank", "actual": node.get("rank")},
        )
    if node["type"] not in NODE_STYLES:
        raise contracts.DiagramError(
            f"Unsupported node type: {node['type']}",
            code="schema/enum",
            subject={"kind": "node", "id": node.get("id")},
            evidence={"field": "type", "allowed": sorted(NODE_STYLES)},
        )
    contracts.require_string(node["label"], f"{subject}.label", allow_empty=True)
    if node["type"] == "end" and node["label"].strip():
        raise contracts.DiagramError(
            f"{subject}.label must be empty for a solid end node",
            code="schema/end-label-not-empty",
            subject={"kind": "node", "id": node.get("id")},
            evidence={"label": node["label"]},
            supported_fixes=["clear-end-label"],
        )
    for field in ("width", "height"):
        if field in node:
            validate_number(node[field], f"{subject}.{field}", minimum=1)
    for field in ("x", "y"):
        if field in node:
            validate_number(node[field], f"{subject}.{field}")
    if "slot" in node:
        slot = contracts.require_string(node["slot"], f"{subject}.slot")
        if slot not in SLOT_CLASSES:
            raise contracts.DiagramError(
                f"Unsupported lane-local slot: {slot}",
                code="schema/enum",
                subject={"kind": "node", "id": node.get("id")},
                evidence={"field": "slot", "allowed": sorted(SLOT_CLASSES)},
            )
    if "anchor" in node:
        anchor = contracts.require_mapping(node["anchor"], f"{subject}.anchor")
        contracts.reject_unknown_fields(anchor, ANCHOR_FIELDS, f"{subject}.anchor")
        for field in ("node", "side"):
            if field not in anchor:
                raise contracts.DiagramError(
                    f"Missing required field in {subject}.anchor: {field}",
                    code="schema/required",
                    subject={"kind": "node", "id": node.get("id")},
                    evidence={"field": field},
                )
        contracts.validate_semantic_id(anchor["node"], f"{subject}.anchor.node")
        side = contracts.require_string(anchor["side"], f"{subject}.anchor.side")
        if side not in ANCHOR_SIDES:
            raise contracts.DiagramError(
                f"Unsupported note anchor side: {side}",
                code="schema/enum",
                subject={"kind": "node", "id": node.get("id")},
                evidence={"field": "anchor.side", "allowed": sorted(ANCHOR_SIDES)},
            )
        if node["type"] != "note":
            raise contracts.DiagramError(
                f"{subject}.anchor is supported only for note nodes",
                code="semantic/anchor-node-type",
                subject={"kind": "node", "id": node.get("id")},
                supported_fixes=["change-node-to-note", "remove-anchor"],
            )
    if (
        node["type"] in FIXED_ASPECT_NODE_TYPES
        and "width" in node
        and "height" in node
        and abs(float(node["width"]) - float(node["height"])) >= core_geometry.GEOMETRY_TOLERANCE
    ):
        raise contracts.DiagramError(
            f"{subject} requires equal width and height for fixed-aspect node type {node['type']}",
            code="geometry/fixed-aspect-ratio",
            subject={"kind": "node", "id": node.get("id")},
            evidence={"width": node["width"], "height": node["height"]},
            supported_fixes=["set-equal-width-and-height", "remove-one-size-dimension"],
        )


def validate_edge_object(edge: dict, subject: str, *, update: bool = False) -> None:
    contracts.require_mapping(edge, subject)
    contracts.reject_unknown_fields(edge, EDGE_UPDATE_FIELDS if update else EDGE_FIELDS, subject)
    required = ("id",) if update else ("id", "from", "to")
    for field in required:
        if field not in edge:
            raise contracts.DiagramError(
                f"Missing required field in {subject}: {field}",
                code="schema/required",
                subject={"kind": subject},
                evidence={"field": field},
            )
    contracts.validate_semantic_id(edge["id"], f"{subject}.id")
    for field in ("from", "to"):
        if field in edge:
            contracts.validate_semantic_id(edge[field], f"{subject}.{field}")
    if "type" in edge and edge["type"] not in EDGE_TYPES:
        raise contracts.DiagramError(
            f"Unsupported edge type: {edge['type']}",
            code="schema/enum",
            subject={"kind": "edge", "id": edge.get("id")},
            evidence={"field": "type", "allowed": sorted(EDGE_TYPES)},
        )
    if "route" in edge and edge["route"] not in ROUTE_CLASSES:
        raise contracts.DiagramError(
            f"Unsupported route class: {edge['route']}",
            code="schema/enum",
            subject={"kind": "edge", "id": edge.get("id")},
            evidence={"field": "route", "allowed": sorted(ROUTE_CLASSES)},
        )
    if "branch" in edge and edge["branch"] not in BRANCH_CLASSES:
        raise contracts.DiagramError(
            f"Unsupported branch class: {edge['branch']}",
            code="schema/enum",
            subject={"kind": "edge", "id": edge.get("id")},
            evidence={"field": "branch", "allowed": sorted(BRANCH_CLASSES)},
        )
    if "flow_role" in edge and edge["flow_role"] not in FLOW_ROLES:
        raise contracts.DiagramError(
            f"Unsupported flow role: {edge['flow_role']}",
            code="schema/enum",
            subject={"kind": "edge", "id": edge.get("id")},
            evidence={"field": "flow_role", "allowed": sorted(FLOW_ROLES)},
        )
    if "outcome" in edge:
        contracts.validate_semantic_id(edge["outcome"], f"{subject}.outcome")
    for field in ("exit_side", "entry_side"):
        if field in edge:
            validate_side(edge[field], field)
    for field in ("exit_offset", "entry_offset"):
        if field in edge:
            validate_offset(edge[field], field)
    if "label" in edge:
        contracts.require_string(edge["label"], f"{subject}.label", allow_empty=True)
    if "allow_port_reuse" in edge and not isinstance(edge["allow_port_reuse"], bool):
        raise contracts.DiagramError(
            f"{subject}.allow_port_reuse must be a boolean",
            code="schema/type",
            subject={"kind": "edge", "id": edge.get("id")},
        )
    if "reroute" in edge and not isinstance(edge["reroute"], bool):
        raise contracts.DiagramError(
            f"{subject}.reroute must be a boolean",
            code="schema/type",
            subject={"kind": "edge", "id": edge.get("id")},
        )
    if "waypoints" in edge:
        contracts.require_list(edge["waypoints"], f"{subject}.waypoints")
        normalize_waypoints(edge["waypoints"])


def validate_phase_object(phase: dict, subject: str, *, update: bool = False) -> None:
    contracts.require_mapping(phase, subject)
    contracts.reject_unknown_fields(phase, PHASE_UPDATE_FIELDS, subject)
    required = ("id",) if update else ("id", "label", "from_rank", "to_rank")
    for field in required:
        if field not in phase:
            raise contracts.DiagramError(
                f"Missing required field in {subject}: {field}",
                code="schema/required",
                subject={"kind": subject},
                evidence={"field": field},
            )
    contracts.validate_semantic_id(phase["id"], f"{subject}.id")
    if "label" in phase:
        contracts.require_string(phase["label"], f"{subject}.label")
    for field in ("from_rank", "to_rank"):
        if field in phase:
            value = phase[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise contracts.DiagramError(
                    f"{subject}.{field} must be an integer greater than or equal to 1",
                    code="schema/range",
                    subject={"kind": "phase", "id": phase.get("id")},
                    evidence={"field": field, "actual": value},
                )
    if "from_rank" in phase and "to_rank" in phase and phase["to_rank"] < phase["from_rank"]:
        raise contracts.DiagramError(
            f"{subject}.to_rank must not be less than from_rank",
            code="schema/range",
            subject={"kind": "phase", "id": phase.get("id")},
        )
    if "fill_color" in phase:
        color = contracts.require_string(phase["fill_color"], f"{subject}.fill_color")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise contracts.DiagramError(
                f"{subject}.fill_color must use #RRGGBB format",
                code="schema/format",
                subject={"kind": "phase", "id": phase.get("id")},
            )


def validate_build_spec(spec: dict) -> str:
    contracts.require_mapping(spec, "spec")
    contracts.reject_unknown_fields(spec, TOP_LEVEL_FIELDS, "spec")
    for field in ("title", "lanes", "nodes", "edges"):
        if field not in spec:
            raise contracts.DiagramError(
                f"Missing required field: {field}",
                code="schema/required",
                subject={"kind": "spec"},
                evidence={"field": field},
            )

    if "schema_version" in spec:
        contracts.require_string(spec["schema_version"], "spec.schema_version")
    schema_version = spec.get("schema_version", "1")
    if schema_version not in {"1", *contracts.STRUCTURED_SCHEMA_VERSIONS}:
        raise contracts.DiagramError(
            f"Unsupported schema_version: {schema_version}",
            code="schema/version",
            subject={"kind": "spec"},
            evidence={
                "supported": ["1", contracts.SCHEMA_VERSION, contracts.V3_SCHEMA_VERSION],
                "actual": schema_version,
            },
            supported_fixes=["migrate-spec"],
        )
    contracts.require_string(spec["title"], "spec.title")
    lanes = contracts.require_list(spec["lanes"], "spec.lanes")
    nodes = contracts.require_list(spec["nodes"], "spec.nodes")
    edges = contracts.require_list(spec["edges"], "spec.edges")
    if not lanes:
        raise contracts.DiagramError("spec.lanes must contain at least one lane", code="schema/min-items")
    if schema_version in contracts.STRUCTURED_SCHEMA_VERSIONS and len(nodes) < 2:
        raise contracts.DiagramError(
            f"schema version {schema_version} requires at least two nodes",
            code="schema/min-items",
        )

    if schema_version == contracts.V3_SCHEMA_VERSION:
        if "behavior_pattern" not in spec:
            raise contracts.DiagramError(
                "schema_version 3 requires behavior_pattern",
                code="schema/required",
                subject={"kind": "spec"},
                evidence={"field": "behavior_pattern"},
            )
        behavior_pattern = contracts.require_string(
            spec["behavior_pattern"], "spec.behavior_pattern"
        )
        if behavior_pattern not in BEHAVIOR_PATTERNS:
            raise contracts.DiagramError(
                f"Unsupported behavior pattern: {behavior_pattern}",
                code="schema/enum",
                subject={"kind": "spec"},
                evidence={
                    "field": "behavior_pattern",
                    "allowed": sorted(BEHAVIOR_PATTERNS),
                },
            )
        if "layout" in spec:
            layout = contracts.require_mapping(spec["layout"], "spec.layout")
            contracts.reject_unknown_fields(layout, LAYOUT_FIELDS, "spec.layout")
            if "profile" in layout:
                profile = contracts.require_string(layout["profile"], "spec.layout.profile")
                if profile not in LAYOUT_PROFILES:
                    raise contracts.DiagramError(
                        f"Unsupported layout profile: {profile}",
                        code="schema/enum",
                        subject={"kind": "layout"},
                        evidence={"field": "profile", "allowed": sorted(LAYOUT_PROFILES)},
                    )
            if "phase_presentation" in layout:
                presentation = contracts.require_string(
                    layout["phase_presentation"],
                    "spec.layout.phase_presentation",
                )
                if presentation not in PHASE_PRESENTATIONS:
                    raise contracts.DiagramError(
                        f"Unsupported phase presentation: {presentation}",
                        code="schema/enum",
                        subject={"kind": "layout"},
                        evidence={
                            "field": "phase_presentation",
                            "allowed": sorted(PHASE_PRESENTATIONS),
                        },
                    )
    else:
        v3_fields = sorted(
            field
            for field in ("behavior_pattern", "groups", "layout")
            if field in spec
        )
        if v3_fields:
            raise contracts.DiagramError(
                "v3 layout-intent fields require schema_version 3",
                code="schema/version-field",
                subject={"kind": "spec"},
                evidence={"fields": v3_fields, "schema_version": schema_version},
                supported_fixes=["set-schema-version-3", "remove-v3-fields"],
            )

    for index, lane in enumerate(lanes):
        validate_lane_object(
            lane,
            f"lane[{index}]",
            minimum_width=120 if schema_version in contracts.STRUCTURED_SCHEMA_VERSIONS else 1,
        )

    for index, node in enumerate(nodes):
        validate_node_object(node, f"node[{index}]")
    for index, edge in enumerate(edges):
        validate_edge_object(edge, f"edge[{index}]")
    if schema_version != contracts.V3_SCHEMA_VERSION:
        v3_node_fields = [
            node["id"]
            for node in nodes
            if "slot" in node or "anchor" in node
        ]
        v3_edge_fields = [
            edge["id"]
            for edge in edges
            if "flow_role" in edge or "outcome" in edge
        ]
        if v3_node_fields or v3_edge_fields:
            raise contracts.DiagramError(
                "v3 node or edge fields require schema_version 3",
                code="schema/version-field",
                subject={"kind": "spec"},
                evidence={"nodes": v3_node_fields, "edges": v3_edge_fields},
                supported_fixes=["set-schema-version-3", "remove-v3-fields"],
            )
    require_unique(lanes, "lane")
    require_unique(nodes, "node")
    require_unique(edges, "edge")

    lane_ids = {lane["id"] for lane in lanes}
    node_ids = {node["id"] for node in nodes}
    node_by_id = {node["id"]: node for node in nodes}
    for node in nodes:
        if node["lane"] not in lane_ids:
            raise contracts.DiagramError(
                f"Node {node['id']} references an unknown lane",
                code="semantic/unknown-lane",
                subject={"kind": "node", "id": node["id"]},
                evidence={"lane": node["lane"]},
            )
    for edge in edges:
        missing = [field for field in ("from", "to") if edge[field] not in node_ids]
        if missing:
            raise contracts.DiagramError(
                f"Edge {edge['id']} references a missing node",
                code="semantic/missing-endpoint",
                subject={"kind": "edge", "id": edge["id"]},
                evidence={field: edge[field] for field in missing},
            )

    if "canvas" in spec:
        canvas = contracts.require_mapping(spec["canvas"], "spec.canvas")
        contracts.reject_unknown_fields(canvas, CANVAS_FIELDS, "spec.canvas")
        for field, value in canvas.items():
            minimum = 1 if field in {"title_height", "lane_header_height", "row_gap"} else None
            validate_number(value, f"spec.canvas.{field}", minimum=minimum)

    main_path = spec.get("main_path")
    if schema_version in contracts.STRUCTURED_SCHEMA_VERSIONS and main_path is None:
        raise contracts.DiagramError(
            f"schema_version {schema_version} requires main_path",
            code="schema/required",
            subject={"kind": "spec"},
            evidence={"field": "main_path"},
        )
    if main_path is not None:
        main_path = contracts.validate_id_list(main_path, "spec.main_path")
        if len(main_path) < 2:
            raise contracts.DiagramError("spec.main_path must contain at least two nodes", code="schema/min-items")
        missing = [node_id for node_id in main_path if node_id not in node_ids]
        if missing:
            raise contracts.DiagramError(
                "main_path references missing nodes",
                code="semantic/main-path-node",
                subject={"kind": "main_path"},
                evidence={"missing": missing},
            )
        edge_pairs = {(edge["from"], edge["to"]) for edge in edges}
        for source_id, target_id in zip(main_path, main_path[1:]):
            if (source_id, target_id) not in edge_pairs:
                raise contracts.DiagramError(
                    f"main_path has no edge from {source_id} to {target_id}",
                    code="semantic/main-path-edge",
                    subject={"kind": "main_path"},
                    evidence={"from": source_id, "to": target_id},
                    supported_fixes=["add-main-path-edge", "correct-main-path"],
                )
            if node_by_id[target_id]["rank"] < node_by_id[source_id]["rank"]:
                raise contracts.DiagramError(
                    f"main_path moves backward from {source_id} to {target_id}",
                    code="semantic/main-path-rank",
                    subject={"kind": "main_path"},
                    evidence={"from": source_id, "to": target_id},
                    supported_fixes=["correct-rank", "remove-return-from-main-path"],
                )
        if node_by_id[main_path[0]]["type"] != "start":
            raise contracts.DiagramError(
                "main_path must begin with a start node",
                code="semantic/main-path-start",
                subject={"kind": "main_path"},
                evidence={"node": main_path[0]},
                supported_fixes=["correct-main-path", "change-node-type"],
            )
        if node_by_id[main_path[-1]]["type"] != "end":
            raise contracts.DiagramError(
                "main_path must end with an end node",
                code="semantic/main-path-end",
                subject={"kind": "main_path"},
                evidence={"node": main_path[-1]},
                supported_fixes=["correct-main-path", "change-node-type"],
            )

    phases = contracts.require_list(spec.get("phases", []), "spec.phases")
    for index, phase in enumerate(phases):
        validate_phase_object(phase, f"phase[{index}]")
    require_unique(phases, "phase")
    max_rank = max((node["rank"] for node in nodes), default=1)
    for phase in phases:
        if phase["to_rank"] > max_rank:
            raise contracts.DiagramError(
                f"Phase {phase['id']} extends beyond the maximum node rank",
                code="semantic/phase-range",
                subject={"kind": "phase", "id": phase["id"]},
                evidence={"to_rank": phase["to_rank"], "max_rank": max_rank},
            )

    groups = contracts.require_list(spec.get("groups", []), "spec.groups")
    for index, group in enumerate(groups):
        contracts.validate_group_object(group, f"group[{index}]")
    require_unique(groups, "group")
    if groups and schema_version != contracts.V3_SCHEMA_VERSION:
        raise contracts.DiagramError(
            "groups require schema_version 3",
            code="schema/version-field",
            subject={"kind": "spec"},
            evidence={"field": "groups"},
        )
    group_members: set[str] = set()
    for group in groups:
        if group["lane"] not in lane_ids:
            raise contracts.DiagramError(
                f"Group {group['id']} references an unknown lane",
                code="semantic/unknown-lane",
                subject={"kind": "group", "id": group["id"]},
                evidence={"lane": group["lane"]},
            )
        for node_id in group["nodes"]:
            if node_id not in node_ids:
                raise contracts.DiagramError(
                    f"Group {group['id']} references a missing node",
                    code="semantic/group-node",
                    subject={"kind": "group", "id": group["id"]},
                    evidence={"node": node_id},
                )
            if node_by_id[node_id]["lane"] != group["lane"]:
                raise contracts.DiagramError(
                    f"Group {group['id']} contains a node from another lane",
                    code="semantic/group-lane",
                    subject={"kind": "group", "id": group["id"]},
                    evidence={
                        "node": node_id,
                        "expected_lane": group["lane"],
                        "actual_lane": node_by_id[node_id]["lane"],
                    },
                )
            if node_id in group_members:
                raise contracts.DiagramError(
                    f"Node {node_id} belongs to more than one group",
                    code="semantic/group-membership",
                    subject={"kind": "node", "id": node_id},
                    supported_fixes=["keep-one-group-membership"],
                )
            group_members.add(node_id)

    if schema_version == contracts.V3_SCHEMA_VERSION:
        for node in nodes:
            anchor = node.get("anchor")
            if not anchor:
                continue
            target = node_by_id.get(anchor["node"])
            if target is None:
                raise contracts.DiagramError(
                    f"Note {node['id']} anchors to a missing node",
                    code="semantic/anchor-target",
                    subject={"kind": "node", "id": node["id"]},
                    evidence={"anchor": anchor["node"]},
                )
            if target["lane"] != node["lane"] or target["rank"] != node["rank"]:
                raise contracts.DiagramError(
                    f"Note {node['id']} must share lane and rank with its anchor in v3",
                    code="semantic/anchor-alignment",
                    subject={"kind": "node", "id": node["id"]},
                    evidence={
                        "anchor": anchor["node"],
                        "note_lane": node["lane"],
                        "anchor_lane": target["lane"],
                        "note_rank": node["rank"],
                        "anchor_rank": target["rank"],
                    },
                    supported_fixes=["align-note-with-anchor", "remove-anchor"],
                )
            if "slot" in node and node["slot"] != anchor["side"]:
                raise contracts.DiagramError(
                    f"Note {node['id']} slot conflicts with its anchor side",
                    code="layout/anchor-slot-conflict",
                    subject={"kind": "node", "id": node["id"]},
                    evidence={"slot": node["slot"], "anchor_side": anchor["side"]},
                    supported_fixes=["match-slot-to-anchor", "remove-explicit-slot"],
                )

        occupied: dict[tuple[str, int, str], str] = {}
        for node in nodes:
            if "x" in node:
                continue
            slot = effective_node_slot(node)
            key = (node["lane"], int(node["rank"]), slot)
            if key in occupied:
                raise contracts.DiagramError(
                    f"Nodes {occupied[key]} and {node['id']} occupy the same lane, rank, and slot",
                    code="layout/slot-conflict",
                    subject={"kind": "node", "id": node["id"]},
                    evidence={"lane": key[0], "rank": key[1], "slot": key[2]},
                    supported_fixes=["assign-distinct-slots", "change-rank", "set-explicit-geometry"],
                )
            occupied[key] = node["id"]
    return schema_version


def validate_patch_spec(changes: dict) -> None:
    contracts.require_mapping(changes, "patch")
    contracts.reject_unknown_fields(changes, PATCH_FIELDS, "patch")
    for field in (
        "update_lanes", "lanes", "update_nodes", "update_edges", "nodes",
        "edges", "update_phases", "phases", "update_groups", "groups",
    ):
        if field in changes:
            contracts.require_list(changes[field], f"patch.{field}")
    for index, update in enumerate(changes.get("update_lanes", [])):
        validate_lane_object(update, f"update_lane[{index}]", update=True)
    for index, lane in enumerate(changes.get("lanes", [])):
        validate_lane_object(lane, f"new_lane[{index}]", patch_addition=True)
    for index, update in enumerate(changes.get("update_nodes", [])):
        contracts.require_mapping(update, f"update_node[{index}]")
        contracts.reject_unknown_fields(update, NODE_UPDATE_FIELDS, f"update_node[{index}]")
        contracts.validate_semantic_id(update.get("id"), f"update_node[{index}].id")
        if "type" in update and update["type"] not in NODE_STYLES:
            raise contracts.DiagramError(f"Unsupported node type: {update['type']}", code="schema/enum")
        if "label" in update:
            contracts.require_string(update["label"], f"update_node[{index}].label", allow_empty=True)
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
    for index, update in enumerate(changes.get("update_groups", [])):
        contracts.validate_group_object(update, f"update_group[{index}]", update=True)
    for index, group in enumerate(changes.get("groups", [])):
        contracts.validate_group_object(group, f"new_group[{index}]")
    for field in (
        "delete_lanes", "delete_nodes", "delete_edges", "delete_phases",
        "delete_groups",
    ):
        if field in changes:
            contracts.validate_id_list(changes[field], f"patch.{field}")
    if "main_path" in changes:
        contracts.validate_id_list(changes["main_path"], "patch.main_path")
    for field in (
        "update_lanes", "lanes", "update_nodes", "update_edges", "nodes",
        "edges", "update_phases", "phases", "update_groups", "groups",
    ):
        if field in changes:
            require_unique(changes[field], field)


def canvas_values(spec: dict) -> dict:
    values = dict(DEFAULTS)
    values.update(spec.get("canvas", {}))
    return values


def layout_profile(spec: dict) -> str:
    if spec.get("schema_version") != contracts.V3_SCHEMA_VERSION:
        return "legacy"
    return spec.get("layout", {}).get("profile", "review")


def profile_slot_gap(spec: dict) -> float:
    profile = layout_profile(spec)
    return PROFILE_SLOT_GAPS.get(profile, SLOT_GAP)


def profile_side_padding(spec: dict) -> float:
    profile = layout_profile(spec)
    return PROFILE_SIDE_PADDING.get(profile, SLOT_SIDE_PADDING)


def inferred_spec_route_class(edge: dict, nodes: dict[str, dict]) -> str:
    requested = edge.get("route", "auto")
    if requested != "auto":
        return requested
    def rank(node: dict) -> int:
        if "rank" in node:
            return int(node["rank"])
        return int(node["cell"].attrib.get(contracts.DATA_RANK, "0"))

    source_rank = rank(nodes[edge["from"]])
    target_rank = rank(nodes[edge["to"]])
    if edge.get("type") == "retry" or target_rank < source_rank:
        return "back"
    if target_rank > source_rank:
        return "forward"
    return "side"


def effective_node_slot(node: dict) -> str:
    """Return the semantic horizontal slot used by the v3 compiler."""
    if "slot" in node:
        return node["slot"]
    if node.get("anchor"):
        return node["anchor"]["side"]
    return "main"


def v3_slot_row_required_width(
    row_nodes: list[dict],
    *,
    gap: float,
    side_padding: float,
) -> float:
    left_extent, right_extent = v3_slot_row_extents(row_nodes, gap=gap)
    return 2 * side_padding + left_extent + right_extent


def v3_slot_row_extents(
    row_nodes: list[dict],
    *,
    gap: float,
) -> tuple[float, float]:
    by_slot = {effective_node_slot(node): node for node in row_nodes}
    if "main" not in by_slot:
        content = sum(node_size(node)[0] for node in row_nodes) + gap * max(
            0, len(row_nodes) - 1
        )
        return content / 2.0, content / 2.0

    main_width = node_size(by_slot["main"])[0]
    left_extent = main_width / 2.0
    right_extent = main_width / 2.0
    if "left" in by_slot:
        left_extent += gap + node_size(by_slot["left"])[0]
    if "right" in by_slot:
        right_extent += gap + node_size(by_slot["right"])[0]
    return left_extent, right_extent


def v3_lane_main_axes(
    spec: dict,
    lane_widths: dict[str, float],
) -> dict[str, float]:
    gap = profile_slot_gap(spec)
    side_padding = profile_side_padding(spec)
    by_lane_rank: dict[tuple[str, int], list[dict]] = {}
    for node in spec["nodes"]:
        if "x" in node:
            continue
        by_lane_rank.setdefault((node["lane"], int(node["rank"])), []).append(node)

    extents: dict[str, tuple[float, float]] = {}
    for (lane_id, _rank), row_nodes in by_lane_rank.items():
        if not any(effective_node_slot(node) == "main" for node in row_nodes):
            continue
        left, right = v3_slot_row_extents(row_nodes, gap=gap)
        previous = extents.get(lane_id, (0.0, 0.0))
        extents[lane_id] = max(previous[0], left), max(previous[1], right)

    axes: dict[str, float] = {}
    for lane_id, (left, right) in extents.items():
        spare = max(0.0, lane_widths[lane_id] - left - right - 2 * side_padding)
        axes[lane_id] = side_padding + spare / 2.0 + left
    return axes


def v3_node_x_positions(
    spec: dict,
    lane_widths: dict[str, float],
) -> dict[str, float]:
    """Compile left/main/right slots into deterministic lane-local positions."""
    if spec.get("schema_version") != contracts.V3_SCHEMA_VERSION:
        return {}
    gap = profile_slot_gap(spec)
    lane_axes = v3_lane_main_axes(spec, lane_widths)

    positions: dict[str, float] = {}
    by_lane_rank: dict[tuple[str, int], list[dict]] = {}
    for node in spec["nodes"]:
        if "x" in node:
            continue
        by_lane_rank.setdefault((node["lane"], int(node["rank"])), []).append(node)

    for (lane_id, _rank), row_nodes in by_lane_rank.items():
        ordered = sorted(row_nodes, key=lambda item: SLOT_ORDER[effective_node_slot(item)])
        by_slot = {effective_node_slot(node): node for node in ordered}
        if "main" in by_slot:
            main = by_slot["main"]
            main_width = node_size(main)[0]
            main_x = lane_axes.get(lane_id, lane_widths[lane_id] / 2.0) - main_width / 2.0
            positions[main["id"]] = main_x
            if "left" in by_slot:
                left = by_slot["left"]
                positions[left["id"]] = main_x - gap - node_size(left)[0]
            if "right" in by_slot:
                right = by_slot["right"]
                positions[right["id"]] = main_x + main_width + gap
        else:
            widths = [node_size(node)[0] for node in ordered]
            content_width = sum(widths) + gap * max(0, len(ordered) - 1)
            cursor = (lane_widths[lane_id] - content_width) / 2.0
            for node, width in zip(ordered, widths):
                positions[node["id"]] = cursor
                cursor += width + gap
    return positions


def effective_lane_widths(spec: dict) -> dict[str, float]:
    """Expand automatic-layout lanes enough to host internal back-route gutters."""
    widths = {
        lane["id"]: float(lane.get("width", 200))
        for lane in spec["lanes"]
    }
    nodes = {node["id"]: node for node in spec["nodes"]}
    required_gutter = LANE_BOUNDARY_CLEARANCE + 2 * core_geometry.GEOMETRY_TOLERANCE

    for node in spec["nodes"]:
        if "x" in node:
            continue
        node_width, _ = node_size(node)
        widths[node["lane"]] = max(
            widths[node["lane"]],
            math.ceil(node_width + 8.0),
        )

    if spec.get("schema_version") == contracts.V3_SCHEMA_VERSION:
        gap = profile_slot_gap(spec)
        side_padding = profile_side_padding(spec)
        by_lane_rank: dict[tuple[str, int], list[dict]] = {}
        for node in spec["nodes"]:
            if "x" in node:
                continue
            by_lane_rank.setdefault((node["lane"], int(node["rank"])), []).append(node)
        for (lane_id, _rank), row_nodes in by_lane_rank.items():
            required_width = v3_slot_row_required_width(
                row_nodes,
                gap=gap,
                side_padding=side_padding,
            )
            widths[lane_id] = max(widths[lane_id], float(math.ceil(required_width)))

    for edge in spec["edges"]:
        if inferred_spec_route_class(edge, nodes) != "back":
            continue
        target = nodes[edge["to"]]
        if "x" in target:
            continue
        target_width, _ = node_size(target)
        minimum_width = math.ceil(
            target_width + 2 * required_gutter + core_geometry.GEOMETRY_TOLERANCE
        )
        widths[target["lane"]] = max(widths[target["lane"]], float(minimum_width))

        if spec.get("schema_version") == contracts.V3_SCHEMA_VERSION:
            # Cross-lane returns need a safe escape gutter at the source as
            # well as an entry gutter at the historical target.  Otherwise a
            # centered node can leave only a few pixels between its jetty and
            # the lane boundary, forcing the route through another lane.
            required_side_space = (
                LANE_BOUNDARY_CLEARANCE + ROUTE_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE
            )
            for endpoint in (nodes[edge["from"]], target):
                if "x" in endpoint:
                    continue
                endpoint_width, _ = node_size(endpoint)
                endpoint_minimum = math.ceil(
                    endpoint_width + 2 * required_side_space
                )
                widths[endpoint["lane"]] = max(
                    widths[endpoint["lane"]],
                    float(endpoint_minimum),
                )

    return widths


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


def recommended_process_height(label: str, width: float) -> float:
    if not label:
        return float(NODE_SIZES["process"][1])
    lines = estimated_text_lines(label, width)
    return min(
        MAX_AUTOMATIC_PROCESS_HEIGHT,
        max(
            float(NODE_SIZES["process"][1]),
            PROCESS_VERTICAL_PADDING + PROCESS_TEXT_LINE_HEIGHT * lines,
        ),
    )


def node_size(node: dict) -> tuple[float, float]:
    kind = node.get("type", "process")
    if kind not in NODE_SIZES:
        raise contracts.DiagramError(f"Unsupported node type: {kind}")
    default_width, default_height = NODE_SIZES[kind]
    explicit_width = node.get("width")
    explicit_height = node.get("height")
    if kind in FIXED_ASPECT_NODE_TYPES:
        if explicit_width is not None and explicit_height is not None:
            if abs(float(explicit_width) - float(explicit_height)) >= core_geometry.GEOMETRY_TOLERANCE:
                raise contracts.DiagramError(
                    f"Fixed-aspect node {node.get('id', '<unknown>')} requires equal width and height",
                    code="geometry/fixed-aspect-ratio",
                    subject={"kind": "node", "id": node.get("id")},
                    evidence={"width": explicit_width, "height": explicit_height},
                    supported_fixes=["set-equal-width-and-height", "remove-one-size-dimension"],
                )
            diameter = float(explicit_width)
        elif explicit_width is not None:
            diameter = float(explicit_width)
        elif explicit_height is not None:
            diameter = float(explicit_height)
        else:
            diameter = float(default_width)
        if str(node.get("label", "")).strip():
            diameter = max(diameter, LABELED_FIXED_NODE_MIN_SIZE)
        return diameter, diameter

    if kind == "decision" and explicit_width is None:
        label_units = sum(
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            for character in str(node.get("label", ""))
            if character != "\n"
        )
        width = min(168.0, max(float(default_width), 8.0 + label_units * 4.0))
    else:
        width = float(explicit_width if explicit_width is not None else default_width)
    if explicit_height is not None:
        height = float(explicit_height)
    elif kind == "process":
        height = recommended_process_height(str(node.get("label", "")), width)
    elif kind == "decision":
        lines = estimated_text_lines(str(node.get("label", "")), width, diamond=True)
        height = max(float(default_height), 16.0 + 16.0 * lines)
    else:
        height = float(default_height)
    return width, height


def adaptive_canvas_values(spec: dict) -> dict:
    """Increase automatic rank spacing when nodes and edge labels need more room."""
    values = canvas_values(spec)
    if "row_gap" in spec.get("canvas", {}):
        return values
    if spec.get("schema_version") == contracts.V3_SCHEMA_VERSION:
        values["row_gap"] = PROFILE_ROW_GAPS[layout_profile(spec)]

    rank_heights: dict[int, float] = {}
    for node in spec["nodes"]:
        _, height = node_size(node)
        rank = int(node["rank"])
        rank_heights[rank] = max(rank_heights.get(rank, 0.0), height)

    required = float(values["row_gap"])
    labeled_pairs = {
        (int(next(node for node in spec["nodes"] if node["id"] == edge["from"])["rank"]),
         int(next(node for node in spec["nodes"] if node["id"] == edge["to"])["rank"]))
        for edge in spec["edges"]
        if str(edge.get("label", "")).strip()
    }
    for rank in sorted(rank_heights):
        next_rank = rank + 1
        if next_rank not in rank_heights:
            continue
        # A label on an adjacent-rank connector is normally placed beside a
        # vertical segment or above a horizontal carrier.  Reserving a full
        # extra 12 px around the label made every decision-heavy diagram grow
        # by one 8 px grid unit per rank, even when the default 96 px gap was
        # already sufficient.  Keep a small safety allowance and let the
        # label-aware router increase spacing only when it cannot find a clear
        # carrier.
        label_space = EDGE_LABEL_HEIGHT + 4.0 if (rank, next_rank) in labeled_pairs else 16.0
        needed = rank_heights[rank] / 2 + rank_heights[next_rank] / 2 + label_space
        required = max(required, needed)
    values["row_gap"] = math.ceil(required / 8.0) * 8.0
    return values


def lane_height(max_rank: int, values: dict) -> float:
    content = values["top_padding"] + max(0, max_rank - 1) * values["row_gap"]
    return values["lane_header_height"] + content + 40 + values["bottom_padding"]


def node_y(node: dict, values: dict) -> float:
    _, height = node_size(node)
    center = values["lane_header_height"] + values["top_padding"] + (int(node["rank"]) - 1) * values["row_gap"]
    return float(node.get("y", center - height / 2))


def create_lane_cell(
    root: ET.Element,
    pool: ET.Element,
    lane: dict,
    values: dict,
    *,
    x: float,
    width: float,
    height: float,
) -> ET.Element:
    cell = ET.SubElement(
        root,
        "mxCell",
        {
            "id": mx_id("lane", lane["id"]),
            "parent": pool.attrib["id"],
            "vertex": "1",
            "value": lane["label"],
            "style": (
                "swimlane;html=1;"
                f"startSize={document.number(values['lane_header_height'])};"
                "horizontal=1;rounded=0;strokeWidth=1;fontSize=13;fontStyle=1;"
                "fillColor=#dae8fc;swimlaneFillColor=#ffffff;"
            ),
            contracts.DATA_KIND: "lane",
            contracts.DATA_SEMANTIC_ID: lane["id"],
        },
    )
    document.geometry(
        cell,
        x=x,
        y=values["title_height"],
        width=width,
        height=height,
    )
    return cell


def create_node_cell(
    root: ET.Element,
    parent: ET.Element,
    node: dict,
    lane_width: float,
    values: dict,
    *,
    automatic_x: float | None = None,
    group_id: str | None = None,
) -> ET.Element:
    kind = node.get("type", "process")
    width, height = node_size(node)
    x = float(
        node.get(
            "x",
            automatic_x if automatic_x is not None else (lane_width - width) / 2,
        )
    )
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
            contracts.DATA_KIND: "node",
            contracts.DATA_SEMANTIC_ID: node["id"],
            contracts.DATA_NODE_TYPE: kind,
            contracts.DATA_LANE_ID: node["lane"],
            contracts.DATA_RANK: str(node["rank"]),
        },
    )
    if "slot" in node or automatic_x is not None:
        cell.attrib[contracts.DATA_SLOT] = effective_node_slot(node)
    if node.get("anchor"):
        cell.attrib[contracts.DATA_ANCHOR] = json.dumps(
            node["anchor"], ensure_ascii=True, separators=(",", ":")
        )
    if group_id:
        cell.attrib[contracts.DATA_GROUP_ID] = group_id
    document.geometry(cell, x=x, y=y, width=width, height=height)
    return cell


def phase_geometry_values(
    phase: dict,
    values: dict,
    pool_width: float,
    *,
    presentation: str = "bands",
    rail_width: float = PHASE_RAIL_WIDTH,
) -> dict[str, float]:
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
    width = rail_width if presentation == "rail" else pool_width
    return {"x": 0.0, "y": top, "width": width, "height": max(24.0, bottom - top)}


def create_phase_cell(
    root: ET.Element,
    pool: ET.Element,
    phase: dict,
    values: dict,
    pool_width: float,
) -> ET.Element:
    fill_color = phase.get("fill_color", "#f5f5f5")
    presentation = pool.attrib.get(contracts.DATA_PHASE_PRESENTATION, "bands")
    rail_width = float(pool.attrib.get(contracts.DATA_PHASE_RAIL_WIDTH, PHASE_RAIL_WIDTH))
    if presentation == "rail":
        phase_style = (
            "rounded=0;whiteSpace=wrap;html=1;verticalAlign=middle;align=center;"
            "spacing=4;fontSize=10;fontStyle=1;fontColor=#555555;"
            "fillColor=#ffffff;strokeColor=#808080;pointerEvents=0;"
        )
    else:
        phase_style = (
            "rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;align=left;"
            "spacingTop=4;spacingLeft=6;fontSize=10;fontStyle=1;fontColor=#666666;"
            f"fillColor={fill_color};fillOpacity=12;strokeColor=#b3b3b3;"
            "strokeOpacity=55;dashed=1;pointerEvents=0;"
        )
    cell = ET.SubElement(
        root,
        "mxCell",
        {
            "id": mx_id("phase", phase["id"]),
            "parent": pool.attrib["id"],
            "vertex": "1",
            "connectable": "0",
            "value": str(phase["label"]),
            "style": phase_style,
            contracts.DATA_KIND: "phase",
            contracts.DATA_SEMANTIC_ID: phase["id"],
            contracts.DATA_FROM_RANK: str(phase["from_rank"]),
            contracts.DATA_TO_RANK: str(phase["to_rank"]),
            contracts.DATA_FILL_COLOR: fill_color,
        },
    )
    if pool.attrib.get(contracts.DATA_SCHEMA_VERSION) == contracts.V3_SCHEMA_VERSION:
        cell.attrib[contracts.DATA_PRESENTATION] = presentation
    document.geometry(
        cell,
        **phase_geometry_values(
            phase,
            values,
            pool_width,
            presentation=presentation,
            rail_width=rail_width,
        ),
    )
    return cell


def normalize_phase_layering(
    root: ET.Element,
    pool: ET.Element,
    *,
    restore_lane_fill_without_phases: bool = False,
) -> None:
    """Keep phase bands behind editable content and visible through lane bodies."""
    phases = [
        cell
        for cell in list(root)
        if cell.attrib.get(contracts.DATA_KIND) == "phase"
        and cell.attrib.get("parent") == pool.attrib["id"]
    ]
    lanes = [
        cell
        for cell in list(root)
        if cell.attrib.get(contracts.DATA_KIND) == "lane"
        and cell.attrib.get("parent") == pool.attrib["id"]
    ]
    presentation = pool.attrib.get(contracts.DATA_PHASE_PRESENTATION, "bands")
    if phases or restore_lane_fill_without_phases:
        for lane in lanes:
            document.set_style_option(
                lane,
                "swimlaneFillColor",
                "none" if phases and presentation == "bands" else "#ffffff",
            )
    if not phases:
        return

    # Retain canonical serialization for generated diagrams. Unknown cells
    # need an additional sibling-order guard: a fixed XML slot alone does not
    # preserve their paint order when other parents' descendants move past it.
    layer_order = {"phase": 0, "lane": 1, "node": 2, "edge": 3}
    def is_drawing_cell(cell):
        return (cell.tag == "mxCell" and cell.get(contracts.DATA_KIND) in layer_order
                and bool(cell.get(contracts.DATA_SEMANTIC_ID)))

    children = list(root)
    semantic_positions = [
        index
        for index, cell in enumerate(children)
        if is_drawing_cell(cell)
    ]
    semantic_cells = [children[index] for index in semantic_positions]
    semantic_cells.sort(key=lambda cell: layer_order[cell.attrib[contracts.DATA_KIND]])
    for index, cell in zip(semantic_positions, semantic_cells):
        children[index] = cell
    # A wrapped cell and its metadata are one drawing unit. The parent lives
    # on the nested mxCell; the identity usually lives on the wrapper.
    natives = {element: document.native_cell(element) for element in root}
    original_siblings: dict[str | None, list[ET.Element]] = {}
    for element, cell in natives.items():
        if cell is not None:
            original_siblings.setdefault(cell.get("parent"), []).append(element)
    positions_by_parent: dict[str | None, list[int]] = {}
    for index, element in enumerate(children):
        cell = natives[element]
        if cell is not None:
            positions_by_parent.setdefault(cell.get("parent"), []).append(index)
    for parent, siblings in original_siblings.items():
        if all(is_drawing_cell(cell) for cell in siblings):
            continue
        # Unknown siblings are anchors. Sort only the semantic runs between
        # them, never moving an existing cell across an unknown sibling. If
        # this prevents safe phase layering, validation rejects the candidate.
        ordered: list[ET.Element] = []
        run: list[ET.Element] = []
        for cell in siblings:
            if is_drawing_cell(cell):
                run.append(cell)
            else:
                ordered.extend(sorted(run, key=lambda c: layer_order[c.get(contracts.DATA_KIND)]))
                run.clear()
                ordered.append(cell)
        ordered.extend(sorted(run, key=lambda c: layer_order[c.get(contracts.DATA_KIND)]))
        for index, cell in zip(positions_by_parent[parent], ordered):
            children[index] = cell
    root[:] = children


def validate_side(side: str, field: str) -> str:
    if side not in PORT_SIDES:
        raise contracts.DiagramError(f"Unsupported {field}: {side}")
    return side


def validate_offset(offset, field: str) -> float:
    value = float(offset)
    if not 0.05 <= value <= 0.95:
        raise contracts.DiagramError(f"{field} must be between 0.05 and 0.95")
    return value


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
            raise contracts.DiagramError(
                f"Port {node_id}:{side}@{document.number(offset)} is already used by {used_by}; "
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
        raise contracts.DiagramError(f"No free {side} port remains on node {node_id}")

    def available(
        self,
        node_id: str,
        side: str,
        offset: float,
        *,
        allow_reuse: bool = False,
        minimum_offset_gap: float = 0.0,
    ) -> bool:
        if allow_reuse:
            return True
        if self.key(node_id, side, offset) in self.occupied:
            return False
        return all(
            occupied_node != node_id
            or occupied_side != side
            or abs(occupied_offset - float(offset)) >= minimum_offset_gap
            for occupied_node, occupied_side, occupied_offset in self.occupied
        )


def candidate_port_offsets(
    bounds: dict[str, float],
    side: str,
    other_bounds: dict[str, float],
    other_side: str,
) -> list[float]:
    candidates = list(PORT_OFFSETS)
    if side in {"left", "right"} and other_side in {"left", "right"}:
        other_center_y = other_bounds["top"] + other_bounds["height"] / 2
        candidates.append((other_center_y - bounds["top"]) / bounds["height"])
    elif side in {"top", "bottom"} and other_side in {"top", "bottom"}:
        other_center_x = other_bounds["left"] + other_bounds["width"] / 2
        candidates.append((other_center_x - bounds["left"]) / bounds["width"])
    return sorted(
        {
            round(value, 6)
            for value in candidates
            if 0.05 <= value <= 0.95
        },
        key=lambda value: (abs(value - 0.5), value),
    )


def allocate_port_pair(
    allocator: PortAllocator,
    edge: dict,
    source_bounds: dict[str, float],
    target_bounds: dict[str, float],
    exit_side: str,
    entry_side: str,
    offset_limits: dict[str, dict[str, float]] | None = None,
    *,
    prefer_center_ports: bool = False,
) -> tuple[float, float]:
    """Allocate source and target ports together so simple routes stay aligned."""
    allow_reuse = bool(edge.get("allow_port_reuse", False))
    requested_exit = edge.get("exit_offset")
    requested_entry = edge.get("entry_offset")
    limits = offset_limits or {}

    def allowed(endpoint: str, offset: float) -> bool:
        endpoint_limits = limits.get(endpoint, {})
        return (
            offset >= endpoint_limits.get("min", 0.0) - core_geometry.GEOMETRY_TOLERANCE / 100
            and offset <= endpoint_limits.get("max", 1.0) + core_geometry.GEOMETRY_TOLERANCE / 100
        )

    if requested_exit is not None and requested_entry is not None:
        return (
            allocator.reserve(
                edge["from"], exit_side, requested_exit, edge["id"], allow_reuse=allow_reuse
            ),
            allocator.reserve(
                edge["to"], entry_side, requested_entry, edge["id"], allow_reuse=allow_reuse
            ),
        )

    exit_candidates = (
        [validate_offset(requested_exit, "exit_offset")]
        if requested_exit is not None
        else candidate_port_offsets(source_bounds, exit_side, target_bounds, entry_side)
    )
    entry_candidates = (
        [validate_offset(requested_entry, "entry_offset")]
        if requested_entry is not None
        else candidate_port_offsets(target_bounds, entry_side, source_bounds, exit_side)
    )

    pairs: list[tuple[float, float, float]] = []
    for exit_offset in exit_candidates:
        if not allowed("exit", exit_offset):
            continue
        exit_gap = (
            0.0
            if requested_exit is not None
            else NEAR_PARALLEL_CLEARANCE
            / max(
                source_bounds["height"]
                if exit_side in {"left", "right"}
                else source_bounds["width"],
                1.0,
            )
        )
        if not allocator.available(
            edge["from"],
            exit_side,
            exit_offset,
            allow_reuse=allow_reuse,
            minimum_offset_gap=exit_gap,
        ):
            continue
        source_point = core_geometry.port_point(source_bounds, exit_side, exit_offset)
        matched_entry = None
        if requested_entry is None:
            if exit_side in {"left", "right"} and entry_side in {"left", "right"}:
                matched_entry = (source_point[1] - target_bounds["top"]) / target_bounds["height"]
            elif exit_side in {"top", "bottom"} and entry_side in {"top", "bottom"}:
                matched_entry = (source_point[0] - target_bounds["left"]) / target_bounds["width"]
        dynamic_entries = list(entry_candidates)
        if matched_entry is not None and 0.05 <= matched_entry <= 0.95:
            dynamic_entries.append(round(matched_entry, 6))
        for entry_offset in sorted(set(dynamic_entries)):
            if not allowed("entry", entry_offset):
                continue
            entry_gap = (
                0.0
                if requested_entry is not None
                else NEAR_PARALLEL_CLEARANCE
                / max(
                    target_bounds["height"]
                    if entry_side in {"left", "right"}
                    else target_bounds["width"],
                    1.0,
                )
            )
            if not allocator.available(
                edge["to"],
                entry_side,
                entry_offset,
                allow_reuse=allow_reuse,
                minimum_offset_gap=entry_gap,
            ):
                continue
            target_point = core_geometry.port_point(target_bounds, entry_side, entry_offset)
            if exit_side in {"left", "right"} and entry_side in {"left", "right"}:
                alignment = abs(source_point[1] - target_point[1])
            elif exit_side in {"top", "bottom"} and entry_side in {"top", "bottom"}:
                alignment = abs(source_point[0] - target_point[0])
            else:
                alignment = 0.0
            center_cost = abs(exit_offset - 0.5) + abs(entry_offset - 0.5)
            if prefer_center_ports:
                # Centered side ports are the normal human-authored attachment
                # points.  Chasing endpoint alignment commonly pushes a long
                # return to 0.1/0.9 even when both center ports are free.  Keep
                # alignment as a secondary preference and move off center only
                # when the center port is occupied or explicitly overridden.
                pair_cost = center_cost * 10000.0 + alignment
            else:
                pair_cost = alignment * 100.0 + center_cost
            pairs.append((pair_cost, exit_offset, entry_offset))
    if not pairs:
        raise contracts.DiagramError(f"No compatible free port pair remains for edge {edge['id']}")
    _, exit_offset, entry_offset = min(pairs)
    allocator.reserve(
        edge["from"], exit_side, exit_offset, edge["id"], allow_reuse=allow_reuse
    )
    allocator.reserve(
        edge["to"], entry_side, entry_offset, edge["id"], allow_reuse=allow_reuse
    )
    return exit_offset, entry_offset


def edge_style(
    edge_type: str,
    exit_side: str,
    entry_side: str,
    exit_offset: float,
    entry_offset: float,
) -> str:
    exit_x, exit_y = core_geometry.port_xy(exit_side, exit_offset)
    entry_x, entry_y = core_geometry.port_xy(entry_side, entry_offset)
    extra = "dashed=1;" if edge_type == "async" else ""
    return (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
        "endArrow=block;endFill=1;labelBackgroundColor=#ffffff;fontSize=11;"
        f"exitX={document.number(exit_x)};exitY={document.number(exit_y)};exitDx=0;exitDy=0;"
        f"entryX={document.number(entry_x)};entryY={document.number(entry_y)};entryDx=0;entryDy=0;{extra}"
    )


def infer_route_class(edge: dict, source: dict, target: dict) -> str:
    requested = edge.get("route", "auto")
    if requested not in ROUTE_CLASSES:
        raise contracts.DiagramError(f"Unsupported route class: {requested}")
    if requested != "auto":
        return requested
    source_rank = int(source["cell"].attrib.get(contracts.DATA_RANK, "0"))
    target_rank = int(target["cell"].attrib.get(contracts.DATA_RANK, "0"))
    if edge.get("type") == "retry" or target_rank < source_rank:
        return "back"
    if target_rank > source_rank:
        return "forward"
    return "side"


def preferred_sides(
    edge: dict,
    route_class: str,
    source: dict,
    target: dict,
    lanes: dict[str, dict],
    *,
    main_path_pairs: set[tuple[str, str]] | None = None,
    outgoing_counts: dict[str, int] | None = None,
    bottom_reserved_sources: set[str] | None = None,
    v3_semantics: bool = False,
) -> tuple[str, str]:
    branch = edge.get("branch")
    if branch is not None and branch not in BRANCH_CLASSES:
        raise contracts.DiagramError(f"Unsupported branch class: {branch}")
    source_type = source["cell"].attrib.get(contracts.DATA_NODE_TYPE, "process")
    target_type = target["cell"].attrib.get(contracts.DATA_NODE_TYPE, "process")
    source_rank = int(source["cell"].attrib.get(contracts.DATA_RANK, "0"))
    target_rank = int(target["cell"].attrib.get(contracts.DATA_RANK, "0"))
    is_main_path = (edge["from"], edge["to"]) in (main_path_pairs or set())
    same_lane_down = source["lane"] == target["lane"] and target_rank > source_rank
    actual_split = (outgoing_counts or {}).get(edge["from"], 0) > 1

    if route_class == "back":
        source_index = list(lanes).index(source["lane"])
        target_index = list(lanes).index(target["lane"])
        if edge.get("flow_role") and abs(source_index - target_index) == 1:
            if source_index < target_index:
                default_exit, default_entry = "right", "right"
            else:
                default_exit, default_entry = "left", "left"
        elif edge.get("flow_role") and source_index > target_index:
            default_exit, default_entry = "right", "left"
        elif edge.get("flow_role") and source_index < target_index:
            default_exit, default_entry = "left", "right"
        else:
            default_exit = "left"
            default_entry = "left"
    elif route_class == "forward":
        if (
            is_main_path
            and (
                same_lane_down
                or (
                    v3_semantics
                    and target_rank > source_rank
                    and edge["from"] not in (bottom_reserved_sources or set())
                )
            )
        ):
            default_exit = "bottom"
        elif (
            v3_semantics
            and source_type == "decision"
            and target_type == "end"
            and same_lane_down
        ):
            default_exit = "bottom"
        elif source_type == "decision" and branch == "positive" and source["lane"] != target["lane"]:
            source_index = list(lanes).index(source["lane"])
            target_index = list(lanes).index(target["lane"])
            default_exit = "right" if target_index > source_index else "left"
        elif source_type == "decision" and branch == "positive" and actual_split:
            default_exit = "right"
        elif source_type == "decision" and branch == "negative":
            source_index = list(lanes).index(source["lane"])
            target_index = list(lanes).index(target["lane"])
            default_exit = "right" if target_index > source_index else "left"
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
                raise contracts.DiagramError("Every waypoint object must contain x and y")
            x, y = item["x"], item["y"]
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            x, y = item
        else:
            raise contracts.DiagramError("Waypoints must be {x, y} objects or [x, y] pairs")
        points.append((float(x), float(y)))
    return points


def internal_lane_boundaries(lanes: dict[str, dict]) -> list[float]:
    right_edges = {
        round(record["geometry"]["x"] + record["geometry"]["width"], 4)
        for record in lanes.values()
    }
    if not right_edges:
        return []
    pool_right = max(right_edges)
    return sorted(edge for edge in right_edges if edge < pool_right - core_geometry.GEOMETRY_TOLERANCE)


def safe_vertical_corridor(
    candidate: float,
    boundaries: list[float],
    direction: str,
    pool_width: float,
) -> float:
    """Move an automatic vertical corridor away from internal lane boundaries."""
    if direction not in {"left", "right"}:
        raise contracts.DiagramError(f"Unsupported corridor direction: {direction}")

    lower = POOL_EDGE_MARGIN
    upper = max(lower, pool_width - POOL_EDGE_MARGIN)
    candidate = min(max(candidate, lower), upper)
    safe_gap = LANE_BOUNDARY_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE

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
        if abs(shifted - candidate) < core_geometry.GEOMETRY_TOLERANCE:
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
        if exit_side == "bottom" and entry_side == "top" and abs(sx - tx) < core_geometry.GEOMETRY_TOLERANCE:
            return []
        corridor_y = (sy + ty) / 2
        if exit_side == "bottom":
            return core_geometry.compact_points([(sx, corridor_y), (tx, corridor_y)])
        if exit_side in {"left", "right"}:
            escape_x = sx + (ROUTE_CLEARANCE if exit_side == "right" else -ROUTE_CLEARANCE)
            escape_x = safe_vertical_corridor(
                escape_x, lane_boundaries, exit_side, pool_width
            )
            return core_geometry.compact_points([(escape_x, sy), (escape_x, corridor_y), (tx, corridor_y)])
        return core_geometry.compact_points([(sx, corridor_y), (tx, corridor_y)])

    if route_class == "back":
        if exit_side == entry_side == "left":
            route_x = safe_vertical_corridor(
                min(source_bounds["left"], target_bounds["left"]) - ROUTE_CLEARANCE,
                lane_boundaries,
                "left",
                pool_width,
            )
            return core_geometry.compact_points([(route_x, sy), (route_x, ty)])
        if exit_side == entry_side == "right":
            route_x = safe_vertical_corridor(
                max(source_bounds["right"], target_bounds["right"]) + ROUTE_CLEARANCE,
                lane_boundaries,
                "right",
                pool_width,
            )
            return core_geometry.compact_points([(route_x, sy), (route_x, ty)])
        corridor_y = (sy + ty) / 2
        return core_geometry.compact_points([(sx, corridor_y), (tx, corridor_y)])

    if abs(sy - ty) < core_geometry.GEOMETRY_TOLERANCE:
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
    return core_geometry.compact_points([(route_x, sy), (route_x, ty)])


def automatic_polyline_is_safe(
    points: list[tuple[float, float]],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    source_id: str,
    target_id: str,
) -> bool:
    """Check geometry constraints before accepting an automatic simplification."""
    lane_boundaries = internal_lane_boundaries(lanes)
    obstacle_bounds = {
        node_id: document.node_bounds_in_pool(record, lanes[record["lane"]])
        for node_id, record in nodes.items()
    }
    segments = list(zip(points, points[1:]))
    for index, segment in enumerate(segments):
        axis = core_geometry.segment_axis(segment)
        if axis == "diagonal":
            return False
        if axis == "vertical":
            x = segment[0][0]
            if any(
                abs(x - boundary) < LANE_BOUNDARY_CLEARANCE
                for boundary in lane_boundaries
            ):
                return False
        for node_id, bounds in obstacle_bounds.items():
            if node_id == source_id and index == 0:
                continue
            if node_id == target_id and index == len(segments) - 1:
                continue
            if core_geometry.segment_crosses_bounds(segment, bounds):
                return False
    return True


def simplify_automatic_waypoints(
    route_class: str,
    source_point: tuple[float, float],
    target_point: tuple[float, float],
    exit_side: str,
    entry_side: str,
    points: list[tuple[float, float]],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    source_id: str,
    target_id: str,
) -> list[tuple[float, float]]:
    """Prefer the fewest safe bends for automatically generated orthogonal routes."""
    full_path = core_geometry.remove_collinear_points([source_point, *points, target_point])

    if route_class == "forward" and entry_side == "top":
        sx, sy = source_point
        tx, _ = target_point
        exits_toward_target = (
            exit_side == "right" and tx > sx + core_geometry.GEOMETRY_TOLERANCE
        ) or (
            exit_side == "left" and tx < sx - core_geometry.GEOMETRY_TOLERANCE
        )
        if exits_toward_target:
            direct_elbow = core_geometry.remove_collinear_points(
                [source_point, (tx, sy), target_point]
            )
            if automatic_polyline_is_safe(
                direct_elbow,
                lanes,
                nodes,
                source_id,
                target_id,
            ):
                full_path = direct_elbow

    if route_class == "back" and exit_side == entry_side and entry_side in {"left", "right"}:
        target = nodes[target_id]
        target_lane = lanes[target["lane"]]["geometry"]
        target_bounds = document.node_bounds_in_pool(target, lanes[target["lane"]])
        safe_gap = LANE_BOUNDARY_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE
        if entry_side == "left":
            corridor_x = target_lane["x"] + safe_gap
            has_internal_space = corridor_x < target_bounds["left"] - core_geometry.GEOMETRY_TOLERANCE
        else:
            corridor_x = target_lane["x"] + target_lane["width"] - safe_gap
            has_internal_space = corridor_x > target_bounds["right"] + core_geometry.GEOMETRY_TOLERANCE
        if has_internal_space:
            _, sy = source_point
            _, ty = target_point
            target_lane_path = core_geometry.remove_collinear_points(
                [source_point, (corridor_x, sy), (corridor_x, ty), target_point]
            )
            if automatic_polyline_is_safe(
                target_lane_path,
                lanes,
                nodes,
                source_id,
                target_id,
            ):
                full_path = target_lane_path

    return full_path[1:-1]


def endpoint_direction_is_valid(
    points: list[tuple[float, float]],
    exit_side: str,
    entry_side: str,
) -> bool:
    if len(points) < 2:
        return False
    (sx, sy), (nx, ny) = points[0], points[1]
    (px, py), (tx, ty) = points[-2], points[-1]
    first_axis = core_geometry.segment_axis((points[0], points[1]))
    last_axis = core_geometry.segment_axis((points[-2], points[-1]))
    if first_axis != ("vertical" if exit_side in {"top", "bottom"} else "horizontal"):
        return False
    if last_axis != ("vertical" if entry_side in {"top", "bottom"} else "horizontal"):
        return False
    source_ok = {
        "top": ny <= sy + core_geometry.GEOMETRY_TOLERANCE,
        "bottom": ny >= sy - core_geometry.GEOMETRY_TOLERANCE,
        "left": nx <= sx + core_geometry.GEOMETRY_TOLERANCE,
        "right": nx >= sx - core_geometry.GEOMETRY_TOLERANCE,
    }[exit_side]
    target_ok = {
        "top": py <= ty + core_geometry.GEOMETRY_TOLERANCE,
        "bottom": py >= ty - core_geometry.GEOMETRY_TOLERANCE,
        "left": px <= tx + core_geometry.GEOMETRY_TOLERANCE,
        "right": px >= tx - core_geometry.GEOMETRY_TOLERANCE,
    }[entry_side]
    return source_ok and target_ok


def offset_point(point: tuple[float, float], side: str, distance: float) -> tuple[float, float]:
    x, y = point
    return {
        "top": (x, y - distance),
        "bottom": (x, y + distance),
        "left": (x - distance, y),
        "right": (x + distance, y),
    }[side]


def edge_label_size(label: str) -> tuple[float, float]:
    logical_lines = label.splitlines() or [""]
    widest_units = max(
        sum(
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            for character in line
        )
        for line in logical_lines
    )
    width = min(190.0, max(28.0, widest_units * (EDGE_LABEL_FONT_SIZE * 0.54) + 12.0))
    height = max(EDGE_LABEL_HEIGHT, len(logical_lines) * 14.0 + 4.0)
    return width, height


def label_box_candidates(
    points: list[tuple[float, float]],
    label: str,
) -> list[tuple[int, dict[str, float], float]]:
    if not label.strip():
        return []
    width, height = edge_label_size(label)
    candidates: list[tuple[int, dict[str, float], float]] = []
    for index, segment in enumerate(zip(points, points[1:])):
        length = core_geometry.segment_length(segment)
        axis = core_geometry.segment_axis(segment)
        (x1, y1), (x2, y2) = segment
        if axis == "horizontal" and length >= width + EDGE_LABEL_PADDING:
            low_x, high_x = sorted((x1, x2))
            center_positions = (
                (low_x + high_x) / 2,
                low_x + width / 2 + EDGE_LABEL_GAP,
                low_x + width * 1.5 + EDGE_LABEL_GAP * 2,
                high_x - width / 2 - EDGE_LABEL_GAP,
                high_x - width * 1.5 - EDGE_LABEL_GAP * 2,
            )
            for center_x in dict.fromkeys(round(value, 4) for value in center_positions):
                if not low_x + width / 2 <= center_x <= high_x - width / 2:
                    continue
                for top in (y1 - height - EDGE_LABEL_GAP, y1 + EDGE_LABEL_GAP):
                    box = {
                        "left": center_x - width / 2,
                        "right": center_x + width / 2,
                        "top": top,
                        "bottom": top + height,
                        "width": width,
                        "height": height,
                    }
                    candidates.append((index, box, length + 1000.0))
        elif axis == "vertical" and length >= height + EDGE_LABEL_VERTICAL_PADDING:
            low_y, high_y = sorted((y1, y2))
            center_positions = (
                (low_y + high_y) / 2,
                low_y + height / 2 + EDGE_LABEL_GAP,
                low_y + height * 2 + EDGE_LABEL_GAP * 2,
                high_y - height / 2 - EDGE_LABEL_GAP,
                high_y - height * 2 - EDGE_LABEL_GAP * 2,
            )
            for center_y in dict.fromkeys(round(value, 4) for value in center_positions):
                if not low_y + height / 2 <= center_y <= high_y - height / 2:
                    continue
                for left in (x1 + EDGE_LABEL_GAP, x1 - width - EDGE_LABEL_GAP):
                    box = {
                        "left": left,
                        "right": left + width,
                        "top": center_y - height / 2,
                        "bottom": center_y + height / 2,
                        "width": width,
                        "height": height,
                    }
                    candidates.append((index, box, length))
    return sorted(candidates, key=lambda item: -item[2])


def choose_label_box(
    points: list[tuple[float, float]],
    label: str,
    node_boxes: list[dict[str, float]],
    other_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    other_labels: list[dict[str, float]],
    preferred_side: str | None = None,
    container_bounds: dict[str, float] | None = None,
    prefer_source_proximity: bool = False,
) -> tuple[int, dict[str, float]] | None:
    candidates = label_box_candidates(points, label)

    def side_preference(item: tuple[int, dict[str, float], float]) -> int:
        if preferred_side not in {"left", "right"}:
            return 0
        segment_index, box, _ = item
        segment = list(zip(points, points[1:]))[segment_index]
        if core_geometry.segment_axis(segment) != "vertical":
            return 1
        box_center = box["left"] + box["width"] / 2
        is_preferred = (
            box_center < segment[0][0]
            if preferred_side == "left"
            else box_center > segment[0][0]
        )
        return 0 if is_preferred else 2

    if prefer_source_proximity and points:
        source_x, source_y = points[0]

        def source_distance(item: tuple[int, dict[str, float], float]) -> float:
            _, box, _ = item
            center_x = box["left"] + box["width"] / 2
            center_y = box["top"] + box["height"] / 2
            return (center_x - source_x) ** 2 + (center_y - source_y) ** 2

        candidates = sorted(
            candidates,
            key=lambda item: (source_distance(item), side_preference(item), -item[2]),
        )
    if preferred_side in {"left", "right"}:
        if not prefer_source_proximity:
            candidates = sorted(candidates, key=side_preference)
    for segment_index, box, _ in candidates:
        if container_bounds is not None and not (
            container_bounds["left"] <= box["left"]
            and box["right"] <= container_bounds["right"]
            and container_bounds["top"] <= box["top"]
            and box["bottom"] <= container_bounds["bottom"]
        ):
            continue
        if any(core_geometry.bounds_overlap(box, node_box, gap=2.0) for node_box in node_boxes):
            continue
        if any(core_geometry.bounds_overlap(box, other, gap=2.0) for other in other_labels):
            continue
        if any(core_geometry.segment_intersects_box(segment, box, gap=2.0) for segment in other_segments):
            continue
        own_segments = list(zip(points, points[1:]))
        if any(
            index != segment_index and core_geometry.segment_intersects_box(segment, box, gap=2.0)
            for index, segment in enumerate(own_segments)
        ):
            continue
        return segment_index, box
    return None


def segments_near_parallel(
    first: tuple[tuple[float, float], tuple[float, float]],
    second: tuple[tuple[float, float], tuple[float, float]],
    *,
    clearance: float = NEAR_PARALLEL_CLEARANCE,
) -> bool:
    axis = core_geometry.segment_axis(first)
    if axis != core_geometry.segment_axis(second) or axis not in {"horizontal", "vertical"}:
        return False
    if axis == "horizontal":
        distance = abs(first[0][1] - second[0][1])
        overlap = min(max(first[0][0], first[1][0]), max(second[0][0], second[1][0])) - max(
            min(first[0][0], first[1][0]), min(second[0][0], second[1][0])
        )
    else:
        distance = abs(first[0][0] - second[0][0])
        overlap = min(max(first[0][1], first[1][1]), max(second[0][1], second[1][1])) - max(
            min(first[0][1], first[1][1]), min(second[0][1], second[1][1])
        )
    return core_geometry.GEOMETRY_TOLERANCE < distance < clearance and overlap >= MIN_INTERNAL_SEGMENT


def path_has_hairpin(points: list[tuple[float, float]]) -> bool:
    segments = list(zip(points, points[1:]))
    for first, second in zip(segments, segments[1:]):
        axis = core_geometry.segment_axis(first)
        if axis != core_geometry.segment_axis(second) or axis not in {"horizontal", "vertical"}:
            continue
        first_delta = (
            first[1][0] - first[0][0]
            if axis == "horizontal"
            else first[1][1] - first[0][1]
        )
        second_delta = (
            second[1][0] - second[0][0]
            if axis == "horizontal"
            else second[1][1] - second[0][1]
        )
        if first_delta * second_delta < 0:
            return True
    for first, middle, last in zip(segments, segments[1:], segments[2:]):
        first_axis = core_geometry.segment_axis(first)
        if first_axis != core_geometry.segment_axis(last) or first_axis == core_geometry.segment_axis(middle):
            continue
        first_delta = (
            first[1][0] - first[0][0]
            if first_axis == "horizontal"
            else first[1][1] - first[0][1]
        )
        last_delta = (
            last[1][0] - last[0][0]
            if first_axis == "horizontal"
            else last[1][1] - last[0][1]
        )
        if first_delta * last_delta < 0 and core_geometry.segment_length(middle) < MIN_INTERNAL_SEGMENT:
            return True
    return False


def route_candidates(
    route_class: str,
    source_point: tuple[float, float],
    target_point: tuple[float, float],
    exit_side: str,
    entry_side: str,
    source_bounds: dict[str, float],
    target_bounds: dict[str, float],
    target_lane: dict[str, float],
    pool_width: float,
    pool_height: float,
    lane_boundaries: list[float],
    base_waypoints: list[tuple[float, float]],
    minimum_carrier_span: float = MIN_INTERNAL_SEGMENT,
) -> list[list[tuple[float, float]]]:
    sx, sy = source_point
    tx, ty = target_point
    candidates: list[list[tuple[float, float]]] = []

    def add(full_path: list[tuple[float, float]]) -> None:
        simplified = core_geometry.remove_collinear_points(full_path)
        if simplified not in candidates and endpoint_direction_is_valid(
            simplified, exit_side, entry_side
        ):
            candidates.append(simplified)

    add([source_point, *base_waypoints, target_point])
    if abs(sx - tx) < core_geometry.GEOMETRY_TOLERANCE or abs(sy - ty) < core_geometry.GEOMETRY_TOLERANCE:
        add([source_point, target_point])
    add([source_point, (tx, sy), target_point])
    add([source_point, (sx, ty), target_point])

    source_escape = offset_point(source_point, exit_side, ROUTE_CLEARANCE)
    target_escape = offset_point(target_point, entry_side, ROUTE_CLEARANCE)
    if exit_side == "top" and entry_side == "bottom" and sy > ty:
        jetty = max(
            4.0,
            min(ROUTE_CLEARANCE, (sy - ty - minimum_carrier_span) / 2),
        )
        source_escape = offset_point(source_point, exit_side, jetty)
        target_escape = offset_point(target_point, entry_side, jetty)
    elif exit_side == "bottom" and entry_side == "top" and sy < ty:
        jetty = max(
            4.0,
            min(ROUTE_CLEARANCE, (ty - sy - minimum_carrier_span) / 2),
        )
        source_escape = offset_point(source_point, exit_side, jetty)
        target_escape = offset_point(target_point, entry_side, jetty)
    safe_gap = LANE_BOUNDARY_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE
    target_columns = [
        target_lane["x"] + safe_gap,
        target_lane["x"] + target_lane["width"] - safe_gap,
        source_escape[0],
        target_escape[0],
        POOL_EDGE_MARGIN,
        pool_width - POOL_EDGE_MARGIN,
    ]
    for raw_x in target_columns:
        direction = "right" if raw_x >= (sx + tx) / 2 else "left"
        corridor_x = safe_vertical_corridor(raw_x, lane_boundaries, direction, pool_width)
        add(
            [
                source_point,
                source_escape,
                (corridor_x, source_escape[1]),
                (corridor_x, target_escape[1]),
                target_escape,
                target_point,
            ]
        )

        if route_class == "back":
            # A long cross-lane return must first leave the source rank before
            # traversing other lanes.  Carrying it through the source center
            # line is likely to cut through peers that share that rank.
            carrier_y = (
                source_bounds["bottom"] + ROUTE_CLEARANCE
                if ty < sy
                else source_bounds["top"] - ROUTE_CLEARANCE
            )
            add(
                [
                    source_point,
                    source_escape,
                    (source_escape[0], carrier_y),
                    (corridor_x, carrier_y),
                    (corridor_x, target_escape[1]),
                    target_escape,
                    target_point,
                ]
            )
            outer_y = pool_height - POOL_EDGE_MARGIN
            add(
                [
                    source_point,
                    source_escape,
                    (source_escape[0], outer_y),
                    (corridor_x, outer_y),
                    (corridor_x, target_escape[1]),
                    target_escape,
                    target_point,
                ]
            )

    for corridor_y in ((sy + ty) / 2, sy + ROUTE_CLEARANCE, ty - ROUTE_CLEARANCE):
        add(
            [
                source_point,
                source_escape,
                (source_escape[0], corridor_y),
                (target_escape[0], corridor_y),
                target_escape,
                target_point,
            ]
        )
    return candidates


def candidate_score(
    points: list[tuple[float, float]],
    *,
    route_class: str,
    is_main_path: bool,
    same_lane_down: bool,
    target_lane: dict[str, float],
    target_bounds: dict[str, float],
    entry_side: str,
    existing_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    reciprocal_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    label_choice: tuple[int, dict[str, float]] | None,
    has_label: bool,
) -> float:
    segments = list(zip(points, points[1:]))
    bends = core_geometry.bend_count(points)
    score = core_geometry.polyline_length(points) + bends * ROUTE_BEND_PENALTY
    internal = segments[1:-1]
    score += sum(
        5000.0 for segment in internal if core_geometry.segment_length(segment) < MIN_INTERNAL_SEGMENT
    )
    if path_has_hairpin(points):
        score += 8000.0
    if is_main_path and same_lane_down and bends:
        score += 12000.0 * bends
    if route_class == "forward" and bends > 2:
        score += 5000.0 * (bends - 2)
    for segment in segments:
        for other in existing_segments:
            if core_geometry.segments_conflict(segment, other):
                score += ROUTE_CONFLICT_PENALTY
            elif segments_near_parallel(segment, other):
                score += ROUTE_CONFLICT_PENALTY / 2
        for other in reciprocal_segments:
            if core_geometry.segments_conflict(segment, other) or segments_near_parallel(segment, other):
                score += ROUTE_CONFLICT_PENALTY * 2
    if route_class == "back":
        vertical_x = [
            segment[0][0]
            for segment in segments[1:-1]
            if core_geometry.segment_axis(segment) == "vertical"
        ]
        lane_left = target_lane["x"] + LANE_BOUNDARY_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE
        lane_right = (
            target_lane["x"]
            + target_lane["width"]
            - LANE_BOUNDARY_CLEARANCE
            - core_geometry.GEOMETRY_TOLERANCE
        )
        left_slots = [
            value
            for value in vertical_x
            if lane_left <= value < target_bounds["left"] - core_geometry.GEOMETRY_TOLERANCE
        ]
        right_slots = [
            value
            for value in vertical_x
            if target_bounds["right"] + core_geometry.GEOMETRY_TOLERANCE < value <= lane_right
        ]
        has_target_lane_slot = bool(left_slots if entry_side == "left" else right_slots)
        if entry_side in {"top", "bottom"}:
            has_target_lane_slot = bool(left_slots or right_slots)
        if not has_target_lane_slot:
            score += 6000.0
    if has_label and label_choice is None:
        score += ROUTE_LABEL_CONFLICT_PENALTY
    return score


def route_edge(
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: PortAllocator,
    routing_context: dict | None = None,
) -> dict:
    context = routing_context or {}
    if edge["from"] not in nodes or edge["to"] not in nodes:
        raise contracts.DiagramError(f"Edge {edge.get('id')} references a missing node")
    source = nodes[edge["from"]]
    target = nodes[edge["to"]]
    source_lane = lanes[source["lane"]]
    target_lane = lanes[target["lane"]]
    route_class = infer_route_class(edge, source, target)
    main_path_pairs = context.get("main_path_pairs", set())
    outgoing_counts = context.get("outgoing_counts", {})
    exit_side, entry_side = preferred_sides(
        edge,
        route_class,
        source,
        target,
        lanes,
        main_path_pairs=main_path_pairs,
        outgoing_counts=outgoing_counts,
        bottom_reserved_sources=context.get("bottom_reserved_sources", set()),
        v3_semantics=bool(context.get("v3_semantics", False)),
    )
    source_bounds = document.node_bounds_in_pool(source, source_lane)
    target_bounds = document.node_bounds_in_pool(target, target_lane)
    exit_offset, entry_offset = allocate_port_pair(
        allocator,
        edge,
        source_bounds,
        target_bounds,
        exit_side,
        entry_side,
        context.get("port_limits", {}).get(edge["id"]),
        prefer_center_ports=(
            (
                bool(context.get("v3_semantics", False))
                and (
                    route_class == "back"
                    or source["cell"].attrib.get(contracts.DATA_NODE_TYPE) == "decision"
                )
            )
            or (
                route_class == "forward"
                and source["lane"] != target["lane"]
                and exit_side in {"top", "bottom"}
                and entry_side in {"top", "bottom"}
            )
        ),
    )
    source_point = core_geometry.port_point(source_bounds, exit_side, exit_offset)
    target_point = core_geometry.port_point(target_bounds, entry_side, entry_offset)
    pool_width = max(record["geometry"]["x"] + record["geometry"]["width"] for record in lanes.values())
    pool_height = max(
        record["geometry"]["y"] + record["geometry"]["height"]
        for record in lanes.values()
    )
    label_container = {
        "left": 0.0,
        "right": pool_width,
        "top": 0.0,
        "bottom": pool_height,
    }
    lane_boundaries = internal_lane_boundaries(lanes)
    node_boxes = [
        document.node_bounds_in_pool(record, lanes[record["lane"]])
        for record in nodes.values()
    ]
    existing_paths = context.get("paths", {})
    existing_segments = [
        segment
        for path in existing_paths.values()
        for segment in zip(path, path[1:])
    ]
    reciprocal_segments = [
        segment
        for edge_id, path in existing_paths.items()
        if context.get("endpoints", {}).get(edge_id) == (edge["to"], edge["from"])
        for segment in zip(path, path[1:])
    ]
    existing_labels = list(context.get("labels", {}).values())
    preferred_label_side = context.get("label_sides", {}).get(edge["id"])
    label = str(edge.get("label", ""))
    label_choice: tuple[int, dict[str, float]] | None = None
    if "waypoints" in edge:
        points = normalize_waypoints(edge["waypoints"])
        full_path = core_geometry.compact_points([source_point, *points, target_point])
        label_choice = choose_label_box(
            full_path,
            label,
            node_boxes,
            existing_segments,
            existing_labels,
            preferred_label_side,
            label_container,
            route_class == "back",
        )
    else:
        base_points = automatic_waypoints(
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
        base_points = simplify_automatic_waypoints(
            route_class,
            source_point,
            target_point,
            exit_side,
            entry_side,
            base_points,
            lanes,
            nodes,
            edge["from"],
            edge["to"],
        )
        candidates = route_candidates(
            route_class,
            source_point,
            target_point,
            exit_side,
            entry_side,
            source_bounds,
            target_bounds,
            target_lane["geometry"],
            pool_width,
            pool_height,
            lane_boundaries,
            base_points,
            max(
                MIN_INTERNAL_SEGMENT,
                edge_label_size(label)[1] + EDGE_LABEL_PADDING,
            )
            if label.strip()
            else MIN_INTERNAL_SEGMENT,
        )
        safe_candidates = [
            candidate
            for candidate in candidates
            if automatic_polyline_is_safe(
                candidate, lanes, nodes, edge["from"], edge["to"]
            )
        ]
        if not safe_candidates:
            safe_candidates = [core_geometry.compact_points([source_point, *base_points, target_point])]
        is_main_path = (edge["from"], edge["to"]) in main_path_pairs
        source_rank = int(source["cell"].attrib.get(contracts.DATA_RANK, "0"))
        target_rank = int(target["cell"].attrib.get(contracts.DATA_RANK, "0"))
        same_lane_down = source["lane"] == target["lane"] and target_rank > source_rank
        ranked: list[tuple[float, list[tuple[float, float]], tuple[int, dict[str, float]] | None]] = []
        for candidate in safe_candidates:
            candidate_label = choose_label_box(
                candidate,
                label,
                node_boxes,
                existing_segments,
                existing_labels,
                preferred_label_side,
                label_container,
                route_class == "back",
            )
            score = candidate_score(
                candidate,
                route_class=route_class,
                is_main_path=is_main_path,
                same_lane_down=same_lane_down,
                target_lane=target_lane["geometry"],
                target_bounds=target_bounds,
                entry_side=entry_side,
                existing_segments=existing_segments,
                reciprocal_segments=reciprocal_segments,
                label_choice=candidate_label,
                has_label=bool(label.strip()),
            )
            ranked.append((score, candidate, candidate_label))
        _, full_path, label_choice = min(
            ranked,
            key=lambda item: (
                item[0],
                len(item[1]),
                [(round(x, 4), round(y, 4)) for x, y in item[1]],
            ),
        )
        points = full_path[1:-1]
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
        "full_path": core_geometry.compact_points([source_point, *points, target_point]),
        "label_choice": label_choice,
    }


def polyline_midpoint(points: list[tuple[float, float]]) -> tuple[float, float]:
    total = core_geometry.polyline_length(points)
    if total <= core_geometry.GEOMETRY_TOLERANCE:
        return points[0] if points else (0.0, 0.0)
    remaining = total / 2
    for segment in zip(points, points[1:]):
        length = core_geometry.segment_length(segment)
        if remaining <= length:
            ratio = remaining / length if length else 0.0
            return (
                segment[0][0] + (segment[1][0] - segment[0][0]) * ratio,
                segment[0][1] + (segment[1][1] - segment[0][1]) * ratio,
            )
        remaining -= length
    return points[-1]


def set_edge_label_position(
    cell: ET.Element,
    full_path: list[tuple[float, float]],
    label_choice: tuple[int, dict[str, float]] | None,
) -> None:
    for key in (
        contracts.DATA_LABEL_LEFT,
        contracts.DATA_LABEL_TOP,
        contracts.DATA_LABEL_WIDTH,
        contracts.DATA_LABEL_HEIGHT,
        contracts.DATA_LABEL_SEGMENT,
    ):
        cell.attrib.pop(key, None)
    geom = cell.find("mxGeometry")
    if geom is None:
        return
    existing_offset = geom.find("./mxPoint[@as='offset']")
    if existing_offset is not None:
        geom.remove(existing_offset)
    if label_choice is None:
        return
    segment_index, box = label_choice
    cell.attrib.update(
        {
            contracts.DATA_LABEL_LEFT: document.number(box["left"]),
            contracts.DATA_LABEL_TOP: document.number(box["top"]),
            contracts.DATA_LABEL_WIDTH: document.number(box["width"]),
            contracts.DATA_LABEL_HEIGHT: document.number(box["height"]),
            contracts.DATA_LABEL_SEGMENT: str(segment_index),
        }
    )
    midpoint = polyline_midpoint(full_path)
    desired = (
        box["left"] + box["width"] / 2,
        box["top"] + box["height"] / 2,
    )
    ET.SubElement(
        geom,
        "mxPoint",
        {
            "as": "offset",
            "x": document.number(desired[0] - midpoint[0]),
            "y": document.number(desired[1] - midpoint[1]),
        },
    )


def reflow_automatic_edge_labels(
    root: ET.Element,
    pool: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    preferred_sides: dict[str, str] | None = None,
) -> None:
    """Place labels after all routes exist so later edges cannot invalidate them."""
    records = document.edge_records(root)
    paths = {
        edge_id: document.edge_polyline(cell, lanes, nodes)
        for edge_id, cell in records.items()
    }
    node_boxes = [
        document.node_bounds_in_pool(record, lanes[record["lane"]])
        for record in nodes.values()
    ]
    pool_geometry = document.parse_geometry(pool)
    container = {
        "left": 0.0,
        "right": pool_geometry["width"],
        "top": 0.0,
        "bottom": pool_geometry["height"],
    }
    main_pairs = set(zip(read_main_path(pool), read_main_path(pool)[1:]))

    def order(item: tuple[str, ET.Element]) -> tuple[int, str]:
        edge_id, cell = item
        pair = (cell.attrib.get(contracts.DATA_FROM), cell.attrib.get(contracts.DATA_TO))
        route = cell.attrib.get(contracts.DATA_ROUTE, "auto")
        return (0 if pair in main_pairs else 2 if route == "back" else 1, edge_id)

    assigned_labels: list[dict[str, float]] = []
    for edge_id, cell in sorted(records.items(), key=order):
        path = paths.get(edge_id, [])
        label = cell.attrib.get("value", "")
        other_segments = [
            segment
            for other_id, other_path in paths.items()
            if other_id != edge_id
            for segment in zip(other_path, other_path[1:])
        ]
        choice = choose_label_box(
            path,
            label,
            node_boxes,
            other_segments,
            assigned_labels,
            (preferred_sides or {}).get(edge_id),
            container,
            cell.attrib.get(contracts.DATA_ROUTE, "auto") == "back",
        )
        set_edge_label_position(cell, path, choice)
        if choice is not None:
            assigned_labels.append(choice[1])


def apply_edge_route(
    cell: ET.Element,
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: PortAllocator,
    routing_context: dict | None = None,
) -> ET.Element:
    routed = route_edge(edge, lanes, nodes, allocator, routing_context)
    cell.attrib.update(
        {
            "source": nodes[edge["from"]]["cell"].attrib["id"],
            "target": nodes[edge["to"]]["cell"].attrib["id"],
            "style": routed["style"],
            "value": str(edge.get("label", "")),
            contracts.DATA_EDGE_TYPE: edge.get("type", "flow"),
            contracts.DATA_FROM: edge["from"],
            contracts.DATA_TO: edge["to"],
            contracts.DATA_ROUTE: routed["route"],
            contracts.DATA_EXIT_SIDE: routed["exit_side"],
            contracts.DATA_ENTRY_SIDE: routed["entry_side"],
            contracts.DATA_EXIT_OFFSET: document.number(routed["exit_offset"]),
            contracts.DATA_ENTRY_OFFSET: document.number(routed["entry_offset"]),
            contracts.DATA_ALLOW_PORT_REUSE: "1" if edge.get("allow_port_reuse") else "0",
            contracts.DATA_WAYPOINTS_ORIGIN: "explicit" if "waypoints" in edge else "automatic",
            contracts.DATA_EXIT_SIDE_EXPLICIT: "1" if "exit_side" in edge else "0",
            contracts.DATA_ENTRY_SIDE_EXPLICIT: "1" if "entry_side" in edge else "0",
            contracts.DATA_EXIT_OFFSET_EXPLICIT: "1" if "exit_offset" in edge else "0",
            contracts.DATA_ENTRY_OFFSET_EXPLICIT: "1" if "entry_offset" in edge else "0",
        }
    )
    if edge.get("branch"):
        cell.attrib[contracts.DATA_BRANCH] = edge["branch"]
    else:
        cell.attrib.pop(contracts.DATA_BRANCH, None)
    if edge.get("flow_role"):
        cell.attrib[contracts.DATA_FLOW_ROLE] = edge["flow_role"]
    else:
        cell.attrib.pop(contracts.DATA_FLOW_ROLE, None)
    if edge.get("outcome"):
        cell.attrib[contracts.DATA_OUTCOME] = edge["outcome"]
    else:
        cell.attrib.pop(contracts.DATA_OUTCOME, None)
    document.set_edge_points(cell, routed["points"])
    set_edge_label_position(cell, routed["full_path"], routed["label_choice"])
    if routing_context is not None:
        routing_context.setdefault("paths", {})[edge["id"]] = routed["full_path"]
        routing_context.setdefault("endpoints", {})[edge["id"]] = (edge["from"], edge["to"])
        if routed["label_choice"] is not None:
            routing_context.setdefault("labels", {})[edge["id"]] = routed["label_choice"][1]
    return cell


def create_edge_cell(
    root: ET.Element,
    pool: ET.Element,
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: PortAllocator,
    routing_context: dict | None = None,
) -> ET.Element:
    cell = ET.SubElement(
        root,
        "mxCell",
        {
            "id": mx_id("edge", edge["id"]),
            "parent": pool.attrib["id"],
            "edge": "1",
            contracts.DATA_KIND: "edge",
            contracts.DATA_SEMANTIC_ID: edge["id"],
        },
    )
    document.geometry(cell, relative=1)
    return apply_edge_route(cell, edge, lanes, nodes, allocator, routing_context)


def new_routing_context(
    main_path: list[str],
    edges: list[dict],
    nodes: dict[str, dict] | None = None,
    *,
    v3_semantics: bool = False,
) -> dict:
    bottom_reserved_sources: set[str] = set()
    for edge in edges:
        if not nodes or edge["from"] not in nodes or edge["to"] not in nodes:
            continue
        source = nodes[edge["from"]]
        target = nodes[edge["to"]]
        source_type = (
            source["cell"].attrib.get(contracts.DATA_NODE_TYPE, "process")
            if "cell" in source
            else source.get("type", "process")
        )
        target_type = (
            target["cell"].attrib.get(contracts.DATA_NODE_TYPE, "process")
            if "cell" in target
            else target.get("type", "process")
        )
        source_rank = int(
            source["cell"].attrib.get(contracts.DATA_RANK, "0")
            if "cell" in source
            else source.get("rank", 0)
        )
        target_rank = int(
            target["cell"].attrib.get(contracts.DATA_RANK, "0")
            if "cell" in target
            else target.get("rank", 0)
        )
        source_lane = source.get("lane")
        target_lane = target.get("lane")
        if (
            source_type == "decision"
            and target_type == "end"
            and source_lane == target_lane
            and target_rank > source_rank
        ):
            bottom_reserved_sources.add(edge["from"])
    return {
        "main_path_pairs": set(zip(main_path, main_path[1:])),
        "outgoing_counts": {
            source_id: sum(edge["from"] == source_id for edge in edges)
            for source_id in {edge["from"] for edge in edges}
        },
        "bottom_reserved_sources": bottom_reserved_sources,
        "v3_semantics": v3_semantics,
        "paths": {},
        "endpoints": {},
        "labels": {},
        "port_limits": {},
        "label_sides": {},
    }


def derive_port_limits(
    context: dict,
    edges: list[dict],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
) -> None:
    """Keep automatic branch ports on the non-crossing side of explicit returns."""

    def merge(edge_id: str, endpoint: str, bound: str, value: float) -> None:
        limits = context.setdefault("port_limits", {}).setdefault(edge_id, {}).setdefault(
            endpoint, {}
        )
        if bound == "min":
            limits[bound] = max(limits.get(bound, 0.05), min(0.95, value))
        else:
            limits[bound] = min(limits.get(bound, 0.95), max(0.05, value))

    for back_edge in edges:
        source_id = back_edge.get("from")
        target_id = back_edge.get("to")
        if source_id not in nodes or target_id not in nodes:
            continue
        source = nodes[source_id]
        target = nodes[target_id]
        if infer_route_class(back_edge, source, target) != "back":
            continue
        if "exit_side" not in back_edge or "exit_offset" not in back_edge:
            continue
        back_side = validate_side(back_edge["exit_side"], "exit_side")
        back_offset = validate_offset(back_edge["exit_offset"], "exit_offset")
        source_bounds = document.node_bounds_in_pool(source, lanes[source["lane"]])
        target_bounds = document.node_bounds_in_pool(target, lanes[target["lane"]])
        span = source_bounds["height"] if back_side in {"left", "right"} else source_bounds["width"]
        normalized_clearance = NEAR_PARALLEL_CLEARANCE / max(span, 1.0)

        for other in edges:
            if other.get("id") == back_edge.get("id"):
                continue
            other_source = nodes.get(other.get("from"))
            other_target = nodes.get(other.get("to"))
            if other_source is None or other_target is None:
                continue
            route_class = infer_route_class(other, other_source, other_target)
            exit_side, entry_side = preferred_sides(
                other,
                route_class,
                other_source,
                other_target,
                lanes,
                main_path_pairs=context.get("main_path_pairs", set()),
                outgoing_counts=context.get("outgoing_counts", {}),
                bottom_reserved_sources=context.get("bottom_reserved_sources", set()),
                v3_semantics=bool(context.get("v3_semantics", False)),
            )
            if other.get("to") == source_id and entry_side == back_side and back_side in {"left", "right"}:
                target_is_above = target_bounds["top"] < source_bounds["top"]
                merge(
                    other["id"],
                    "entry",
                    "min" if target_is_above else "max",
                    back_offset + normalized_clearance
                    if target_is_above
                    else back_offset - normalized_clearance,
                )
            if other.get("from") == source_id and exit_side == back_side and back_side in {"top", "bottom"}:
                target_is_right = (
                    target_bounds["left"] + target_bounds["right"]
                    > source_bounds["left"] + source_bounds["right"]
                )
                merge(
                    other["id"],
                    "exit",
                    "max" if target_is_right else "min",
                    back_offset - normalized_clearance
                    if target_is_right
                    else back_offset + normalized_clearance,
                )
                context.setdefault("label_sides", {})[other["id"]] = (
                    "left" if target_is_right else "right"
                )


def seed_routing_context(
    context: dict,
    edges: dict[str, ET.Element],
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    *,
    exclude: set[str] | None = None,
) -> None:
    excluded = exclude or set()
    for edge_id, cell in edges.items():
        if edge_id in excluded:
            continue
        path = document.edge_polyline(cell, lanes, nodes)
        if len(path) < 2:
            continue
        context.setdefault("paths", {})[edge_id] = path
        context.setdefault("endpoints", {})[edge_id] = (
            cell.attrib.get(contracts.DATA_FROM),
            cell.attrib.get(contracts.DATA_TO),
        )
        label_bounds = document.stored_label_bounds(cell)
        if label_bounds is not None:
            context.setdefault("labels", {})[edge_id] = label_bounds


def edge_routing_order(edges: list[dict], main_path: list[str], nodes: dict[str, dict]) -> list[dict]:
    pair_index = {
        pair: index for index, pair in enumerate(zip(main_path, main_path[1:]))
    }

    def key(edge: dict) -> tuple[int, int, str]:
        pair = (edge["from"], edge["to"])
        if pair in pair_index:
            return 0, pair_index[pair], edge["id"]
        route_class = inferred_spec_route_class(edge, nodes)
        priority = 2 if route_class == "back" else 1
        source = nodes[edge["from"]]
        source_rank = source.get("rank")
        if source_rank is None:
            source_rank = source["cell"].attrib.get(contracts.DATA_RANK, "0")
        return priority, int(source_rank), edge["id"]

    return sorted(edges, key=key)


def compile_v3_edges(spec: dict) -> list[dict]:
    """Apply topology-aware routing defaults without mutating the source IR."""
    if spec.get("schema_version") != contracts.V3_SCHEMA_VERSION:
        return spec["edges"]

    nodes = {node["id"]: node for node in spec["nodes"]}
    compiled: list[dict] = []
    for original in spec["edges"]:
        edge = dict(original)
        source = nodes[edge["from"]]
        target = nodes[edge["to"]]
        target_slot = effective_node_slot(target)
        is_forward_split = (
            source.get("type") == "decision"
            and target.get("type") != "end"
            and target["lane"] == source["lane"]
            and int(target["rank"]) > int(source["rank"])
            and target_slot in {"left", "right"}
        )
        if is_forward_split:
            edge.setdefault("route", "forward")
            edge.setdefault("exit_side", target_slot)
            edge.setdefault("entry_side", "top")
        compiled.append(edge)
    return compiled


def build_tree(spec: dict) -> ET.ElementTree:
    schema_version = validate_build_spec(spec)

    values = adaptive_canvas_values(spec)
    lane_widths = effective_lane_widths(spec)
    node_x_positions = v3_node_x_positions(spec, lane_widths)
    phase_presentation = (
        spec.get("layout", {}).get("phase_presentation", "bands")
        if schema_version == contracts.V3_SCHEMA_VERSION
        else "bands"
    )
    phase_rail_width = (
        PHASE_RAIL_WIDTH
        if phase_presentation == "rail" and spec.get("phases")
        else 0.0
    )
    max_rank = max((int(node["rank"]) for node in spec["nodes"]), default=1)
    current_lane_height = lane_height(max_rank, values)
    pool_width = phase_rail_width + sum(
        lane_widths[lane["id"]] for lane in spec["lanes"]
    )
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
            contracts.DATA_KIND: "pool", contracts.DATA_SEMANTIC_ID: "main", contracts.DATA_TITLE_HEIGHT: document.number(values["title_height"]),
            contracts.DATA_LANE_HEADER_HEIGHT: document.number(values["lane_header_height"]), contracts.DATA_ROW_GAP: document.number(values["row_gap"]),
            contracts.DATA_TOP_PADDING: document.number(values["top_padding"]), contracts.DATA_BOTTOM_PADDING: document.number(values["bottom_padding"]),
            contracts.DATA_MAX_RANK: str(max_rank),
            contracts.DATA_SCHEMA_VERSION: schema_version,
            contracts.DATA_TOOL_VERSION: contracts.TOOL_VERSION,
            contracts.DATA_MODEL_HASH_VERSION: contracts.MODEL_HASH_VERSION,
            contracts.DATA_LANE_ORDER: json.dumps(
                [lane["id"] for lane in spec["lanes"]],
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            contracts.DATA_MAIN_PATH: json.dumps(spec.get("main_path", []), ensure_ascii=True, separators=(",", ":")),
        },
    )
    if schema_version == contracts.V3_SCHEMA_VERSION:
        pool.attrib[contracts.DATA_BEHAVIOR_PATTERN] = spec["behavior_pattern"]
        pool.attrib[contracts.DATA_LAYOUT_PROFILE] = spec.get("layout", {}).get("profile", "review")
        pool.attrib[contracts.DATA_PHASE_PRESENTATION] = phase_presentation
        pool.attrib[contracts.DATA_PHASE_RAIL_WIDTH] = document.number(phase_rail_width)
        pool.attrib[contracts.DATA_GROUPS] = json.dumps(
            spec.get("groups", []), ensure_ascii=True, separators=(",", ":")
        )
    document.geometry(pool, x=values["x"], y=values["y"], width=pool_width, height=pool_height)

    lane_cells: dict[str, ET.Element] = {}
    offset_x = phase_rail_width
    for lane in spec["lanes"]:
        width = lane_widths[lane["id"]]
        lane_cell = create_lane_cell(
            root,
            pool,
            lane,
            values,
            x=offset_x,
            width=width,
            height=current_lane_height,
        )
        lane_cells[lane["id"]] = lane_cell
        offset_x += width

    for phase in spec.get("phases", []):
        create_phase_cell(root, pool, phase, values, pool_width)

    group_by_node = {
        node_id: group["id"]
        for group in spec.get("groups", [])
        for node_id in group["nodes"]
    }
    for node in spec["nodes"]:
        lane_cell = lane_cells[node["lane"]]
        lane_width = document.parse_geometry(lane_cell)["width"]
        create_node_cell(
            root,
            lane_cell,
            node,
            lane_width,
            values,
            automatic_x=node_x_positions.get(node["id"]),
            group_id=group_by_node.get(node["id"]),
        )

    lanes, nodes = document.lane_node_records(root, pool)
    compiled_edges = compile_v3_edges(spec)
    allocator = PortAllocator()
    routing_context = new_routing_context(
        spec.get("main_path", []),
        compiled_edges,
        nodes,
        v3_semantics=schema_version == contracts.V3_SCHEMA_VERSION,
    )
    derive_port_limits(routing_context, compiled_edges, lanes, nodes)
    spec_nodes = {node["id"]: node for node in spec["nodes"]}
    for edge in edge_routing_order(compiled_edges, spec.get("main_path", []), spec_nodes):
        create_edge_cell(root, pool, edge, lanes, nodes, allocator, routing_context)
    reflow_automatic_edge_labels(
        root,
        pool,
        lanes,
        nodes,
        routing_context.get("label_sides", {}),
    )
    normalize_phase_layering(root, pool)
    tree = ET.ElementTree(mxfile)
    metadata.refresh_managed_metadata(tree)
    return tree


def unmanaged_edge_specs(root: ET.Element, nodes: dict[str, dict]) -> list[dict]:
    """Describe Draw.io connectors redrawn manually without semantic metadata."""
    node_by_cell_id = {
        record["cell"].attrib.get("id"): semantic_id
        for semantic_id, record in nodes.items()
    }
    children_by_parent: dict[str, list[ET.Element]] = {}
    for child in root.iter("mxCell"):
        children_by_parent.setdefault(child.attrib.get("parent", ""), []).append(child)

    recovered: list[dict] = []
    for cell in root.iter("mxCell"):
        if cell.attrib.get("edge") != "1" or cell.attrib.get(contracts.DATA_KIND) == "edge":
            continue
        source = node_by_cell_id.get(cell.attrib.get("source"))
        target = node_by_cell_id.get(cell.attrib.get("target"))
        if source is None or target is None:
            continue
        label = cell.attrib.get("value", "")
        if not label:
            label = next(
                (
                    child.attrib.get("value", "")
                    for child in children_by_parent.get(cell.attrib.get("id", ""), [])
                    if "edgeLabel" in child.attrib.get("style", "")
                ),
                "",
            )
        recovered.append(
            {
                "cell_id": cell.attrib.get("id", ""),
                "from": source,
                "to": target,
                "label": label,
                "exit_port": document.port_from_style(cell, "exit"),
                "entry_port": document.port_from_style(cell, "entry"),
                "waypoints": [
                    {"x": x, "y": y} for x, y in document.edge_waypoints(cell)
                ],
            }
        )
    return sorted(recovered, key=lambda item: (item["from"], item["to"], item["cell_id"]))


def reserve_existing_ports(
    root: ET.Element,
    allocator: PortAllocator,
    *,
    exclude: set[str] | None = None,
) -> None:
    excluded = exclude or set()
    for edge_id, cell in document.edge_records(root).items():
        if edge_id in excluded:
            continue
        allow_reuse = cell.attrib.get(contracts.DATA_ALLOW_PORT_REUSE) == "1"
        exit_port = document.port_from_style(cell, "exit")
        entry_port = document.port_from_style(cell, "entry")
        if exit_port and cell.attrib.get(contracts.DATA_FROM):
            allocator.reserve(
                cell.attrib[contracts.DATA_FROM],
                exit_port[0],
                exit_port[1],
                edge_id,
                allow_reuse=allow_reuse,
                fail_on_conflict=False,
            )
        if entry_port and cell.attrib.get(contracts.DATA_TO):
            allocator.reserve(
                cell.attrib[contracts.DATA_TO],
                entry_port[0],
                entry_port[1],
                edge_id,
                allow_reuse=allow_reuse,
                fail_on_conflict=False,
            )


def existing_edge_spec(cell: ET.Element, *, for_reroute: bool = False) -> dict:
    spec = {
        "id": cell.attrib[contracts.DATA_SEMANTIC_ID],
        "from": cell.attrib.get(contracts.DATA_FROM),
        "to": cell.attrib.get(contracts.DATA_TO),
        "type": cell.attrib.get(contracts.DATA_EDGE_TYPE, "flow"),
        "label": cell.attrib.get("value", ""),
        "route": cell.attrib.get(contracts.DATA_ROUTE, "auto"),
        "allow_port_reuse": cell.attrib.get(contracts.DATA_ALLOW_PORT_REUSE) == "1",
    }
    if cell.attrib.get(contracts.DATA_BRANCH):
        spec["branch"] = cell.attrib[contracts.DATA_BRANCH]
    if cell.attrib.get(contracts.DATA_FLOW_ROLE):
        spec["flow_role"] = cell.attrib[contracts.DATA_FLOW_ROLE]
    if cell.attrib.get(contracts.DATA_OUTCOME):
        spec["outcome"] = cell.attrib[contracts.DATA_OUTCOME]
    explicit_waypoints = cell.attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) == "explicit"
    exit_port = document.port_from_style(cell, "exit")
    entry_port = document.port_from_style(cell, "entry")
    preserve_exit_side = (
        not for_reroute
        or explicit_waypoints
        or cell.attrib.get(contracts.DATA_EXIT_SIDE_EXPLICIT) == "1"
    )
    preserve_entry_side = (
        not for_reroute
        or explicit_waypoints
        or cell.attrib.get(contracts.DATA_ENTRY_SIDE_EXPLICIT) == "1"
    )
    preserve_exit_offset = (
        not for_reroute
        or explicit_waypoints
        or cell.attrib.get(contracts.DATA_EXIT_OFFSET_EXPLICIT) == "1"
    )
    preserve_entry_offset = (
        not for_reroute
        or explicit_waypoints
        or cell.attrib.get(contracts.DATA_ENTRY_OFFSET_EXPLICIT) == "1"
    )
    if exit_port and preserve_exit_side:
        spec["exit_side"] = exit_port[0]
    if exit_port and preserve_exit_offset:
        spec["exit_offset"] = exit_port[1]
    if entry_port and preserve_entry_side:
        spec["entry_side"] = entry_port[0]
    if entry_port and preserve_entry_offset:
        spec["entry_offset"] = entry_port[1]
    if explicit_waypoints:
        spec["waypoints"] = [
            {"x": x, "y": y}
            for x, y in document.edge_waypoints(cell)
        ]
    return spec


def phase_cell_spec(cell: ET.Element) -> dict:
    return {
        "id": cell.attrib[contracts.DATA_SEMANTIC_ID],
        "label": cell.attrib.get("value", ""),
        "from_rank": int(cell.attrib.get(contracts.DATA_FROM_RANK, "1")),
        "to_rank": int(cell.attrib.get(contracts.DATA_TO_RANK, "1")),
        "fill_color": cell.attrib.get(contracts.DATA_FILL_COLOR, "#f5f5f5"),
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
    cell.attrib[contracts.DATA_FROM_RANK] = str(current["from_rank"])
    cell.attrib[contracts.DATA_TO_RANK] = str(current["to_rank"])
    cell.attrib[contracts.DATA_FILL_COLOR] = current["fill_color"]
    presentation = cell.attrib.get(contracts.DATA_PRESENTATION, "bands")
    style = cell.attrib.get("style", "")
    if presentation == "bands":
        style = re.sub(
            r"fillColor=#[0-9A-Fa-f]{6}",
            f"fillColor={current['fill_color']}",
            style,
        )
    cell.attrib["style"] = style
    geom = cell.find("mxGeometry")
    if geom is None:
        geom = document.geometry(cell)
    rail_width = (
        document.parse_geometry(cell)["width"]
        if presentation == "rail"
        else PHASE_RAIL_WIDTH
    )
    geom.attrib.update(
        {
            key: document.number(value)
            for key, value in phase_geometry_values(
                current,
                values,
                pool_width,
                presentation=presentation,
                rail_width=rail_width,
            ).items()
        }
    )


def edge_route_is_locally_valid(
    cell: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
) -> bool:
    points = document.edge_polyline(cell, lanes, nodes)
    if len(points) < 2:
        return False
    boundaries = internal_lane_boundaries(lanes)
    node_bounds = {
        semantic_id: document.node_bounds_in_pool(record, lanes[record["lane"]])
        for semantic_id, record in nodes.items()
    }
    for segment in zip(points, points[1:]):
        axis = core_geometry.segment_axis(segment)
        if axis == "diagonal":
            return False
        if axis == "vertical" and any(
            abs(segment[0][0] - boundary) < LANE_BOUNDARY_CLEARANCE
            for boundary in boundaries
        ):
            return False
        for node_id, bounds in node_bounds.items():
            if node_id in {cell.attrib.get(contracts.DATA_FROM), cell.attrib.get(contracts.DATA_TO)}:
                continue
            if core_geometry.segment_crosses_bounds(segment, bounds):
                return False
    return True


def read_main_path(pool: ET.Element) -> list[str]:
    raw = pool.attrib.get(contracts.DATA_MAIN_PATH, "[]")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def read_lane_order(pool: ET.Element, root: ET.Element, lanes: dict[str, dict]) -> list[str]:
    order = metadata.managed_id_list_attribute(pool, contracts.DATA_LANE_ORDER, None)
    if order is None:
        order = [
            cell.attrib[contracts.DATA_SEMANTIC_ID]
            for cell in list(root)
            if cell.attrib.get(contracts.DATA_KIND) == "lane"
            and cell.attrib.get(contracts.DATA_SEMANTIC_ID) in lanes
        ]
    if len(order) != len(set(order)) or set(order) != set(lanes):
        raise contracts.DiagramError(
            "Managed lane order does not match the diagram lanes",
            code="integrity/schema-composition-mismatch",
            evidence={"lane_order": order, "lane_ids": sorted(lanes)},
            supported_fixes=["restore-lane-order", "controlled-rebuild"],
        )
    return order


def reflow_lane_order_geometry(
    pool: ET.Element,
    order: list[str],
    lanes: dict[str, dict],
    previous: dict[str, dict],
) -> list[dict]:
    phase_rail_width = (
        float(pool.attrib.get(contracts.DATA_PHASE_RAIL_WIDTH, PHASE_RAIL_WIDTH))
        if pool.attrib.get(contracts.DATA_PHASE_PRESENTATION) == "rail"
        else 0.0
    )
    cursor = phase_rail_width
    shifts: list[dict] = []
    for lane_id in order:
        record = lanes[lane_id]
        geom = record["cell"].find("mxGeometry")
        assert geom is not None
        width = float(geom.attrib.get("width", "0"))
        old = previous.get(lane_id)
        geom.attrib["x"] = document.number(cursor)
        record["geometry"] = document.parse_geometry(record["cell"])
        if old is not None and (
            abs(old["x"] - cursor) >= core_geometry.GEOMETRY_TOLERANCE
            or abs(old["width"] - width) >= core_geometry.GEOMETRY_TOLERANCE
        ):
            shifts.append(
                {
                    "id": lane_id,
                    "from_x": old["x"],
                    "to_x": cursor,
                    "from_width": old["width"],
                    "to_width": width,
                }
            )
        cursor += width

    pool_geom = pool.find("mxGeometry")
    assert pool_geom is not None
    pool_geom.attrib["width"] = document.number(cursor)
    pool.attrib[contracts.DATA_LANE_ORDER] = json.dumps(
        order, ensure_ascii=True, separators=(",", ":")
    )
    return shifts


def apply_lane_operations(
    root: ET.Element,
    pool: ET.Element,
    lanes: dict[str, dict],
    values: dict,
    changes: dict,
) -> tuple[list[str], dict[str, dict], list[dict]]:
    """Apply lane semantics and deterministic horizontal geometry.

    Existing lane-local node geometry remains untouched. Inserting or resizing
    a lane changes only lane/pool geometry plus automatic incident routes.
    """
    order = read_lane_order(pool, root, lanes)
    deleted = set(changes.get("delete_lanes", []))
    for lane_id in deleted:
        if lane_id not in lanes:
            raise contracts.DiagramError(
                f"Cannot delete missing lane: {lane_id}",
                code="patch/missing-lane",
            )
    if len(order) - len(deleted) + len(changes.get("lanes", [])) < 1:
        raise contracts.DiagramError(
            "A diagram must retain at least one lane",
            code="patch/delete-last-lane",
            supported_fixes=["retain-one-lane", "add-replacement-lane"],
        )

    updates = {item["id"]: item for item in changes.get("update_lanes", [])}
    for lane_id in updates:
        if lane_id not in lanes:
            raise contracts.DiagramError(
                f"Cannot update missing lane: {lane_id}",
                code="patch/missing-lane",
            )
        if lane_id in deleted:
            raise contracts.DiagramError(
                f"Lane {lane_id} cannot be updated and deleted in one patch",
                code="patch/conflicting-operation",
                subject={"kind": "lane", "id": lane_id},
            )

    added_ids = {item["id"] for item in changes.get("lanes", [])}
    duplicate_added = sorted(added_ids.intersection(lanes))
    if duplicate_added:
        raise contracts.DiagramError(
            f"Lane already exists: {duplicate_added[0]}",
            code="patch/duplicate-lane",
        )

    previous = {
        lane_id: {
            "x": record["geometry"]["x"],
            "width": record["geometry"]["width"],
        }
        for lane_id, record in lanes.items()
    }
    for lane_id in deleted:
        root.remove(lanes[lane_id]["cell"])
        order.remove(lane_id)
        del lanes[lane_id]

    lane_height_value = next(
        (record["geometry"]["height"] for record in lanes.values()),
        lane_height(int(pool.attrib.get(contracts.DATA_MAX_RANK, "1")), values),
    )
    for lane in changes.get("lanes", []):
        placement = "before" if "before" in lane else "after"
        reference = lane[placement]
        if reference not in order:
            raise contracts.DiagramError(
                f"New lane {lane['id']} references missing placement lane {reference}",
                code="patch/lane-placement-target",
                subject={"kind": "lane", "id": lane["id"]},
                evidence={"placement": placement, "reference": reference},
                supported_fixes=["use-existing-placement-lane"],
            )
        index = order.index(reference) + (1 if placement == "after" else 0)
        order.insert(index, lane["id"])
        width = float(lane.get("width", 200))
        cell = create_lane_cell(
            root,
            pool,
            lane,
            values,
            x=0,
            width=width,
            height=lane_height_value,
        )
        lanes[lane["id"]] = {"cell": cell, "geometry": document.parse_geometry(cell)}

    for lane_id, update in updates.items():
        cell = lanes[lane_id]["cell"]
        if "label" in update:
            cell.attrib["value"] = update["label"]
        if "width" in update:
            geom = cell.find("mxGeometry")
            assert geom is not None
            geom.attrib["width"] = document.number(update["width"])
            lanes[lane_id]["geometry"] = document.parse_geometry(cell)

    shifts = reflow_lane_order_geometry(pool, order, lanes, previous)
    return order, lanes, shifts


def current_groups_for_patch(pool: ET.Element) -> list[dict]:
    return copy.deepcopy(metadata.json_attribute(pool, contracts.DATA_GROUPS, list, []))


def apply_group_operations(
    pool: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    changes: dict,
) -> tuple[list[str], list[str], list[str]]:
    groups = current_groups_for_patch(pool)
    by_id = {group.get("id"): group for group in groups}
    if len(by_id) != len(groups) or None in by_id:
        raise metadata.managed_metadata_error(
            "Managed group metadata is invalid",
            attribute=contracts.DATA_GROUPS,
        )
    deleted = set(changes.get("delete_groups", []))
    for group_id in deleted:
        if group_id not in by_id:
            raise contracts.DiagramError(
                f"Cannot delete missing group: {group_id}",
                code="patch/missing-group",
            )
    groups = [group for group in groups if group["id"] not in deleted]
    by_id = {group["id"]: group for group in groups}

    updated_ids: list[str] = []
    for update in changes.get("update_groups", []):
        group_id = update["id"]
        if group_id not in by_id:
            raise contracts.DiagramError(
                f"Cannot update missing group: {group_id}",
                code="patch/missing-group",
            )
        if group_id in deleted:
            raise contracts.DiagramError(
                f"Group {group_id} cannot be updated and deleted in one patch",
                code="patch/conflicting-operation",
                subject={"kind": "group", "id": group_id},
            )
        by_id[group_id].update(update)
        updated_ids.append(group_id)

    added_ids: list[str] = []
    for group in changes.get("groups", []):
        if group["id"] in by_id:
            raise contracts.DiagramError(
                f"Group already exists: {group['id']}",
                code="patch/duplicate-group",
            )
        item = copy.deepcopy(group)
        groups.append(item)
        by_id[item["id"]] = item
        added_ids.append(item["id"])

    member_to_group: dict[str, str] = {}
    for group in groups:
        contracts.validate_group_object(group, f"group[{group['id']}]")
        if group["lane"] not in lanes:
            raise contracts.DiagramError(
                f"Group {group['id']} references a deleted or missing lane",
                code="patch/group-lane-dependency",
                subject={"kind": "group", "id": group["id"]},
                evidence={"lane": group["lane"]},
                supported_fixes=["delete-group", "update-group-lane"],
            )
        for node_id in group["nodes"]:
            if node_id not in nodes:
                raise contracts.DiagramError(
                    f"Group {group['id']} references a deleted or missing node",
                    code="patch/group-node-dependency",
                    subject={"kind": "group", "id": group["id"]},
                    evidence={"node": node_id},
                    supported_fixes=["delete-group", "update-group-nodes"],
                )
            if nodes[node_id]["lane"] != group["lane"]:
                raise contracts.DiagramError(
                    f"Group {group['id']} contains a node from another lane",
                    code="semantic/group-lane",
                    subject={"kind": "group", "id": group["id"]},
                    evidence={"node": node_id},
                )
            if node_id in member_to_group:
                raise contracts.DiagramError(
                    f"Node {node_id} belongs to more than one group",
                    code="semantic/group-membership",
                    subject={"kind": "node", "id": node_id},
                )
            member_to_group[node_id] = group["id"]

    for node_id, record in nodes.items():
        cell = record["cell"]
        group_id = member_to_group.get(node_id)
        if group_id:
            cell.attrib[contracts.DATA_GROUP_ID] = group_id
        else:
            cell.attrib.pop(contracts.DATA_GROUP_ID, None)
    pool.attrib[contracts.DATA_GROUPS] = json.dumps(
        groups, ensure_ascii=True, separators=(",", ":")
    )
    return sorted(added_ids), sorted(updated_ids), sorted(deleted)


def patch_node_automatic_x(
    node: dict,
    lane_record: dict,
    nodes: dict[str, dict],
    layout_profile_name: str,
) -> float:
    lane_width = lane_record["geometry"]["width"]
    width, _ = node_size(node)
    slot = effective_node_slot(node)
    rank = int(node["rank"])
    row = [
        record
        for record in nodes.values()
        if record["lane"] == node["lane"]
        and int(record["cell"].attrib.get(contracts.DATA_RANK, "0")) == rank
    ]
    occupied = {
        record["cell"].attrib.get(contracts.DATA_SLOT, "main"): record
        for record in row
    }
    if slot in occupied:
        raise contracts.DiagramError(
            f"Node {node['id']} conflicts with an occupied lane-local slot",
            code="layout/slot-conflict",
            subject={"kind": "node", "id": node["id"]},
            evidence={"lane": node["lane"], "rank": rank, "slot": slot},
            supported_fixes=["assign-distinct-slot", "change-rank", "set-explicit-geometry"],
        )

    gap = PROFILE_SLOT_GAPS.get(layout_profile_name, PROFILE_SLOT_GAPS["review"])
    if node.get("anchor"):
        target_id = node["anchor"]["node"]
        target = nodes.get(target_id)
        if target is None:
            raise contracts.DiagramError(
                f"Note {node['id']} anchors to a missing node",
                code="semantic/anchor-target",
                subject={"kind": "node", "id": node["id"]},
                evidence={"anchor": target_id},
            )
        target_rank = int(target["cell"].attrib.get(contracts.DATA_RANK, "0"))
        if target["lane"] != node["lane"] or target_rank != rank:
            raise contracts.DiagramError(
                f"Note {node['id']} must share lane and rank with its anchor",
                code="semantic/anchor-alignment",
                subject={"kind": "node", "id": node["id"]},
            )
        target_geom = target["geometry"]
        if node["anchor"]["side"] == "left":
            x = target_geom["x"] - gap - width
        else:
            x = target_geom["x"] + target_geom["width"] + gap
    elif slot == "main" or "main" not in occupied:
        x = (lane_width - width) / 2.0
    else:
        main_geom = occupied["main"]["geometry"]
        if slot == "left":
            x = main_geom["x"] - gap - width
        else:
            x = main_geom["x"] + main_geom["width"] + gap

    side_padding = PROFILE_SIDE_PADDING.get(
        layout_profile_name,
        PROFILE_SIDE_PADDING["review"],
    )
    if x < side_padding - core_geometry.GEOMETRY_TOLERANCE:
        raise contracts.DiagramError(
            f"Node {node['id']} does not fit in the requested left slot without moving existing geometry",
            code="layout/slot-space",
            subject={"kind": "node", "id": node["id"]},
            evidence={"required_x": x, "minimum_x": side_padding},
            supported_fixes=["widen-and-realign-lane", "set-explicit-geometry", "change-slot"],
        )
    return x


def patch_tree(tree: ET.ElementTree, changes: dict, allow_geometry_updates: bool) -> dict:
    validate_patch_spec(changes)
    pool = document.find_pool(tree)
    root = document.graph_root(tree)
    values = document.values_from_pool(pool, DEFAULTS)
    lanes, nodes = document.lane_node_records(root, pool)
    existing_edges = document.edge_records(root)
    explicit_waypoints_before = {
        edge_id: document.edge_waypoints(cell)
        for edge_id, cell in existing_edges.items()
        if cell.attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) == "explicit"
    }
    phases = document.phase_records(root, pool)
    had_phases_before_patch = bool(phases)
    pool_width = max(
        record["geometry"]["x"] + record["geometry"]["width"]
        for record in lanes.values()
    )

    deleted_edge_ids = set(changes.get("delete_edges", []))
    deleted_lane_ids = set(changes.get("delete_lanes", []))
    deleted_node_ids = set(changes.get("delete_nodes", []))
    deleted_phase_ids = set(changes.get("delete_phases", []))
    existing_lane_nodes = {
        lane_id: sorted(
            node_id for node_id, record in nodes.items() if record["lane"] == lane_id
        )
        for lane_id in deleted_lane_ids
        if lane_id in lanes
    }
    undeclared_lane_nodes = {
        lane_id: [node_id for node_id in node_ids if node_id not in deleted_node_ids]
        for lane_id, node_ids in existing_lane_nodes.items()
        if any(node_id not in deleted_node_ids for node_id in node_ids)
    }
    if undeclared_lane_nodes:
        raise contracts.DiagramError(
            "Deleting a lane requires explicitly deleting every node in that lane",
            code="semantic/lane-not-empty",
            evidence={"lanes": undeclared_lane_nodes},
            supported_fixes=["add-delete-nodes", "retain-lane"],
        )
    for edge_id in deleted_edge_ids:
        if edge_id not in existing_edges:
            raise contracts.DiagramError(f"Cannot delete missing edge: {edge_id}", code="patch/missing-edge")
    for node_id in deleted_node_ids:
        if node_id not in nodes:
            raise contracts.DiagramError(f"Cannot delete missing node: {node_id}", code="patch/missing-node")
    for phase_id in deleted_phase_ids:
        if phase_id not in phases:
            raise contracts.DiagramError(f"Cannot delete missing phase: {phase_id}", code="patch/missing-phase")

    undeclared_incident = sorted(
        edge_id
        for edge_id, cell in existing_edges.items()
        if edge_id not in deleted_edge_ids
        and (
            cell.attrib.get(contracts.DATA_FROM) in deleted_node_ids
            or cell.attrib.get(contracts.DATA_TO) in deleted_node_ids
        )
    )
    if undeclared_incident:
        raise contracts.DiagramError(
            "Deleting a node requires explicitly deleting every incident edge",
            code="patch/incident-edge",
            evidence={"edges": undeclared_incident},
            supported_fixes=["add-delete-edges"],
        )

    deleted_main_path_nodes = deleted_node_ids.intersection(read_main_path(pool))
    if deleted_main_path_nodes and "main_path" not in changes:
        raise contracts.DiagramError(
            "Deleting a main_path node requires supplying the replacement main_path",
            code="patch/main-path",
            evidence={"deleted_nodes": sorted(deleted_main_path_nodes)},
            supported_fixes=["supply-main-path"],
        )

    edge_updates = changes.get("update_edges", [])
    explicit_type_reroutes = {
        update["id"]
        for update in edge_updates
        if update.get("reroute") is True
        or any(key in update for key in ROUTING_FIELDS if key != "reroute")
    }
    for update in changes.get("update_nodes", []):
        node_id = update["id"]
        if node_id not in nodes or "type" not in update:
            continue
        current_type = nodes[node_id]["cell"].attrib.get(contracts.DATA_NODE_TYPE, "process")
        if current_type == update["type"]:
            continue
        incident = sorted(
            edge_id
            for edge_id, cell in existing_edges.items()
            if edge_id not in deleted_edge_ids
            and node_id in {cell.attrib.get(contracts.DATA_FROM), cell.attrib.get(contracts.DATA_TO)}
            and edge_id not in explicit_type_reroutes
        )
        if incident:
            raise contracts.DiagramError(
                "Changing a node type requires explicit rerouting of every incident edge",
                code="patch/node-type-incident-edge",
                subject={"kind": "node", "id": node_id},
                evidence={"edges": incident, "from": current_type, "to": update["type"]},
                supported_fixes=["add-update-edges-reroute"],
            )

    for edge_id in deleted_edge_ids:
        root.remove(existing_edges[edge_id])
    for node_id in deleted_node_ids:
        root.remove(nodes[node_id]["cell"])
    for phase_id in deleted_phase_ids:
        root.remove(phases[phase_id])

    lanes, nodes = document.lane_node_records(root, pool)
    lane_order, lanes, lane_shifts = apply_lane_operations(
        root, pool, lanes, values, changes
    )
    pool_width = document.parse_geometry(pool)["width"]
    existing_edges = document.edge_records(root)
    phases = document.phase_records(root, pool)
    moved_node_ids: set[str] = set()

    for update in changes.get("update_nodes", []):
        semantic_id = update.get("id")
        if semantic_id not in nodes:
            raise contracts.DiagramError(f"Cannot update missing node: {semantic_id}")
        cell = nodes[semantic_id]["cell"]
        if "label" in update:
            cell.attrib["value"] = str(update["label"])
        if "type" in update:
            kind = update["type"]
            if kind not in NODE_STYLES:
                raise contracts.DiagramError(f"Unsupported node type: {kind}")
            cell.attrib["style"] = NODE_STYLES[kind]
            cell.attrib[contracts.DATA_NODE_TYPE] = kind
        requested_geometry = any(key in update for key in ("x", "y", "width", "height"))
        if requested_geometry and not allow_geometry_updates:
            raise contracts.DiagramError("Existing geometry update requires --allow-geometry-updates")
        if requested_geometry:
            moved_node_ids.add(semantic_id)
            geom = cell.find("mxGeometry")
            assert geom is not None
            kind = cell.attrib.get(contracts.DATA_NODE_TYPE, "process")
            geometry_update = dict(update)
            if kind in FIXED_ASPECT_NODE_TYPES:
                update_width = geometry_update.get("width")
                update_height = geometry_update.get("height")
                if update_width is not None and update_height is not None:
                    if abs(float(update_width) - float(update_height)) >= core_geometry.GEOMETRY_TOLERANCE:
                        raise contracts.DiagramError(
                            f"Fixed-aspect node {semantic_id} requires equal width and height",
                            code="geometry/fixed-aspect-ratio",
                            subject={"kind": "node", "id": semantic_id},
                            evidence={"width": update_width, "height": update_height},
                            supported_fixes=["set-equal-width-and-height", "remove-one-size-dimension"],
                        )
                elif update_width is not None:
                    geometry_update["height"] = update_width
                elif update_height is not None:
                    geometry_update["width"] = update_height
            for key in ("x", "y", "width", "height"):
                if key in geometry_update:
                    geom.attrib[key] = document.number(geometry_update[key])
            nodes[semantic_id]["geometry"] = document.parse_geometry(cell)

    new_nodes = changes.get("nodes", [])
    schema_version = pool.attrib.get(contracts.DATA_SCHEMA_VERSION, "1")
    if schema_version != contracts.V3_SCHEMA_VERSION:
        v3_new_nodes = [
            node["id"] for node in new_nodes if "slot" in node or "anchor" in node
        ]
        if v3_new_nodes:
            raise contracts.DiagramError(
                "slot and anchor intent require a schema version 3 diagram",
                code="schema/version-field",
                evidence={"nodes": v3_new_nodes, "schema_version": schema_version},
                supported_fixes=["migrate-to-v3", "remove-v3-fields"],
            )
    new_node_by_id = {node["id"]: node for node in new_nodes}
    new_node_ids = set(new_node_by_id)
    all_target_ids = set(nodes) | new_node_ids
    for node in new_nodes:
        if node["id"] in nodes:
            raise contracts.DiagramError(f"Node already exists: {node['id']}")
        if node.get("lane") not in lanes:
            raise contracts.DiagramError(f"Unknown lane for node {node.get('id')}: {node.get('lane')}")
        anchor = node.get("anchor")
        if anchor and anchor["node"] not in all_target_ids:
            raise contracts.DiagramError(
                f"Note {node['id']} anchors to a missing node",
                code="semantic/anchor-target",
                subject={"kind": "node", "id": node["id"]},
                evidence={"anchor": anchor["node"]},
            )
        if not anchor:
            continue
        target_id = anchor["node"]
        if target_id in nodes:
            target_lane = nodes[target_id]["lane"]
            target_rank = int(nodes[target_id]["cell"].attrib.get(contracts.DATA_RANK, "0"))
        else:
            target = new_node_by_id[target_id]
            target_lane = target["lane"]
            target_rank = int(target["rank"])
        if target_lane != node["lane"] or target_rank != int(node["rank"]):
            raise contracts.DiagramError(
                f"Note {node['id']} must share lane and rank with its anchor",
                code="semantic/anchor-alignment",
                subject={"kind": "node", "id": node["id"]},
                evidence={
                    "anchor": target_id,
                    "note_lane": node["lane"],
                    "anchor_lane": target_lane,
                    "note_rank": int(node["rank"]),
                    "anchor_rank": target_rank,
                },
                supported_fixes=["align-note-with-anchor", "remove-anchor"],
            )
        if "slot" in node and node["slot"] != anchor["side"]:
            raise contracts.DiagramError(
                f"Note {node['id']} slot conflicts with its anchor side",
                code="layout/anchor-slot-conflict",
                subject={"kind": "node", "id": node["id"]},
                evidence={"slot": node["slot"], "anchor_side": anchor["side"]},
                supported_fixes=["match-slot-to-anchor", "remove-explicit-slot"],
            )

    layout_profile_name = pool.attrib.get(contracts.DATA_LAYOUT_PROFILE, "review")
    ordered_new_nodes = sorted(new_nodes, key=lambda node: bool(node.get("anchor")))
    for node in ordered_new_nodes:
        lane_cell = lanes[node["lane"]]["cell"]
        lane_width = lanes[node["lane"]]["geometry"]["width"]
        automatic_x = None
        if schema_version == contracts.V3_SCHEMA_VERSION and "x" not in node:
            automatic_x = patch_node_automatic_x(
                node,
                lanes[node["lane"]],
                nodes,
                layout_profile_name,
            )
            width, _ = node_size(node)
            side_padding = PROFILE_SIDE_PADDING.get(
                layout_profile_name,
                PROFILE_SIDE_PADDING["review"],
            )
            required_width = automatic_x + width + side_padding
            if required_width > lane_width + core_geometry.GEOMETRY_TOLERANCE:
                before_reflow = {
                    lane_id: dict(record["geometry"])
                    for lane_id, record in lanes.items()
                }
                lane_geom = lane_cell.find("mxGeometry")
                assert lane_geom is not None
                lane_geom.attrib["width"] = document.number(math.ceil(required_width))
                lanes[node["lane"]]["geometry"] = document.parse_geometry(lane_cell)
                expansion_shifts = reflow_lane_order_geometry(
                    pool,
                    lane_order,
                    lanes,
                    before_reflow,
                )
                by_lane = {item["id"]: item for item in lane_shifts}
                for item in expansion_shifts:
                    existing = by_lane.get(item["id"])
                    if existing:
                        existing["to_x"] = item["to_x"]
                        existing["to_width"] = item["to_width"]
                    else:
                        lane_shifts.append(item)
                        by_lane[item["id"]] = item
                lane_width = lanes[node["lane"]]["geometry"]["width"]
                pool_width = document.parse_geometry(pool)["width"]
        created = create_node_cell(
            root,
            lane_cell,
            node,
            lane_width,
            values,
            automatic_x=automatic_x,
        )
        nodes[node["id"]] = {
            "cell": created,
            "geometry": document.parse_geometry(created),
            "lane": node["lane"],
        }

    lanes, nodes = document.lane_node_records(root, pool)
    group_patch_requested = any(
        field in changes for field in ("update_groups", "groups", "delete_groups")
    )
    if schema_version == contracts.V3_SCHEMA_VERSION:
        added_group_ids, updated_group_ids, deleted_group_ids = apply_group_operations(
            pool, lanes, nodes, changes
        )
    elif group_patch_requested:
        raise contracts.DiagramError(
            "Group patch operations require a schema version 3 diagram",
            code="schema/version-field",
            evidence={"schema_version": schema_version},
            supported_fixes=["migrate-to-v3", "remove-group-operations"],
        )
    else:
        added_group_ids, updated_group_ids, deleted_group_ids = [], [], []
    existing_edges = document.edge_records(root)
    for update in edge_updates:
        if update["id"] not in existing_edges:
            raise contracts.DiagramError(f"Cannot update missing edge: {update['id']}", code="patch/missing-edge")
        if "label" in update:
            existing_edges[update["id"]].attrib["value"] = str(update["label"])

    explicit_reroute_ids = {
        update["id"]
        for update in edge_updates
        if "label" in update or update.get("reroute") or any(
            key in update for key in ROUTING_FIELDS if key != "reroute"
        )
    }
    changed_lane_ids = {item["id"] for item in lane_shifts}
    lane_impacted_edge_ids = {
        edge_id
        for edge_id, cell in existing_edges.items()
        if (
            nodes.get(cell.attrib.get(contracts.DATA_FROM, ""), {}).get("lane")
            in changed_lane_ids
            or nodes.get(cell.attrib.get(contracts.DATA_TO, ""), {}).get("lane")
            in changed_lane_ids
        )
    }
    manual_waypoint_edges_affected_by_lane_changes = sorted(
        edge_id
        for edge_id in lane_impacted_edge_ids
        if existing_edges[edge_id].attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) == "explicit"
    )
    auto_reroute_ids = {
        edge_id
        for edge_id, cell in existing_edges.items()
        if cell.attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) != "explicit"
        and (
            edge_id in lane_impacted_edge_ids
            or (
                cell.attrib.get(contracts.DATA_FROM) in moved_node_ids
                or cell.attrib.get(contracts.DATA_TO) in moved_node_ids
            )
            and not edge_route_is_locally_valid(cell, lanes, nodes)
        )
    }
    reroute_ids = explicit_reroute_ids | auto_reroute_ids
    allocator = PortAllocator()
    reserve_existing_ports(root, allocator, exclude=reroute_ids)
    update_by_id = {update["id"]: update for update in edge_updates}
    effective_main_path = list(changes.get("main_path", read_main_path(pool)))
    updated_specs: dict[str, dict] = {}
    for semantic_id, cell in existing_edges.items():
        edge = existing_edge_spec(cell, for_reroute=semantic_id in reroute_ids)
        edge.update(
            {
                key: value
                for key, value in update_by_id.get(semantic_id, {}).items()
                if key != "reroute"
            }
        )
        updated_specs[semantic_id] = edge

    new_edges = changes.get("edges", [])
    routing_context = new_routing_context(
        effective_main_path,
        [*updated_specs.values(), *new_edges],
        nodes,
        v3_semantics=pool.attrib.get(contracts.DATA_SCHEMA_VERSION) == contracts.V3_SCHEMA_VERSION,
    )
    derive_port_limits(
        routing_context,
        [*updated_specs.values(), *new_edges],
        lanes,
        nodes,
    )
    seed_routing_context(
        routing_context,
        existing_edges,
        lanes,
        nodes,
        exclude=reroute_ids,
    )
    reroute_specs = [updated_specs[edge_id] for edge_id in reroute_ids]
    for edge in edge_routing_order(reroute_specs, effective_main_path, nodes):
        apply_edge_route(
            existing_edges[edge["id"]],
            edge,
            lanes,
            nodes,
            allocator,
            routing_context,
        )

    for edge in new_edges:
        if edge["id"] in existing_edges:
            raise contracts.DiagramError(f"Edge already exists: {edge['id']}")
    for edge in edge_routing_order(new_edges, effective_main_path, nodes):
        create_edge_cell(
            root,
            pool,
            edge,
            lanes,
            nodes,
            allocator,
            routing_context,
        )

    phases = document.phase_records(root, pool)
    for update in changes.get("update_phases", []):
        phase_id = update["id"]
        if phase_id not in phases:
            raise contracts.DiagramError(f"Cannot update missing phase: {phase_id}", code="patch/missing-phase")
        apply_phase_update(phases[phase_id], update, values, pool_width)
    for phase in changes.get("phases", []):
        if phase["id"] in phases:
            raise contracts.DiagramError(f"Phase already exists: {phase['id']}", code="patch/duplicate-phase")
        create_phase_cell(root, pool, phase, values, pool_width)

    if "main_path" in changes:
        pool.attrib[contracts.DATA_MAIN_PATH] = json.dumps(
            changes["main_path"], ensure_ascii=True, separators=(",", ":")
        )
        if pool.attrib.get(contracts.DATA_SCHEMA_VERSION) not in contracts.STRUCTURED_SCHEMA_VERSIONS:
            pool.attrib[contracts.DATA_SCHEMA_VERSION] = contracts.SCHEMA_VERSION
    elif deleted_node_ids.intersection(read_main_path(pool)):
        raise contracts.DiagramError(
            "Deleting a main_path node requires supplying the replacement main_path",
            code="patch/main-path",
            evidence={"deleted_nodes": sorted(deleted_node_ids.intersection(read_main_path(pool)))},
            supported_fixes=["supply-main-path"],
        )

    requested_max_rank = max(
        [int(pool.attrib.get(contracts.DATA_MAX_RANK, "1"))]
        + [int(node["rank"]) for node in new_nodes]
    )
    if requested_max_rank > int(pool.attrib.get(contracts.DATA_MAX_RANK, "1")):
        new_lane_height = lane_height(requested_max_rank, values)
        for lane in lanes.values():
            lane["cell"].find("mxGeometry").attrib["height"] = document.number(new_lane_height)
        pool_geom = pool.find("mxGeometry")
        assert pool_geom is not None
        pool_geom.attrib["height"] = document.number(values["title_height"] + new_lane_height)
        pool.attrib[contracts.DATA_MAX_RANK] = str(requested_max_rank)

    phases = document.phase_records(root, pool)
    for cell in phases.values():
        apply_phase_update(cell, phase_cell_spec(cell), values, pool_width)

    normalize_phase_layering(
        root,
        pool,
        restore_lane_fill_without_phases=had_phases_before_patch,
    )
    metadata.refresh_managed_metadata(tree)

    remaining_explicit_waypoints = {
        edge_id: points
        for edge_id, points in explicit_waypoints_before.items()
        if edge_id not in deleted_edge_ids
    }
    final_edges = document.edge_records(root)
    manual_waypoints_preserved = (
        all(
            edge_id in final_edges
            and final_edges[edge_id].attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) == "explicit"
            and document.edge_waypoints(final_edges[edge_id]) == points
            for edge_id, points in remaining_explicit_waypoints.items()
        )
        if remaining_explicit_waypoints
        else None
    )

    return {
        "added_lanes": sorted(lane["id"] for lane in changes.get("lanes", [])),
        "updated_lanes": sorted(lane["id"] for lane in changes.get("update_lanes", [])),
        "deleted_lanes": sorted(deleted_lane_ids),
        "lane_order": lane_order,
        "lane_geometry_changes": sorted(lane_shifts, key=lambda item: item["id"]),
        "dependent_lane_shifts": sorted(
            (
                item
                for item in lane_shifts
                if item["id"]
                not in {lane["id"] for lane in changes.get("update_lanes", [])}
            ),
            key=lambda item: item["id"],
        ),
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
        "added_groups": added_group_ids,
        "updated_groups": updated_group_ids,
        "deleted_groups": deleted_group_ids,
        "main_path_updated": "main_path" in changes,
        "manual_waypoints_preserved": manual_waypoints_preserved,
        "manual_waypoints_checked": len(remaining_explicit_waypoints),
        "manual_waypoint_edges_affected_by_lane_changes": (
            manual_waypoint_edges_affected_by_lane_changes
        ),
        "requested_changes": {
            "lanes": sorted(
                {lane["id"] for lane in changes.get("lanes", [])}
                | {lane["id"] for lane in changes.get("update_lanes", [])}
                | deleted_lane_ids
            ),
            "nodes": sorted(
                {node["id"] for node in new_nodes}
                | {node["id"] for node in changes.get("update_nodes", [])}
                | deleted_node_ids
            ),
            "edges": sorted(
                {edge["id"] for edge in new_edges}
                | {edge["id"] for edge in edge_updates}
                | deleted_edge_ids
            ),
        },
        "dependency_changes": {
            "shifted_lanes": sorted(item["id"] for item in lane_shifts),
            "auto_rerouted_edges": sorted(auto_reroute_ids - explicit_reroute_ids),
        },
    }


def effective_label_bounds(
    cell: ET.Element,
    points: list[tuple[float, float]],
) -> tuple[int, dict[str, float]] | None:
    stored = document.stored_label_bounds(cell)
    if stored is not None:
        try:
            segment_index = int(cell.attrib.get(contracts.DATA_LABEL_SEGMENT, "0"))
        except ValueError:
            segment_index = 0
        return segment_index, stored
    candidates = label_box_candidates(points, cell.attrib.get("value", ""))
    if not candidates:
        return None
    segment_index, box, _ = candidates[0]
    return segment_index, box


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
            contracts.make_diagnostic(
                code,
                severity,
                message,
                subject=subject,
                evidence=evidence,
                supported_fixes=supported_fixes,
            )
        )

    try:
        pool = document.find_pool(tree)
        root = document.graph_root(tree)
        lanes, nodes = document.lane_node_records(root, pool)
    except contracts.DiagramError as exc:
        diagnostic = exc.diagnostic()
        return {
            "valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "diagnostics": [diagnostic],
        }

    schema_version = pool.attrib.get(contracts.DATA_SCHEMA_VERSION, "1")

    if schema_version not in {"1", *contracts.STRUCTURED_SCHEMA_VERSIONS}:
        add(
            "integrity/schema-composition-mismatch",
            "error",
            f"Diagram declares an unsupported schema version: {schema_version}",
            subject={"kind": "pool", "id": "main"},
            evidence={"schema_version": schema_version},
            supported_fixes=["controlled-rebuild"],
        )
    if schema_version == contracts.V3_SCHEMA_VERSION:
        missing_v3_metadata = [
            attribute
            for attribute in (
                contracts.DATA_BEHAVIOR_PATTERN,
                contracts.DATA_LAYOUT_PROFILE,
                contracts.DATA_PHASE_PRESENTATION,
                contracts.DATA_GROUPS,
            )
            if attribute not in pool.attrib
        ]
        if missing_v3_metadata:
            add(
                "integrity/schema-composition-mismatch",
                "error",
                "Schema v3 diagram is missing required semantic metadata",
                subject={"kind": "pool", "id": "main"},
                evidence={"missing_attributes": missing_v3_metadata},
                supported_fixes=["restore-semantic-metadata", "controlled-rebuild"],
            )
    elif schema_version == contracts.SCHEMA_VERSION:
        unexpected_v3_metadata = [
            attribute
            for attribute in (
                contracts.DATA_BEHAVIOR_PATTERN,
                contracts.DATA_LAYOUT_PROFILE,
                contracts.DATA_PHASE_PRESENTATION,
                contracts.DATA_GROUPS,
            )
            if attribute in pool.attrib
        ]
        unexpected_v3_cells = [
            cell.attrib.get(contracts.DATA_SEMANTIC_ID, cell.attrib.get("id", ""))
            for cell in root.iter("mxCell")
            if any(
                attribute in cell.attrib
                for attribute in (
                    contracts.DATA_SLOT,
                    contracts.DATA_ANCHOR,
                    contracts.DATA_FLOW_ROLE,
                    contracts.DATA_OUTCOME,
                )
            )
        ]
        if unexpected_v3_metadata or unexpected_v3_cells:
            add(
                "integrity/schema-composition-mismatch",
                "error",
                "Schema v2 diagram contains v3-only metadata",
                subject={"kind": "pool", "id": "main"},
                evidence={
                    "pool_attributes": unexpected_v3_metadata,
                    "cells": unexpected_v3_cells,
                },
                supported_fixes=["correct-schema-version", "controlled-rebuild"],
            )

    known_vertex_kinds = {"pool", "lane", "node", "phase"}
    edge_cell_ids = {
        cell.attrib.get("id")
        for cell in root.iter("mxCell")
        if cell.attrib.get("edge") == "1"
    }
    unmanaged_vertices = []
    for cell in root.iter("mxCell"):
        if cell.attrib.get("vertex") != "1":
            continue
        if cell.attrib.get(contracts.DATA_KIND) in known_vertex_kinds:
            continue
        if (
            "edgeLabel" in cell.attrib.get("style", "")
            and cell.attrib.get("parent") in edge_cell_ids
        ):
            continue
        unmanaged_vertices.append(cell.attrib.get("id", ""))
    if unmanaged_vertices:
        add(
            "structure/unmanaged-vertex",
            "warning",
            f"Draw.io contains {len(unmanaged_vertices)} unmanaged vertex cell(s)",
            subject={"kind": "diagram"},
            evidence={"cell_ids": sorted(unmanaged_vertices)},
            supported_fixes=["restore-vertex-semantic-metadata", "controlled-rebuild"],
        )

    lane_cell_ids = {
        lane_id: record["cell"].attrib.get("id")
        for lane_id, record in lanes.items()
    }
    for node_id, record in nodes.items():
        if record["cell"].attrib.get("parent") != lane_cell_ids.get(record["lane"]):
            add(
                "integrity/schema-composition-mismatch",
                "error",
                f"Node parent does not match its semantic lane: {node_id}",
                subject={"kind": "node", "id": node_id},
                evidence={"lane": record["lane"]},
                supported_fixes=["restore-node-parent", "controlled-rebuild"],
            )

    node_cell_ids = {
        node_id: record["cell"].attrib.get("id")
        for node_id, record in nodes.items()
    }
    for edge_id, cell in document.edge_records(root).items():
        source_id = cell.attrib.get(contracts.DATA_FROM)
        target_id = cell.attrib.get(contracts.DATA_TO)
        if (
            cell.attrib.get("source") != node_cell_ids.get(source_id)
            or cell.attrib.get("target") != node_cell_ids.get(target_id)
        ):
            add(
                "integrity/schema-composition-mismatch",
                "error",
                f"Edge endpoints do not match semantic metadata: {edge_id}",
                subject={"kind": "edge", "id": edge_id},
                evidence={"from": source_id, "to": target_id},
                supported_fixes=["restore-edge-semantic-metadata", "controlled-rebuild"],
            )

    try:
        integrity = metadata.managed_artifact_summary(tree)
    except contracts.DiagramError as exc:
        diagnostics.append(exc.diagnostic())
        integrity = {
            "tool_version": pool.attrib.get(contracts.DATA_TOOL_VERSION),
            "model_hash_version": pool.attrib.get(contracts.DATA_MODEL_HASH_VERSION),
            "stored_model_hash": pool.attrib.get(contracts.DATA_MODEL_HASH),
            "computed_model_hash": None,
            "model_hash_matches": False,
        }
    if integrity["model_hash_version"] not in {None, contracts.MODEL_HASH_VERSION}:
        add(
            "integrity/schema-composition-mismatch",
            "error",
            "Diagram uses an unsupported model hash version",
            subject={"kind": "pool", "id": "main"},
            evidence={"model_hash_version": integrity["model_hash_version"]},
            supported_fixes=["controlled-rebuild"],
        )
    if integrity["stored_model_hash"] is None:
        add(
            "integrity/model-hash-missing",
            "warning",
            "Diagram predates managed semantic hashing",
            subject={"kind": "pool", "id": "main"},
            supported_fixes=["patch-to-upgrade-metadata", "controlled-rebuild"],
        )
    elif integrity["model_hash_matches"] is False:
        add(
            "integrity/model-hash-mismatch",
            "warning",
            "Stored semantic model hash does not match the current diagram metadata",
            subject={"kind": "pool", "id": "main"},
            evidence={
                "stored": integrity["stored_model_hash"],
                "computed": integrity["computed_model_hash"],
            },
            supported_fixes=["review-semantic-drift", "accept-reviewed-model-drift"],
        )

    semantic_ids: set[str] = set()
    for cell in root.iter("mxCell"):
        semantic_id = cell.attrib.get(contracts.DATA_SEMANTIC_ID)
        kind = cell.attrib.get(contracts.DATA_KIND)
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
        cell for cell in root.iter("mxCell") if cell.attrib.get(contracts.DATA_KIND) == "edge"
    ]
    unmanaged_edges = unmanaged_edge_specs(root, nodes)
    if unmanaged_edges:
        add(
            "interoperability/unmanaged-edges",
            "warning",
            f"Draw.io contains {len(unmanaged_edges)} manually redrawn connector(s) without semantic IDs",
            subject={"kind": "diagram"},
            evidence={
                "count": len(unmanaged_edges),
                "connectors": [
                    {
                        "cell_id": edge["cell_id"],
                        "from": edge["from"],
                        "to": edge["to"],
                        "label": edge["label"],
                    }
                    for edge in unmanaged_edges
                ],
            },
            supported_fixes=["restore-edge-semantic-metadata", "redraw-with-patch"],
        )

    phases = document.phase_records(root, pool)
    if phases:
        layer_order = {"phase": 0, "lane": 1, "node": 2, "edge": 3}
        semantic_layers = [
            {
                "index": index,
                "kind": cell.attrib.get(contracts.DATA_KIND),
                "id": cell.attrib.get(contracts.DATA_SEMANTIC_ID),
            }
            for index, cell in enumerate(list(root))
            if cell.attrib.get(contracts.DATA_KIND) in layer_order
        ]
        # Draw.io serializes descendants immediately after their parent. XML
        # order across different parents is not sibling paint order: a node in
        # lane A may precede lane B without covering the phase background.
        # Include the pool and its ancestors as background containers. A
        # connector reparented beside one must not be hidden behind it.
        cells_by_id = {cell.attrib.get("id"): cell for cell in list(root)}
        background_containers: set[str] = set()
        container = pool
        while container is not None:
            container_id = container.attrib.get("id")
            if not container_id or container_id in background_containers:
                break
            background_containers.add(container_id)
            container = cells_by_id.get(container.attrib.get("parent"))
        sibling_ranks: dict[str | None, list[int]] = {}
        for cell in list(root):
            kind = cell.attrib.get(contracts.DATA_KIND)
            rank = (
                -1 if cell.attrib.get("id") in background_containers
                else layer_order.get(kind)
            )
            if rank is not None:
                sibling_ranks.setdefault(cell.attrib.get("parent"), []).append(
                    rank
                )
        pool_index = list(root).index(pool)
        phase_indices = [
            item["index"] for item in semantic_layers if item["kind"] == "phase"
        ]
        if (
            any(ranks != sorted(ranks) for ranks in sibling_ranks.values())
            or not phase_indices
            or min(phase_indices) <= pool_index
        ):
            add(
                "layout/phase-z-order",
                "error",
                "Phase backgrounds must be behind lanes, nodes, and connectors",
                subject={"kind": "diagram"},
                evidence={"layers": semantic_layers},
                supported_fixes=["normalize-phase-layering"],
            )

        phase_presentation = pool.attrib.get(contracts.DATA_PHASE_PRESENTATION, "bands")
        opaque_lanes = [
            lane_id
            for lane_id, record in lanes.items()
            if document.style_values(record["cell"].attrib.get("style", "")).get(
                "swimlaneFillColor"
            )
            != "none"
        ]
        if phase_presentation == "bands" and opaque_lanes:
            add(
                "layout/phase-lane-visibility",
                "error",
                "Lane bodies must be transparent when phase backgrounds exist",
                subject={"kind": "diagram"},
                evidence={"lanes": sorted(opaque_lanes)},
                supported_fixes=["make-lane-bodies-transparent"],
            )
        if phase_presentation == "rail":
            transparent_lanes = sorted(set(lanes) - set(opaque_lanes))
            if transparent_lanes:
                add(
                    "layout/phase-rail-lane-fill",
                    "error",
                    "Lane bodies must remain opaque when phases use a label rail",
                    subject={"kind": "diagram"},
                    evidence={"lanes": transparent_lanes},
                    supported_fixes=["restore-lane-body-fill"],
                )

        interactive_phases = [
            phase_id
            for phase_id, cell in phases.items()
            if cell.attrib.get("connectable") != "0"
            or document.style_values(cell.attrib.get("style", "")).get("pointerEvents") != "0"
        ]
        if interactive_phases:
            add(
                "layout/phase-interactive",
                "error",
                "Phase backgrounds must not intercept editing interactions",
                subject={"kind": "phase"},
                evidence={"phases": sorted(interactive_phases)},
                supported_fixes=["disable-phase-interaction"],
            )
    for cell in edge_cells:
        if cell.attrib.get("source") not in cell_ids or cell.attrib.get("target") not in cell_ids:
            edge_id = cell.attrib.get(contracts.DATA_SEMANTIC_ID)
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
        if schema_version in contracts.STRUCTURED_SCHEMA_VERSIONS:
            label = record["cell"].attrib.get("value", "")
            node_type = record["cell"].attrib.get(contracts.DATA_NODE_TYPE, "process")
            if (
                node_type in FIXED_ASPECT_NODE_TYPES
                and abs(node_geom["width"] - node_geom["height"]) >= core_geometry.GEOMETRY_TOLERANCE
            ):
                add(
                    "geometry/fixed-aspect-ratio",
                    "error",
                    f"Fixed-aspect node is not square: {semantic_id}",
                    subject={"kind": "node", "id": semantic_id},
                    evidence={"width": node_geom["width"], "height": node_geom["height"]},
                    supported_fixes=["set-equal-width-and-height"],
                )
            if node_type == "end" and label.strip():
                add(
                    "schema/end-label-not-empty",
                    "error",
                    f"Solid end node must not contain a label: {semantic_id}",
                    subject={"kind": "node", "id": semantic_id},
                    evidence={"label": label},
                    supported_fixes=["clear-end-label"],
                )
            required_lines = estimated_text_lines(
                label,
                node_geom["width"],
                diamond=node_type == "decision",
            )
            if node_type == "process":
                available_lines = max(
                    1,
                    int(max(0.0, node_geom["height"] - PROCESS_VERTICAL_PADDING) / PROCESS_TEXT_LINE_HEIGHT),
                )
            else:
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
            if node_type == "process":
                recommended_height = recommended_process_height(label, node_geom["width"])
                if node_geom["height"] > recommended_height + EXCESSIVE_HEIGHT_TOLERANCE:
                    add(
                        "layout/excessive-node-height",
                        "warning",
                        f"Process node is substantially taller than its label requires: {semantic_id}",
                        subject={"kind": "node", "id": semantic_id},
                        evidence={
                            "actual_height": node_geom["height"],
                            "recommended_height": recommended_height,
                            "estimated_lines": required_lines,
                        },
                        supported_fixes=["remove-explicit-height", "reduce-node-height"],
                    )

    port_usage: dict[tuple[str, str, float], list[str]] = {}
    for cell in edge_cells:
        if cell.attrib.get(contracts.DATA_ALLOW_PORT_REUSE) == "1":
            continue
        edge_id = cell.attrib.get(contracts.DATA_SEMANTIC_ID, cell.attrib.get("id", "unknown"))
        for prefix, endpoint_field in (("exit", contracts.DATA_FROM), ("entry", contracts.DATA_TO)):
            endpoint = cell.attrib.get(endpoint_field)
            port = document.port_from_style(cell, prefix)
            if endpoint and port:
                key = endpoint, port[0], round(port[1], 4)
                port_usage.setdefault(key, []).append(edge_id)
    for (node_id, side, offset), used_by in sorted(port_usage.items()):
        if len(used_by) > 1:
            add(
                "routing/port-reuse",
                "warning",
                f"Port reused at node {node_id} ({side}@{document.number(offset)}): {', '.join(sorted(used_by))}",
                subject={"kind": "node", "id": node_id},
                evidence={"side": side, "offset": offset, "edges": sorted(used_by)},
                supported_fixes=["allocate-distinct-port"],
            )

    internal_boundaries = internal_lane_boundaries(lanes)
    node_bounds = {
        semantic_id: document.node_bounds_in_pool(record, lanes[record["lane"]])
        for semantic_id, record in nodes.items()
    }
    node_ids = sorted(node_bounds)
    for index, first_id in enumerate(node_ids):
        for second_id in node_ids[index + 1 :]:
            if core_geometry.bounds_overlap(node_bounds[first_id], node_bounds[second_id]):
                add(
                    "layout/node-overlap",
                    "error",
                    f"Nodes overlap: {first_id} and {second_id}",
                    subject={"kind": "diagram"},
                    evidence={"nodes": [first_id, second_id]},
                    supported_fixes=["assign-distinct-slots", "change-rank", "move-node"],
                )
    edge_segments: dict[str, list[tuple[tuple[float, float], tuple[float, float]]]] = {}
    edge_points: dict[str, list[tuple[float, float]]] = {}
    edge_label_bounds: dict[str, tuple[int, dict[str, float]]] = {}
    edge_cells_by_id: dict[str, ET.Element] = {}
    for cell in edge_cells:
        edge_id = cell.attrib.get(contracts.DATA_SEMANTIC_ID, cell.attrib.get("id", "unknown"))
        points = document.edge_polyline(cell, lanes, nodes)
        edge_points[edge_id] = points
        edge_cells_by_id[edge_id] = cell
        segments = list(zip(points, points[1:]))
        edge_segments[edge_id] = segments
        short_segments = [
            (index, core_geometry.segment_length(segment))
            for index, segment in enumerate(segments[1:-1], start=1)
            if core_geometry.segment_length(segment) < MIN_INTERNAL_SEGMENT - core_geometry.GEOMETRY_TOLERANCE
        ]
        if short_segments:
            waypoint_origin = cell.attrib.get(contracts.DATA_WAYPOINTS_ORIGIN, "unknown")
            add(
                "routing/short-segment",
                "warning",
                f"Connector contains an internal segment shorter than {document.number(MIN_INTERNAL_SEGMENT)} px: {edge_id}",
                subject={"kind": "edge", "id": edge_id},
                evidence={
                    "segments": [
                        {"index": index, "length": length}
                        for index, length in short_segments
                    ],
                    "minimum": MIN_INTERNAL_SEGMENT,
                    "waypoints_origin": waypoint_origin,
                },
                supported_fixes=(
                    ["edit-explicit-waypoints"]
                    if waypoint_origin == "explicit"
                    else ["reroute-edge", "increase-rank-spacing"]
                ),
            )
        bends = core_geometry.bend_count(points)
        simpler_forward_route = False
        if (
            cell.attrib.get(contracts.DATA_ROUTE) == "forward"
            and bends > 2
        ):
            source_id = cell.attrib.get(contracts.DATA_FROM)
            target_id = cell.attrib.get(contracts.DATA_TO)
            exit_port = document.port_from_style(cell, "exit")
            entry_port = document.port_from_style(cell, "entry")
            if source_id in nodes and target_id in nodes and exit_port and entry_port:
                source_bounds = node_bounds[source_id]
                target_bounds = node_bounds[target_id]
                pool_width = max(
                    record["geometry"]["x"] + record["geometry"]["width"]
                    for record in lanes.values()
                )
                pool_height = max(
                    record["geometry"]["y"] + record["geometry"]["height"]
                    for record in lanes.values()
                )
                simple_candidates = route_candidates(
                    "forward",
                    points[0],
                    points[-1],
                    exit_port[0],
                    entry_port[0],
                    source_bounds,
                    target_bounds,
                    lanes[nodes[target_id]["lane"]]["geometry"],
                    pool_width,
                    pool_height,
                    internal_boundaries,
                    [],
                )
                simpler_forward_route = any(
                    core_geometry.bend_count(candidate) <= 2
                    and not path_has_hairpin(candidate)
                    and all(
                        core_geometry.segment_length(segment)
                        >= MIN_INTERNAL_SEGMENT - core_geometry.GEOMETRY_TOLERANCE
                        for segment in list(zip(candidate, candidate[1:]))[1:-1]
                    )
                    and automatic_polyline_is_safe(
                        candidate,
                        lanes,
                        nodes,
                        source_id,
                        target_id,
                    )
                    for candidate in simple_candidates
                )
        if simpler_forward_route:
            add(
                "routing/excessive-bends",
                "warning",
                f"Forward connector has unnecessary bends: {edge_id}",
                subject={"kind": "edge", "id": edge_id},
                evidence={"bends": bends, "maximum": 2},
                supported_fixes=["reroute-edge", "align-ports"],
            )
        if path_has_hairpin(points):
            add(
                "routing/hairpin",
                "warning",
                f"Connector contains a short-distance hairpin: {edge_id}",
                subject={"kind": "edge", "id": edge_id},
                supported_fixes=["reroute-edge", "align-ports"],
            )

        label = cell.attrib.get("value", "")
        label_choice = effective_label_bounds(cell, points)
        if label.strip() and label_choice is None:
            add(
                "text/edge-label-no-clear-span",
                "warning",
                f"Edge label has no clear carrier segment: {edge_id}",
                subject={"kind": "edge", "id": edge_id},
                evidence={"label": label},
                supported_fixes=["reroute-edge", "increase-rank-spacing", "increase-lane-width"],
            )
        elif label_choice is not None:
            edge_label_bounds[edge_id] = label_choice
            _, label_box = label_choice
            overlapping_nodes = sorted(
                node_id
                for node_id, bounds in node_bounds.items()
                if core_geometry.bounds_overlap(label_box, bounds, gap=1.0)
            )
            if overlapping_nodes:
                add(
                    "text/edge-label-node-overlap",
                    "warning",
                    f"Edge label overlaps a node: {edge_id}",
                    subject={"kind": "edge", "id": edge_id},
                    evidence={"nodes": overlapping_nodes},
                    supported_fixes=["move-edge-label", "reroute-edge", "increase-rank-spacing"],
                )
        for segment_index, segment in enumerate(segments):
            axis = core_geometry.segment_axis(segment)
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
                if any(abs(x - boundary) < core_geometry.GEOMETRY_TOLERANCE for boundary in internal_boundaries):
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
                        f"(< {document.number(LANE_BOUNDARY_CLEARANCE)} px): {edge_id}",
                        subject={"kind": "edge", "id": edge_id},
                        evidence={"distance": nearest, "minimum": LANE_BOUNDARY_CLEARANCE},
                        supported_fixes=["reroute-edge", "change-routing-zone"],
                    )
            for node_id, bounds in node_bounds.items():
                if (
                    node_id == cell.attrib.get(contracts.DATA_FROM)
                    and segment_index == 0
                ):
                    continue
                if (
                    node_id == cell.attrib.get(contracts.DATA_TO)
                    and segment_index == len(segments) - 1
                ):
                    continue
                if core_geometry.segment_crosses_bounds(segment, bounds):
                    add(
                        "routing/node-crossing",
                        "warning",
                        f"Connector crosses node: {edge_id} -> {node_id}",
                        subject={"kind": "edge", "id": edge_id},
                        evidence={"node": node_id},
                        supported_fixes=["reroute-edge"],
                    )

        if (
            cell.attrib.get(contracts.DATA_ROUTE) == "back"
            and cell.attrib.get(contracts.DATA_WAYPOINTS_ORIGIN) == "automatic"
        ):
            target_id = cell.attrib.get(contracts.DATA_TO)
            entry_port = document.port_from_style(cell, "entry")
            if target_id in nodes and entry_port and entry_port[0] in {"left", "right"}:
                target = nodes[target_id]
                target_lane = lanes[target["lane"]]["geometry"]
                target_bounds = node_bounds[target_id]
                safe_gap = LANE_BOUNDARY_CLEARANCE - core_geometry.GEOMETRY_TOLERANCE
                vertical_x_values = [
                    segment[0][0]
                    for segment in segments
                    if core_geometry.segment_axis(segment) == "vertical"
                ]
                if entry_port[0] == "left":
                    internal_corridor = any(
                        target_lane["x"] + safe_gap <= x < target_bounds["left"]
                        for x in vertical_x_values
                    )
                else:
                    lane_right = target_lane["x"] + target_lane["width"]
                    internal_corridor = any(
                        target_bounds["right"] < x <= lane_right - safe_gap
                        for x in vertical_x_values
                    )
                if not internal_corridor:
                    add(
                        "routing/back-corridor-outside-target-lane",
                        "warning",
                        f"Automatic back route borrows space outside the target lane: {edge_id}",
                        subject={"kind": "edge", "id": edge_id},
                        evidence={
                            "target_lane": target["lane"],
                            "entry_side": entry_port[0],
                            "vertical_x": vertical_x_values,
                        },
                        supported_fixes=["increase-target-lane-gutter", "set-explicit-waypoints"],
                    )

    edge_ids = sorted(edge_segments)
    for index, first_id in enumerate(edge_ids):
        for second_id in edge_ids[index + 1 :]:
            if any(
                core_geometry.segments_conflict(first_segment, second_segment)
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
            first_cell = edge_cells_by_id[first_id]
            second_cell = edge_cells_by_id[second_id]
            near_parallel = any(
                segments_near_parallel(first_segment, second_segment)
                for first_segment in edge_segments[first_id]
                for second_segment in edge_segments[second_id]
            )
            if near_parallel:
                add(
                    "routing/near-parallel-conflict",
                    "warning",
                    f"Connector segments run too close in parallel: {first_id} / {second_id}",
                    subject={"kind": "edge", "id": first_id},
                    evidence={"other_edge": second_id, "minimum": NEAR_PARALLEL_CLEARANCE},
                    supported_fixes=["separate-routing-corridors", "reroute-edge"],
                )
            reciprocal = (
                first_cell.attrib.get(contracts.DATA_FROM) == second_cell.attrib.get(contracts.DATA_TO)
                and first_cell.attrib.get(contracts.DATA_TO) == second_cell.attrib.get(contracts.DATA_FROM)
            )
            if reciprocal and (
                near_parallel
                or any(
                    core_geometry.segments_conflict(first_segment, second_segment)
                    for first_segment in edge_segments[first_id]
                    for second_segment in edge_segments[second_id]
                )
            ):
                add(
                    "routing/reciprocal-ambiguity",
                    "warning",
                    f"Forward and return connectors share or crowd the same corridor: {first_id} / {second_id}",
                    subject={"kind": "edge", "id": first_id},
                    evidence={"other_edge": second_id},
                    supported_fixes=["separate-forward-and-return-corridors"],
                )

    for edge_id, (carrier_index, label_box) in edge_label_bounds.items():
        overlaps = []
        for other_id, segments in edge_segments.items():
            for index, segment in enumerate(segments):
                if other_id == edge_id and index == carrier_index:
                    continue
                if core_geometry.segment_intersects_box(segment, label_box, gap=1.0):
                    overlaps.append(other_id)
                    break
        if overlaps:
            add(
                "text/edge-label-edge-overlap",
                "warning",
                f"Edge label overlaps a connector: {edge_id}",
                subject={"kind": "edge", "id": edge_id},
                evidence={"edges": sorted(set(overlaps))},
                supported_fixes=["move-edge-label", "reroute-edge"],
            )

    label_ids = sorted(edge_label_bounds)
    for index, first_id in enumerate(label_ids):
        for second_id in label_ids[index + 1:]:
            if core_geometry.bounds_overlap(edge_label_bounds[first_id][1], edge_label_bounds[second_id][1], gap=2.0):
                add(
                    "text/edge-label-edge-overlap",
                    "warning",
                    f"Edge labels overlap: {first_id} / {second_id}",
                    subject={"kind": "edge", "id": first_id},
                    evidence={"other_label": second_id},
                    supported_fixes=["move-edge-label", "reroute-edge", "increase-rank-spacing"],
                )

    if schema_version in contracts.STRUCTURED_SCHEMA_VERSIONS:
        node_ranks = {
            node_id: int(record["cell"].attrib.get(contracts.DATA_RANK, "0"))
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
        if main_path and main_path[0] in nodes and nodes[main_path[0]]["cell"].attrib.get(contracts.DATA_NODE_TYPE) != "start":
            add(
                "semantic/main-path-start",
                "error",
                "Main path must begin with a start node",
                subject={"kind": "main_path"},
                evidence={"node": main_path[0]},
                supported_fixes=["correct-main-path", "change-node-type"],
            )
        if main_path and main_path[-1] in nodes and nodes[main_path[-1]]["cell"].attrib.get(contracts.DATA_NODE_TYPE) != "end":
            add(
                "semantic/main-path-end",
                "error",
                "Main path must end with an end node",
                subject={"kind": "main_path"},
                evidence={"node": main_path[-1]},
                supported_fixes=["correct-main-path", "change-node-type"],
            )
        edge_pairs = {
            (cell.attrib.get(contracts.DATA_FROM), cell.attrib.get(contracts.DATA_TO)): cell
            for cell in edge_cells
        }
        unmanaged_pairs = {(edge["from"], edge["to"]) for edge in unmanaged_edges}
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
            if edge is None and (source_id, target_id) not in unmanaged_pairs:
                add(
                    "semantic/main-path-edge",
                    "error",
                    f"Main path has no edge from {source_id} to {target_id}",
                    subject={"kind": "main_path"},
                    evidence={"from": source_id, "to": target_id},
                    supported_fixes=["add-main-path-edge", "correct-main-path"],
                )
            elif edge is not None and (
                edge.attrib.get(contracts.DATA_ROUTE) == "back"
                or node_ranks[target_id] < node_ranks[source_id]
            ):
                add(
                    "semantic/main-path-rank",
                    "error",
                    f"Main path moves backward from {source_id} to {target_id}",
                    subject={"kind": "main_path"},
                    evidence={"from": source_id, "to": target_id},
                    supported_fixes=["correct-rank", "remove-return-from-main-path"],
                )
            elif (
                nodes[source_id]["lane"] == nodes[target_id]["lane"]
                and node_ranks[target_id] > node_ranks[source_id]
                and edge is not None
                and nodes[source_id]["cell"].attrib.get(contracts.DATA_SLOT, "main") == "main"
                and nodes[target_id]["cell"].attrib.get(contracts.DATA_SLOT, "main") == "main"
            ):
                edge_id = edge.attrib.get(contracts.DATA_SEMANTIC_ID, "unknown")
                bends = core_geometry.bend_count(edge_points.get(edge_id, []))
                if bends:
                    add(
                        "layout/main-path-zigzag",
                        "warning",
                        f"Same-lane main path should continue vertically without a zigzag: {edge_id}",
                        subject={"kind": "edge", "id": edge_id},
                        evidence={"bends": bends, "from": source_id, "to": target_id},
                        supported_fixes=["use-bottom-to-top-main-path", "align-ports"],
                    )

        outgoing: dict[str, list[ET.Element]] = {}
        for cell in edge_cells:
            outgoing.setdefault(cell.attrib.get(contracts.DATA_FROM, ""), []).append(cell)
            if cell.attrib.get(contracts.DATA_EDGE_TYPE) == "retry":
                source_id = cell.attrib.get(contracts.DATA_FROM)
                target_id = cell.attrib.get(contracts.DATA_TO)
                if source_id in node_ranks and target_id in node_ranks and node_ranks[target_id] >= node_ranks[source_id]:
                    edge_id = cell.attrib.get(contracts.DATA_SEMANTIC_ID)
                    add(
                        "semantic/retry-direction",
                        "warning",
                        f"Retry edge does not return to an earlier rank: {edge_id}",
                        subject={"kind": "edge", "id": edge_id},
                        supported_fixes=["correct-rank", "change-edge-type"],
                    )

        unmanaged_outgoing: dict[str, int] = {}
        for edge in unmanaged_edges:
            unmanaged_outgoing[edge["from"]] = unmanaged_outgoing.get(edge["from"], 0) + 1

        for node_id, record in nodes.items():
            if record["cell"].attrib.get(contracts.DATA_NODE_TYPE) != "decision":
                continue
            decision_edges = outgoing.get(node_id, [])
            total_decision_edges = len(decision_edges) + unmanaged_outgoing.get(node_id, 0)
            if total_decision_edges < 2:
                add(
                    "semantic/decision-branches",
                    "warning",
                    f"Decision node has fewer than two outgoing branches: {node_id}",
                    subject={"kind": "node", "id": node_id},
                    supported_fixes=["add-decision-branch"],
                )
                continue
            if unmanaged_outgoing.get(node_id, 0):
                continue
            branches = [edge.attrib.get(contracts.DATA_BRANCH) for edge in decision_edges]
            outcomes = [edge.attrib.get(contracts.DATA_OUTCOME) for edge in decision_edges]
            has_distinct_v3_outcomes = (
                schema_version == contracts.V3_SCHEMA_VERSION
                and all(outcomes)
                and len(set(outcomes)) >= 2
            )
            has_binary_branches = (
                len(branches) == 2
                and all(branch in BRANCH_CLASSES for branch in branches)
                and len(set(branches)) == 2
            )
            if not has_distinct_v3_outcomes and not has_binary_branches:
                add(
                    "semantic/decision-outcome",
                    "warning",
                    f"Decision branches require explicit outcome IDs: {node_id}",
                    subject={"kind": "node", "id": node_id},
                    evidence={"branches": branches, "outcomes": outcomes},
                    supported_fixes=["label-decision-branches"],
                )

        starts = [
            node_id
            for node_id, record in nodes.items()
            if record["cell"].attrib.get(contracts.DATA_NODE_TYPE) == "start"
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
                adjacency.setdefault(cell.attrib.get(contracts.DATA_FROM, ""), []).append(cell.attrib.get(contracts.DATA_TO, ""))
            for edge in unmanaged_edges:
                adjacency.setdefault(edge["from"], []).append(edge["to"])
            while frontier:
                current = frontier.pop()
                for target in adjacency.get(current, []):
                    if target in nodes and target not in reachable:
                        reachable.add(target)
                        frontier.append(target)
            for node_id, record in nodes.items():
                if node_id not in reachable and record["cell"].attrib.get(contracts.DATA_NODE_TYPE) != "note":
                    add(
                        "semantic/unreachable-node",
                        "warning",
                        f"Node is unreachable from a start node: {node_id}",
                        subject={"kind": "node", "id": node_id},
                        supported_fixes=["connect-node", "remove-node"],
                    )

        max_rank = max(node_ranks.values(), default=1)
        for phase_id, cell in document.phase_records(root, pool).items():
            try:
                from_rank = int(cell.attrib.get(contracts.DATA_FROM_RANK, "0"))
                to_rank = int(cell.attrib.get(contracts.DATA_TO_RANK, "0"))
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

    diagnostic_codes = [item["code"] for item in diagnostics]
    if (
        "integrity/model-hash-mismatch" in diagnostic_codes
        or "integrity/schema-composition-mismatch" in diagnostic_codes
    ):
        managed_state = "unsafe"
    elif any(
        code in {
            "integrity/model-hash-missing",
            "structure/unmanaged-vertex",
            "interoperability/unmanaged-edges",
        }
        for code in diagnostic_codes
    ):
        managed_state = "recoverable"
    else:
        managed_state = "managed"
    main_path_bends = 0
    if schema_version in contracts.STRUCTURED_SCHEMA_VERSIONS:
        main_pairs = set(zip(read_main_path(pool), read_main_path(pool)[1:]))
        for edge_id, cell in edge_cells_by_id.items():
            if (cell.attrib.get(contracts.DATA_FROM), cell.attrib.get(contracts.DATA_TO)) in main_pairs:
                main_path_bends += core_geometry.bend_count(edge_points.get(edge_id, []))

    return {
        "valid": not errors,
        "quality_gate_passed": not errors and not warnings,
        "managed_state": managed_state,
        "has_semantic_metadata": True,
        "tool_version": integrity["tool_version"],
        "model_hash_version": integrity["model_hash_version"],
        "stored_model_hash": integrity["stored_model_hash"],
        "computed_model_hash": integrity["computed_model_hash"],
        "model_hash_matches": integrity["model_hash_matches"],
        "errors": errors,
        "warnings": warnings,
        "diagnostics": diagnostics,
        "schema_version": schema_version,
        "lanes": len(lanes),
        "nodes": len(nodes),
        "edges": len(edge_cells),
        "unmanaged_edges": len(unmanaged_edges),
        "main_path_bends": main_path_bends,
        "short_segments": diagnostic_codes.count("routing/short-segment"),
        "label_conflicts": sum(code.startswith("text/edge-label-") for code in diagnostic_codes),
        "reciprocal_ambiguities": diagnostic_codes.count("routing/reciprocal-ambiguity"),
        "manual_waypoints_preserved": None,
        "manual_waypoints_checked": 0,
        "visual_review": "not_available",
    }


def allowed_missing_from_patch(changes: dict | None) -> set[str]:
    if not changes:
        return set()
    allowed = {f"lane:{semantic_id}" for semantic_id in changes.get("delete_lanes", [])}
    allowed.update(f"node:{semantic_id}" for semantic_id in changes.get("delete_nodes", []))
    allowed.update(f"edge:{semantic_id}" for semantic_id in changes.get("delete_edges", []))
    allowed.update(f"phase:{semantic_id}" for semantic_id in changes.get("delete_phases", []))
    return allowed


def compare_trees(before: ET.ElementTree, after: ET.ElementTree, changes: dict | None = None) -> dict:
    """Compare an actual result with the exact result of the declared patch.

    A patch declaration is executable, not an allowlist. Replaying it on a
    copy of ``before`` makes every permitted attribute, geometry change,
    reroute, addition, and deletion explicit. This prevents an unrelated edit
    from being hidden behind a broadly authorized semantic cell.
    """
    before_cells = document.semantic_cells(before)
    after_cells = document.semantic_cells(after)
    missing = sorted(set(before_cells) - set(after_cells))
    added = sorted(set(after_cells) - set(before_cells))
    changed_geometry: list[str] = []
    changed_attributes: list[str] = []

    for key in sorted(set(before_cells) & set(after_cells)):
        before_cell = before_cells[key]
        after_cell = after_cells[key]
        if document.element_signature(before_cell.find("mxGeometry")) != document.element_signature(after_cell.find("mxGeometry")):
            changed_geometry.append(key)
        before_attributes = document.comparison_attributes(before_cell)
        after_attributes = document.comparison_attributes(after_cell)
        if before_attributes != after_attributes:
            changed_attributes.append(key)

    if changes is None:
        expected_tree = before
        expected_cells = before_cells
        expected_added: set[str] = set()
        expected_missing: set[str] = set()
    else:
        expected_tree = copy.deepcopy(before)
        patch_tree(expected_tree, changes, allow_geometry_updates=True)
        expected_cells = document.semantic_cells(expected_tree)
        expected_added = set(expected_cells) - set(before_cells)
        expected_missing = set(before_cells) - set(expected_cells)

    expected_geometry: list[str] = []
    expected_attributes: list[str] = []
    unexpected_geometry: list[str] = []
    unexpected_attributes: list[str] = []
    common_expected_actual = set(expected_cells) & set(after_cells)
    for key in sorted(common_expected_actual):
        expected_cell = expected_cells[key]
        actual_cell = after_cells[key]
        if document.element_signature(expected_cell.find("mxGeometry")) != document.element_signature(
            actual_cell.find("mxGeometry")
        ):
            unexpected_geometry.append(key)
        expected_cell_attributes = document.comparison_attributes(expected_cell)
        actual_attributes = document.comparison_attributes(actual_cell)
        if expected_cell_attributes != actual_attributes:
            unexpected_attributes.append(key)
        if key in before_cells:
            before_cell = before_cells[key]
            if document.element_signature(before_cell.find("mxGeometry")) != document.element_signature(
                expected_cell.find("mxGeometry")
            ):
                expected_geometry.append(key)
            if document.comparison_attributes(before_cell) != expected_cell_attributes:
                expected_attributes.append(key)

    actual_keys = set(after_cells)
    expected_keys = set(expected_cells)
    unexpected_added = sorted(actual_keys - expected_keys)
    unexpected_expected_missing = sorted(expected_keys - actual_keys)
    unexpected_missing = sorted(
        (set(missing) - expected_missing) | set(unexpected_expected_missing)
    )
    allowed_missing = sorted(expected_missing)
    allowed = sorted(
        set(expected_geometry)
        | set(expected_attributes)
        | expected_added
        | expected_missing
    )
    changed_order = document.sibling_order_changes(before, after)
    unexpected_order = document.sibling_order_changes(expected_tree, after)
    expected_unmanaged = document.unmanaged_cell_signatures(expected_tree)
    actual_unmanaged = document.unmanaged_cell_signatures(after)
    unexpected_unmanaged = []
    for cell_id in sorted(expected_unmanaged.keys() | actual_unmanaged.keys(),
                          key=lambda value: value or ""):
        if expected_unmanaged.get(cell_id) != actual_unmanaged.get(cell_id):
            change = ("added" if cell_id not in expected_unmanaged else
                      "missing" if cell_id not in actual_unmanaged else "changed")
            unexpected_unmanaged.append({"cell_id": cell_id, "change": change})
    preserved = (
        not unexpected_missing
        and not unexpected_geometry
        and not unexpected_attributes
        and not unexpected_added
        and not unexpected_order
        and not unexpected_unmanaged
    )
    result = {
        "preserved": preserved,
        "existing_cells_checked": len(set(before_cells) & set(after_cells)),
        "added_cells": added,
        "missing_cells": missing,
        "changed_geometry": changed_geometry,
        "changed_attributes": changed_attributes,
        "allowed_changes": allowed,
        "allowed_missing": allowed_missing,
        "unexpected_missing": unexpected_missing,
        "unexpected_geometry": unexpected_geometry,
        "unexpected_attributes": unexpected_attributes,
        "unexpected_added": unexpected_added,
    }
    # Add evidence only when applicable, keeping existing clean CLI receipts
    # byte-for-byte compatible. Missing optional fields mean no such change.
    if changed_order:
        result["changed_sibling_order"] = changed_order
    if unexpected_order:
        result["unexpected_sibling_order"] = unexpected_order
    if unexpected_unmanaged:
        result["unexpected_unmanaged_cells"] = unexpected_unmanaged
    return result


def inspect_tree(tree: ET.ElementTree) -> dict:
    pool = document.find_pool(tree)
    root = document.graph_root(tree)
    lanes, nodes = document.lane_node_records(root, pool)
    phases = document.phase_records(root, pool)
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
            int(item[1]["cell"].attrib.get(contracts.DATA_RANK, "0")),
            lane_index.get(item[1]["lane"], 999),
            item[0],
        ),
    ):
        node_spec = {
            "id": node_id,
            "lane": record["lane"],
            "rank": int(record["cell"].attrib.get(contracts.DATA_RANK, "0")),
            "type": record["cell"].attrib.get(contracts.DATA_NODE_TYPE, "process"),
            "label": record["cell"].attrib.get("value", ""),
            **record["geometry"],
        }
        if record["cell"].attrib.get(contracts.DATA_SLOT):
            node_spec["slot"] = record["cell"].attrib[contracts.DATA_SLOT]
        if record["cell"].attrib.get(contracts.DATA_ANCHOR):
            node_spec["anchor"] = json.loads(record["cell"].attrib[contracts.DATA_ANCHOR])
        if record["cell"].attrib.get(contracts.DATA_GROUP_ID):
            node_spec["group_id"] = record["cell"].attrib[contracts.DATA_GROUP_ID]
        node_specs.append(node_spec)

    edge_specs = []
    for edge_id, cell in sorted(document.edge_records(root).items()):
        edge = existing_edge_spec(cell)
        points = document.edge_waypoints(cell)
        if points:
            edge["waypoints"] = [{"x": x, "y": y} for x, y in points]
        edge_specs.append(edge)

    phase_specs = [phase_cell_spec(cell) for _, cell in sorted(phases.items())]
    validation = validate_tree(tree)
    result = {
        "compatible": validation.get("managed_state") != "unsafe",
        "has_semantic_metadata": validation.get("has_semantic_metadata", True),
        "managed_state": validation.get("managed_state", "recoverable"),
        "tool_version": validation.get("tool_version"),
        "model_hash_version": validation.get("model_hash_version"),
        "stored_model_hash": validation.get("stored_model_hash"),
        "computed_model_hash": validation.get("computed_model_hash"),
        "model_hash_matches": validation.get("model_hash_matches"),
        "schema_version": pool.attrib.get(contracts.DATA_SCHEMA_VERSION, "1"),
        "title": pool.attrib.get("value", ""),
        "main_path": read_main_path(pool),
        "lanes": lane_specs,
        "phases": phase_specs,
        "nodes": node_specs,
        "edges": edge_specs,
        "unmanaged_edges": unmanaged_edge_specs(root, nodes),
        "validation": validation,
    }
    if result["schema_version"] == contracts.V3_SCHEMA_VERSION:
        result["behavior_pattern"] = pool.attrib.get(contracts.DATA_BEHAVIOR_PATTERN, "custom")
        result["layout"] = {
            "profile": pool.attrib.get(contracts.DATA_LAYOUT_PROFILE, "review"),
            "phase_presentation": pool.attrib.get(contracts.DATA_PHASE_PRESENTATION, "bands"),
        }
        result["groups"] = json.loads(pool.attrib.get(contracts.DATA_GROUPS, "[]"))
    return result


def command_build(args: argparse.Namespace) -> None:
    document.ensure_output_available(args.output, args.force)
    spec = load_json(args.spec)
    tree = build_tree(spec)
    result = validate_tree(tree)
    strict_failed = bool(args.strict and result["warnings"])
    if not result["valid"] or strict_failed:
        raise contracts.DiagramError(
            "Generated diagram failed strict validation"
            if strict_failed
            else "Generated diagram failed validation",
            code="delivery/strict-validation-failed"
            if strict_failed
            else "delivery/validation-failed",
            evidence={"strict": args.strict, "diagnostics": result["diagnostics"]},
        )
    document.write_tree(tree, args.output)
    result.update(
        {
            "operation": "build",
            "strict_mode": args.strict,
            "output": document.file_receipt(args.output),
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_patch(args: argparse.Namespace) -> None:
    document.ensure_different(args.input, args.output)
    document.ensure_output_available(args.output, args.force)
    input_receipt = document.file_receipt(args.input)
    if not args.expected_input_sha256:
        raise contracts.DiagramError(
            "Patch requires the SHA-256 digest returned by inspect",
            code="delivery/input-sha256-required",
            supported_fixes=["inspect-latest-input", "supply-expected-input-sha256"],
        )
    if (
        input_receipt["sha256"] != args.expected_input_sha256
    ):
        raise contracts.DiagramError(
            "Patch input does not match the reviewed SHA-256 baseline",
            code="delivery/input-sha256-mismatch",
            evidence={
                "expected": args.expected_input_sha256,
                "actual": input_receipt["sha256"],
            },
            supported_fixes=["inspect-latest-input", "update-expected-input-sha256"],
        )
    tree = document.read_tree(args.input)
    input_validation = validate_tree(tree)
    integrity_errors = [
        diagnostic
        for diagnostic in input_validation["diagnostics"]
        if diagnostic["severity"] == "error"
        and diagnostic["code"].startswith("integrity/")
    ]
    if integrity_errors:
        raise contracts.DiagramError(
            "Patch input has unsafe managed metadata",
            code="delivery/input-integrity-failed",
            evidence={"diagnostics": integrity_errors},
            supported_fixes=["restore-semantic-metadata", "controlled-rebuild"],
        )
    if (
        input_validation.get("model_hash_matches") is False
        and not args.accept_model_drift
    ):
        raise contracts.DiagramError(
            "Patch input semantic metadata changed after its managed hash was written",
            code="integrity/model-hash-mismatch",
            evidence={
                "stored": input_validation.get("stored_model_hash"),
                "computed": input_validation.get("computed_model_hash"),
            },
            supported_fixes=["review-semantic-drift", "use-accept-model-drift"],
        )
    patch_receipt = patch_tree(tree, load_json(args.changes), args.allow_geometry_updates)
    patch_receipt.update(
        {
            "input_sha256": input_receipt["sha256"],
            "input_bytes": input_receipt["bytes"],
            "input_tool_version": input_validation.get("tool_version"),
            "input_managed_state": input_validation.get("managed_state"),
            "input_model_hash_matches": input_validation.get("model_hash_matches"),
            "accepted_model_drift": bool(
                args.accept_model_drift
                and input_validation.get("model_hash_matches") is False
            ),
        }
    )
    result = validate_tree(tree)
    strict_failed = bool(args.strict and result["warnings"])
    if not result["valid"] or strict_failed:
        raise contracts.DiagramError(
            "Patched diagram failed strict validation"
            if strict_failed
            else "Patched diagram failed validation",
            code="delivery/strict-validation-failed"
            if strict_failed
            else "delivery/validation-failed",
            evidence={"strict": args.strict, "diagnostics": result["diagnostics"]},
        )
    document.write_tree(tree, args.output)
    result.update(
        {
            "operation": "patch",
            "strict_mode": args.strict,
            "manual_waypoints_preserved": patch_receipt["manual_waypoints_preserved"],
            "manual_waypoints_checked": patch_receipt["manual_waypoints_checked"],
            "patch_receipt": patch_receipt,
            "output": document.file_receipt(args.output),
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_validate(args: argparse.Namespace) -> None:
    result = validate_tree(document.read_tree(args.input))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"] or (args.strict and result["warnings"]):
        raise SystemExit(1)


def command_compare(args: argparse.Namespace) -> None:
    changes = load_json(args.changes) if args.changes else None
    result = compare_trees(document.read_tree(args.before), document.read_tree(args.after), changes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["preserved"]:
        raise SystemExit(1)


def command_inspect(args: argparse.Namespace) -> None:
    result = inspect_tree(document.read_tree(args.input))
    result["input"] = document.file_receipt(args.input)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a new editable Draw.io file")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument(
        "--strict",
        action="store_true",
        help="Do not write output when quality warnings are present",
    )
    build.add_argument("--force", action="store_true", help="Replace an existing output file")
    build.set_defaults(func=command_build)

    patch = subparsers.add_parser("patch", help="Incrementally patch an existing generated Draw.io file")
    patch.add_argument("--input", type=Path, required=True)
    patch.add_argument("--changes", type=Path, required=True)
    patch.add_argument("--output", type=Path, required=True)
    patch.add_argument(
        "--strict",
        action="store_true",
        help="Do not write output when quality warnings are present",
    )
    patch.add_argument("--allow-geometry-updates", action="store_true")
    patch.add_argument(
        "--expected-input-sha256",
        help="Fail unless the input matches this reviewed SHA-256 digest",
    )
    patch.add_argument(
        "--accept-model-drift",
        action="store_true",
        help="Rebaseline reviewed semantic edits when the stored model hash differs",
    )
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
    except contracts.DiagramError as exc:
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
        diagnostic = contracts.make_diagnostic(code, "error", str(exc))
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
    except Exception as exc:
        message = "Unexpected internal error"
        diagnostic = contracts.make_diagnostic(
            "internal/unexpected",
            "error",
            message,
            evidence={"exception_type": type(exc).__name__},
            supported_fixes=["report-bug"],
        )
        print("error: Unexpected internal error", file=sys.stderr)
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": [message],
                    "warnings": [],
                    "diagnostics": [diagnostic],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
