"""Pure edge-label sizing, candidate generation, and placement."""

from __future__ import annotations

import unicodedata

from . import contracts, geometry as core_geometry


EDGE_LABEL_FONT_SIZE = 11.0
EDGE_LABEL_HEIGHT = 18.0
EDGE_LABEL_PADDING = 8.0
EDGE_LABEL_VERTICAL_PADDING = 2.0
EDGE_LABEL_GAP = 5.0

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
    *, size: tuple[float, float] | None = None,
) -> list[tuple[int, dict[str, float], float]]:
    if not label.strip():
        return []
    width, height = size if size is not None else edge_label_size(label)
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

def ordered_label_box_candidates(points, label, preferred_side=None,
                                 prefer_source_proximity=False, *, size=None):
    """Preserve the established stable side/source/segment placement preference."""
    candidates = label_box_candidates(points, label, size=size)

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
    return candidates


def choose_label_box(
    points: list[tuple[float, float]],
    label: str,
    node_boxes: list[dict[str, float]],
    other_segments: list[tuple[tuple[float, float], tuple[float, float]]],
    other_labels: list[dict[str, float]],
    preferred_side: str | None = None,
    container_bounds: dict[str, float] | None = None,
    prefer_source_proximity: bool = False,
    *, size: tuple[float, float] | None = None,
) -> tuple[int, dict[str, float]] | None:
    candidates = ordered_label_box_candidates(
        points, label, preferred_side, prefer_source_proximity, size=size,
    )
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


# Native positioning follows mxGraphView.getPoint/updateEdgeLabelOffset and
# mxGraph.getConnectionPoint.  Bounds remain an estimate, not font rendering.
NATIVE_LABEL_PROFILE = "drawio-31.3.2-neutral-orthogonal-v1"


def native_label_anchor(points, *, relative=True, x=0.0, y=0.0,
                        offset=(0.0, 0.0), scale=1.0):
    """Return (model-space anchor, segment index) using mxGraph rounding.

    ``points`` must already represent the editor's absolutePoints in model
    coordinates.  The view rounds distance after scaling, so a 2x render can
    differ from a 1x render by half a model pixel.  Nonrelative edge labels use
    endpoint midpoint (not the bounding-box or arc-length midpoint).
    """
    import math
    if len(points) < 2 or not math.isfinite(scale) or scale <= 0:
        raise ValueError("Native label needs a finite positive scale and a path")
    if not all(math.isfinite(float(v)) for p in points for v in p):
        raise ValueError("Nonfinite native path")
    if not all(math.isfinite(float(v)) for v in (x, y, *offset)):
        raise ValueError("Nonfinite native label geometry")
    if not relative:
        return ((points[0][0] + points[-1][0]) / 2 + offset[0],
                (points[0][1] + points[-1][1]) / 2 + offset[1]), None
    lengths = [math.hypot(b[0]-a[0], b[1]-a[1]) * scale
               for a, b in zip(points, points[1:])]
    # Python round uses ties-to-even; JavaScript Math.round rounds toward +inf.
    distance = math.floor((x / 2 + 0.5) * sum(lengths) + 0.5)
    index, traversed = 0, 0.0
    while distance >= math.floor(traversed + lengths[index] + 0.5) and index < len(lengths)-1:
        traversed += lengths[index]
        index += 1
    length = lengths[index]
    factor = (distance - traversed) / length if length else 0.0
    first, last = points[index:index+2]
    dx, dy = last[0]-first[0], last[1]-first[1]
    nx, ny = (dy * scale / length, dx * scale / length) if length else (0.0, 0.0)
    return (first[0] + dx * factor + nx*y + offset[0],
            first[1] + dy * factor - ny*y + offset[1]), index if length else None


