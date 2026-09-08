"""Native ElementTree adaptation and fidelity-preserving file delivery."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from . import contracts, geometry as core_geometry, labels


def geometry(parent: ET.Element, **attrs) -> ET.Element:
    normalized = {key: contracts.number(value) for key, value in attrs.items() if value is not None}
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





def set_edge_points(
    cell: ET.Element,
    points: list[tuple[float, float]],
    *,
    action: str = "replace_automatic",
) -> None:
    """Replace known route entries while retaining opaque Array payload.

    Point-owned extensions have no safe mapping when a route changes. Reject
    those replacements instead of silently reassigning or dropping metadata.
    """
    if action == "preserve_existing":
        return
    if action not in {"replace_automatic", "replace_explicit"}:
        raise ValueError(f"Unsupported edge points action: {action}")
    geom = cell.find("mxGeometry")
    if geom is None:
        geom = geometry(cell, relative=1)
    arrays = geom.findall("./Array[@as='points']")
    array = arrays[0] if arrays else None
    old_points = array.findall("mxPoint") if array is not None else []
    try:
        old_coordinates = [(float(point.attrib["x"]), float(point.attrib["y"])) for point in old_points]
    except (KeyError, ValueError):
        old_coordinates = None
    if old_coordinates == [(float(x), float(y)) for x, y in points]:
        return
    space_key = "{http://www.w3.org/XML/1998/namespace}space"
    preserve_space = False
    for element in (cell, geom, array):
        if element is not None and element.get(space_key) in {"preserve", "default"}:
            preserve_space = element.get(space_key) == "preserve"
    protected_points = any(
        set(point.attrib) - {"x", "y"} or len(point)
        or (point.text or "").strip() or (point.tail or "").strip()
        or (preserve_space and (point.text or point.tail))
        for point in old_points
    )
    if len(arrays) > 1 or protected_points:
        raise contracts.DiagramError(
            "Cannot replace route points without losing point-owned or ambiguous native payload",
            code="patch/preservation-violation",
            subject={"kind": "edge", "id": cell.get(contracts.DATA_SEMANTIC_ID)},
            evidence={"reason": "point-owned-extension" if protected_points else "multiple-points-arrays"},
            supported_fixes=["retain-saved-edge", "review-native-point-payload"],
        )
    if array is None:
        if not points:
            return
        array = ET.SubElement(geom, "Array", {"as": "points"})
    children = list(array)
    replacement = []
    point_index = 0
    last_point_position = None
    for child in children:
        if child.tag != "mxPoint":
            replacement.append(child)
            continue
        if point_index < len(points):
            x, y = points[point_index]
            child.set("x", contracts.number(x))
            child.set("y", contracts.number(y))
            replacement.append(child)
            point_index += 1
        last_point_position = len(replacement)
    # Extra points remain adjacent to the last native point; vendor siblings
    # retain their own relative order, text and tails.
    insertion = last_point_position if last_point_position is not None else len(replacement)
    for x, y in points[point_index:]:
        replacement.insert(insertion, ET.Element("mxPoint", {"x": contracts.number(x), "y": contracts.number(y)}))
        insertion += 1
    array[:] = replacement
    has_opaque_array = (set(array.attrib) != {"as"} or bool(list(array))
                        or bool((array.text or "").strip()) or bool((array.tail or "").strip())
                        or (preserve_space and bool(array.text or array.tail)))
    # Preserve the established no-Array representation of an empty route when
    # the Array contains only removable formatting and native points.
    if not points and not has_opaque_array:
        geom.remove(array)


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
    source_bounds = core_geometry.node_bounds_in_pool(source, lanes[source["lane"]])
    target_bounds = core_geometry.node_bounds_in_pool(target, lanes[target["lane"]])
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


def _payload_text_rules(tree: ET.ElementTree) -> dict:
    """Identify formatting slots; all unrecognized content stays opaque.

    A child tail belongs to its parent's content, so xml:space on the child
    never resets preservation of that tail. Mixed/extension content also
    preserves separators even if an individual separator is only whitespace.
    """
    rules = {}

    def known_child(parent, child):
        if parent.tag == "mxfile":
            return child.tag == "diagram"
        if parent.tag == "diagram":
            return child.tag == "mxGraphModel"
        if parent.tag == "mxGraphModel":
            return child.tag == "root"
        if parent.tag == "root":
            return (child.tag == "mxCell"
                    and child.get(contracts.DATA_KIND) in contracts.MANAGED_KINDS
                    and bool(child.get(contracts.DATA_SEMANTIC_ID)))
        if parent.tag == "mxCell":
            return child.tag == "mxGeometry"
        if parent.tag == "mxGeometry":
            return ((child.tag == "Array" and child.get("as") == "points")
                    or (child.tag == "mxPoint" and child.get("as") in
                        {"offset", "sourcePoint", "targetPoint"})
                    or (child.tag == "mxRectangle" and child.get("as") == "alternateBounds"))
        if parent.tag == "Array" and parent.get("as") == "points":
            return child.tag == "mxPoint"
        return False

    def visit(element, inherited=False, opaque=False, keep_tail=False):
        setting = element.get("{http://www.w3.org/XML/1998/namespace}space")
        preserve = setting == "preserve" if setting in {"preserve", "default"} else inherited
        children = list(element)
        # Root-level unknown drawing units already have independently protected
        # payload; their outer whitespace remains drawing-root formatting.
        extensions = element.tag != "root" and any(not known_child(element, c) for c in children)
        mixed = bool((element.text or "").strip()) or any((c.tail or "").strip() for c in children)
        keep_content = opaque or preserve or extensions or mixed
        rules[element] = (keep_content, keep_tail)
        for child in children:
            visit(child, preserve, opaque or not known_child(element, child), keep_content)

    visit(tree.getroot())
    return rules


def serialization_signature(tree: ET.ElementTree):
    """Whole working-tree signature used only to verify serialization fidelity."""
    rules = _payload_text_rules(tree)

    def signature(element):
        keep_text, keep_tail = rules[element]
        text, tail = element.text or "", element.tail or ""
        return (element.tag, tuple(sorted(element.attrib.items())),
                text if keep_text or text.strip() else "",
                tail if keep_tail or tail.strip() else "",
                tuple(signature(child) for child in element))

    return signature(tree.getroot())


def cell_payload_signatures(tree: ET.ElementTree) -> dict[str, dict]:
    """Ordered native payload fields, without changing semantic hash scope."""
    rules = _payload_text_rules(tree)
    result = {}
    for key, cell in semantic_cells(tree).items():
        fields = {}

        def collect(element, path, outer=False):
            keep_text, keep_tail = rules[element]
            if not outer:
                fields[path + "/@attributes"] = tuple(sorted(element.attrib.items()))
            text, tail = element.text or "", element.tail or ""
            fields[path + "/text()"] = text if keep_text or text.strip() else ""
            fields[path + "/tail()"] = tail if keep_tail or tail.strip() else ""
            # Indices are absolute child positions: repeated tags and order are
            # observable, including multiple mxGeometry elements.
            for index, child in enumerate(element):
                collect(child, f"{path}/{child.tag}[{index}]")

        collect(cell, "mxCell", outer=True)
        result[key] = fields
    return result


def cell_payload_changes(before: ET.ElementTree, after: ET.ElementTree) -> list[dict]:
    left, right = cell_payload_signatures(before), cell_payload_signatures(after)
    changes = []
    for key in sorted(left.keys() & right.keys()):
        for path in sorted(left[key].keys() | right[key].keys()):
            if path not in left[key] or path not in right[key] or left[key][path] != right[key][path]:
                changes.append({
                    "semantic_id": key.split(":", 1)[1], "kind": key.split(":", 1)[0],
                    "path": path,
                    "change": "added" if path not in left[key] else
                              "missing" if path not in right[key] else "changed",
                })
    return changes


def route_mutable_opaque_guard(before: ET.ElementTree, candidate: ET.ElementTree, changes: dict) -> None:
    """Protect edge extensions independently of route planning or patch replay.

    Known route/label values are projected away; opaque fields and their order
    remain. This also covers mutable routes that the saved-route guard excludes.
    """
    mutable_attributes = {
        "source", "target", "value", "style",
        contracts.DATA_EDGE_TYPE, contracts.DATA_FROM, contracts.DATA_TO,
        contracts.DATA_ROUTE, contracts.DATA_BRANCH, contracts.DATA_FLOW_ROLE,
        contracts.DATA_OUTCOME, contracts.DATA_ALLOW_PORT_REUSE,
        contracts.DATA_EXIT_SIDE, contracts.DATA_ENTRY_SIDE,
        contracts.DATA_EXIT_OFFSET, contracts.DATA_ENTRY_OFFSET,
        contracts.DATA_EXIT_SIDE_EXPLICIT, contracts.DATA_ENTRY_SIDE_EXPLICIT,
        contracts.DATA_EXIT_OFFSET_EXPLICIT, contracts.DATA_ENTRY_OFFSET_EXPLICIT,
        contracts.DATA_WAYPOINTS_ORIGIN, contracts.DATA_LABEL_LEFT,
        contracts.DATA_LABEL_TOP, contracts.DATA_LABEL_WIDTH,
        contracts.DATA_LABEL_HEIGHT, contracts.DATA_LABEL_SEGMENT,
    }
    port_style = {"exitX", "exitY", "exitDx", "exitDy", "entryX", "entryY", "entryDx", "entryDy", "dashed"}

    def signatures(tree):
        rules = _payload_text_rules(tree)

        def project(element, role):
            attributes = dict(element.attrib)
            if role == "cell":
                attributes = {key: value for key, value in attributes.items() if key not in mutable_attributes}
                attributes["style"] = tuple(part for part in element.get("style", "").split(";")
                                            if part and part.split("=", 1)[0] not in port_style)
            elif role == "geometry":
                for key in ("x", "y", "relative"):
                    attributes.pop(key, None)
            elif role in {"array", "point", "offset"}:
                for key in (("as",) if role == "array" else ("x", "y", "as") if role == "offset" else ("x", "y")):
                    attributes.pop(key, None)
            keep_text, keep_tail = rules[element]
            text, tail = element.text or "", element.tail or ""
            text = text if keep_text or text.strip() else ""
            tail = tail if keep_tail or tail.strip() else ""
            children = []
            point_index = 0
            for child in element:
                child_role = "opaque"
                if role == "cell" and child.tag == "mxGeometry":
                    child_role = "geometry"
                elif role == "geometry" and child.tag == "Array" and child.get("as") == "points":
                    child_role = "array"
                elif role == "geometry" and child.tag == "mxPoint" and child.get("as") == "offset":
                    child_role = "offset"
                elif role == "array" and child.tag == "mxPoint":
                    child_role = "point"
                payload = project(child, child_role)
                if payload is not None:
                    children.append((point_index if child_role == "point" else None, payload))
                if child_role == "point":
                    point_index += 1
            if role != "opaque" and not (attributes or text or tail or children):
                return None
            return (element.tag, tuple(sorted(attributes.items())), text, tail, tuple(children))

        return {edge_id: project(cell, "cell") for edge_id, cell in edge_records(graph_root(tree)).items()}

    old, new = signatures(before), signatures(candidate)
    deleted = set(changes.get("delete_edges", []))
    violations = sorted(edge_id for edge_id in old if edge_id not in deleted and old[edge_id] != new.get(edge_id))
    if violations:
        raise contracts.DiagramError(
            "Patch changed opaque content owned by an existing edge",
            code="patch/preservation-violation",
            evidence={"edges": violations, "reason": "opaque-edge-payload"},
            supported_fixes=["retain-native-extension-payload", "review-native-point-payload"],
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


def write_tree(tree: ET.ElementTree, output: Path, *, candidate_check=None) -> None:
    """Write a separate candidate and approve its parsed content before replace.

    The callback receives the actual serialized tree. It may raise to reject
    delivery; neither the accepted in-memory tree nor an old output is changed.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    working = copy.deepcopy(tree)
    saved_text = [
        (element, element.text, element.tail, keep_text, keep_tail)
        for element, (keep_text, keep_tail) in _payload_text_rules(working).items()
    ]
    ET.indent(working, space="  ")
    for element, text, tail, keep_text, keep_tail in saved_text:
        if keep_text:
            element.text = text
        if keep_tail:
            element.tail = tail
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".candidate", dir=output.parent
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        working.write(
            temporary_path,
            encoding="utf-8",
            xml_declaration=False,
            short_empty_elements=True,
        )
        with temporary_path.open("ab") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        candidate = read_tree(temporary_path)
        if candidate_check is not None:
            candidate_check(candidate)
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


