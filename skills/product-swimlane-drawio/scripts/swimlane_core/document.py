"""Native ElementTree adaptation and fidelity-preserving file delivery."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from . import contracts, geometry as core_geometry


def number(value) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def geometry(parent: ET.Element, **attrs) -> ET.Element:
    normalized = {key: number(value) for key, value in attrs.items() if value is not None}
    normalized["as"] = "geometry"
    return ET.SubElement(parent, "mxGeometry", normalized)


def set_style_option(cell: ET.Element, key: str, value: str) -> None:
    """Set one mxGraph style option without disturbing unrelated options."""
    parts = [part for part in cell.attrib.get("style", "").split(";") if part]
    replacement = f"{key}={value}"
    updated: list[str] = []
    replaced = False
    for part in parts:
        if part.split("=", 1)[0] == key:
            if not replaced:
                updated.append(replacement)
                replaced = True
            continue
        updated.append(part)
    if not replaced:
        updated.append(replacement)
    cell.attrib["style"] = ";".join(updated) + ";"


def native_cell(element: ET.Element) -> ET.Element | None:
    """A root entry may be a cell or a native object/UserObject wrapper."""
    return element if element.tag == "mxCell" else element.find("mxCell")


def parse_geometry(cell: ET.Element) -> dict[str, float]:
    geom = cell.find("mxGeometry")
    if geom is None:
        raise contracts.DiagramError(f"Cell {cell.attrib.get('id')} has no geometry")
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
            or child.attrib.get(contracts.DATA_KIND) != "lane"
            or child.attrib.get("parent") != pool.attrib["id"]
        ):
            continue
        semantic_id = child.attrib[contracts.DATA_SEMANTIC_ID]
        lanes[semantic_id] = {"cell": child, "geometry": parse_geometry(child)}
    lane_by_cell_id = {record["cell"].attrib["id"]: semantic_id for semantic_id, record in lanes.items()}
    for child in list(root):
        if child.tag != "mxCell" or child.attrib.get(contracts.DATA_KIND) != "node":
            continue
        lane_semantic_id = lane_by_cell_id.get(child.attrib.get("parent"))
        if lane_semantic_id:
            nodes[child.attrib[contracts.DATA_SEMANTIC_ID]] = {
                "cell": child,
                "geometry": parse_geometry(child),
                "lane": lane_semantic_id,
            }
    return lanes, nodes


def phase_records(root: ET.Element, pool: ET.Element) -> dict[str, ET.Element]:
    return {
        child.attrib[contracts.DATA_SEMANTIC_ID]: child
        for child in list(root)
        if child.tag == "mxCell"
        and child.attrib.get(contracts.DATA_KIND) == "phase"
        and child.attrib.get("parent") == pool.attrib["id"]
        and child.attrib.get(contracts.DATA_SEMANTIC_ID)
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


def graph_root(tree: ET.ElementTree) -> ET.Element:
    root = tree.find("./diagram/mxGraphModel/root")
    if root is None:
        raise contracts.DiagramError("Not a supported uncompressed Draw.io document")
    return root


def find_pool(tree: ET.ElementTree) -> ET.Element:
    root = graph_root(tree)
    for cell in list(root):
        if cell.attrib.get(contracts.DATA_KIND) == "pool":
            return cell
    raise contracts.DiagramError("Diagram is missing compatible swimlane semantic metadata")


def values_from_pool(pool: ET.Element, defaults: dict) -> dict:
    return {
        "title_height": float(pool.attrib.get(contracts.DATA_TITLE_HEIGHT, defaults["title_height"])),
        "lane_header_height": float(pool.attrib.get(contracts.DATA_LANE_HEADER_HEIGHT, defaults["lane_header_height"])),
        "row_gap": float(pool.attrib.get(contracts.DATA_ROW_GAP, defaults["row_gap"])),
        "top_padding": float(pool.attrib.get(contracts.DATA_TOP_PADDING, defaults["top_padding"])),
        "bottom_padding": float(pool.attrib.get(contracts.DATA_BOTTOM_PADDING, defaults["bottom_padding"])),
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
    if abs(y) < core_geometry.GEOMETRY_TOLERANCE / 10:
        return "top", x
    if abs(y - 1.0) < core_geometry.GEOMETRY_TOLERANCE / 10:
        return "bottom", x
    if abs(x) < core_geometry.GEOMETRY_TOLERANCE / 10:
        return "left", y
    if abs(x - 1.0) < core_geometry.GEOMETRY_TOLERANCE / 10:
        return "right", y
    return None


def edge_records(root: ET.Element) -> dict[str, ET.Element]:
    return {
        child.attrib[contracts.DATA_SEMANTIC_ID]: child
        for child in list(root)
        if child.tag == "mxCell"
        and child.attrib.get(contracts.DATA_KIND) == "edge"
        and child.attrib.get(contracts.DATA_SEMANTIC_ID)
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
    source_id = cell.attrib.get(contracts.DATA_FROM)
    target_id = cell.attrib.get(contracts.DATA_TO)
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
    return core_geometry.compact_points(
        [
            core_geometry.port_point(source_bounds, exit_port[0], exit_port[1]),
            *edge_waypoints(cell),
            core_geometry.port_point(target_bounds, entry_port[0], entry_port[1]),
        ]
    )


def stored_label_bounds(cell: ET.Element) -> dict[str, float] | None:
    try:
        left = float(cell.attrib[contracts.DATA_LABEL_LEFT])
        top = float(cell.attrib[contracts.DATA_LABEL_TOP])
        width = float(cell.attrib[contracts.DATA_LABEL_WIDTH])
        height = float(cell.attrib[contracts.DATA_LABEL_HEIGHT])
    except (KeyError, ValueError):
        return None
    return {
        "left": left,
        "right": left + width,
        "top": top,
        "bottom": top + height,
        "width": width,
        "height": height,
    }


def semantic_cells(tree: ET.ElementTree) -> dict[str, ET.Element]:
    cells: dict[str, ET.Element] = {}
    for cell in graph_root(tree).iter("mxCell"):
        kind = cell.attrib.get(contracts.DATA_KIND)
        semantic_id = cell.attrib.get(contracts.DATA_SEMANTIC_ID)
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


def comparison_attributes(cell: ET.Element) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(cell.attrib.items()))


def sibling_order_changes(before: ET.ElementTree, after: ET.ElementTree) -> list[dict]:
    """Compare paint order, not flat serialization across different parents.

    Only shared siblings participate. Additions, removals and reparenting are
    checked separately; a declared new cell still participates when comparing
    the replayed expected output to the actual output.
    """
    def groups(tree):
        result: dict[str | None, list[str | None]] = {}
        for element in graph_root(tree):
            cell = native_cell(element)
            if cell is not None:
                result.setdefault(cell.get("parent"), []).append(element.get("id") or cell.get("id"))
        return result

    left, right = groups(before), groups(after)
    changes = []
    for parent in sorted(left.keys() & right.keys(), key=lambda value: value or ""):
        common = set(left[parent]) & set(right[parent])
        old = [cell_id for cell_id in left[parent] if cell_id in common]
        new = [cell_id for cell_id in right[parent] if cell_id in common]
        if old != new:
            changes.append({"parent": parent, "before": old, "after": new})
    return changes


def unmanaged_root_entries(tree: ET.ElementTree) -> list[ET.Element]:
    """Return opaque drawing units and extensions, including native wrappers."""
    entries = []
    for element in graph_root(tree):
        if not (element.tag == "mxCell"
                and element.get(contracts.DATA_KIND) in {"pool", "lane", "node", "phase", "edge"}
                and element.get(contracts.DATA_SEMANTIC_ID)):
            entries.append(element)
    return entries


def graph_root_preserves_space(tree: ET.ElementTree) -> bool:
    """Resolve inherited xml:space for tails between drawing-root entries."""
    target = graph_root(tree)
    pending = [(tree.getroot(), False)]
    while pending:
        element, preserve = pending.pop()
        setting = element.get("{http://www.w3.org/XML/1998/namespace}space")
        if setting in {"default", "preserve"}:
            preserve = setting == "preserve"
        if element is target:
            return preserve
        pending.extend((child, preserve) for child in element)
    return False


def unmanaged_cell_signatures(tree: ET.ElementTree) -> dict:
    """Retain opaque subtrees exactly, including whitespace and duplicate IDs."""
    preserve_outer_space = graph_root_preserves_space(tree)

    def signature(element, outer=False):
        # Unknown payloads are opaque: even whitespace-only mixed content can
        # be meaningful. Only an entry's outer tail is root-level formatting.
        tail = element.tail or ""
        if outer and not preserve_outer_space and not tail.strip():
            tail = ""
        return (element.tag, tuple(sorted(element.attrib.items())),
                element.text or "", tail,
                tuple(signature(child) for child in element))

    result: dict[str | None, list] = {}
    for element in unmanaged_root_entries(tree):
        cell = native_cell(element)
        cell_id = element.get("id") or (cell.get("id") if cell is not None else None)
        result.setdefault(cell_id, []).append(signature(element, outer=True))
    return result


def ensure_output_available(output: Path, force: bool) -> None:
    if output.exists() and not force:
        raise contracts.DiagramError(
            f"Output already exists: {output}; use --force to replace it",
            code="delivery/output-exists",
            evidence={"output": str(output)},
            supported_fixes=["choose-new-output", "use-force"],
        )


def write_tree(tree: ET.ElementTree, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    # Pretty-print managed XML without interpreting or rewriting opaque text.
    # ET.indent alone would destroy xml:space and whitespace word separators.
    preserve_outer_space = graph_root_preserves_space(tree)
    opaque_text = []
    for entry in unmanaged_root_entries(tree):
        for element in entry.iter():
            keep_tail = (element is not entry or preserve_outer_space
                         or bool((element.tail or "").strip()))
            opaque_text.append((element, element.text, element.tail, keep_tail))
    ET.indent(tree, space="  ")
    for element, text, tail, keep_tail in opaque_text:
        element.text = text
        if keep_tail:
            element.tail = tail
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
        raise contracts.DiagramError("Input and output must differ; review the new file before replacing the original")



def read_tree(path: Path) -> ET.ElementTree:
    """Use the existing ElementTree parser without changing error mapping."""
    return ET.parse(path)
