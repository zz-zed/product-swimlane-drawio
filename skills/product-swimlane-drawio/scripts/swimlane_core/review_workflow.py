"""Explicit immutable prepare/record adapters; never render, view, or repair."""
from __future__ import annotations

import os
import json
import uuid

from . import (context_bundle, context_native, contracts, document, preview_png,
               review_evidence, semantic_context, validation)

REVIEW_LOG_BYTES = 1024 * 1024
REVIEW_GRAPH_BYTES = 16 * 1024 * 1024


def review_json_bytes(data):
    try:
        raw = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, OverflowError, UnicodeError, RecursionError):
        review_evidence.review_failure("schema/type", "Derived review JSON is not finite or safely encodable")
    semantic_context.decode_context(raw, validate=False)
    return raw


def review_read_evidence(path, *, max_bytes, member=False):
    try:
        return context_bundle.bundle_read_input(path, max_bytes=max_bytes)
    except contracts.DiagramError as exc:
        if exc.code == "context/resource-limit":
            review_evidence.review_failure("review/resource-limit", "Review evidence exceeds its byte budget", limit=max_bytes)
        if member and exc.code == "delivery/path-unsafe" and exc.evidence.get("reason") == "FileNotFoundError":
            review_evidence.review_failure("delivery/bundle-incomplete", "An explicitly listed review member is missing")
        raise


def review_read_json(path):
    item = review_read_evidence(path, max_bytes=semantic_context.MAX_BYTES)
    data = semantic_context.decode_context(item.raw, validate=False).data
    return contracts.require_mapping(data, "visual review object"), item


def review_unique_inputs(inputs):
    """Repeated use of the same explicit path is one input; aliases stay invalid."""
    paths = {}
    for item in inputs:
        previous = paths.get(item.path)
        if previous is not None and previous != item:
            review_evidence.review_failure("delivery/input-changed", "One explicit input changed between reads")
        paths[item.path] = item
    result = list(paths.values())
    context_bundle.bundle_check_distinct(result)
    return result


def review_input_expected(item, expected, prefix):
    if expected is None:
        review_evidence.review_failure("delivery/" + prefix + "-sha256-required", "Review requires explicit reviewed input digests")
    review_evidence.review_digest(expected)
    if item.sha256 != expected:
        review_evidence.review_failure("delivery/" + prefix + "-sha256-mismatch", "Review input differs from its reviewed SHA-256")


def review_read_baseline(graph_path, context_path=None, manifest_path=None):
    graph = context_bundle.bundle_read_input(graph_path, max_bytes=REVIEW_GRAPH_BYTES)
    tree = context_native.parse_artifact(graph.raw)
    model = context_native.original_model(tree)
    result = validation.validate_tree(tree)
    inputs, context, manifest, assessment = [graph], None, None, None
    if context_path is None:
        if manifest_path is not None:
            review_evidence.review_failure("input/invalid", "An input completion manifest requires its explicit context")
    else:
        context = context_bundle.bundle_read_input(context_path, max_bytes=semantic_context.MAX_BYTES)
        loaded = semantic_context.decode_context(context.raw)
        inputs.append(context)
        if "provenance" in loaded.data and manifest_path is None:
            review_evidence.review_failure("delivery/bundle-required", "Bound provenance requires its explicit completion manifest")
        if manifest_path is not None:
            manifest = context_bundle.verify_bundle(manifest_path, graph, context)
            inputs.append(manifest)
        assessment = semantic_context.assess_context(loaded, tree, graph.sha256, result)
    context_bundle.bundle_check_distinct(inputs)
    return {"tree": tree, "model": model, "validation": result, "graph": graph, "context": context,
            "manifest": manifest, "assessment": assessment, "inputs": inputs}


