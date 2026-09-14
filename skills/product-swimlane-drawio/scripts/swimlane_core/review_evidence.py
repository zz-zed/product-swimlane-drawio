"""Read-only typed visual observations and declared coordinate calibration.

A valid report is externally supplied data, never authenticated viewing or
permission to change an artifact. No referenced command or locator is run.
"""
from __future__ import annotations

import copy
import math
import re

from . import contracts, document

REVIEW_STATES = {"passed", "failed", "not_run", "not_available"}
REVIEW_CODES = {"visual/edge-label-collision", "visual/arrowhead-hidden", "visual/excessive-detour",
                "visual/node-text-clipped", "visual/main-path-unclear", "visual/crowding", "visual/uneven-spacing"}
REVIEW_MAX_OBJECTS = 4096
REVIEW_MAX_ISSUES = 2048
REVIEW_MAX_REFERENCES = 20_000


def review_failure(code, message, **evidence):
    raise contracts.DiagramError(message, code=code, subject={"kind": "visual_review"}, evidence=evidence)


def review_object(value, allowed, required=None):
    contracts.require_mapping(value, "visual review object")
    contracts.reject_unknown_fields(value, set(allowed), "visual review object")
    missing = sorted(set(allowed if required is None else required) - set(value))
    if missing:
        review_failure("schema/required", "Required visual review fields are missing", fields=missing)
    return value


def review_integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        review_failure("schema/type", "Expected a non-boolean integer within its range")
    return value


def review_number(value, *, positive=False):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except (ValueError, OverflowError):
        valid = False
    if not valid or (positive and value <= 0):
        review_failure("schema/type", "Expected a finite number within its range")
    return value


def review_text(value, *, nullable=False, maximum=8192):
    if nullable and value is None:
        return
    contracts.require_string(value, "visual review text")
    if not value.strip() or len(value) > maximum:
        review_failure("schema/type", "Visual review text is blank or exceeds its limit")