def _native_style_reason(style, text):
    """A small explicit neutral-text profile; unknown geometry is never zero."""
    import math
    import re
    values = {}
    for part in style.split(";"):
        if not part:
            continue
        if "=" not in part:
            return "named_edge_style"
        key, value = part.split("=", 1)
        values[key] = value
    allowed = {
        "edgeStyle", "rounded", "orthogonalLoop", "jettySize", "html",
        "endArrow", "endFill", "endSize", "startArrow", "startFill", "startSize",
        "strokeWidth", "strokeColor", "dashed", "dashPattern", "labelBackgroundColor",
        "labelBorderColor", "fontColor", "fontSize", "fontFamily", "fontStyle",
        "exitX", "exitY", "exitDx", "exitDy", "entryX", "entryY", "entryDx", "entryDy",
        "exitPerimeter", "entryPerimeter", "align", "verticalAlign", "whiteSpace",
        "rotation", "textRotation", "horizontal", "curved", "opacity", "textOpacity",
        "noLabel", "labelPosition", "verticalLabelPosition", "spacing", "spacingTop",
        "spacingBottom", "spacingLeft", "spacingRight",
    }
    unknown = sorted(set(values)-allowed)
    if unknown:
        return "unsupported_style:" + ",".join(unknown)
    exact = {"rounded": "0", "curved": "0", "rotation": "0", "textRotation": "0",
             "horizontal": "1", "fontStyle": "0", "align": "center",
             "verticalAlign": "middle", "labelPosition": "center",
             "verticalLabelPosition": "middle", "spacing": "2", "spacingTop": "0",
             "spacingBottom": "0", "spacingLeft": "0", "spacingRight": "0"}
    for key, expected in exact.items():
        if key in values and values[key] != expected:
            return "unsupported_style:" + key
    if values.get("fontFamily", "Helvetica") != "Helvetica":
        return "unsupported_font_family"
    if values.get("whiteSpace", "nowrap") != "nowrap":
        return "unsupported_wrapping"
    if values.get("html", "1") != "1":
        return "uncalibrated_text_renderer"
    try:
        size = float(values.get("fontSize", "11"))
    except ValueError:
        return "invalid_font_size"
    if not math.isfinite(size) or not 8 <= size <= 24:
        return "unsupported_font_size"
    if values.get("html", "1") == "1" and (re.search(r"<[^>]*>", text) or re.search(r"&(?:#\w+|\w+);", text)):
        return "unsupported_rich_text"
    # Only the calibrated neutral ASCII/CJK profile. A generic BMP fallback
    # would silently mismeasure shaping scripts, combining marks and controls.
    if any(not (32 <= ord(c) <= 126 or c in "\r\n" or
                0x3400 <= ord(c) <= 0x4DBF or 0x4E00 <= ord(c) <= 0x9FFF)
           for c in text):
        return "unsupported_text_glyphs"
    return None


def measure_native_label(inputs):
    """Pure native measurement.  Input contains no XML and no data-label caches."""
    text = inputs.get("text", "")
    result = {"status": "not_available", "position": None, "bounds": None,
              "bounds_quality": None, "carrier_segment": None, "carrier_usable": False, "reason": None,
              "profile": NATIVE_LABEL_PROFILE, "path": inputs.get("path", []),
              "path_available": not inputs.get("reason") and len(inputs.get("path", [])) >= 2}
    if not text.strip() or inputs.get("hidden", False):
        result.update(status="not_applicable", reason="no_visible_text")
        return result
    reason = inputs.get("reason") or _native_style_reason(inputs.get("style", ""), text)
    if reason:
        result["reason"] = reason
        return result
    try:
        position, arc_segment = native_label_anchor(
            inputs["path"], relative=inputs.get("relative", True),
            x=inputs.get("x", 0.0), y=inputs.get("y", 0.0),
            offset=inputs.get("offset", (0.0, 0.0)), scale=inputs.get("scale", 1.0))
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        result["reason"] = "invalid_native_geometry:" + str(exc)
        return result
    style = dict(part.split("=", 1) for part in inputs.get("style", "").split(";") if "=" in part)
    size = float(style.get("fontSize", "11"))
    # Native HTML exports preserve a trailing LF as a blank line. CRLF is a
    # single break; a bare CR is HTML whitespace, not a line break.
    lines = text.replace("\r\n", "\n").replace("\r", " ").split("\n")
    # No candidate-layout 190px cap: saved native text has no such width limit.
    # Helvetica regular advance groups (thousandths of an em), checked
    # against Draw.io 31.3.2 native exports. CJK samples use one em per glyph.
    # A small 2px allowance covers rounding/kerning variation; this remains an
    # estimate, not browser font measurement or a claimed exact pixel bound.
    import math
    advances = {
        222: "ijl", 278: " !'(),./:;Ift[\\]", 333: "-r`", 355: '"', 389: "*",
        469: "^", 500: "Jcksvxyz", 556: "#$0123456789?Labdeghnopqu_",
        584: "+<=>~", 611: "FTZ", 667: "&ABEKPSVXY", 722: "CDHNRUw",
        778: "GOQ", 833: "Mm", 889: "%", 944: "W", 1015: "@",
        334: "{}", 260: "|",
    }
    glyph_widths = {c: advance / 1000 for advance, characters in advances.items() for c in characters}
    width = math.ceil(max(sum(glyph_widths.get(c, 1.0) for c in line) for line in lines) * size) + 2.0
    # Native 8/11/16/24px samples have a 1.2em multiline increment and
    # roughly a quarter-em extra first-line extent. Round outwards, then add
    # one pixel for environment/font rasterization variation.
    height = math.ceil((len(lines) * 1.2 + .25) * size) + 1.0
    box = {"left": position[0]-width/2, "right": position[0]+width/2,
           "top": position[1]-height/2, "bottom": position[1]+height/2,
           "width": width, "height": height}
    # Offset dragging can move a label away from the arc-length anchor's segment.
    # A carrier is only the uniquely nearest segment with an interior projection.
    candidates = []
    for index, (a, b) in enumerate(zip(inputs["path"], inputs["path"][1:])):
        dx, dy = b[0]-a[0], b[1]-a[1]
        length2 = dx*dx + dy*dy
        if not length2:
            continue
        fraction = ((position[0]-a[0])*dx+(position[1]-a[1])*dy)/length2
        if 0 <= fraction <= 1:
            distance2 = (position[0]-a[0]-fraction*dx)**2+(position[1]-a[1]-fraction*dy)**2
            candidates.append((distance2, index))
    candidates.sort()
    carrier = None
    if candidates and (len(candidates) == 1 or abs(candidates[0][0]-candidates[1][0]) > 1e-8):
        carrier = candidates[0][1]
    usable = False
    if carrier is not None:
        segment = inputs["path"][carrier:carrier+2]
        axis = core_geometry.segment_axis(segment)
        span = core_geometry.segment_length(segment)
        usable = (axis == "horizontal" and span >= width + EDGE_LABEL_PADDING) or (
            axis == "vertical" and span >= height + EDGE_LABEL_VERTICAL_PADDING)
    result.update(status="available", position=position, bounds=box,
                  bounds_quality="estimated", carrier_segment=carrier, carrier_usable=usable)
    return result


