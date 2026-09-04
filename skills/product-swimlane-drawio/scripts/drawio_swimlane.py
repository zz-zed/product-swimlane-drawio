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

from swimlane_core import (
    clearance, contracts, document, geometry as core_geometry, labels, metadata, ports,
    routing, routing_adapter, routing_policy, sizing, validation as core_validation,
)


DEFAULTS = {
    "x": 40,
    "y": 40,
    "title_height": 36,
    "lane_header_height": 32,
    "row_gap": 96,
    "top_padding": 40,
    "bottom_padding": 52,
}


EDGE_TYPES = {"flow", "call", "return", "retry", "async"}
ROUTING_FIELDS = {
    "from", "to", "type", "route", "branch", "exit_side", "entry_side",
    "exit_offset", "entry_offset", "waypoints", "allow_port_reuse", "reroute",
}


NODE_STYLES = {
    "start": "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#ffffff;strokeColor=#333333;strokeWidth=1.5;",
    "end": "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#333333;strokeColor=#333333;strokeWidth=1.5;",
    "process": "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;fontColor=#333333;strokeColor=#666666;fontSize=12;",
    "decision": "rhombus;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontColor=#333333;fontSize=12;",
    "note": "shape=note;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontColor=#333333;fontSize=11;",
}


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
            f"{subject} must be at least {contracts.number(minimum)}",
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
        node["type"] in sizing.FIXED_ASPECT_NODE_TYPES
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
    if "route" in edge and edge["route"] not in routing_policy.ROUTE_CLASSES:
        raise contracts.DiagramError(
            f"Unsupported route class: {edge['route']}",
            code="schema/enum",
            subject={"kind": "edge", "id": edge.get("id")},
            evidence={"field": "route", "allowed": sorted(routing_policy.ROUTE_CLASSES)},
        )
    if "branch" in edge and edge["branch"] not in routing_policy.BRANCH_CLASSES:
        raise contracts.DiagramError(
            f"Unsupported branch class: {edge['branch']}",
            code="schema/enum",
            subject={"kind": "edge", "id": edge.get("id")},
            evidence={"field": "branch", "allowed": sorted(routing_policy.BRANCH_CLASSES)},
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
            ports.validate_side(edge[field], field)
    for field in ("exit_offset", "entry_offset"):
        if field in edge:
            ports.validate_offset(edge[field], field)
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
        routing.normalize_waypoints(edge["waypoints"])


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
        content = sum(sizing.node_size(node)[0] for node in row_nodes) + gap * max(
            0, len(row_nodes) - 1
        )
        return content / 2.0, content / 2.0

    main_width = sizing.node_size(by_slot["main"])[0]
    left_extent = main_width / 2.0
    right_extent = main_width / 2.0
    if "left" in by_slot:
        left_extent += gap + sizing.node_size(by_slot["left"])[0]
    if "right" in by_slot:
        right_extent += gap + sizing.node_size(by_slot["right"])[0]
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
            main_width = sizing.node_size(main)[0]
            main_x = lane_axes.get(lane_id, lane_widths[lane_id] / 2.0) - main_width / 2.0
            positions[main["id"]] = main_x
            if "left" in by_slot:
                left = by_slot["left"]
                positions[left["id"]] = main_x - gap - sizing.node_size(left)[0]
            if "right" in by_slot:
                right = by_slot["right"]
                positions[right["id"]] = main_x + main_width + gap
        else:
            widths = [sizing.node_size(node)[0] for node in ordered]
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
    # An internal return carrier has two independent requirements on each
    # target-lane side: it must stay inside the lane-boundary safety margin and
    # it must leave the calibrated terminal run before entering the target.
    # The latter was previously omitted, leaving a mathematically unusable
    # 24px centred gap and forcing automatic routes outside their target lane.
    required_gutter = (
        routing_policy.LANE_BOUNDARY_CLEARANCE
        + clearance.CLEARANCE_THRESHOLD_PX
        + core_geometry.GEOMETRY_TOLERANCE
    )

    for node in spec["nodes"]:
        if "x" in node:
            continue
        node_width, _ = sizing.node_size(node)
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
        if routing.inferred_spec_route_class(edge, nodes) != "back":
            continue
        target = nodes[edge["to"]]
        if "x" in target:
            continue
        target_width, _ = sizing.node_size(target)
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
                routing_policy.LANE_BOUNDARY_CLEARANCE + routing_policy.ROUTE_CLEARANCE + core_geometry.GEOMETRY_TOLERANCE
            )
            for endpoint in (nodes[edge["from"]], target):
                if "x" in endpoint:
                    continue
                endpoint_width, _ = sizing.node_size(endpoint)
                endpoint_minimum = math.ceil(
                    endpoint_width + 2 * required_side_space
                )
                widths[endpoint["lane"]] = max(
                    widths[endpoint["lane"]],
                    float(endpoint_minimum),
                )

    return widths