def routing_node_views(nodes: dict[str, dict]) -> dict[str, dict]:
    """Project raw fields without converting ranks or computing geometry."""
    views = {}
    for node_id, record in nodes.items():
        view = {key: record[key] for key in ("lane", "geometry", "rank", "type") if key in record}
        if "cell" in record:
            attributes = record["cell"].attrib
            view["semantic"] = {
                key: attributes[attribute]
                for key, attribute in (("rank", contracts.DATA_RANK), ("type", contracts.DATA_NODE_TYPE),
                                       ("slot", contracts.DATA_SLOT))
                if attribute in attributes
            }
        views[node_id] = view
    return views


def routing_lane_views(lanes: dict[str, dict]) -> dict[str, dict]:
    """Retain current raw lane geometry and map order without XML cells."""
    return {
        lane_id: {"geometry": record["geometry"]} if "geometry" in record else {}
        for lane_id, record in lanes.items()
    }


def read_main_path(pool: ET.Element) -> list[str]:
    raw = pool.attrib.get(contracts.DATA_MAIN_PATH, "[]")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def json_attribute(
    cell: ET.Element,
    name: str,
    expected_type: type,
    default,
):
    raw = cell.attrib.get(name)
    if raw is None:
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise contracts.DiagramError(
            f"Invalid managed metadata in {name}",
            code="integrity/schema-composition-mismatch",
            subject={"kind": "pool", "id": "main"},
            evidence={"attribute": name},
            supported_fixes=["restore-semantic-metadata", "controlled-rebuild"],
        ) from exc
    if not isinstance(value, expected_type):
        raise contracts.DiagramError(
            f"Managed metadata in {name} has the wrong type",
            code="integrity/schema-composition-mismatch",
            subject={"kind": "pool", "id": "main"},
            evidence={"attribute": name, "expected_type": expected_type.__name__},
            supported_fixes=["restore-semantic-metadata", "controlled-rebuild"],
        )
    return value