def native_fixed_segment_path(source, target, hints):
    """mxEdgeStyle.SegmentConnector's fixed-terminal branch, in model pixels.

    OrthConnector delegates to this router whenever saved control hints exist.
    Fixed terminals suppress the floating-terminal channel/perimeter branches.
    Hints are not necessarily rendered turns: orientation alternates even for
    repeated hints.  Reconstruct them before measuring the label anchor.
    """
    import math
    hints = [list(point) for point in hints]
    if not hints:
        return [source, target]
    # scalePointArray rounds working terminals to tenths, while updatePoints
    # retains the unrounded fixed endpoints in the actual edge state.
    fixed_source, fixed_target = tuple(source), tuple(target)
    source = tuple(math.floor(value*10 + .5)/10 for value in source)
    target = tuple(math.floor(value*10 + .5)/10 for value in target)
    pt = list(source)
    result = [fixed_source]
    last_pushed = fixed_source

    def push(point):
        nonlocal last_pushed
        point = tuple(math.floor(value*10 + .5)/10 for value in point)
        if abs(last_pushed[0]-point[0]) >= 1 or abs(last_pushed[1]-point[1]) >= 1:
            result.append(point)
            last_pushed = point

    for hint, terminal in ((hints[0], source), (hints[-1], target)):
        for axis in (0, 1):
            if abs(hint[axis]-terminal[axis]) < 1:
                hint[axis] = terminal[axis]
    hint = hints[0]
    horizontal = True
    for index, (terminal, current_hint) in enumerate(((source, hints[0]), (target, hints[-1]))):
        vertical_aligned = terminal[0] == current_hint[0]
        horizontal_aligned = terminal[1] == current_hint[1]
        if not (index == 0 and vertical_aligned and horizontal_aligned):
            if vertical_aligned or horizontal_aligned:
                horizontal = horizontal_aligned
                if index == 1:
                    horizontal = horizontal_aligned if len(hints) % 2 == 0 else vertical_aligned
                break
        if vertical_aligned and horizontal_aligned:
            hints = hints[1:]
    if horizontal and source[1] != hint[1]:
        push((pt[0], hint[1]))
    elif not horizontal and source[0] != hint[0]:
        push((hint[0], pt[1]))
    if horizontal:
        pt[1] = hint[1]
    else:
        pt[0] = hint[0]
    for hint in hints:
        horizontal = not horizontal
        if horizontal:
            pt[1] = hint[1]
        else:
            pt[0] = hint[0]
        push(pt)
    if horizontal and target[1] != hint[1]:
        push((target[0], hint[1]))
    elif not horizontal and target[0] != hint[0]:
        push((hint[0], target[1]))
    if len(result) > 1 and abs(target[0]-result[-1][0]) <= 1 and abs(target[1]-result[-1][1]) <= 1:
        result.pop()
        if result:
            previous = list(result[-1])
            for axis in (0, 1):
                if abs(previous[axis]-target[axis]) < 1:
                    previous[axis] = target[axis]
            result[-1] = tuple(previous)
    result.append(fixed_target)
    # These are state.absolutePoints, not mxShape's compacted paint path.
    # Collinear points still participate in getPoint's rounded traversal.
    return result