def adaptive_canvas_values(spec: dict) -> dict:
    """Increase automatic rank spacing when nodes and edge labels need more room."""
    values = canvas_values(spec)
    if "row_gap" in spec.get("canvas", {}):
        return values
    if spec.get("schema_version") == contracts.V3_SCHEMA_VERSION:
        values["row_gap"] = PROFILE_ROW_GAPS[layout_profile(spec)]

    rank_heights: dict[int, float] = {}
    for node in spec["nodes"]:
        _, height = sizing.node_size(node)
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
        label_space = labels.EDGE_LABEL_HEIGHT + 4.0 if (rank, next_rank) in labeled_pairs else 16.0
        needed = rank_heights[rank] / 2 + rank_heights[next_rank] / 2 + label_space
        required = max(required, needed)
    values["row_gap"] = math.ceil(required / 8.0) * 8.0
    return values


def lane_height(max_rank: int, values: dict) -> float:
    content = values["top_padding"] + max(0, max_rank - 1) * values["row_gap"]
    return values["lane_header_height"] + content + 40 + values["bottom_padding"]


def node_y(node: dict, values: dict) -> float:
    _, height = sizing.node_size(node)
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
                f"startSize={contracts.number(values['lane_header_height'])};"
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
    width, height = sizing.node_size(node)
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


def create_edge_cell(
    root: ET.Element,
    pool: ET.Element,
    edge: dict,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    allocator: ports.PortAllocator | None = None,
    routing_context: dict | None = None,
    decision: routing.RouteDecision | None = None,
    explicit_fields: set[str] | None = None,
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
    if decision is not None:
        return routing_adapter.apply_route_decision(
            cell, edge, decision, lanes, nodes, explicit_fields=explicit_fields,
            points_action="replace_explicit" if "waypoints" in (explicit_fields or set()) else "replace_automatic",
            routing_context=routing_context,
        )
    if allocator is None:
        raise contracts.DiagramError("Edge creation requires a route decision or allocator")
    return routing_adapter.apply_edge_route(cell, edge, lanes, nodes, allocator, routing_context)


def route_batch_error(batch: routing.BatchRouteResult) -> contracts.DiagramError:
    """Preserve structured routing evidence at the public build/patch boundary."""
    failure = batch.failure
    evidence = dict(failure.evidence) if failure is not None else {}
    evidence["batch_replays"] = batch.batch_replays
    evidence["component_replans"] = {
        str(key): value for key, value in batch.component_replans.items()
    }
    if failure is not None and failure.component_key is not None:
        evidence.setdefault("component", failure.component_key)
    if failure is not None and failure.assignment_key is not None:
        evidence.setdefault("assignment", failure.assignment_key)
    return contracts.DiagramError(
        failure.message if failure is not None else "Route batch failed",
        code=failure.code if failure is not None else "routing/batch-failed",
        subject=(
            {"kind": "edge", "id": failure.edge_id}
            if failure is not None and failure.edge_id
            else None
        ),
        evidence=evidence,
        supported_fixes=(
            list(failure.supported_fixes)
            if failure is not None
            else ["report-bug"]
        ),
    )


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
            contracts.DATA_KIND: "pool", contracts.DATA_SEMANTIC_ID: "main", contracts.DATA_TITLE_HEIGHT: contracts.number(values["title_height"]),
            contracts.DATA_LANE_HEADER_HEIGHT: contracts.number(values["lane_header_height"]), contracts.DATA_ROW_GAP: contracts.number(values["row_gap"]),
            contracts.DATA_TOP_PADDING: contracts.number(values["top_padding"]), contracts.DATA_BOTTOM_PADDING: contracts.number(values["bottom_padding"]),
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
        pool.attrib[contracts.DATA_PHASE_RAIL_WIDTH] = contracts.number(phase_rail_width)
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
    explicit_by_edge = {edge["id"]: set(edge) for edge in spec["edges"]}
    routing_context = routing.new_routing_context(
        spec.get("main_path", []),
        compiled_edges,
        document.routing_node_views(nodes),
        v3_semantics=schema_version == contracts.V3_SCHEMA_VERSION,
    )
    routing_context["arrowhead_clearance_profiles"] = (
        routing_adapter.arrowhead_clearance_profiles(compiled_edges, lanes, nodes)
    )
    batch = routing.plan_route_batch(
        compiled_edges, document.routing_lane_views(lanes), document.routing_node_views(nodes),
        main_path=spec.get("main_path", []), mutable_edge_ids={edge["id"] for edge in compiled_edges},
        routing_context=routing_context, v3_semantics=schema_version == contracts.V3_SCHEMA_VERSION,
    )
    if batch.status != routing.ROUTE_COMPLETE:
        raise route_batch_error(batch)
    decisions = {decision.edge_id: decision for decision in batch.decisions}
    for edge in routing.edge_routing_order(compiled_edges, spec.get("main_path", []), document.routing_node_views(nodes)):
        create_edge_cell(root, pool, edge, lanes, nodes, routing_context=routing_context,
                         decision=decisions[edge["id"]], explicit_fields=explicit_by_edge[edge["id"]])
    routing_adapter.reflow_automatic_edge_labels(
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
            key: contracts.number(value)
            for key, value in phase_geometry_values(
                current,
                values,
                pool_width,
                presentation=presentation,
                rail_width=rail_width,
            ).items()
        }
    )


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
        geom.attrib["x"] = contracts.number(cursor)
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
    pool_geom.attrib["width"] = contracts.number(cursor)
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
    order = document.read_lane_order(pool, root, lanes)
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
            geom.attrib["width"] = contracts.number(update["width"])
            lanes[lane_id]["geometry"] = document.parse_geometry(cell)

    shifts = reflow_lane_order_geometry(pool, order, lanes, previous)
    return order, lanes, shifts


