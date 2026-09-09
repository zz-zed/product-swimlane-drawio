"""Create and update native Draw.io cells without build or patch orchestration."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from . import (
    contracts, document, layout, ports, routing, routing_adapter, sizing,
    spec_validation,
)


NODE_STYLES = {
    "start": "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#ffffff;strokeColor=#333333;strokeWidth=1.5;",
    "end": "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#333333;strokeColor=#333333;strokeWidth=1.5;",
    "process": "rounded=0;whiteSpace=wrap;html=1;fillColor=#f5f5f5;fontColor=#333333;strokeColor=#666666;fontSize=12;",
    "decision": "rhombus;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontColor=#333333;fontSize=12;",
    "note": "shape=note;whiteSpace=wrap;html=1;fillColor=#fff2cc;strokeColor=#d6b656;fontColor=#333333;fontSize=11;",
}


def clean_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    if not cleaned:
        raise contracts.DiagramError(f"Invalid empty semantic id derived from {value!r}")
    return cleaned


def mx_id(kind: str, semantic_id: str) -> str:
    return f"psd-{kind}-{clean_id(semantic_id)}"


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
    y = layout.node_y(node, values)
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
        cell.attrib[contracts.DATA_SLOT] = layout.effective_node_slot(node)
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
    rail_width: float = layout.PHASE_RAIL_WIDTH,
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
    rail_width = float(pool.attrib.get(contracts.DATA_PHASE_RAIL_WIDTH, layout.PHASE_RAIL_WIDTH))
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
    spec_validation.validate_phase_object(current, f"phase[{current['id']}]")
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
        else layout.PHASE_RAIL_WIDTH
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