def _native_style_values(style):
    return dict(part.split("=", 1) for part in style.split(";") if "=" in part)


def _native_port_from_style(values, prefix):
    try:
        x, y = float(values[prefix + "X"]), float(values[prefix + "Y"])
    except (KeyError, ValueError):
        return None
    tolerance = core_geometry.GEOMETRY_TOLERANCE / 10
    if abs(y) < tolerance:
        return "top", x
    if abs(y - 1.0) < tolerance:
        return "bottom", x
    if abs(x) < tolerance:
        return "left", y
    if abs(x - 1.0) < tolerance:
        return "right", y
    return None


def resolve_native_label_inputs(raw):
    """Resolve plain extracted facts using the existing calibrated native model.

    Saved terminal coordinates and ordered hints are never newly rounded here.
    Early-return fields and reason precedence match the saved-XML contract.
    """
    import math
    result = {"text": raw.get("text", ""),
              "style": raw.get("edge_style", ""), "path": list(raw.get("path") or [])}
    style = _native_style_values(result["style"])
    result["hidden"] = style.get("noLabel") == "1"
    if raw.get("geometry_count", 0) != 1:
        result["reason"] = "missing_or_ambiguous_geometry"
        return result
    native_geometry = raw["native_geometry"]
    geom = native_geometry["attributes"]
    offsets = native_geometry.get("offsets", [])
    if any(child["tag"] != "mxPoint" for child in offsets):
        result["reason"] = "unsupported_native_offset"
        return result
    if len(offsets) > 1:
        result["reason"] = "ambiguous_native_offset"
        return result
    try:
        result.update(relative=geom.get("relative", "0") in {"1", "true"},
                      x=float(geom.get("x", "0")), y=float(geom.get("y", "0")),
                      offset=tuple(float(offsets[0]["attributes"].get(axis, "0")) if offsets else 0.0 for axis in ("x", "y")))
        if any(float(geom.get(key, "0")) != 0 for key in ("width", "height")):
            result["reason"] = "unsupported_label_width_or_height"
            return result
    except (ValueError, OverflowError):
        result["reason"] = "invalid_native_geometry"
        return result
    if not raw.get("scene_available"):
        result["reason"] = "terminal_scene_unavailable"
        return result
    if raw.get("parent_origin_reason"):
        result["reason"] = raw["parent_origin_reason"]
        return result
    try:
        origin = tuple(float(value) for value in raw["parent_origin"])
        if len(origin) != 2 or not all(math.isfinite(value) for value in origin):
            raise ValueError("invalid parent origin")
    except (KeyError, ValueError, TypeError, OverflowError):
        result["reason"] = "invalid_parent_origin"
        return result
    result["parent_origin"] = origin
    if style.get("edgeStyle") != "orthogonalEdgeStyle" or style.get("jettySize", "auto") != "auto":
        result["reason"] = "unsupported_editor_router"
        return result
    if style.get("startArrow", "none") != "none" or style.get("endArrow", "block") not in {"none", "block"}:
        result["reason"] = "unsupported_marker"
        return result
    try:
        if any(float(style.get(key, str(default))) != default for key, default in
               (("endSize", 6), ("endFill", 1), ("strokeWidth", 1), ("exitDx", 0), ("exitDy", 0),
                ("entryDx", 0), ("entryDy", 0))):
            result["reason"] = "unsupported_marker_or_port_style"
            return result
        endpoints, ports = [], []
        for prefix in ("exit", "entry"):
            terminal = raw.get("terminals", {}).get(prefix, {})
            port = _native_port_from_style(style, prefix)
            if not terminal.get("present") or port is None or not 0 <= port[1] <= 1:
                result["reason"] = "unrecoverable_terminal_port"
                return result
            if not terminal.get("identity_matches"):
                result["reason"] = "terminal_identity_mismatch"
                return result
            node_style = _native_style_values(terminal.get("style", ""))
            kind = terminal.get("type", "process")
            flags = {p for p in terminal.get("style", "").split(";") if p and "=" not in p}
            if any(k in node_style for k in ("perimeter", "direction", "flipH", "flipV", "perimeterSpacing")) or float(node_style.get("rotation", "0")) != 0:
                result["reason"] = "unsupported_terminal_transform"
                return result
            if kind == "process":
                supported = not flags and "shape" not in node_style and node_style.get("rounded", "0") == "0"
            elif kind == "decision":
                supported = flags == {"rhombus"} and "shape" not in node_style
            elif kind in {"start", "end"}:
                supported = flags == {"ellipse"} and "shape" not in node_style and node_style.get("aspect") == "fixed"
            else:
                supported = False
            if not supported:
                result["reason"] = "unsupported_terminal_shape"
                return result
            if terminal.get("bounds_reason"):
                result["reason"] = terminal["bounds_reason"]
                return result
            bounds = terminal["bounds"]
            point = core_geometry.port_point(bounds, *port)
            # Off-center curved/diamond perimeter constraints can induce native
            # SegmentConnector turns; this first calibrated profile rejects them.
            if kind != "process" and abs(port[1] - 0.5) > 1e-9:
                result["reason"] = "uncalibrated_off_center_perimeter"
                return result
            # Draw.io 31.3.2 getFixedTerminalPoint explicitly disables
            # getConnectionPoint rounding. Only router working copies round.
            endpoints.append(tuple(value + origin[axis] for axis, value in enumerate(point)))
            ports.append(port)
        arrays = native_geometry.get("points_arrays", [])
        if len(arrays) > 1:
            result["reason"] = "ambiguous_native_waypoints"
            return result
        hints = []
        if arrays:
            for point in arrays[0]:
                if point["tag"] != "mxPoint":
                    result["reason"] = "unsupported_native_waypoint"
                    return result
                hint = tuple(float(point["attributes"][axis]) for axis in ("x", "y"))
                if not all(math.isfinite(value) for value in hint):
                    raise ValueError("nonfinite waypoint")
                hints.append(tuple(value + origin[axis] for axis, value in enumerate(hint)))
        # Preserve raw hint order when reproducing the native router; duplicate
        # and collinear hints still affect its alternating orientation.
        candidate = native_fixed_segment_path(endpoints[0], endpoints[1], hints)
        if len(candidate) < 2 or any(
            abs(a[0]-b[0]) > 1e-9 and abs(a[1]-b[1]) > 1e-9
            for a, b in zip(candidate, candidate[1:])
        ):
            result["reason"] = "editor_router_additional_turns_required"
            return result
        if not hints:
            opposite = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
            dx, dy = endpoints[1][0]-endpoints[0][0], endpoints[1][1]-endpoints[0][1]
            facing = {"bottom": dy > 0 and dx == 0, "top": dy < 0 and dx == 0,
                      "left": dx < 0 and dy == 0, "right": dx > 0 and dy == 0}
            if ports[1][0] != opposite[ports[0][0]] or not facing[ports[0][0]]:
                result["reason"] = "editor_router_additional_turns_required"
                return result
        result["path"] = [tuple(value-origin[axis] for axis, value in enumerate(point)) for point in candidate]
    except (KeyError, ValueError, TypeError, OverflowError):
        result["reason"] = "invalid_native_path"
    return result