def review_initial_run(baseline, run_id):
    graph, context, manifest = baseline["graph"], baseline["context"], baseline["manifest"]
    model, result = baseline["model"], baseline["validation"]
    return {"review_contract_version": 1, "run_format_version": 1, "run_id": run_id, "tool_version": contracts.TOOL_VERSION,
            "artifact": {"sha256": graph.sha256, "bytes": len(graph.raw), "schema_version": model["schema_version"],
                         "model_hash_version": model["model_hash_version"],
                         "model_hash": document.find_pool(baseline["tree"]).get(contracts.DATA_MODEL_HASH)},
            "context": {"status": "not_provided" if context is None else "provided",
                        "sha256": None if context is None else context.sha256, "bytes": None if context is None else len(context.raw),
                        "completion_sha256": None if manifest is None else manifest.sha256},
            "round": {"index": 0, "kind": "initial", "parent_record_sha256": None, "repair_attempt": None},
            "repair": {"support": "not_available", "maximum_attempts": 2, "attempts_started": 0, "authorization_sha256": None},
            "original": {"artifact_sha256": graph.sha256, "context_sha256": None if context is None else context.sha256},
            "last_accepted": None, "last_good": None,
            "validation": {"strict_validation": "passed" if result["valid"] and not result["warnings"] else "failed",
                           "preview_export": "not_run", "agent_image_review": "not_run", "human_review": "not_run"},
            "context_assessment": baseline["assessment"]}


def review_member(name, raw, *, limit=semantic_context.MAX_BYTES):
    if len(raw) > limit:
        review_evidence.review_failure("review/resource-limit", "Review package member exceeds its byte budget", name=name, limit=limit)
    return {"name": name, "raw": raw, "limit": limit}


def review_read_package(path, *, kind):
    review_evidence.review_enum(kind, {"prepared", "record"}, code="delivery/bundle-invalid")
    try:
        data, manifest = review_read_json(path)
    except contracts.DiagramError as exc:
        if exc.code == "delivery/path-unsafe" and exc.evidence.get("reason") == "FileNotFoundError":
            review_evidence.review_failure("delivery/bundle-incomplete", "Explicit review completion manifest is missing")
        raise
    if manifest.path.name != "completion.json":
        review_evidence.review_failure("delivery/bundle-invalid", "Review completion filename must be completion.json")
    fields = {"review_bundle_version", "kind", "run_id", "members"}
    if kind == "record":
        fields.add("prepared_sha256")
    review_evidence.review_object(data, fields)
    review_evidence.review_version(data["review_bundle_version"])
    if data["kind"] != kind:
        review_evidence.review_failure("delivery/bundle-invalid", "Review completion has the wrong package kind")
    try:
        identifier = uuid.UUID(data["run_id"])
    except (ValueError, TypeError, AttributeError):
        review_evidence.review_failure("schema/type", "Review run identity must be a canonical UUID4")
    if identifier.version != 4 or str(identifier) != data["run_id"]:
        review_evidence.review_failure("schema/type", "Review run identity must be a canonical UUID4")
    if kind == "record":
        review_evidence.review_digest(data["prepared_sha256"])
    entries = contracts.require_list(data["members"], "review completion members")
    if len(entries) > 24:
        review_evidence.review_failure("review/resource-limit", "Review package has too many members")
    names = []
    for entry in entries:
        review_evidence.review_object(entry, {"name", "sha256", "bytes"})
        review_evidence.review_text(entry["name"], maximum=512)
        review_evidence.review_digest(entry["sha256"])
        review_evidence.review_integer(entry["bytes"])
        names.append(entry["name"])
    if names != sorted(set(names)):
        review_evidence.review_failure("delivery/bundle-invalid", "Review completion members must be unique and sorted")
    if kind == "prepared":
        required = {"run.json", "objects.json", "original/diagram.drawio"}
        allowed = required | {"original/context.json", "original/completion.json"}
        if not required <= set(names) <= allowed or ("original/completion.json" in names and "original/context.json" not in names):
            review_evidence.review_failure("delivery/bundle-invalid", "Prepared package has an invalid member set")
    else:
        required = {"report.json", "receipt.json", "prepared.json"}
        allowed = required | {"full.png", "export.stdout.txt", "export.stderr.txt"}
        for name in names:
            if name.startswith("cutouts/") and name.endswith(".png"):
                contracts.validate_semantic_id(name[len("cutouts/"):-4], "cutout file ID")
                allowed.add(name)
        if not required <= set(names) <= allowed:
            review_evidence.review_failure("delivery/bundle-invalid", "Recorded package has an invalid member set")
    members, total = {}, 0
    for entry in entries:
        name = entry["name"]
        limit = (preview_png.PNG_MAX_BYTES if name.endswith(".png") else REVIEW_LOG_BYTES if name.endswith(".txt")
                 else REVIEW_GRAPH_BYTES if name.endswith(".drawio") else semantic_context.MAX_BYTES)
        item = review_read_evidence(manifest.path.parent / name, max_bytes=limit, member=True)
        if len(item.raw) != entry["bytes"] or item.sha256 != entry["sha256"]:
            review_evidence.review_failure("delivery/bundle-incomplete", "Review completion differs from actual member bytes", name=name)
        if name.endswith(".png"):
            total += len(item.raw)
        members[name] = item
    if total > preview_png.PNG_TOTAL_BYTES:
        review_evidence.review_failure("review/resource-limit", "Total PNG byte budget exceeded")
    all_inputs = [manifest, *members.values()]
    context_bundle.bundle_check_distinct(all_inputs)
    for item in all_inputs:
        item.verify()
    return data, manifest, members