def review_digest(value, *, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        review_failure("schema/type", "Visual review digest must be lowercase SHA-256")


def review_enum(value, values, *, code="schema/type"):
    if not isinstance(value, str) or value not in values:
        review_failure(code, "Unsupported visual review value", value=value)


def review_version(value, expected=1):
    if type(value) is not int:
        review_failure("schema/type", "Review versions must be non-boolean integers")
    if value != expected:
        review_failure("review/version-unsupported", "Unsupported visual review contract version")


def review_target_key(target):
    return (("outcome", target["decision_id"], target["outcome_id"]) if target["kind"] == "outcome"
            else (target["kind"], target["id"]))


class ReviewValidator:
    def __init__(self):
        self.references = 0
        self.issue_count = 0

    def sid(self, value):
        self.references += 1
        if self.references > REVIEW_MAX_REFERENCES:
            review_failure("review/resource-limit", "Visual review ID reference budget exceeded")
        return contracts.validate_semantic_id(value, "visual review reference")

    def ids(self, values):
        contracts.require_list(values, "visual review references")
        for value in values:
            self.sid(value)
        if len(values) != len(set(values)):
            review_failure("schema/duplicate", "Duplicate visual review reference")

    def target(self, target):
        contracts.require_mapping(target, "review target")
        review_enum(target.get("kind"), {"node", "edge", "lane", "pool", "phase", "outcome"})
        keys = {"kind", "decision_id", "outcome_id"} if target["kind"] == "outcome" else {"kind", "id"}
        review_object(target, keys)
        for key in sorted(keys - {"kind"}):
            self.sid(target[key])
        return review_target_key(target)

    def report(self, data):
        review_object(data, {"visual_report_version", "run_id", "prepared_sha256", "artifact_sha256", "context_sha256",
                             "round_index", "export", "previews", "reviews"})
        review_version(data["visual_report_version"])
        review_text(data["run_id"], maximum=64)
        for key in ("prepared_sha256", "artifact_sha256", "context_sha256"):
            review_digest(data[key], nullable=key == "context_sha256")
        review_integer(data["round_index"])
        if data["round_index"] != 0:
            review_failure("review/version-unsupported", "E4 supports only initial round zero")
        previews = contracts.require_list(data["previews"], "previews")
        if len(previews) > 17:
            review_failure("review/resource-limit", "At most one full preview and 16 cutouts are supported")
        ids = []
        for preview in previews:
            review_object(preview, {"id", "kind", "name", "sha256", "bytes", "width", "height", "mapping"})
            ids.append(self.sid(preview["id"]))
            review_enum(preview["kind"], {"full", "cutout"})
            expected = "full.png" if preview["kind"] == "full" else "cutouts/" + preview["id"] + ".png"
            if preview["name"] != expected or (preview["kind"] == "full") != (preview["id"] == "full"):
                review_failure("review/reference-invalid", "Preview must use its exact allowed ID and relative filename")
            review_digest(preview["sha256"])
            for key in ("bytes", "width", "height"):
                review_integer(preview[key], minimum=1)
            self.mapping(preview)
        if len(ids) != len(set(ids)):
            review_failure("schema/duplicate", "Duplicate preview identity")
        if previews and sum(item["kind"] == "full" for item in previews) != 1:
            review_failure("review/report-inconsistent", "Image evidence requires one full preview entry")
        self.export(data["export"])
        review_object(data["reviews"], {"agent", "human"})
        for kind in ("agent", "human"):
            self.reviewer(data["reviews"][kind], kind)

    def mapping(self, preview):
        mapping = preview["mapping"]
        if mapping is None and preview["kind"] == "full":
            return
        if mapping is None:
            review_failure("review/mapping-invalid", "Cutouts require an explicit transform to the full image")
        fields = ({"mapping_version", "kind", "scale_x", "scale_y", "translate_x", "translate_y", "calibration_id"}
                  if preview["kind"] == "full" else {"mapping_version", "kind", "full_preview_sha256", "crop", "scale_x", "scale_y"})
        review_object(mapping, fields)
        review_version(mapping["mapping_version"])
        review_enum(mapping["kind"], {"axis_aligned"} if preview["kind"] == "full" else {"cutout"}, code="review/mapping-invalid")
        for key in ("scale_x", "scale_y"):
            review_number(mapping[key], positive=True)
        if preview["kind"] == "full":
            self.sid(mapping["calibration_id"])
            review_number(mapping["translate_x"])
            review_number(mapping["translate_y"])
        else:
            review_digest(mapping["full_preview_sha256"])
            review_rect(mapping["crop"])

    def export(self, value):
        if value is None:
            return
        review_object(value, {"state", "reason", "renderer", "command", "exit_code", "input_before_sha256", "input_after_sha256",
                              "full_preview_sha256", "stdout", "stderr", "calibration"})
        review_enum(value["state"], {"passed", "failed", "not_available"})
        review_text(value["reason"], nullable=True)
        if value["exit_code"] is not None:
            if type(value["exit_code"]) is not int:
                review_failure("schema/type", "Exporter exit code must be an integer or null")
        for key in ("input_before_sha256", "input_after_sha256", "full_preview_sha256"):
            review_digest(value[key], nullable=True)
        renderer = value["renderer"]
        if renderer is not None:
            review_object(renderer, {"name", "version", "profile_id", "fonts"})
            for key in ("name", "version", "profile_id"):
                review_text(renderer[key], nullable=True, maximum=256)
            if renderer["fonts"] is not None:
                fonts = contracts.require_list(renderer["fonts"], "fonts")
                if len(fonts) > 128:
                    review_failure("review/resource-limit", "Too many declared fonts")
                for font in fonts:
                    review_text(font, maximum=256)
        if value["command"] is not None:
            command = contracts.require_list(value["command"], "export command")
            if not command or len(command) > 128:
                review_failure("review/resource-limit", "Export command argument budget exceeded")
            for arg in command:
                review_text(arg, maximum=4096)
        for key in ("stdout", "stderr"):
            if value[key] is not None:
                review_object(value[key], {"name", "sha256", "bytes"})
                if value[key]["name"] != "export." + key + ".txt":
                    review_failure("review/reference-invalid", "Exporter logs require their fixed local filenames")
                review_digest(value[key]["sha256"])
                review_integer(value[key]["bytes"])
                if value[key]["bytes"] > 1024 * 1024:
                    review_failure("review/resource-limit", "Exporter log exceeds its byte budget")
        calibration = value["calibration"]
        if calibration is not None:
            review_object(calibration, {"calibration_version", "id", "profile_id", "artifact_sha256", "preview_sha256",
                                         "method", "anchors", "tolerance_px"})
            review_version(calibration["calibration_version"])
            self.sid(calibration["id"])
            review_text(calibration["profile_id"], maximum=256)
            review_digest(calibration["artifact_sha256"])
            review_digest(calibration["preview_sha256"])
            review_enum(calibration["method"], {"externally_observed_anchors"}, code="review/mapping-invalid")
            if review_number(calibration["tolerance_px"]) != 2:
                review_failure("review/mapping-invalid", "Calibration tolerance is fixed at two pixels")
            anchors = contracts.require_list(calibration["anchors"], "calibration anchors")
            if not 3 <= len(anchors) <= 64:
                review_failure("review/mapping-invalid", "Calibration needs between three and 64 anchors")
            for anchor in anchors:
                review_object(anchor, {"target", "anchor", "pixel"})
                self.target(anchor["target"])
                review_enum(anchor["anchor"], {"top_left", "top_right", "bottom_left", "bottom_right", "center"})
                review_object(anchor["pixel"], {"x", "y"})
                for number in anchor["pixel"].values():
                    review_number(number)

    def reviewer(self, value, kind):
        review_object(value, {"state", "reason", "reviewer", "full_image_viewed", "viewed_previews", "issues"})
        review_enum(value["state"], REVIEW_STATES)
        review_text(value["reason"], nullable=True)
        if type(value["full_image_viewed"]) is not bool:
            review_failure("schema/type", "full_image_viewed must be a boolean")
        self.ids(value["viewed_previews"])
        reviewer = value["reviewer"]
        if reviewer is not None:
            review_object(reviewer, {"kind", "name", "host", "model", "tool_receipt_ids"})
            if reviewer["kind"] != kind:
                review_failure("review/report-inconsistent", "Reviewer kind differs from its evidence layer")
            for key in ("name", "host", "model"):
                review_text(reviewer[key], nullable=True, maximum=256)
            self.ids(reviewer["tool_receipt_ids"])
        issues = contracts.require_list(value["issues"], "visual issues")
        self.issue_count += len(issues)
        if self.issue_count > REVIEW_MAX_ISSUES:
            review_failure("review/resource-limit", "Visual issue budget exceeded")
        ids = []
        for issue in issues:
            review_object(issue, {"issue_id", "code", "severity", "targets", "preview_id", "preview_sha256", "region",
                                  "observation", "suggested_intent", "binding", "uncertainty"})
            ids.append(self.sid(issue["issue_id"]))
            review_enum(issue["code"], REVIEW_CODES, code="review/version-unsupported")
            review_enum(issue["severity"], {"blocker", "warning", "note"})
            targets = contracts.require_list(issue["targets"], "issue targets")
            if not targets or len(targets) > 32:
                review_failure("review/resource-limit", "Each issue needs between one and 32 typed targets")
            keys = [self.target(target) for target in targets]
            if len(keys) != len(set(keys)):
                review_failure("schema/duplicate", "Duplicate issue target")
            self.sid(issue["preview_id"])
            review_digest(issue["preview_sha256"])
            review_rect(issue["region"])
            review_text(issue["observation"])
            review_text(issue["uncertainty"], nullable=True)
            review_enum(issue["binding"], {"bound", "ambiguous"})
            if issue["binding"] == "ambiguous" and issue["uncertainty"] is None:
                review_failure("review/report-inconsistent", "Ambiguous binding requires uncertainty")
            if issue["suggested_intent"] is not None:
                review_enum(issue["suggested_intent"], {"reposition-edge-label", "reroute-edge", "manual-review"})
                if issue["suggested_intent"] != "manual-review" and any(target["kind"] != "edge" for target in targets):
                    review_failure("review/report-inconsistent", "Edge repair suggestions require edge targets")
        if len(ids) != len(set(ids)):
            review_failure("schema/duplicate", "Duplicate issue identity within one reviewer")


def review_rect(value):
    review_object(value, {"x", "y", "width", "height"})
    for key, number in value.items():
        review_number(number, positive=key in {"width", "height"})
    return value


def review_inside(rect, width, height):
    return (rect["x"] >= 0 and rect["y"] >= 0 and rect["x"] + rect["width"] <= width
            and rect["y"] + rect["height"] <= height)


def review_index_finite(index):
    for entry in index["objects"]:
        for box in (entry["bounds"], entry["label_bounds"]):
            if box is not None:
                for value in (*box.values(), box["x"] + box["width"], box["y"] + box["height"]):
                    review_number(value)
        for point in entry["path"] or []:
            for value in point.values():
                review_number(value)


def review_object_index(tree, validation):
    cells = document.semantic_cells(tree)
    pool = document.find_pool(tree)
    root = document.graph_root(tree)
    lanes, nodes = document.lane_node_records(root, pool)
    by_native = {cell.get("id"): cell for cell in root if cell.tag == "mxCell"}
    positions = {}

    def origin(cell, visiting=None):
        sid = cell.get("id")
        if sid in positions:
            return positions[sid]
        visiting = set() if visiting is None else set(visiting)
        if sid in visiting:
            review_failure("context/artifact-invalid", "Cyclic native parent relation")
        visiting.add(sid)
        parent = by_native.get(cell.get("parent"))
        px, py = origin(parent, visiting) if parent is not None else (0.0, 0.0)
        geom = cell.find("mxGeometry")
        x, y = ((float(geom.get("x", "0")), float(geom.get("y", "0"))) if geom is not None else (0.0, 0.0))
        positions[sid] = (px + x, py + y)
        for value in positions[sid]:
            review_number(value)
        return positions[sid]

    objects, outcomes = [], {}
    pool_x, pool_y = origin(pool)
    for cell in cells.values():
        kind, sid = cell.get(contracts.DATA_KIND), cell.get(contracts.DATA_SEMANTIC_ID)
        if kind not in {"pool", "lane", "node", "edge", "phase"}:
            continue
        entry = {"target": {"kind": kind, "id": sid}, "native_id": cell.get("id"),
                 "bounds": None, "path": None, "label_bounds": None, "related_targets": []}
        if kind == "edge":
            path = document.edge_polyline(cell, lanes, nodes)
            entry["path"] = [{"x": x + pool_x, "y": y + pool_y} for x, y in path]
            measurement = document.edge_label_measurement(cell, path, {"lanes": lanes, "nodes": nodes, "pool": pool})
            box = measurement.get("bounds")
            if box is not None:
                entry["label_bounds"] = {"x": box["left"] + pool_x, "y": box["top"] + pool_y,
                                         "width": box["width"], "height": box["height"]}
            outcome = cell.get(contracts.DATA_OUTCOME)
            decision = cell.get(contracts.DATA_FROM)
            if outcome and decision in nodes and nodes[decision]["cell"].get(contracts.DATA_NODE_TYPE) == "decision":
                outcomes.setdefault((decision, outcome), []).append({"kind": "edge", "id": sid})
        else:
            x, y = origin(cell)
            geometry = document.parse_geometry(cell)
            entry["bounds"] = {"x": x, "y": y, "width": geometry["width"], "height": geometry["height"]}
        objects.append(entry)
    for (decision, outcome), related in outcomes.items():
        objects.append({"target": {"kind": "outcome", "decision_id": decision, "outcome_id": outcome},
                        "native_id": None, "bounds": None, "path": None, "label_bounds": None,
                        "related_targets": sorted(related, key=review_target_key) + [{"kind": "node", "id": decision}]})
    if len(objects) > REVIEW_MAX_OBJECTS:
        review_failure("review/resource-limit", "Visual object index exceeds its budget")
    index = {"object_index_version": 1, "coordinate_system": "mxgraph-root",
             "objects": sorted(objects, key=lambda item: review_target_key(item["target"])), "validation": validation}
    review_index_finite(index)
    return index


def review_project_point(point, mapping):
    return (point[0] * mapping["scale_x"] + mapping["translate_x"],
            point[1] * mapping["scale_y"] + mapping["translate_y"])


def review_anchor(box, name):
    fx = 0.5 if name == "center" else 1.0 if name.endswith("right") else 0.0
    fy = 0.5 if name == "center" else 1.0 if name.startswith("bottom") else 0.0
    return box["x"] + box["width"] * fx, box["y"] + box["height"] * fy


def review_calibration(calibration, mapping, full, export, objects, artifact_sha256):
    if calibration is None or (calibration["id"] != mapping["calibration_id"]
            or calibration["artifact_sha256"] != artifact_sha256 or calibration["preview_sha256"] != full["sha256"]
            or export["renderer"] is None or calibration["profile_id"] != export["renderer"]["profile_id"]):
        review_failure("review/mapping-invalid", "Calibration identity/profile differs from its actual full preview")
    pixels, projections, keys = [], [], set()
    for anchor in calibration["anchors"]:
        key = review_target_key(anchor["target"])
        target = objects.get(key)
        if target is None or target["bounds"] is None:
            review_failure("review/reference-invalid", "Calibration needs a typed target with original native bounds")
        point = review_project_point(review_anchor(target["bounds"], anchor["anchor"]), mapping)
        pixel = anchor["pixel"]["x"], anchor["pixel"]["y"]
        if (not 0 <= pixel[0] <= full["width"] or not 0 <= pixel[1] <= full["height"]
                or any(not math.isfinite(value) for value in point)
                or abs(point[0] - pixel[0]) > 2 or abs(point[1] - pixel[1]) > 2):
            review_failure("review/mapping-invalid", "Observed calibration anchor differs from the original geometry projection")
        pixels.append(pixel)
        projections.append(point)
        keys.add(key)
    non_collinear = []
    for points in (pixels, projections):
        distinct = list(dict.fromkeys(points))
        established = False
        if len(distinct) >= 3:
            ax, ay = distinct[0]
            bx, by = distinct[1]
            established = any(abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) > 1e-6 for cx, cy in distinct[2:])
        non_collinear.append(established)
    if (len(keys) < 2 or not all(non_collinear) or max(x for x, _ in pixels) - min(x for x, _ in pixels) < 10
            or max(y for _, y in pixels) - min(y for _, y in pixels) < 10):
        review_failure("review/mapping-invalid", "Calibration anchors do not establish two-dimensional mapping")


