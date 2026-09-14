"""Six explicit context adapters; the original single-file commands stay separate."""
from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET

from . import (build, context_bundle, context_native, contracts, document, metadata,
               migration, provenance, roundtrip, semantic_context, validation)


def workflow_identity(tree, sha256):
    model = context_native.original_model(tree)
    return {"sha256": sha256, "schema_version": model["schema_version"],
            "model_hash_version": model["model_hash_version"],
            "model_hash": document.find_pool(tree).get(contracts.DATA_MODEL_HASH)}


def workflow_json_input(path):
    item = context_bundle.bundle_read_input(path, max_bytes=semantic_context.MAX_BYTES)
    return semantic_context.decode_context(item.raw, validate=False), item


def workflow_parse_native(raw):
    tree, reasons = migration.parse_native_bytes(raw)
    if reasons:
        provenance.provenance_error("context/artifact-unsupported", "Context delivery cannot preserve this XML payload", diagnostics=reasons)
    return tree


def workflow_template(data, tree, graph_sha256):
    provenance.provenance_object(data, {"context_template_version", "patterns", "group_contracts", "provenance"}, {"context_template_version"})
    if type(data["context_template_version"]) is not int:
        provenance.provenance_error("schema/type", "Template version must be a non-boolean integer")
    if data["context_template_version"] != 1:
        provenance.provenance_error("context/version-unsupported", "Unsupported context template version")
    bound = {key: copy.deepcopy(value) for key, value in data.items() if key != "context_template_version"}
    bound.update(context_version=1, artifact=workflow_identity(tree, graph_sha256))
    semantic_context._InputValidator().validate(bound)
    if "provenance" in bound:
        provenance.provenance_template_check(bound["provenance"], context_native.original_model(tree))
    return bound


def workflow_read_bound(graph_path, context_path, manifest_path=None, *, allow_template=False, projected=None):
    graph = context_bundle.bundle_read_input(graph_path)
    loaded, context = workflow_json_input(context_path)
    inputs = [graph, context]
    context_bundle.bundle_check_distinct(inputs)
    tree = projected if projected is not None else workflow_parse_native(graph.raw)
    template = isinstance(loaded.data, dict) and "context_template_version" in loaded.data
    if template and allow_template:
        if manifest_path is not None:
            provenance.provenance_error("input/invalid", "Unbound templates cannot claim an input completion manifest")
        loaded = semantic_context.LoadedContext(workflow_template(loaded.data, tree, graph.sha256), loaded.sha256)
    else:
        semantic_context._InputValidator().validate(loaded.data)
        if "provenance" in loaded.data and manifest_path is None:
            provenance.provenance_error("delivery/bundle-required", "Bound provenance requires its explicit completion manifest")
        if manifest_path is not None:
            inputs.append(context_bundle.verify_bundle(manifest_path, graph, context))
    result = validation.validate_tree(tree)
    assessment = semantic_context.assess_context(loaded, tree, graph.sha256, result)
    return tree, loaded, inputs, assessment, template


def workflow_expected(inputs, expected_graph, expected_context, *, required=True):
    for item, expected, prefix in ((inputs[0], expected_graph, "input"), (inputs[1], expected_context, "context")):
        if required and not expected:
            provenance.provenance_error("delivery/" + prefix + "-sha256-required", "Context write requires both reviewed input digests")
        if expected is not None and expected != item.sha256:
            provenance.provenance_error("delivery/" + prefix + "-sha256-mismatch", "Input differs from the reviewed raw SHA-256")


def workflow_changes(path):
    loaded, item = workflow_json_input(path)
    provenance.validate_context_changes(loaded.data, semantic_context._InputValidator())
    return loaded.data, item


def workflow_writer_flags(args):
    if getattr(args, "force", False) or getattr(args, "accept_model_drift", False):
        provenance.provenance_error("input/invalid", "Context bundles cannot overwrite or adopt model drift")
    if args.context_output is None or args.completion_manifest is None:
        provenance.provenance_error("delivery/bundle-required", "Context write requires context-output and completion-manifest")
    context_bundle.bundle_validate_targets(args.output, args.context_output, args.completion_manifest)