def candidate_label_geometry(path, label_choice):
    """The exact native attributes installed by the automatic label writer."""
    _, box = label_choice
    midpoint, _ = native_label_anchor(path)
    desired = (box["left"] + box["width"] / 2,
               box["top"] + box["height"] / 2)
    return {"relative": "1", "x": "0", "y": "0"}, {
        "x": contracts.number(desired[0] - midpoint[0]),
        "y": contracts.number(desired[1] - midpoint[1]),
    }


def project_candidate_native_inputs(profile, assignment, hints, label_choice=None):
    """Project only planned writer values, leaving saved terminal facts intact.

    ``assignment`` is an EdgePortAssignment or a four-field side/offset dict.
    ``profile`` is the ordinary-value record from extract_native_label_profile.
    The profile is never mutated, including nested geometry and terminal records.
    """
    import copy
    import math
    raw = copy.deepcopy(profile)
    updates = {}
    for prefix in ("exit", "entry"):
        if isinstance(assignment, dict):
            side, offset = assignment[prefix + "_side"], assignment[prefix + "_offset"]
        else:
            endpoint = getattr(assignment, prefix)
            side, offset = endpoint.side, endpoint.offset
        if not math.isfinite(float(offset)):
            raise ValueError("Nonfinite candidate port")
        x, y = core_geometry.port_xy(side, offset)
        updates[prefix + "X"] = contracts.number(x)
        updates[prefix + "Y"] = contracts.number(y)
    # Match set_style_option's replacement/deduplication, retaining every
    # unrecognized token.  The projection cannot disguise a custom style.
    parts = [part for part in raw.get("edge_style", "").split(";") if part]
    for key, value in updates.items():
        output, replaced = [], False
        for part in parts:
            if part.split("=", 1)[0] == key:
                if not replaced:
                    output.append(key + "=" + value)
                    replaced = True
            else:
                output.append(part)
        if not replaced:
            output.append(key + "=" + value)
        parts = output
    raw["edge_style"] = ";".join(parts) + ";"
    serialized_hints = []
    for point in hints:
        if len(point) != 2 or not all(math.isfinite(float(value)) for value in point):
            raise ValueError("Nonfinite or invalid candidate waypoint")
        serialized_hints.append({"tag": "mxPoint", "attributes": {
            "x": contracts.number(point[0]), "y": contracts.number(point[1])}})
    geom = raw.get("native_geometry")
    if geom is not None:
        arrays = geom.get("points_arrays", [])
        # Keep malformed structure visible; route search never fixes input XML.
        if len(arrays) <= 1 and not any(point.get("tag") != "mxPoint" for array in arrays for point in array):
            geom["points_arrays"] = [serialized_hints] if serialized_hints else []
        offsets = geom.get("offsets", [])
        valid_offsets = len(offsets) <= 1 and all(point.get("tag") == "mxPoint" for point in offsets)
        try:
            attributes = geom["attributes"]
            # Invalid saved native geometry is an immutable input failure, not
            # an invitation to silently replace it with the automatic default.
            native_values = [float(attributes.get(key, "0")) for key in ("x", "y", "width", "height")]
            native_values.extend(float(point["attributes"].get(axis, "0"))
                                 for point in offsets for axis in ("x", "y"))
            valid_geometry = all(math.isfinite(value) for value in native_values)
        except (KeyError, TypeError, ValueError, OverflowError):
            valid_geometry = False
        if valid_offsets and valid_geometry:
            geom["attributes"].update(relative="1", x="0", y="0")
            geom["offsets"] = [{"tag": "mxPoint", "attributes": {"x": "0", "y": "0"}}]
    resolved = resolve_native_label_inputs(raw)
    if label_choice is not None and not resolved.get("reason"):
        attrs, offset = candidate_label_geometry(resolved["path"], label_choice)
        geom["attributes"].update(attrs)
        geom["offsets"] = [{"tag": "mxPoint", "attributes": offset}]
        resolved = resolve_native_label_inputs(raw)
    # The signature includes structural flags, styles, terminal bounds, exact
    # serialized decimals and ordered hints. It is not a geometry rounding key.
    import hashlib
    import json
    resolved["input_signature"] = hashlib.sha256(json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=True).encode("utf-8")).hexdigest()
    return resolved