def current_groups_for_patch(pool: ET.Element) -> list[dict]:
    return copy.deepcopy(document.json_attribute(pool, contracts.DATA_GROUPS, list, []))


def apply_group_operations(
    pool: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
    changes: dict,
) -> tuple[list[str], list[str], list[str]]:
    groups = current_groups_for_patch(pool)
    by_id = {group.get("id"): group for group in groups}
    if len(by_id) != len(groups) or None in by_id:
        raise document.managed_metadata_error(
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
    width, _ = sizing.node_size(node)
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

    deleted_main_path_nodes = deleted_node_ids.intersection(document.read_main_path(pool))
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
            if kind in sizing.FIXED_ASPECT_NODE_TYPES:
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
                    geom.attrib[key] = contracts.number(geometry_update[key])
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
            width, _ = sizing.node_size(node)
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
                lane_geom.attrib["width"] = contracts.number(math.ceil(required_width))
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
            and not routing_adapter.edge_route_is_locally_valid(cell, lanes, nodes)
        )
    }
    reroute_ids = explicit_reroute_ids | auto_reroute_ids
    update_by_id = {update["id"]: update for update in edge_updates}
    effective_main_path = list(changes.get("main_path", document.read_main_path(pool)))
    updated_specs: dict[str, dict] = {}
    for semantic_id, cell in existing_edges.items():
        edge = routing_adapter.existing_edge_spec(cell, for_reroute=semantic_id in reroute_ids)
        edge.update(
            {
                key: value
                for key, value in update_by_id.get(semantic_id, {}).items()
                if key != "reroute"
            }
        )
        updated_specs[semantic_id] = edge

    new_edges = changes.get("edges", [])
    routing_context = routing.new_routing_context(
        effective_main_path,
        [*updated_specs.values(), *new_edges],
        document.routing_node_views(nodes),
        v3_semantics=pool.attrib.get(contracts.DATA_SCHEMA_VERSION) == contracts.V3_SCHEMA_VERSION,
    )
    routing_context["arrowhead_clearance_profiles"] = (
        routing_adapter.arrowhead_clearance_profiles(
            [*updated_specs.values(), *new_edges], lanes, nodes,
            existing_edges=existing_edges,
        )
    )
    routing_adapter.seed_routing_context(
        routing_context,
        existing_edges,
        lanes,
        nodes,
        exclude=reroute_ids,
    )
    for edge in new_edges:
        if edge["id"] in existing_edges:
            raise contracts.DiagramError(f"Edge already exists: {edge['id']}")
    mutable_edge_ids = set(reroute_ids) | {edge["id"] for edge in new_edges}
    all_specs = [*updated_specs.values(), *new_edges]
    batch = routing.plan_route_batch(
        all_specs, document.routing_lane_views(lanes), document.routing_node_views(nodes),
        main_path=effective_main_path, mutable_edge_ids=mutable_edge_ids,
        routing_context=routing_context,
        v3_semantics=pool.attrib.get(contracts.DATA_SCHEMA_VERSION) == contracts.V3_SCHEMA_VERSION,
    )
    if batch.status != routing.ROUTE_COMPLETE:
        raise route_batch_error(batch)
    decisions = {decision.edge_id: decision for decision in batch.decisions}
    # No edge XML has been changed before this point.  Existing records retain
    # their unrelated style tokens/geometry children; additions are appended.
    for edge_id in sorted(reroute_ids):
        edge = updated_specs[edge_id]
        update = update_by_id.get(edge_id, {})
        existing_explicit = {
            field for field, marker in (
                ("waypoints", contracts.DATA_WAYPOINTS_ORIGIN),
                ("exit_side", contracts.DATA_EXIT_SIDE_EXPLICIT),
                ("entry_side", contracts.DATA_ENTRY_SIDE_EXPLICIT),
                ("exit_offset", contracts.DATA_EXIT_OFFSET_EXPLICIT),
                ("entry_offset", contracts.DATA_ENTRY_OFFSET_EXPLICIT),
            ) if existing_edges[edge_id].attrib.get(marker) in {"explicit", "1"}
        }
        explicit = existing_explicit | set(update)
        points_action = (
            "replace_explicit" if "waypoints" in update
            else "preserve_existing" if "waypoints" in existing_explicit
            else "replace_automatic"
        )
        routing_adapter.apply_route_decision(
            existing_edges[edge_id], edge, decisions[edge_id], lanes, nodes,
            existing=True, explicit_fields=explicit, points_action=points_action,
            routing_context=routing_context,
        )
    for edge in routing.edge_routing_order(new_edges, effective_main_path, document.routing_node_views(nodes)):
        create_edge_cell(
            root, pool, edge, lanes, nodes, routing_context=routing_context,
            decision=decisions[edge["id"]], explicit_fields=set(edge),
        )
    routing_adapter.reflow_mutable_edge_labels(
        root, pool, lanes, nodes, mutable_edge_ids,
        routing_context.get("label_sides", {}),
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
    elif deleted_node_ids.intersection(document.read_main_path(pool)):
        raise contracts.DiagramError(
            "Deleting a main_path node requires supplying the replacement main_path",
            code="patch/main-path",
            evidence={"deleted_nodes": sorted(deleted_node_ids.intersection(document.read_main_path(pool)))},
            supported_fixes=["supply-main-path"],
        )

    requested_max_rank = max(
        [int(pool.attrib.get(contracts.DATA_MAX_RANK, "1"))]
        + [int(node["rank"]) for node in new_nodes]
    )
    if requested_max_rank > int(pool.attrib.get(contracts.DATA_MAX_RANK, "1")):
        new_lane_height = lane_height(requested_max_rank, values)
        for lane in lanes.values():
            lane["cell"].find("mxGeometry").attrib["height"] = contracts.number(new_lane_height)
        pool_geom = pool.find("mxGeometry")
        assert pool_geom is not None
        pool_geom.attrib["height"] = contracts.number(values["title_height"] + new_lane_height)
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
        edge = routing_adapter.existing_edge_spec(cell)
        points = document.edge_waypoints(cell)
        if points:
            edge["waypoints"] = [{"x": x, "y": y} for x, y in points]
        edge_specs.append(edge)

    phase_specs = [phase_cell_spec(cell) for _, cell in sorted(phases.items())]
    validation = core_validation.validate_tree(tree)
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
        "main_path": document.read_main_path(pool),
        "lanes": lane_specs,
        "phases": phase_specs,
        "nodes": node_specs,
        "edges": edge_specs,
        "unmanaged_edges": document.unmanaged_edge_specs(root, nodes),
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
    result = core_validation.validate_tree(tree)
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
    input_validation = core_validation.validate_tree(tree)
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
    result = core_validation.validate_tree(tree)
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
    result = core_validation.validate_tree(document.read_tree(args.input))
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