def review_verify_published(path, written):
    data, manifest = review_read_json(path)
    _, checked, members = review_read_package(path, kind=data.get("kind"))
    if {item.path: item.raw for item in written} != {item.path: item.raw for item in members.values()}:
        review_evidence.review_failure("delivery/candidate-preservation-failed", "Published review members differ from validated candidates")
    if manifest != checked:
        review_evidence.review_failure("delivery/input-changed", "Review completion changed while being verified")
    return checked


def review_load_prepared(path):
    data, manifest, members = review_read_package(path, kind="prepared")
    run = semantic_context.decode_context(members["run.json"].raw, validate=False).data
    index = semantic_context.decode_context(members["objects.json"].raw, validate=False).data
    baseline = review_read_baseline(members["original/diagram.drawio"].path,
                                   members["original/context.json"].path if "original/context.json" in members else None,
                                   members["original/completion.json"].path if "original/completion.json" in members else None)
    expected_run = review_initial_run(baseline, data["run_id"])
    expected_index = review_evidence.review_object_index(baseline["tree"], baseline["validation"])
    if (review_json_bytes(run) != review_json_bytes(expected_run)
            or review_json_bytes(index) != review_json_bytes(expected_index)):
        review_evidence.review_failure("review/baseline-mismatch", "Prepared state/index does not match its original graph and context")
    return run, index, manifest, review_unique_inputs([manifest, *members.values(), *baseline["inputs"]])


def review_prepare(args):
    baseline = review_read_baseline(args.input, args.context, args.input_completion_manifest)
    review_input_expected(baseline["graph"], args.expected_input_sha256, "input")
    if baseline["context"] is not None:
        review_input_expected(baseline["context"], args.expected_context_sha256, "context")
    run = review_initial_run(baseline, str(uuid.uuid4()))
    index = review_evidence.review_object_index(baseline["tree"], baseline["validation"])
    members = [review_member("run.json", review_json_bytes(run)),
               review_member("objects.json", review_json_bytes(index)),
               review_member("original/diagram.drawio", baseline["graph"].raw, limit=REVIEW_GRAPH_BYTES)]
    if baseline["context"] is not None:
        members.append(review_member("original/context.json", baseline["context"].raw))
    if baseline["manifest"] is not None:
        members.append(review_member("original/completion.json", baseline["manifest"].raw))
    delivery = context_bundle.bundle_publish_members(args.output, sorted(members, key=lambda item: item["name"]),
                {"review_bundle_version": 1, "kind": "prepared", "run_id": run["run_id"]}, baseline["inputs"], review_verify_published)
    return {"operation": "review", "action": "prepare", "valid": True, "run_id": run["run_id"],
            "states": run["validation"], "semantic_context": baseline["assessment"],
            "object_count": len(index["objects"]), "repair_support": "not_available", "delivery": delivery}, 0


