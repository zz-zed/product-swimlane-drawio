#!/usr/bin/env python3
"""Build, patch, validate, and compare editable Draw.io swimlane diagrams."""

from __future__ import annotations

import argparse
import json
import re
import sys
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
ROUTING_FIELDS = {
    "from", "to", "type", "route", "branch", "exit_side", "entry_side",
    "exit_offset", "entry_offset", "waypoints", "allow_port_reuse", "reroute",
}
ROUTE_CLEARANCE = 24.0
GEOMETRY_TOLERANCE = 0.75


class DiagramError(ValueError):
    pass


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


def automatic_waypoints(
    route_class: str,
    source_bounds: dict[str, float],
    target_bounds: dict[str, float],
    source_point: tuple[float, float],
    target_point: tuple[float, float],
    exit_side: str,
    entry_side: str,
    pool_width: float,
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
            return compact_points([(escape_x, sy), (escape_x, corridor_y), (tx, corridor_y)])
        return compact_points([(sx, corridor_y), (tx, corridor_y)])

    if route_class == "back":
        if exit_side == entry_side == "left":
            route_x = max(8.0, min(source_bounds["left"], target_bounds["left"]) - ROUTE_CLEARANCE)
            return compact_points([(route_x, sy), (route_x, ty)])
        if exit_side == entry_side == "right":
            route_x = min(
                pool_width - 8.0,
                max(source_bounds["right"], target_bounds["right"]) + ROUTE_CLEARANCE,
            )
            return compact_points([(route_x, sy), (route_x, ty)])
        corridor_y = (sy + ty) / 2
        return compact_points([(sx, corridor_y), (tx, corridor_y)])

    if abs(sy - ty) < GEOMETRY_TOLERANCE:
        return []
    if exit_side == "right":
        route_x = min(source_bounds["right"] + ROUTE_CLEARANCE, pool_width - 8.0)
    elif exit_side == "left":
        route_x = max(source_bounds["left"] - ROUTE_CLEARANCE, 8.0)
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
    for field in ("title", "lanes", "nodes", "edges"):
        if field not in spec:
            raise DiagramError(f"Missing required field: {field}")
    require_unique(spec["lanes"], "lane")
    require_unique(spec["nodes"], "node")
    require_unique(spec["edges"], "edge")
    lane_ids = {lane["id"] for lane in spec["lanes"]}
    for node in spec["nodes"]:
        if node.get("lane") not in lane_ids:
            raise DiagramError(f"Node {node.get('id')} references an unknown lane")
        if int(node.get("rank", 0)) < 1:
            raise DiagramError(f"Node {node.get('id')} must have rank >= 1")

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
    return spec


def patch_tree(tree: ET.ElementTree, changes: dict, allow_geometry_updates: bool) -> ET.ElementTree:
    pool = find_pool(tree)
    root = graph_root(tree)
    values = values_from_pool(pool)
    lanes, nodes = lane_node_records(root, pool)

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
            geom = cell.find("mxGeometry")
            assert geom is not None
            for key in ("x", "y", "width", "height"):
                if key in update:
                    geom.attrib[key] = number(update[key])
            nodes[semantic_id]["geometry"] = parse_geometry(cell)

    new_nodes = changes.get("nodes", [])
    require_unique(new_nodes, "new node")
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
    require_unique(edge_updates, "edge update")
    reroute_ids = {
        update["id"]
        for update in edge_updates
        if update.get("reroute") or any(
            key in update for key in ROUTING_FIELDS if key != "reroute"
        )
    }
    allocator = PortAllocator()
    reserve_existing_ports(root, allocator, exclude=reroute_ids)

    for update in edge_updates:
        semantic_id = update.get("id")
        if semantic_id not in existing_edges:
            raise DiagramError(f"Cannot update missing edge: {semantic_id}")
        cell = existing_edges[semantic_id]
        if "label" in update:
            cell.attrib["value"] = str(update["label"])
        if semantic_id in reroute_ids:
            edge = existing_edge_spec(cell)
            edge.update({key: value for key, value in update.items() if key != "reroute"})
            apply_edge_route(cell, edge, lanes, nodes, allocator)

    new_edges = changes.get("edges", [])
    require_unique(new_edges, "new edge")
    for edge in new_edges:
        if edge["id"] in existing_edges:
            raise DiagramError(f"Edge already exists: {edge['id']}")
        create_edge_cell(root, pool, edge, lanes, nodes, allocator)

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

    return tree


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


def validate_tree(tree: ET.ElementTree) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        pool = find_pool(tree)
        root = graph_root(tree)
        lanes, nodes = lane_node_records(root, pool)
    except DiagramError as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": []}

    semantic_ids: set[str] = set()
    for cell in root.iter("mxCell"):
        semantic_id = cell.attrib.get("data-semantic-id")
        kind = cell.attrib.get("data-kind")
        if semantic_id and kind:
            composite = f"{kind}:{semantic_id}"
            if composite in semantic_ids:
                errors.append(f"Duplicate semantic cell: {composite}")
            semantic_ids.add(composite)

    cell_ids = {cell.attrib.get("id") for cell in tree.iter("mxCell")}
    edge_cells = [
        cell for cell in root.iter("mxCell") if cell.attrib.get("data-kind") == "edge"
    ]
    for cell in edge_cells:
        if cell.attrib.get("source") not in cell_ids or cell.attrib.get("target") not in cell_ids:
            errors.append(f"Broken edge endpoints: {cell.attrib.get('data-semantic-id')}")

    for semantic_id, record in nodes.items():
        lane = lanes[record["lane"]]
        node_geom = record["geometry"]
        lane_geom = lane["geometry"]
        if node_geom["x"] < 0 or node_geom["x"] + node_geom["width"] > lane_geom["width"]:
            warnings.append(f"Node outside lane horizontally: {semantic_id}")
        if node_geom["y"] < 0 or node_geom["y"] + node_geom["height"] > lane_geom["height"]:
            warnings.append(f"Node outside lane vertically: {semantic_id}")

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
            warnings.append(
                f"Port reused at node {node_id} ({side}@{number(offset)}): {', '.join(sorted(used_by))}"
            )

    internal_boundaries = sorted(
        {
            round(record["geometry"]["x"] + record["geometry"]["width"], 4)
            for index, record in enumerate(lanes.values())
            if index < len(lanes) - 1
        }
    )
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
                warnings.append(f"Non-orthogonal connector segment: {edge_id}")
                continue
            if axis == "vertical":
                x = segment[0][0]
                if any(abs(x - boundary) < GEOMETRY_TOLERANCE for boundary in internal_boundaries):
                    warnings.append(f"Connector overlaps a lane boundary: {edge_id}")
            for node_id, bounds in node_bounds.items():
                if node_id in {cell.attrib.get("data-from"), cell.attrib.get("data-to")}:
                    continue
                if segment_crosses_bounds(segment, bounds):
                    warnings.append(f"Connector crosses node: {edge_id} -> {node_id}")

    edge_ids = sorted(edge_segments)
    for index, first_id in enumerate(edge_ids):
        for second_id in edge_ids[index + 1 :]:
            if any(
                segments_conflict(first_segment, second_segment)
                for first_segment in edge_segments[first_id]
                for second_segment in edge_segments[second_id]
            ):
                warnings.append(f"Connector segments cross or overlap: {first_id} / {second_id}")

    warnings = sorted(set(warnings))

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
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
    if changes.get("nodes"):
        before_pool = find_pool(before)
        after_pool = find_pool(after)
        if before_pool.attrib.get("data-max-rank") != after_pool.attrib.get("data-max-rank"):
            allowed.add("pool:main")
            after_root = graph_root(after)
            after_lanes, _ = lane_node_records(after_root, after_pool)
            allowed.update(f"lane:{lane_id}" for lane_id in after_lanes)
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
    unexpected_geometry = sorted(set(changed_geometry) - allowed)
    unexpected_attributes = sorted(set(changed_attributes) - allowed)
    preserved = not missing and not unexpected_geometry and not unexpected_attributes
    return {
        "preserved": preserved,
        "existing_cells_checked": len(set(before_cells) & set(after_cells)),
        "added_cells": added,
        "missing_cells": missing,
        "changed_geometry": changed_geometry,
        "changed_attributes": changed_attributes,
        "allowed_changes": sorted(allowed),
        "unexpected_geometry": unexpected_geometry,
        "unexpected_attributes": unexpected_attributes,
    }


def write_tree(tree: ET.ElementTree, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=False, short_empty_elements=True)


def ensure_different(input_path: Path, output_path: Path) -> None:
    if input_path.resolve() == output_path.resolve():
        raise DiagramError("Input and output must differ; review the new file before replacing the original")


def command_build(args: argparse.Namespace) -> None:
    spec = load_json(args.spec)
    tree = build_tree(spec)
    write_tree(tree, args.output)
    print(json.dumps(validate_tree(tree), ensure_ascii=False, indent=2))


def command_patch(args: argparse.Namespace) -> None:
    ensure_different(args.input, args.output)
    tree = ET.parse(args.input)
    patch_tree(tree, load_json(args.changes), args.allow_geometry_updates)
    write_tree(tree, args.output)
    print(json.dumps(validate_tree(tree), ensure_ascii=False, indent=2))


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a new editable Draw.io file")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.set_defaults(func=command_build)

    patch = subparsers.add_parser("patch", help="Incrementally patch an existing generated Draw.io file")
    patch.add_argument("--input", type=Path, required=True)
    patch.add_argument("--changes", type=Path, required=True)
    patch.add_argument("--output", type=Path, required=True)
    patch.add_argument("--allow-geometry-updates", action="store_true")
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
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except (DiagramError, json.JSONDecodeError, ET.ParseError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