def managed_metadata_error(
    message: str,
    *,
    attribute: str,
    evidence: dict | None = None,
) -> contracts.DiagramError:
    return contracts.DiagramError(
        message,
        code="integrity/schema-composition-mismatch",
        subject={"kind": "pool", "id": "main"},
        evidence={"attribute": attribute, **(evidence or {})},
        supported_fixes=["restore-semantic-metadata", "controlled-rebuild"],
    )


def managed_id_list_attribute(
    cell: ET.Element,
    name: str,
    default,
) -> list[str] | None:
    value = json_attribute(cell, name, list, default)
    if value is None:
        return value
    try:
        return contracts.validate_id_list(value, f"managed metadata {name}")
    except contracts.DiagramError as exc:
        raise managed_metadata_error(
            f"Managed metadata in {name} must contain semantic IDs",
            attribute=name,
            evidence={"cause": exc.code},
        ) from exc


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
                "exit_port": port_from_style(cell, "exit"),
                "entry_port": port_from_style(cell, "entry"),
                "waypoints": [
                    {"x": x, "y": y} for x, y in edge_waypoints(cell)
                ],
            }
        )
    return sorted(recovered, key=lambda item: (item["from"], item["to"], item["cell_id"]))


def read_lane_order(pool: ET.Element, root: ET.Element, lanes: dict[str, dict]) -> list[str]:
    order = managed_id_list_attribute(pool, contracts.DATA_LANE_ORDER, None)
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


