"""Validate build and patch input without reading XML or CLI state."""

from __future__ import annotations

import re

from . import (
    contracts, geometry as core_geometry, layout, ports, routing, routing_policy, sizing,
)


EDGE_TYPES = {"flow", "call", "return", "retry", "async"}
ROUTING_FIELDS = {
    "from", "to", "type", "route", "branch", "exit_side", "entry_side",
    "exit_offset", "entry_offset", "waypoints", "allow_port_reuse", "reroute",
}
SLOT_CLASSES = {"left", "main", "right"}
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
    if node["type"] not in contracts.NODE_TYPES:
        raise contracts.DiagramError(
            f"Unsupported node type: {node['type']}",
            code="schema/enum",
            subject={"kind": "node", "id": node.get("id")},
            evidence={"field": "type", "allowed": sorted(contracts.NODE_TYPES)},
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
            layout_spec = contracts.require_mapping(spec["layout"], "spec.layout")
            contracts.reject_unknown_fields(layout_spec, LAYOUT_FIELDS, "spec.layout")
            if "profile" in layout_spec:
                profile = contracts.require_string(layout_spec["profile"], "spec.layout.profile")
                if profile not in LAYOUT_PROFILES:
                    raise contracts.DiagramError(
                        f"Unsupported layout profile: {profile}",
                        code="schema/enum",
                        subject={"kind": "layout"},
                        evidence={"field": "profile", "allowed": sorted(LAYOUT_PROFILES)},
                    )
            if "phase_presentation" in layout_spec:
                presentation = contracts.require_string(
                    layout_spec["phase_presentation"],
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
            slot = layout.effective_node_slot(node)
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
        if "type" in update and update["type"] not in contracts.NODE_TYPES:
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
