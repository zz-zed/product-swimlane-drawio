"""Compile a validated specification into a new native Draw.io tree."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from . import (
    construction, contracts, document, layout, metadata, routing, routing_adapter,
    spec_validation,
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
        target_slot = layout.effective_node_slot(target)
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
    schema_version = spec_validation.validate_build_spec(spec)

    values = layout.adaptive_canvas_values(spec)
    lane_widths = layout.effective_lane_widths(spec)
    node_x_positions = layout.v3_node_x_positions(spec, lane_widths)
    phase_presentation = (
        spec.get("layout", {}).get("phase_presentation", "bands")
        if schema_version == contracts.V3_SCHEMA_VERSION
        else "bands"
    )
    phase_rail_width = (
        layout.PHASE_RAIL_WIDTH
        if phase_presentation == "rail" and spec.get("phases")
        else 0.0
    )
    max_rank = max((int(node["rank"]) for node in spec["nodes"]), default=1)
    current_lane_height = layout.lane_height(max_rank, values)
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
        lane_cell = construction.create_lane_cell(
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
        construction.create_phase_cell(root, pool, phase, values, pool_width)

    group_by_node = {
        node_id: group["id"]
        for group in spec.get("groups", [])
        for node_id in group["nodes"]
    }
    for node in spec["nodes"]:
        lane_cell = lane_cells[node["lane"]]
        lane_width = document.parse_geometry(lane_cell)["width"]
        construction.create_node_cell(
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
    routing_context["native_label_profiles"] = routing_adapter.native_label_profiles(compiled_edges, pool, lanes, nodes)
    batch = routing.plan_route_batch(
        compiled_edges, document.routing_lane_views(lanes), document.routing_node_views(nodes),
        main_path=spec.get("main_path", []), mutable_edge_ids={edge["id"] for edge in compiled_edges},
        routing_context=routing_context, v3_semantics=schema_version == contracts.V3_SCHEMA_VERSION,
    )
    if batch.status != routing.ROUTE_COMPLETE:
        raise routing_adapter.route_batch_error(batch)
    decisions = {decision.edge_id: decision for decision in batch.decisions}
    for edge in routing.edge_routing_order(compiled_edges, spec.get("main_path", []), document.routing_node_views(nodes)):
        construction.create_edge_cell(root, pool, edge, lanes, nodes, routing_context=routing_context,
                                     decision=decisions[edge["id"]], explicit_fields=explicit_by_edge[edge["id"]])
    routing_adapter.apply_label_plan(document.edge_records(root), batch.decisions, batch.label_choices)
    unplanned_labels = {edge["id"] for edge in compiled_edges} - set(batch.label_choices)
    if unplanned_labels:
        routing_adapter.reflow_mutable_edge_labels(root, pool, lanes, nodes, unplanned_labels,
                                                   routing_context.get("label_sides", {}))
    construction.normalize_phase_layering(root, pool)
    tree = ET.ElementTree(mxfile)
    metadata.refresh_managed_metadata(tree)
    return tree