def preflight_candidate(profile, assignment, hints, label_choice=None):
    """Report path support independently of optional visible-label placement.

    A measurable default midpoint may have no usable carrier: callers should
    use its calibrated dimensions to search positions before requiring one.
    """
    inputs = project_candidate_native_inputs(profile, assignment, hints, label_choice)
    measured = measure_native_label(inputs)
    path_reason = inputs.get("reason")
    # Empty/hidden text does not turn unsupported edge styles into safe paths.
    # This extra candidate check leaves saved-XML measurement output unchanged.
    if not path_reason:
        path_reason = _native_style_reason(inputs.get("style", ""), "")
    path_available = not path_reason and len(inputs.get("path", [])) >= 2
    bounds = measured["bounds"]
    return {
        "path_status": "available" if path_available else "not_available",
        "path_reason": path_reason if path_reason else (None if path_available else "invalid_native_path"),
        "path": inputs.get("path", []), "label_status": measured["status"],
        "label_reason": measured["reason"], "profile": measured["profile"],
        "renderer_model": "31.3.2", "bounds_quality": measured["bounds_quality"],
        "size": (bounds["width"], bounds["height"]) if bounds else None,
        "bounds": bounds, "position": measured["position"],
        "carrier_segment": measured["carrier_segment"], "carrier_usable": measured["carrier_usable"],
        "input_signature": inputs["input_signature"], "measurement": measured,
    }


def label_path_conflicts(edge_id, carrier_index, box, edge_segments, *, gap=1.0):
    """IDs whose lines touch a label; only its own carrier segment is exempt."""
    return tuple(sorted({
        other_id for other_id, segments in edge_segments.items()
        if any(not (other_id == edge_id and index == carrier_index)
               and core_geometry.segment_intersects_box(segment, box, gap=gap)
               for index, segment in enumerate(segments))
    }))