def workflow_serialize(tree, *, before=None, changes=None, migration_before=None):
    raw = ET.tostring(tree.getroot(), encoding="utf-8", short_empty_elements=True)
    candidate = workflow_parse_native(raw)
    if migration_before is None:
        result = roundtrip._check_delivery_candidate(tree, candidate, True, before=before, changes=changes)
    else:
        result, _ = migration._validate_migration_candidate(migration_before, tree, candidate, metadata.semantic_model_document(tree))
    context_native.original_model(candidate)
    return raw, candidate, result


def workflow_deliver(args, raw, tree, result, context_data, inputs, **receipt):
    context_raw = context_bundle.bundle_json(context_data)
    loaded = semantic_context.decode_context(context_raw)
    assessment = semantic_context.assess_context(loaded, tree, context_bundle.bundle_hash(raw), result, strict=True)
    if assessment["gate"] in {"failed", "incomplete"}:
        provenance.provenance_error("delivery/context-gate-failed", "Declared pattern/group contract did not pass", semantic_context=assessment)
    delivery = context_bundle.deliver_bundle(args.output, args.context_output, args.completion_manifest, raw, context_raw, inputs)
    result.update(operation=args.command, semantic_context=assessment, delivery=delivery,
                  output={"path": str(args.output), "sha256": context_bundle.bundle_hash(raw), "bytes": len(raw)},
                  context_output={"path": str(args.context_output), "sha256": loaded.sha256, "bytes": len(context_raw)}, **receipt)
    return result, 0


def workflow_build(args):
    workflow_writer_flags(args)
    template, template_input = workflow_json_input(args.context)
    spec, spec_input = workflow_json_input(args.spec)
    tree = build.build_tree(spec.data)
    raw, candidate, result = workflow_serialize(tree)
    bound = workflow_template(template.data, candidate, context_bundle.bundle_hash(raw))
    return workflow_deliver(args, raw, candidate, result, bound, [spec_input, template_input],
                            provenance_operation="initialize", prior_context_preserved=False)


def workflow_patch(args):
    workflow_writer_flags(args)
    if args.context_changes is None:
        provenance.provenance_error("input/invalid", "Context patch requires an explicit context-changes declaration")
    before, loaded, inputs, _, template = workflow_read_bound(
        args.input, args.context, args.input_completion_manifest, allow_template=True)
    workflow_expected(inputs, args.expected_input_sha256, args.expected_context_sha256)
    context_changes, context_changes_input = workflow_changes(args.context_changes)
    graph_changes, graph_changes_input = workflow_json_input(args.changes)
    inputs.extend([context_changes_input, graph_changes_input])
    if context_changes["mode"] == "migration":
        provenance.provenance_error("input/invalid", "Patch cannot claim metadata migration proof")
    source = copy.deepcopy(loaded.data)
    if template:
        if (context_changes["mode"] != "semantic" or "provenance" not in source
                or context_changes.get("provenance") != source["provenance"]):
            provenance.provenance_error("input/invalid", "Template patch initialization requires explicit identical provenance addition in semantic mode")
        del source["provenance"]
    tree = copy.deepcopy(before)
    patch_receipt = roundtrip.patch_tree(tree, graph_changes.data, args.allow_geometry_updates)
    raw, candidate, result = workflow_serialize(tree, before=before, changes=graph_changes.data)
    after_context = provenance.transition_context(source, context_changes, context_native.original_model(before),
                         context_native.original_model(candidate), context_sha256=loaded.sha256,
                         artifact=workflow_identity(candidate, context_bundle.bundle_hash(raw)))
    audit = provenance.audit_context_preservation(source, after_context, context_changes)
    if audit:
        provenance.provenance_error("provenance/change-not-declared", "Context transition failed independent preservation checks", differences=audit)
    return workflow_deliver(args, raw, candidate, result, after_context, inputs, patch_receipt=patch_receipt,
                            provenance_operation="initialize" if template else "update",
                            prior_context_preserved=not template,
                            input_context_sha256=loaded.sha256)