def review_segment_intersects(a, b, region):
    """Liang-Barsky intersection, with the fixed two-pixel stroke tolerance."""
    left, right = region["x"] - 2, region["x"] + region["width"] + 2
    top, bottom = region["y"] - 2, region["y"] + region["height"] + 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, a[0] - left), (dx, right - a[0]), (-dy, a[1] - top), (dy, bottom - a[1])):
        if p == 0:
            if q < 0:
                return False
        elif p < 0:
            low = max(low, q / p)
        else:
            high = min(high, q / p)
        if low > high:
            return False
    return True


def review_target_intersects(target, mapping, region, objects):
    if target["related_targets"]:
        return any(review_target_intersects(objects[review_target_key(related)], mapping, region, objects)
                   for related in target["related_targets"])
    for box in (target["bounds"], target["label_bounds"]):
        if box is None:
            continue
        x, y = review_project_point((box["x"], box["y"]), mapping)
        width, height = box["width"] * mapping["scale_x"], box["height"] * mapping["scale_y"]
        if (all(math.isfinite(value) for value in (x, y, width, height))
                and x <= region["x"] + region["width"] + 2 and x + width >= region["x"] - 2
                and y <= region["y"] + region["height"] + 2 and y + height >= region["y"] - 2):
            return True
    path = [review_project_point((p["x"], p["y"]), mapping) for p in (target["path"] or [])]
    return (all(math.isfinite(value) for point in path for value in point)
            and any(review_segment_intersects(a, b, region) for a, b in zip(path, path[1:])))


