#!/usr/bin/env python3
"""Extract anonymous layout metrics from uncompressed Draw.io references."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path


GEOMETRY_TAG = "mxGeometry"
ROW_TOLERANCE = 28.0


def number(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def style_values(style: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in style.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            values[key] = value
        elif item:
            values[item] = "1"
    return values


def geometry(cell: ET.Element) -> dict[str, float]:
    item = cell.find(GEOMETRY_TAG)
    if item is None:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    return {
        "x": number(item.attrib.get("x")),
        "y": number(item.attrib.get("y")),
        "width": number(item.attrib.get("width")),
        "height": number(item.attrib.get("height")),
    }


def absolute_geometry(
    cell: ET.Element,
    cells: dict[str, ET.Element],
    cache: dict[str, dict[str, float]],
) -> dict[str, float]:
    cell_id = cell.attrib.get("id", "")
    if cell_id in cache:
        return cache[cell_id]
    current = geometry(cell)
    parent = cells.get(cell.attrib.get("parent", ""))
    if parent is not None and parent.attrib.get("vertex") == "1":
        parent_geometry = absolute_geometry(parent, cells, cache)
        current = {
            **current,
            "x": current["x"] + parent_geometry["x"],
            "y": current["y"] + parent_geometry["y"],
        }
    cache[cell_id] = current
    return current


def cluster_rows(nodes: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for node in sorted(nodes, key=lambda item: item["center_y"]):
        if not rows:
            rows.append([node])
            continue
        row_center = statistics.mean(item["center_y"] for item in rows[-1])
        if abs(node["center_y"] - row_center) <= ROW_TOLERANCE:
            rows[-1].append(node)
        else:
            rows.append([node])
    return rows


def edge_waypoint_count(cell: ET.Element) -> int:
    geometry_item = cell.find(GEOMETRY_TAG)
    if geometry_item is None:
        return 0
    array = geometry_item.find("mxArray[@as='points']")
    return len(array.findall("mxPoint")) if array is not None else 0


def analyze_page(model: ET.Element) -> dict:
    root = model.find("root")
    if root is None:
        raise ValueError("mxGraphModel has no root")
    cells = {
        cell.attrib["id"]: cell
        for cell in root.findall("mxCell")
        if cell.attrib.get("id")
    }
    cache: dict[str, dict[str, float]] = {}
    vertices = [cell for cell in cells.values() if cell.attrib.get("vertex") == "1"]
    edges = [cell for cell in cells.values() if cell.attrib.get("edge") == "1"]
    swimlanes = [cell for cell in vertices if "swimlane" in style_values(cell.attrib.get("style", ""))]

    lane_ids = {
        cell.attrib.get("id")
        for cell in swimlanes
        if cell.attrib.get("parent") in {candidate.attrib.get("id") for candidate in swimlanes}
    }
    if not lane_ids:
        lane_ids = {
            cell.attrib.get("id")
            for cell in swimlanes
            if geometry(cell)["height"] >= geometry(cell)["width"]
        }
    lanes = [cells[cell_id] for cell_id in lane_ids if cell_id in cells]
    lane_geometry = {
        lane.attrib["id"]: absolute_geometry(lane, cells, cache)
        for lane in lanes
    }

    structural_ids = {cell.attrib.get("id") for cell in swimlanes}
    nodes: list[dict] = []
    for cell in vertices:
        if cell.attrib.get("id") in structural_ids:
            continue
        current = absolute_geometry(cell, cells, cache)
        if current["width"] <= 0 or current["height"] <= 0:
            continue
        parent_id = cell.attrib.get("parent")
        lane_id = parent_id if parent_id in lane_ids else None
        if lane_id is None:
            center_x = current["x"] + current["width"] / 2.0
            center_y = current["y"] + current["height"] / 2.0
            containing = [
                candidate_id
                for candidate_id, bounds in lane_geometry.items()
                if bounds["x"] <= center_x <= bounds["x"] + bounds["width"]
                and bounds["y"] <= center_y <= bounds["y"] + bounds["height"]
            ]
            lane_id = containing[0] if len(containing) == 1 else None
        nodes.append(
            {
                "id": cell.attrib.get("id"),
                "lane": lane_id,
                "x": current["x"],
                "y": current["y"],
                "width": current["width"],
                "height": current["height"],
                "center_x": current["x"] + current["width"] / 2.0,
                "center_y": current["y"] + current["height"] / 2.0,
            }
        )

    rows = cluster_rows(nodes)
    row_centers = [statistics.mean(item["center_y"] for item in row) for row in rows]
    rank_gaps = [right - left for left, right in zip(row_centers, row_centers[1:])]
    parallel_rows = [row for row in rows if len(row) > 1]
    lane_parallel_rows = 0
    slot_gaps: list[float] = []
    for row in rows:
        by_lane: dict[str, list[dict]] = {}
        for node in row:
            if node["lane"]:
                by_lane.setdefault(node["lane"], []).append(node)
        for lane_nodes in by_lane.values():
            if len(lane_nodes) <= 1:
                continue
            lane_parallel_rows += 1
            ordered = sorted(lane_nodes, key=lambda item: item["x"])
            slot_gaps.extend(
                max(0.0, right["x"] - (left["x"] + left["width"]))
                for left, right in zip(ordered, ordered[1:])
            )

    edge_points = [edge_waypoint_count(edge) for edge in edges]
    backward_edges = 0
    same_rank_edges = 0
    node_by_id = {node["id"]: node for node in nodes}
    for edge in edges:
        source = node_by_id.get(edge.attrib.get("source"))
        target = node_by_id.get(edge.attrib.get("target"))
        if not source or not target:
            continue
        delta = target["center_y"] - source["center_y"]
        if delta < -ROW_TOLERANCE:
            backward_edges += 1
        elif abs(delta) <= ROW_TOLERANCE:
            same_rank_edges += 1

    orthogonal_edges = sum(
        style_values(edge.attrib.get("style", "")).get("edgeStyle")
        in {"orthogonalEdgeStyle", "elbowEdgeStyle"}
        or style_values(edge.attrib.get("style", "")).get("orthogonalLoop") == "1"
        for edge in edges
    )
    labeled_edges = sum(bool(str(edge.attrib.get("value", "")).strip()) for edge in edges)
    lane_widths = [bounds["width"] for bounds in lane_geometry.values() if bounds["width"] > 0]
    node_widths = [node["width"] for node in nodes]
    node_heights = [node["height"] for node in nodes]

    return {
        "lanes": len(lanes),
        "nodes": len(nodes),
        "edges": len(edges),
        "rank_bands": len(rows),
        "parallel_rank_bands": len(parallel_rows),
        "lane_local_parallel_bands": lane_parallel_rows,
        "max_nodes_per_rank": max((len(row) for row in rows), default=0),
        "median_rank_gap": round(statistics.median(rank_gaps), 2) if rank_gaps else None,
        "minimum_rank_gap": round(min(rank_gaps), 2) if rank_gaps else None,
        "backward_edges": backward_edges,
        "same_rank_edges": same_rank_edges,
        "labeled_edges": labeled_edges,
        "orthogonal_edge_ratio": round(orthogonal_edges / len(edges), 4) if edges else 1.0,
        "explicit_waypoint_edges": sum(count > 0 for count in edge_points),
        "mean_waypoints_per_edge": round(statistics.mean(edge_points), 3) if edge_points else 0.0,
        "median_lane_width": round(statistics.median(lane_widths), 2) if lane_widths else None,
        "median_node_width": round(statistics.median(node_widths), 2) if node_widths else None,
        "median_node_height": round(statistics.median(node_heights), 2) if node_heights else None,
        "minimum_lane_local_slot_gap": round(min(slot_gaps), 2) if slot_gaps else None,
    }


def analyze_file(path: Path) -> dict:
    tree = ET.parse(path)
    pages = []
    for diagram in tree.getroot().findall("diagram"):
        model = diagram.find("mxGraphModel")
        if model is None:
            raise ValueError(f"compressed Draw.io pages are not supported: {path.name}")
        pages.append(analyze_page(model))
    return {"file": path.name, "pages": pages}


def aggregate(files: list[dict]) -> dict:
    pages = [page for item in files for page in item["pages"]]
    return {
        "files": len(files),
        "pages": len(pages),
        "lanes": sum(page["lanes"] for page in pages),
        "nodes": sum(page["nodes"] for page in pages),
        "edges": sum(page["edges"] for page in pages),
        "pages_with_parallel_bands": sum(page["parallel_rank_bands"] > 0 for page in pages),
        "pages_with_lane_local_parallelism": sum(page["lane_local_parallel_bands"] > 0 for page in pages),
        "backward_edges": sum(page["backward_edges"] for page in pages),
        "same_rank_edges": sum(page["same_rank_edges"] for page in pages),
        "labeled_edges": sum(page["labeled_edges"] for page in pages),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    files = [analyze_file(path) for path in args.inputs]
    report = {"aggregate": aggregate(files), "references": files}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