def workflow_read_command(args):
    tree, loaded, inputs, assessment, _ = workflow_read_bound(args.input, args.context, args.completion_manifest)
    if args.command == "inspect":
        result = roundtrip.inspect_tree(tree)
        result["input"] = {"path": str(args.input), "sha256": inputs[0].sha256, "bytes": len(inputs[0].raw)}
    else:
        result = validation.validate_tree(tree)
        assessment = semantic_context.assess_context(loaded, tree, inputs[0].sha256, result, strict=args.strict)
    result["semantic_context"] = assessment
    if len(inputs) == 3:
        result["bundle"] = {"verified": True, "manifest_sha256": inputs[2].sha256}
    failed = (args.command == "validate" and (not result["valid"] or (args.strict and result["warnings"])
              or assessment["gate"] in {"failed", "incomplete"} or assessment.get("source_gate") in {"failed", "incomplete"}))
    for item in inputs:
        item.verify()
    return result, 1 if failed else 0


def workflow_migration_projection(raw, *, accept=False):
    plan = migration.plan_migration(raw, accept_unverified_baseline=accept)
    if plan["classification"] not in migration.ELIGIBLE_CLASSES:
        provenance.provenance_error("migration/input-unsafe" if plan["classification"] == "unsafe" else "migration/input-unsupported",
                                   "Original input is not eligible for exact migration", migration=plan)
    tree = workflow_parse_native(raw)
    projected = migration._apply_exact_changes(tree, plan["planned_changes"])
    context_native.original_model(projected)
    return plan, projected


def workflow_migrate(args):
    graph = context_bundle.bundle_read_input(args.input)
    plan, projected = workflow_migration_projection(graph.raw, accept=args.accept_unverified_baseline)
    before, loaded, inputs, assessment, _ = workflow_read_bound(
        args.input, args.context, args.input_completion_manifest, projected=projected)
    if inputs[0].raw != graph.raw or inputs[0].signature != graph.signature:
        provenance.provenance_error("delivery/input-changed", "Migration source changed while planning")
    workflow_expected(inputs, args.expected_input_sha256, args.expected_context_sha256, required=not args.dry_run)
    if args.dry_run:
        if args.context_output is not None or args.completion_manifest is not None or args.context_changes is not None:
            provenance.provenance_error("input/invalid", "Context migration dry-run does not accept output or context-change paths")
        plan["semantic_context"] = assessment
        plan["provenance_operation"] = "plan-rebind"
        for item in inputs:
            item.verify()
        return plan, 0
    workflow_writer_flags(args)
    if args.context_changes is None:
        provenance.provenance_error("input/invalid", "Context migration requires explicit context-changes")
    changes, changes_input = workflow_changes(args.context_changes)
    if changes["mode"] != "migration":
        provenance.provenance_error("input/invalid", "Migration context changes require migration mode")
    if plan["baseline_acceptance_required"] and not plan["baseline_accepted"]:
        provenance.provenance_error("migration/baseline-acceptance-required", "Exact migration still requires explicit baseline acceptance")
    if plan["classification"] == "not-needed":
        provenance.transition_context(loaded.data, changes, context_native.original_model(before),
             context_native.original_model(before), context_sha256=loaded.sha256, artifact=loaded.data["artifact"])
        for item in inputs + [changes_input]:
            item.verify()
        plan["semantic_context"] = assessment
        return plan, 0
    raw, candidate, result = workflow_serialize(projected, migration_before=graph.raw)
    rebound = provenance.transition_context(loaded.data, changes, context_native.original_model(before),
                   context_native.original_model(candidate), context_sha256=loaded.sha256,
                   artifact=workflow_identity(candidate, context_bundle.bundle_hash(raw)))
    return workflow_deliver(args, raw, candidate, result, rebound, inputs + [changes_input],
                            migration=plan, provenance_operation="rebind", prior_context_preserved=True)