def assess_visual_report(data, run, index, prepared_sha256, report_sha256):
    ReviewValidator().report(data)
    if (data["run_id"] != run["run_id"] or data["prepared_sha256"] != prepared_sha256
            or data["artifact_sha256"] != run["artifact"]["sha256"] or data["context_sha256"] != run["context"]["sha256"]):
        review_failure("review/baseline-mismatch", "Visual report differs from the frozen graph/context/prepared identity")
    previews = {item["id"]: item for item in data["previews"]}
    full = previews.get("full")
    export = data["export"]
    objects = {review_target_key(item["target"]): item for item in index["objects"]}
    export_state = "not_run" if export is None else export["state"]
    if export is None:
        if previews:
            review_failure("review/report-inconsistent", "Preview bytes need an explicit exporter declaration")
    else:
        for key in ("input_before_sha256", "input_after_sha256"):
            if export[key] is not None and export[key] != run["artifact"]["sha256"]:
                review_failure("review/baseline-mismatch", "Exporter saved or read a different artifact")
        if export["state"] == "passed":
            if (export["exit_code"] != 0 or export["command"] is None or export["renderer"] is None
                    or any(export["renderer"][key] is None for key in ("name", "version", "profile_id"))
                    or export["input_before_sha256"] != run["artifact"]["sha256"]
                    or export["input_after_sha256"] != run["artifact"]["sha256"]
                    or full is None or export["full_preview_sha256"] != full["sha256"]):
                review_failure("review/report-inconsistent", "Passed export requires a full image and complete actual identity declarations")
        elif export["reason"] is None:
            review_failure("review/report-inconsistent", "Unavailable or failed export requires a reason")
        if export["state"] == "not_available" and (previews or any(export[key] is not None for key in
                ("command", "exit_code", "full_preview_sha256", "calibration"))):
            review_failure("review/report-inconsistent", "Unavailable exporter cannot claim a call or preview")
    mapping = full["mapping"] if full else None
    if mapping is not None:
        if export_state != "passed":
            review_failure("review/mapping-invalid", "Calibrated mapping requires a passed full export")
        review_calibration(export["calibration"], mapping, full, export, objects, run["artifact"]["sha256"])
    elif export is not None and export["calibration"] is not None:
        review_failure("review/mapping-invalid", "Calibration cannot be detached from a full mapping")
    mapping_receipt = []
    for preview in sorted(previews.values(), key=lambda item: item["id"]):
        if preview["kind"] == "cutout":
            transform = preview["mapping"]
            crop = transform["crop"]
            if (full is None or transform["full_preview_sha256"] != full["sha256"]
                    or not review_inside(crop, full["width"], full["height"])
                    or abs(crop["width"] * transform["scale_x"] - preview["width"]) > 1
                    or abs(crop["height"] * transform["scale_y"] - preview["height"]) > 1):
                review_failure("review/mapping-invalid", "Cutout transform does not match its full image or pixel dimensions")
        mapping_receipt.append({"preview_id": preview["id"], "verification": "declared_anchors_consistent" if preview["kind"] == "full" and mapping else
                                "declared_transform_and_bounds" if preview["kind"] == "cutout" else "unverified_spatial"})
    all_issues, coverage = [], {}
    for kind in ("agent", "human"):
        review = data["reviews"][kind]
        state = review["state"]
        if set(review["viewed_previews"]) - set(previews):
            review_failure("review/reference-invalid", "Reviewed image reference is absent")
        if state in {"not_run", "not_available"}:
            if review["issues"] or review["viewed_previews"] or review["full_image_viewed"] or review["reviewer"] is not None:
                review_failure("review/report-inconsistent", "Unperformed review cannot claim observations or viewing")
            if state == "not_available" and review["reason"] is None:
                review_failure("review/report-inconsistent", "Unavailable review requires a reason")
        else:
            if review["reviewer"] is None or export_state != "passed" or not review["viewed_previews"]:
                review_failure("review/report-inconsistent", "An actual review requires declared reviewer and viewed successful preview")
            if review["full_image_viewed"] != ("full" in review["viewed_previews"]):
                review_failure("review/report-inconsistent", "Full-image coverage flag differs from viewed preview entries")
            unresolved = any(issue["severity"] in {"blocker", "warning"} for issue in review["issues"])
            if state == "passed" and (not review["full_image_viewed"] or unresolved):
                review_failure("review/report-inconsistent", "Passed review requires whole-image coverage without blocker/warning")
            if state == "failed" and not unresolved and review["reason"] is None:
                review_failure("review/report-inconsistent", "Failed review without blocking issues requires a reason")
        referenced = set()
        for issue in review["issues"]:
            preview = previews.get(issue["preview_id"])
            if (preview is None or issue["preview_id"] not in review["viewed_previews"]
                    or preview["sha256"] != issue["preview_sha256"]):
                review_failure("review/reference-invalid", "Issue points outside its actually declared reviewed images")
            if not review_inside(issue["region"], preview["width"], preview["height"]):
                review_failure("review/mapping-invalid", "Issue region is outside the actual image")
            region = dict(issue["region"])
            if preview["kind"] == "cutout":
                transform = preview["mapping"]
                region = {"x": region["x"] / transform["scale_x"] + transform["crop"]["x"],
                          "y": region["y"] / transform["scale_y"] + transform["crop"]["y"],
                          "width": region["width"] / transform["scale_x"], "height": region["height"] / transform["scale_y"]}
            derived = False
            for target in issue["targets"]:
                key = review_target_key(target)
                if key not in objects:
                    review_failure("review/reference-invalid", "Issue typed object does not exist", target=target)
                referenced.add(key)
                derived = derived or bool(objects[key]["related_targets"])
                if mapping and issue["binding"] == "bound" and not review_target_intersects(objects[key], mapping, region, objects):
                    review_failure("review/mapping-invalid", "Bound issue region does not intersect the projected target", target=target)
            all_issues.append({"reviewer_kind": kind, **copy.deepcopy(issue),
                               "binding_status": "ambiguous" if issue["binding"] == "ambiguous" else "spatial_bound" if mapping else "identity_bound",
                               "spatial_verification": "ambiguous" if issue["binding"] == "ambiguous" else "projected_intersection" if mapping else "unverified_spatial",
                               "derived_geometry": derived})
        coverage[kind] = {"full_image_viewed": review["full_image_viewed"], "viewed_previews": sorted(review["viewed_previews"]),
                          "typed_objects": [list(key) for key in sorted(referenced)],
                          "regions": [{"issue_id": issue["issue_id"], "preview_id": issue["preview_id"], "region": copy.deepcopy(issue["region"])}
                                      for issue in sorted(review["issues"], key=lambda item: item["issue_id"])]}
    active = [review["state"] for review in data["reviews"].values() if review["state"] in {"passed", "failed"}]
    conflict = len(set(active)) > 1
    agent = {issue["issue_id"]: issue for issue in data["reviews"]["agent"]["issues"]}
    for issue in data["reviews"]["human"]["issues"]:
        other = agent.get(issue["issue_id"])
        if other and (other["code"] != issue["code"] or other["severity"] != issue["severity"]
                      or {review_target_key(t) for t in other["targets"]} != {review_target_key(t) for t in issue["targets"]}):
            conflict = True
    return {"review_receipt_version": 1, "run_id": run["run_id"], "prepared_sha256": prepared_sha256,
            "artifact_sha256": run["artifact"]["sha256"], "context_sha256": run["context"]["sha256"], "round_index": 0,
            "report_sha256": report_sha256, "report_validation": "passed", "trust": "externally_supplied",
            "states": {"strict_validation": run["validation"]["strict_validation"], "preview_export": export_state,
                       "agent_image_review": data["reviews"]["agent"]["state"], "human_review": data["reviews"]["human"]["state"]},
            "coverage": coverage, "mapping": mapping_receipt, "issues": sorted(all_issues, key=lambda item: (item["reviewer_kind"], item["issue_id"])),
            "review_consensus": "conflict" if conflict else "agreement" if len(active) == 2 else "single_reviewer" if active else "not_assessed",
            "repair_support": "not_available", "can_repair": False}
