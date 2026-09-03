"""Read-only diagnostic collectors and quality summaries for native diagrams."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from . import (
    contracts, document, geometry as core_geometry, labels, metadata,
    routing, routing_policy, sizing,
)


EXCESSIVE_HEIGHT_TOLERANCE = 8.0


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
    candidates = labels.label_box_candidates(points, cell.attrib.get("value", ""))
    if not candidates:
        return None
    segment_index, box, _ = candidates[0]
    return segment_index, box


def _collect_schema_integrity(pool, root, schema_version, add):
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


def _collect_unmanaged_vertices(root, add):
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


def _collect_parent_endpoint_integrity(root, lanes, nodes, add):
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


def _collect_managed_hash_integrity(tree, pool, diagnostics, add):
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
    return integrity


def _collect_duplicate_semantic_ids(root, add):
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


def _collect_unmanaged_edges(unmanaged_edges, add):
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


def _collect_phase_structure(root, pool, lanes, add):
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


def _collect_broken_endpoints(edge_cells, cell_ids, add):
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


def _collect_node_geometry(nodes, lanes, schema_version, add):
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
                node_type in sizing.FIXED_ASPECT_NODE_TYPES
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
            required_lines = sizing.estimated_text_lines(
                label,
                node_geom["width"],
                diamond=node_type == "decision",
            )
            if node_type == "process":
                available_lines = max(
                    1,
                    int(max(0.0, node_geom["height"] - sizing.PROCESS_VERTICAL_PADDING) / sizing.PROCESS_TEXT_LINE_HEIGHT),
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
                recommended_height = sizing.recommended_process_height(label, node_geom["width"])
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


def _collect_port_reuse(edge_cells, add):
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
                f"Port reused at node {node_id} ({side}@{contracts.number(offset)}): {', '.join(sorted(used_by))}",
                subject={"kind": "node", "id": node_id},
                evidence={"side": side, "offset": offset, "edges": sorted(used_by)},
                supported_fixes=["allocate-distinct-port"],
            )


def _collect_node_overlaps(node_bounds, add):
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


def _collect_edge_path_shape(
    cell,
    edge_id,
    points,
    segments,
    lanes,
    nodes,
    node_bounds,
    internal_boundaries,
    add,
):
    short_segments = [
        (index, core_geometry.segment_length(segment))
        for index, segment in enumerate(segments[1:-1], start=1)
        if core_geometry.segment_length(segment) < routing_policy.MIN_INTERNAL_SEGMENT - core_geometry.GEOMETRY_TOLERANCE
    ]
    if short_segments:
        waypoint_origin = cell.attrib.get(contracts.DATA_WAYPOINTS_ORIGIN, "unknown")
        add(
            "routing/short-segment",
            "warning",
            f"Connector contains an internal segment shorter than {contracts.number(routing_policy.MIN_INTERNAL_SEGMENT)} px: {edge_id}",
            subject={"kind": "edge", "id": edge_id},
            evidence={
                "segments": [
                    {"index": index, "length": length}
                    for index, length in short_segments
                ],
                "minimum": routing_policy.MIN_INTERNAL_SEGMENT,
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
            simple_candidates = routing.route_candidates(
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
                and not routing.path_has_hairpin(candidate)
                and all(
                    core_geometry.segment_length(segment)
                    >= routing_policy.MIN_INTERNAL_SEGMENT - core_geometry.GEOMETRY_TOLERANCE
                    for segment in list(zip(candidate, candidate[1:]))[1:-1]
                )
                and routing.automatic_polyline_is_safe(
                    candidate,
                    document.routing_lane_views(lanes),
                    document.routing_node_views(nodes),
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
    if routing.path_has_hairpin(points):
        add(
            "routing/hairpin",
            "warning",
            f"Connector contains a short-distance hairpin: {edge_id}",
            subject={"kind": "edge", "id": edge_id},
            supported_fixes=["reroute-edge", "align-ports"],
        )


def _collect_edge_label_quality(
    cell,
    edge_id,
    points,
    node_bounds,
    edge_label_bounds,
    add,
):
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


def _collect_edge_segment_quality(
    cell,
    edge_id,
    segments,
    internal_boundaries,
    node_bounds,
    add,
):
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
                abs(x - boundary) < routing_policy.LANE_BOUNDARY_CLEARANCE
                for boundary in internal_boundaries
            ):
                nearest = min(abs(x - boundary) for boundary in internal_boundaries)
                add(
                    "routing/lane-boundary-clearance",
                    "warning",
                    "Connector is too close to a lane boundary "
                    f"(< {contracts.number(routing_policy.LANE_BOUNDARY_CLEARANCE)} px): {edge_id}",
                    subject={"kind": "edge", "id": edge_id},
                    evidence={"distance": nearest, "minimum": routing_policy.LANE_BOUNDARY_CLEARANCE},
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


def _collect_back_corridor_quality(
    cell,
    edge_id,
    segments,
    lanes,
    nodes,
    node_bounds,
    add,
):
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
            safe_gap = routing_policy.LANE_BOUNDARY_CLEARANCE - core_geometry.GEOMETRY_TOLERANCE
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


def _collect_edge_pair_quality(edge_segments, edge_cells_by_id, add):
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
                routing.segments_near_parallel(first_segment, second_segment)
                for first_segment in edge_segments[first_id]
                for second_segment in edge_segments[second_id]
            )
            if near_parallel:
                add(
                    "routing/near-parallel-conflict",
                    "warning",
                    f"Connector segments run too close in parallel: {first_id} / {second_id}",
                    subject={"kind": "edge", "id": first_id},
                    evidence={"other_edge": second_id, "minimum": routing_policy.NEAR_PARALLEL_CLEARANCE},
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


def _collect_label_path_conflicts(edge_label_bounds, edge_segments, add):
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


def _collect_label_pair_conflicts(edge_label_bounds, add):
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


def _collect_semantic_diagnostics(
    schema_version,
    pool,
    root,
    nodes,
    edge_cells,
    unmanaged_edges,
    edge_points,
    add,
):
    if schema_version in contracts.STRUCTURED_SCHEMA_VERSIONS:
        node_ranks = {
            node_id: int(record["cell"].attrib.get(contracts.DATA_RANK, "0"))
            for node_id, record in nodes.items()
        }
        main_path = document.read_main_path(pool)
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
                and all(branch in routing_policy.BRANCH_CLASSES for branch in branches)
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


def _summarize_validation(
    diagnostics,
    pool,
    schema_version,
    integrity,
    lanes,
    nodes,
    edge_cells,
    unmanaged_edges,
    edge_cells_by_id,
    edge_points,
):
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
        main_pairs = set(zip(document.read_main_path(pool), document.read_main_path(pool)[1:]))
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


def validate_tree(tree: ET.ElementTree) -> dict:
    """Collect read-only diagnostics in the original document traversal order."""
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

    _collect_schema_integrity(pool, root, schema_version, add)
    _collect_unmanaged_vertices(root, add)
    _collect_parent_endpoint_integrity(root, lanes, nodes, add)
    integrity = _collect_managed_hash_integrity(tree, pool, diagnostics, add)
    _collect_duplicate_semantic_ids(root, add)

    cell_ids = {cell.attrib.get("id") for cell in tree.iter("mxCell")}
    edge_cells = [
        cell for cell in root.iter("mxCell") if cell.attrib.get(contracts.DATA_KIND) == "edge"
    ]
    unmanaged_edges = document.unmanaged_edge_specs(root, nodes)
    _collect_unmanaged_edges(unmanaged_edges, add)
    _collect_phase_structure(root, pool, lanes, add)
    _collect_broken_endpoints(edge_cells, cell_ids, add)
    _collect_node_geometry(nodes, lanes, schema_version, add)
    _collect_port_reuse(edge_cells, add)

    internal_boundaries = routing.internal_lane_boundaries(document.routing_lane_views(lanes))
    node_bounds = {
        semantic_id: core_geometry.node_bounds_in_pool(record, lanes[record["lane"]])
        for semantic_id, record in nodes.items()
    }
    _collect_node_overlaps(node_bounds, add)
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
        # Label checks remain between path-shape and segment diagnostics.
        _collect_edge_path_shape(
            cell, edge_id, points, segments, lanes, nodes, node_bounds,
            internal_boundaries, add,
        )
        _collect_edge_label_quality(
            cell, edge_id, points, node_bounds, edge_label_bounds, add,
        )
        _collect_edge_segment_quality(
            cell, edge_id, segments, internal_boundaries, node_bounds, add,
        )
        _collect_back_corridor_quality(
            cell, edge_id, segments, lanes, nodes, node_bounds, add,
        )

    _collect_edge_pair_quality(edge_segments, edge_cells_by_id, add)
    _collect_label_path_conflicts(edge_label_bounds, edge_segments, add)
    _collect_label_pair_conflicts(edge_label_bounds, add)
    _collect_semantic_diagnostics(
        schema_version, pool, root, nodes, edge_cells, unmanaged_edges,
        edge_points, add,
    )
    return _summarize_validation(
        diagnostics, pool, schema_version, integrity, lanes, nodes, edge_cells,
        unmanaged_edges, edge_cells_by_id, edge_points,
    )