def review_record(args):
    run, index, prepared, inputs = review_load_prepared(args.prepared)
    review_input_expected(prepared, args.expected_prepared_sha256, "prepared")
    baseline = review_read_baseline(args.input, args.context, args.input_completion_manifest)
    review_input_expected(baseline["graph"], args.expected_input_sha256, "input")
    if baseline["context"] is not None:
        review_input_expected(baseline["context"], args.expected_context_sha256, "context")
    current = review_initial_run(baseline, run["run_id"])
    if review_json_bytes(current) != review_json_bytes(run):
        review_evidence.review_failure("review/baseline-mismatch", "Current artifact/context differs from the prepared review baseline")
    inputs.extend(baseline["inputs"])
    evidence_dir = context_bundle.bundle_absolute(args.evidence_dir)
    descriptor, _ = context_bundle.bundle_directory(evidence_dir)
    os.close(descriptor)
    if context_bundle.bundle_absolute(args.report) != evidence_dir / "report.json":
        review_evidence.review_failure("delivery/path-unsafe", "Review report must be report.json in the explicit evidence directory")
    report, report_input = review_read_json(args.report)
    review_input_expected(report_input, args.expected_report_sha256, "report")
    review_evidence.ReviewValidator().report(report)
    inputs.append(report_input)
    members = [review_member("report.json", report_input.raw), review_member("prepared.json", prepared.raw)]
    total = 0
    png_checks = []
    for preview in report["previews"]:
        image = review_read_evidence(evidence_dir / preview["name"], max_bytes=preview_png.PNG_MAX_BYTES)
        inspection = preview_png.inspect_png(image.raw)
        if any(inspection[key] != preview[key] for key in ("sha256", "bytes", "width", "height")):
            review_evidence.review_failure("review/preview-mismatch", "Preview declaration differs from its actual PNG bytes", preview_id=preview["id"])
        total += len(image.raw)
        if total > preview_png.PNG_TOTAL_BYTES:
            review_evidence.review_failure("review/resource-limit", "Total PNG byte budget exceeded")
        inputs.append(image)
        members.append(review_member(preview["name"], image.raw, limit=preview_png.PNG_MAX_BYTES))
        png_checks.append({"preview_id": preview["id"], **inspection})
    if report["export"] is not None:
        for key in ("stdout", "stderr"):
            declaration = report["export"][key]
            if declaration is None:
                continue
            log = review_read_evidence(evidence_dir / declaration["name"], max_bytes=REVIEW_LOG_BYTES)
            if log.sha256 != declaration["sha256"] or len(log.raw) != declaration["bytes"]:
                review_evidence.review_failure("review/preview-mismatch", "Exporter log declaration differs from actual bytes", stream=key)
            inputs.append(log)
            members.append(review_member(declaration["name"], log.raw, limit=REVIEW_LOG_BYTES))
    receipt = review_evidence.assess_visual_report(report, run, index, prepared.sha256, report_input.sha256)
    members.append(review_member("receipt.json", review_json_bytes(receipt)))
    delivery = context_bundle.bundle_publish_members(args.output, sorted(members, key=lambda item: item["name"]),
                {"review_bundle_version": 1, "kind": "record", "run_id": run["run_id"], "prepared_sha256": prepared.sha256},
                review_unique_inputs(inputs), review_verify_published)
    return {"operation": "review", "action": "record", "valid": True, "review": receipt,
            "semantic_context": baseline["assessment"], "png_checks": png_checks, "delivery": delivery}, 0


def run_review_workflow(args):
    if args.action not in {"prepare", "record"}:
        review_evidence.review_failure("review/action-unsupported", "Only review prepare and record are implemented", action=args.action)
    required = {"input", "expected_input_sha256", "output"}
    record_only = {"prepared", "expected_prepared_sha256", "evidence_dir", "report", "expected_report_sha256"}
    if args.action == "record":
        required |= record_only
    elif any(getattr(args, key) is not None for key in record_only):
        review_evidence.review_failure("input/invalid", "Record-only arguments cannot be used with prepare")
    missing = sorted(key for key in required if getattr(args, key) is None)
    if missing:
        review_evidence.review_failure("input/invalid", "Required review arguments are missing", arguments=missing)
    if args.context is None and (args.expected_context_sha256 is not None or args.input_completion_manifest is not None):
        review_evidence.review_failure("input/invalid", "Context digest/manifest require the explicit context file")
    return review_prepare(args) if args.action == "prepare" else review_record(args)