def workflow_compare(args):
    if args.before_context is None or args.after_context is None or args.context_changes is None:
        provenance.provenance_error("input/invalid", "Context comparison requires both contexts and context-changes")
    projected = None
    if args.migration:
        original = context_bundle.bundle_read_input(args.before)
        _, projected = workflow_migration_projection(original.raw)
    before, loaded, before_inputs, _, template = workflow_read_bound(args.before, args.before_context,
                              args.before_completion_manifest, allow_template=not args.migration, projected=projected)
    after, actual, after_inputs, assessment, _ = workflow_read_bound(args.after, args.after_context, args.after_completion_manifest)
    changes, change_input = workflow_changes(args.context_changes)
    source = copy.deepcopy(loaded.data)
    if template:
        if changes["mode"] != "semantic" or changes.get("provenance") != source.get("provenance") or "provenance" not in source:
            provenance.provenance_error("input/invalid", "Template comparison requires declared first provenance initialization")
        del source["provenance"]
    if args.migration:
        if changes["mode"] != "migration":
            provenance.provenance_error("input/invalid", "Migration comparison requires migration context mode")
        graph_result = migration._compare_migration_tree(before_inputs[0].raw, after)
    else:
        if changes["mode"] == "migration":
            provenance.provenance_error("input/invalid", "Only migration comparison can claim metadata migration")
        graph_changes, graph_input = workflow_json_input(args.changes) if args.changes else (None, None)
        graph_result = roundtrip.compare_trees(before, after, graph_changes.data if graph_changes else None)
        if graph_input:
            before_inputs.append(graph_input)
    differences = provenance.audit_context_preservation(source, actual.data, changes)
    expected = provenance.transition_context(source, changes, context_native.original_model(before), context_native.original_model(after),
                     context_sha256=loaded.sha256, artifact=workflow_identity(after, after_inputs[0].sha256))
    if actual.data != expected:
        differences.append({"code": "provenance/change-not-declared", "field": "context", "reason": "actual context differs from the exact declared transition"})
    for item in before_inputs + after_inputs + [change_input]:
        item.verify()
    preserved = graph_result["preserved"] and not differences
    return {"operation": "compare-context", "preserved": preserved, "graph_comparison": graph_result,
            "context_comparison": {"preserved": not differences, "differences": differences,
                                   "before_context_sha256": loaded.sha256, "after_context_sha256": actual.sha256,
                                   "prior_context_preserved": not template and not differences},
            "semantic_context": assessment}, 0 if preserved else 1


def run_context_workflow(args):
    try:
        adapter = {"build": workflow_build, "patch": workflow_patch, "inspect": workflow_read_command,
                   "validate": workflow_read_command, "migrate": workflow_migrate, "compare": workflow_compare}[args.command]
        return adapter(args)
    except (contracts.DiagramError, json.JSONDecodeError, ET.ParseError) as error:
        exc = error if isinstance(error, contracts.DiagramError) else contracts.DiagramError(
            str(error), code="input/json-invalid" if isinstance(error, json.JSONDecodeError) else "input/drawio-xml-invalid")
        failure = {"operation": args.command + "-context", "valid": False, "errors": [str(exc)], "warnings": [],
                   "diagnostics": [exc.diagnostic()]}
        if "delivery" in exc.evidence:
            failure["delivery"] = exc.evidence["delivery"]
        elif args.command in {"build", "patch", "migrate"} and not getattr(args, "dry_run", False):
            failure["delivery"] = {"stage": "validation", "committed": False, "complete": False, "members_written": []}
        semantic_failure = exc.code in {"delivery/context-gate-failed", "delivery/candidate-preservation-failed",
                                         "delivery/strict-validation-failed", "delivery/validation-failed"}
        comparison_failure = args.command == "compare" and exc.code in {
            "provenance/confirmation-required", "provenance/change-not-declared", "provenance/record-loss", "provenance/geometry-not-pure"}
        return failure, 1 if semantic_failure or comparison_failure else 2