def extract_native_label_profile(cell, path=None, scene=None):
    """Extract native XML facts once; no capability or router decisions here."""
    import math
    geometries = cell.findall("mxGeometry")
    geom = geometries[0] if len(geometries) == 1 else None
    raw = {"text": cell.get("value", ""), "edge_style": cell.get("style", ""),
           "path": list(path or []), "geometry_count": len(geometries),
           "scene_available": bool(scene), "terminals": {}}
    if geom is not None:
        raw["native_geometry"] = {
            "attributes": dict(geom.attrib),
            "offsets": [{"tag": child.tag, "attributes": dict(child.attrib)}
                        for child in geom if child.get("as") == "offset"],
            "points_arrays": [[{"tag": point.tag, "attributes": dict(point.attrib)}
                               for point in array]
                              for array in geom.findall("./Array[@as='points']")],
        }
    if not scene:
        return raw
    lanes, nodes = scene.get("lanes", {}), scene.get("nodes", {})
    pool = scene.get("pool")
    try:
        if "parent_origin" in scene:
            origin = tuple(float(value) for value in scene["parent_origin"])
            if len(origin) != 2:
                raise ValueError("invalid parent origin")
        elif pool is not None and cell.get("parent") == pool.get("id") and pool.get("parent") == "1":
            pool_geometry = parse_geometry(pool)
            origin = pool_geometry["x"], pool_geometry["y"]
        else:
            raw["parent_origin_reason"] = "parent_origin_unavailable"
            origin = None
        if origin is not None:
            if not all(math.isfinite(value) for value in origin):
                raise ValueError("nonfinite parent origin")
            raw["parent_origin"] = origin
    except (ValueError, TypeError, contracts.DiagramError):
        raw["parent_origin_reason"] = "invalid_parent_origin"
    for field, prefix in ((contracts.DATA_FROM, "exit"), (contracts.DATA_TO, "entry")):
        node = nodes.get(cell.get(field))
        terminal = {"present": node is not None}
        raw["terminals"][prefix] = terminal
        if node is None:
            continue
        terminal.update(identity_matches=cell.get("source" if prefix == "exit" else "target") == node["cell"].get("id"),
                        style=node["cell"].get("style", ""),
                        type=node["cell"].get(contracts.DATA_NODE_TYPE, "process"))
        try:
            terminal["bounds"] = core_geometry.node_bounds_in_pool(node, lanes[node["lane"]])
        except (KeyError, ValueError, TypeError, OverflowError):
            terminal["bounds_reason"] = "invalid_native_path"
    return raw


def native_label_inputs(cell, path=None, scene=None):
    """Compatible saved-XML entry point to the pure native capability model."""
    return labels.resolve_native_label_inputs(extract_native_label_profile(cell, path, scene))


def edge_label_measurement(cell, path=None, scene=None):
    """Shared read-only native label measurement for QA, inspect and routing."""
    return labels.measure_native_label(native_label_inputs(cell, path, scene))