def label_pair_conflicts(edge_id, box, label_boxes, *, gap=2.0):
    """Sorted other-label IDs, with the strict collector's original gap."""
    return tuple(sorted(other_id for other_id, other in label_boxes.items()
                        if other_id != edge_id and core_geometry.bounds_overlap(box, other, gap=gap)))


def label_placement_conflicts(edge_id, carrier_index, box, edge_segments,
                              node_bounds, label_boxes, container):
    """Pure placement checks over measured bounds, shared with strict geometry."""
    return {
        "blocking_edge_ids": list(label_path_conflicts(edge_id, carrier_index, box, edge_segments)),
        "blocking_label_ids": list(label_pair_conflicts(edge_id, box, label_boxes)),
        "blocking_node_ids": sorted(node_id for node_id, bounds in node_bounds.items()
                                    if core_geometry.bounds_overlap(box, bounds, gap=1.0)),
        "outside_container": container is not None and not (
            container["left"] <= box["left"] and box["right"] <= container["right"]
            and container["top"] <= box["top"] and box["bottom"] <= container["bottom"]
        ),
    }


def plan_label_batch(items, paths, node_bounds, container, *, frozen_labels=None,
                     preferred_sides=None, locked_choices=None,
                     max_label_pairs=32, max_batch_label_pairs=128):
    """Plan a bounded, atomic batch using measured native size and final bounds.

    Items are in caller-established routing order. A failed batch exposes no
    provisional choices. Previously locked choices and frozen boxes are never
    moved. Each blocked owner may revisit one actual movable label blocker;
    each pair of alternative positions consumes both local and batch budgets.
    This is finite placement feedback, not a general label-layout solver.
    """
    import copy
    if max_label_pairs <= 0 or max_batch_label_pairs < 0:
        raise ValueError("Label pair budgets must be positive")
    items = list(items)
    frozen = copy.deepcopy(frozen_labels or {})
    locked = copy.deepcopy(locked_choices or {})
    choices = dict(locked)
    placed = dict(frozen)
    placed.update({key: choice[1] for key, choice in locked.items() if choice is not None})
    segments = {key: list(zip(points, points[1:])) for key, points in paths.items()}
    candidates = {}
    pair_attempts = 0
    candidate_count = 0
    generated_candidates = 0
    stats = {}
    signatures = {}

    def record(edge_id, reason, conflicts=None):
        entry = stats.setdefault(edge_id, {"rejected_counts": {}, "blocking_edge_ids": set(),
                                          "blocking_label_ids": set(), "blocking_node_ids": set()})
        entry["rejected_counts"][reason] = entry["rejected_counts"].get(reason, 0) + 1
        if conflicts:
            for name in ("blocking_edge_ids", "blocking_label_ids", "blocking_node_ids"):
                entry[name].update(conflicts.get(name, ()))

    def fail(edge_id, reason, **details):
        entry = stats.get(edge_id, {})
        failure = {
            "reason": reason, "edge_id": edge_id, "stage": "label_batch",
            "blocking_edge_ids": sorted(entry.get("blocking_edge_ids", ())),
            "blocking_label_ids": sorted(entry.get("blocking_label_ids", ())),
            "blocking_node_ids": sorted(entry.get("blocking_node_ids", ())),
            "candidate_count": candidate_count, "generated_candidates": generated_candidates,
            "rejected_counts": dict(entry.get("rejected_counts", {})),
            "input_signatures": list(signatures.get(edge_id, ()))[:3],
            "input_signature_count": len(signatures.get(edge_id, ())),
            **details,
        }
        return {"status": "failed", "choices": {}, "failure": failure,
                "provisional_choices": copy.deepcopy({key: value for key, value in choices.items()
                                                     if key != edge_id}),
                "pair_attempts": pair_attempts, "candidate_count": candidate_count,
                "generated_candidates": generated_candidates}

    def clear(edge_id, choice, obstacles):
        nonlocal candidate_count
        candidate_count += 1
        conflicts = label_placement_conflicts(
            edge_id, choice[0], choice[1], segments, node_bounds, obstacles, container,
        )
        bad = False
        for field, reason in (("blocking_edge_ids", "edge_overlap"),
                              ("blocking_label_ids", "label_overlap"),
                              ("blocking_node_ids", "node_overlap"),
                              ("outside_container", "outside_container")):
            if conflicts[field]:
                bad = True
                record(edge_id, reason, conflicts)
        return not bad

    def prepare(item):
        nonlocal generated_candidates
        edge_id = item["id"]
        native = item["native"]
        signatures[edge_id] = [native.get("input_signature")]
        if native.get("path_status") != "available":
            return fail(edge_id, "native_profile_unavailable", native_reason=native.get("path_reason"))
        if native.get("label_status") == "not_applicable":
            candidates[edge_id] = [None]
            return None
        if native.get("label_status") != "available" or native.get("size") is None:
            return fail(edge_id, "native_profile_unavailable", native_reason=native.get("label_reason"))
        # Trust neither cached estimated boxes nor hints as rendered lines.
        # Every proposed native offset is independently projected and measured.
        path = native["path"]
        if list(paths.get(edge_id, ())) != list(path):
            return fail(edge_id, "native_path_mismatch")
        options = ordered_label_box_candidates(
            path, item["text"], (preferred_sides or {}).get(edge_id),
            item.get("route") == "back", size=native["size"],
        )
        # The native midpoint is a real finite placement candidate, not an
        # unchecked None fallback. Wide labels can straddle their own carrier
        # while remaining inside the pool and clear of every other object.
        if native.get("carrier_usable") and native.get("carrier_segment") is not None:
            options.append((native["carrier_segment"], dict(native["bounds"]), 0.0))
        result = []
        seen = set()
        for carrier, box, _ in options:
            generated_candidates += 1
            measured = preflight_candidate(item["profile"], item["assignment"], item["hints"], (carrier, box))
            signature = measured.get("input_signature")
            if signature not in signatures[edge_id]:
                signatures[edge_id].append(signature)
            if measured.get("path_status") != "available" or measured.get("label_status") != "available":
                return fail(edge_id, "native_profile_unavailable", native_reason=(
                    measured.get("path_reason") or measured.get("label_reason")))
            if list(measured["path"]) != list(path):
                return fail(edge_id, "native_path_mismatch")
            if not measured.get("carrier_usable") or measured.get("carrier_segment") is None:
                record(edge_id, "no_clear_carrier")
                continue
            actual_box = measured["bounds"]
            actual = (measured["carrier_segment"], dict(actual_box))
            key = (actual[0], *(actual_box[name] for name in ("left", "right", "top", "bottom")))
            if key not in seen:
                seen.add(key)
                result.append(actual)
        candidates[edge_id] = result
        return None

    for item in items:
        edge_id = item["id"]
        if edge_id in locked:
            # Locked choices are already measured native choices from the
            # successful dependency-external snapshot; preserve their bytes.
            continue
        if edge_id in frozen:
            return fail(edge_id, "frozen_label_not_mutable")
        failure = prepare(item)
        if failure is not None:
            return failure
        if candidates[edge_id] == [None]:
            choices[edge_id] = None
            continue
        selected = next((choice for choice in candidates[edge_id] if clear(edge_id, choice, placed)), None)
        if selected is None:
            actual_blockers = stats.get(edge_id, {}).get("blocking_label_ids", set())
            blocker = next((key for key in reversed(choices)
                            if key in actual_blockers and key not in locked and key not in frozen
                            and choices[key] is not None and key in candidates), None)
            local_pairs = 0
            if blocker is not None:
                obstacles = {key: box for key, box in placed.items() if key != blocker}
                repaired = False
                for alternative in candidates[blocker]:
                    if alternative == choices[blocker] or alternative is None:
                        continue
                    for choice in candidates[edge_id]:
                        if local_pairs >= max_label_pairs or pair_attempts >= max_batch_label_pairs:
                            return fail(edge_id, "budget_exhausted", budget=(
                                "label_pairs" if local_pairs >= max_label_pairs else "batch_label_pairs"),
                                used=local_pairs if local_pairs >= max_label_pairs else pair_attempts,
                                limit=max_label_pairs if local_pairs >= max_label_pairs else max_batch_label_pairs,
                                blocker=blocker)
                        local_pairs += 1
                        pair_attempts += 1
                        # Evaluate both positions without committing either.
                        first_ok = clear(blocker, alternative, obstacles)
                        second_ok = clear(edge_id, choice, {**obstacles, blocker: alternative[1]})
                        if first_ok and second_ok:
                            choices[blocker] = alternative
                            placed[blocker] = alternative[1]
                            selected = choice
                            repaired = True
                            break
                    if repaired:
                        break
            if selected is None:
                return fail(edge_id, "label_placement_unavailable")
        choices[edge_id] = selected
        placed[edge_id] = selected[1]

    # Recheck the completed batch, including locked labels, against every
    # final path and other label; the own carrier exception remains local.
    for edge_id, choice in choices.items():
        if choice is not None and not clear(edge_id, choice, placed):
            return fail(edge_id, "label_postcheck_failed")
    return {"status": "complete", "choices": choices, "failure": None,
            "pair_attempts": pair_attempts, "candidate_count": candidate_count,
            "generated_candidates": generated_candidates}
