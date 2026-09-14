"""Explicit E5 cycle adapters; E4 version-one readers and writers stay separate."""
from __future__ import annotations

import copy

from . import (context_bundle, context_native, contracts, document, preview_png, provenance,
               review_evidence, review_preservation, review_repair, review_state, review_workflow,
               semantic_context, validation)

CYCLE_AUTH_FIELDS = {"review_authorization_version", "run_id", "prepared_sha256", "prepared_path_sha256", "original_artifact_sha256",
                     "original_context_sha256", "maximum_attempts", "asserted_by", "basis", "actions"}
CYCLE_PLAN_FIELDS = {"repair_plan_version", "run_id", "initial_prepared_sha256", "parent_record_sha256", "parent_assessment_sha256",
                     "current_artifact_sha256", "current_context_sha256", "authorization_sha256", "tool_version", "repair_rule_version",
                     "attempt_index", "ledger_root_sha256", "can_repair", "actions", "rejected", "baseline_issue_keys", "input_validation",
                     "context_assessment", "stop_reason"}
CYCLE_CANDIDATE_FIELDS = {"candidate_version", "run_id", "attempt_index", "claim_sha256", "plan_sha256", "authorization_sha256",
                         "parent_record_sha256", "before_artifact_sha256", "before_context_sha256", "artifact_sha256", "artifact_bytes",
                         "context_sha256", "actions", "metrics", "validation", "semantic_context", "source_preserved", "protected_projection",
                         "technical_status", "accepted"}
CYCLE_CHECKS = ("technical", "protected_projection", "semantic_context", "source_preservation", "environment",
                "full_and_target_coverage", "resolutions", "no_regression", "metric_improvement")
CYCLE_ASSESS_FIELDS = {"review_assessment_version", "run_id", "attempt_index", "claim_sha256", "parent_record_sha256", "candidate_sha256",
                      "candidate_record_sha256", "resolutions_sha256", "checks", "decision", "stop_reason", "continue", "visual_status",
                      "remaining_issues", "metrics", "original", "last_accepted", "last_good", "attempts_started", "maximum_attempts",
                      "trust", "acceptance_basis", "remaining_coverage"}
CYCLE_ISSUE_INTENTS = {"visual/edge-label-collision": "reposition-edge-label", "visual/excessive-detour": "reroute-edge",
                      "visual/arrowhead-hidden": "reroute-edge"}


def cycle_failure(code, message, **evidence):
    review_evidence.review_failure(code, message, **evidence)


def cycle_required(args, fields):
    missing = sorted(field for field in fields if getattr(args, field, None) is None)
    if missing:
        cycle_failure("input/invalid", "Required cycle arguments are missing", arguments=missing)


def cycle_issue_key(issue):
    return {"code": issue["code"], "targets": sorted(copy.deepcopy(issue["targets"]), key=review_evidence.review_target_key)}


def cycle_key_tuple(key):
    return key["code"], tuple(sorted(review_evidence.review_target_key(target) for target in key["targets"]))


def cycle_initial(args):
    run, index, manifest, inputs = review_workflow.review_load_prepared(args.prepared)
    review_workflow.review_input_expected(manifest, args.expected_prepared_sha256, "prepared")
    return {"run": run, "index": index, "manifest": manifest, "inputs": inputs}


def cycle_authorization(args, initial):
    data, item = review_workflow.review_read_json(args.authorization)
    review_workflow.review_input_expected(item, args.expected_authorization_sha256, "authorization")
    review_evidence.review_object(data, CYCLE_AUTH_FIELDS)
    review_evidence.review_version(data["review_authorization_version"])
    review_evidence.review_integer(data["maximum_attempts"])
    if data["maximum_attempts"] != 2:
        cycle_failure("review/authorization-mismatch", "Repair authorization must retain the two-attempt limit")
    for key in ("asserted_by", "basis"):
        review_evidence.review_text(data[key])
    for key in ("prepared_sha256", "prepared_path_sha256", "original_artifact_sha256", "original_context_sha256"):
        review_evidence.review_digest(data[key], nullable=key == "original_context_sha256")
    expected_path = context_bundle.bundle_hash(str(initial["manifest"].path).encode("utf-8"))
    run = initial["run"]
    if (data["run_id"] != run["run_id"] or data["prepared_sha256"] != initial["manifest"].sha256
            or data["prepared_path_sha256"] != expected_path or data["original_artifact_sha256"] != run["artifact"]["sha256"]
            or data["original_context_sha256"] != run["context"]["sha256"]):
        cycle_failure("review/authorization-mismatch", "Independent authorization does not bind this original prepared run")
    actions = contracts.require_list(data["actions"], "authorization actions")
    if not 1 <= len(actions) <= 32:
        cycle_failure("review/resource-limit", "Authorization requires one to 32 targets")
    seen = set()
    for action in actions:
        review_evidence.review_object(action, {"target", "intents"})
        key = review_evidence.ReviewValidator().target(action["target"])
        if key[0] != "edge" or key in seen:
            cycle_failure("review/action-not-authorized", "Authorization targets must be unique typed edges")
        seen.add(key)
        intents = contracts.require_list(action["intents"], "authorized intents")
        for intent in intents:
            review_evidence.review_enum(intent, review_preservation.REPAIR_INTENTS, code="review/action-not-authorized")
        if not intents or len(set(intents)) != len(intents):
            cycle_failure("schema/duplicate", "Authorized intents must be nonempty and unique")
    root = {"state_version": 1, "run_id": run["run_id"], "prepared_sha256": initial["manifest"].sha256,
            "prepared_path_sha256": expected_path, "original": copy.deepcopy(run["original"]), "authorization_sha256": item.sha256,
            "tool_version": contracts.TOOL_VERSION, "repair_rule_version": review_preservation.REPAIR_RULE_VERSION, "maximum_attempts": 2}
    return data, item, root


def cycle_read_prepared(path):
    data, first = review_workflow.review_read_json(path)
    if data.get("review_bundle_version") == 1:
        run, index, manifest, inputs = review_workflow.review_load_prepared(path)
        return {"run": run, "index": index, "manifest": manifest, "inputs": review_workflow.review_unique_inputs([first, *inputs])}
    return cycle_read_candidate_prepared(path)


def cycle_assess_report(report, prepared, report_sha256):
    if prepared["run"]["round"]["index"] == 0:
        return review_evidence.assess_visual_report(report, prepared["run"], prepared["index"], prepared["manifest"].sha256, report_sha256)
    review_evidence.review_version(report.get("visual_report_version"), 2)
    fields = {"claim_sha256", "candidate_sha256", "parent_record_sha256"}
    if not fields <= set(report):
        cycle_failure("schema/required", "Candidate review is missing its claim identity")
    current_round = prepared["run"]["round"]
    if (type(report.get("round_index")) is not int or report["round_index"] != current_round["index"]
            or any(report[key] != current_round[key] for key in fields)):
        cycle_failure("review/baseline-mismatch", "Candidate review belongs to another attempt or parent")
    normalized = {key: copy.deepcopy(value) for key, value in report.items() if key not in fields}
    normalized.update(visual_report_version=1, round_index=0)
    receipt = review_evidence.assess_visual_report(normalized, prepared["run"], prepared["index"], prepared["manifest"].sha256, report_sha256)
    receipt.update(review_receipt_version=2, round_index=current_round["index"], **{key: current_round[key] for key in fields})
    return receipt


def cycle_verify_images(report, members):
    expected = {"report.json", "receipt.json", "prepared.json"}
    inspections = []
    for preview in report["previews"]:
        expected.add(preview["name"])
        if preview["name"] not in members:
            cycle_failure("delivery/bundle-incomplete", "Record is missing a declared preview")
        actual = preview_png.inspect_png(members[preview["name"]].raw)
        if any(actual[key] != preview[key] for key in ("sha256", "bytes", "width", "height")):
            cycle_failure("review/preview-mismatch", "Recorded PNG differs from its declaration")
        inspections.append({"preview_id": preview["id"], **actual})
    if report["export"] is not None:
        for key in ("stdout", "stderr"):
            declaration = report["export"][key]
            if declaration is not None:
                expected.add(declaration["name"])
                item = members.get(declaration["name"])
                if item is None or item.sha256 != declaration["sha256"] or len(item.raw) != declaration["bytes"]:
                    cycle_failure("review/preview-mismatch", "Recorded log differs from actual bytes")
    if expected != set(members):
        cycle_failure("delivery/bundle-invalid", "Record has undeclared evidence members")
    return inspections


def cycle_read_record(path, expected_sha256, prepared):
    if prepared["run"]["round"]["index"] == 0:
        header, manifest, members = review_workflow.review_read_package(path, kind="record")
    else:
        header, manifest, members = review_state.state_package_read(path, "candidate-record")
    review_workflow.review_input_expected(manifest, expected_sha256, "record")
    if header["run_id"] != prepared["run"]["run_id"] or members["prepared.json"].raw != prepared["manifest"].raw:
        cycle_failure("review/baseline-mismatch", "Record is not bound to the actual parent prepared package")
    report = review_state.state_payload(members["report.json"])
    receipt = cycle_assess_report(report, prepared, members["report.json"].sha256)
    cycle_verify_images(report, members)
    if review_workflow.review_json_bytes(receipt) != review_workflow.review_json_bytes(review_state.state_payload(members["receipt.json"])):
        cycle_failure("review/baseline-mismatch", "Recorded receipt differs from independent report validation")
    return {"header": header, "manifest": manifest, "members": members, "report": report, "receipt": receipt,
            "inputs": [manifest, *members.values()]}


def cycle_parent(args, initial, root):
    parent = cycle_read_prepared(args.parent_prepared)
    review_workflow.review_input_expected(parent["manifest"], args.expected_parent_prepared_sha256, "parent-prepared")
    baseline = review_workflow.review_read_baseline(args.input, args.context, args.input_completion_manifest)
    review_workflow.review_input_expected(baseline["graph"], args.expected_input_sha256, "input")
    if baseline["context"] is not None:
        review_workflow.review_input_expected(baseline["context"], args.expected_context_sha256, "context")
    elif args.expected_context_sha256 is not None:
        cycle_failure("input/invalid", "A context digest requires its context input")
    if (parent["run"]["run_id"] != initial["run"]["run_id"] or parent["run"]["artifact"]["sha256"] != baseline["graph"].sha256
            or parent["run"]["context"]["sha256"] != (baseline["context"].sha256 if baseline["context"] else None)):
        cycle_failure("review/baseline-mismatch", "Current graph/context differs from its actual parent prepared package")
    record = cycle_read_record(args.record, args.expected_record_sha256, parent)
    state = review_state.inspect_review_state(initial["manifest"], root)
    extra_inputs, parent_assessment = [], None
    if parent["run"]["round"]["index"] == 0:
        if parent["manifest"].sha256 != initial["manifest"].sha256 or args.parent_assessment is not None:
            cycle_failure("review/state-conflict", "Initial parent must be the original prepared run without an assessment")
    else:
        cycle_required(args, {"parent_assessment", "expected_assessment_sha256"})
        _, am, members = review_state.state_package_read(args.parent_assessment, "assessment")
        review_workflow.review_input_expected(am, args.expected_assessment_sha256, "assessment")
        assessment = review_state.state_payload(members["assessment.json"])
        review_evidence.review_object(assessment, CYCLE_ASSESS_FIELDS)
        matching = [item for item in state["attempts"] if item["index"] == parent["run"]["round"]["index"]]
        if (not matching or matching[0]["marker"] is None or matching[0]["marker"]["assessment_sha256"] != am.sha256
                or assessment["decision"] != "accepted" or not assessment["continue"]
                or assessment["last_accepted"]["artifact_sha256"] != baseline["graph"].sha256
                or assessment["last_accepted"]["record_sha256"] != record["manifest"].sha256):
            cycle_failure("review/state-conflict", "Parent assessment is not the committed accepted parent of this input")
        marker = matching[0]["marker"]
        cycle_assessment_schema(assessment)
        if any(assessment[key] != marker[key] for key in ("run_id", "attempt_index", "claim_sha256", "decision", "continue", "original", "last_accepted", "last_good")):
            cycle_failure("review/state-conflict", "Actual parent assessment differs from its durable marker")
        if (assessment["candidate_sha256"] != parent["run"]["round"]["candidate_sha256"]
                or assessment["candidate_record_sha256"] != record["manifest"].sha256):
            cycle_failure("review/state-conflict", "Parent assessment does not identify its actual candidate evidence")
        parent_assessment = am.sha256; extra_inputs = [am, *members.values()]
    return parent, record, baseline, state, parent_assessment, extra_inputs


def cycle_semantic_gate(assessment):
    if assessment is None:
        return True
    return (all(scope["gate"] in {"passed", "not_applicable"} for scope in assessment["scope_results"])
            and assessment.get("group_gate", "not_applicable") in {"passed", "not_applicable"})


def cycle_plan_data(initial, auth, auth_input, parent, record, baseline, state, parent_assessment):
    payload_error = None
    try:
        input_tree = review_preservation.preservation_parse(baseline["graph"].raw)
    except contracts.DiagramError as exc:
        input_tree, payload_error = baseline["tree"], exc.diagnostic()
    permissions = {item["target"]["id"]: set(item["intents"]) for item in auth["actions"]}
    actions, rejected, issue_keys = {}, [], {}
    for issue in record["receipt"]["issues"]:
        if issue["severity"] not in {"blocker", "warning"}:
            continue
        key = cycle_issue_key(issue); issue_keys[cycle_key_tuple(key)] = key
        intent = CYCLE_ISSUE_INTENTS.get(issue["code"])
        candidates = [target for target in issue["targets"] if target["kind"] == "edge" and intent in permissions.get(target["id"], set())]
        reason = ("unsupported_issue" if intent is None else "action_not_authorized" if not candidates else
                  "unreliable_spatial_binding" if issue["binding_status"] != "spatial_bound" else None)
        if reason:
            rejected.append({"target": None, "issue_key": key, "reason": reason, "diagnostic": None}); continue
        for target in candidates:
            try:
                review_preservation.preservation_native_eligibility(input_tree, target["id"], intent)
                metric = review_preservation.repair_native_metric(input_tree, target["id"], intent)
                if (intent == "reposition-edge-label" and metric["conflict_count"] == 0):
                    raise contracts.DiagramError("Visual observation has no measurable native target conflict", code="review/candidate-ineligible")
            except contracts.DiagramError as exc:
                rejected.append({"target": target, "issue_key": key, "reason": "native_ineligible", "diagnostic": exc.diagnostic()}); continue
            previous = actions.get(target["id"])
            if previous is None or intent == "reroute-edge" and previous["intent"] != intent:
                actions[target["id"]] = {"intent": intent, "target": target, "issue_keys": ([*previous["issue_keys"], key] if previous else [key]), "before_metric": metric,
                                         "strategy_id": "native-label-reflow-v1" if intent == "reposition-edge-label" else "bounded-native-route-v1"}
            elif key not in previous["issue_keys"]:
                previous["issue_keys"].append(key)
    for action in actions.values():
        action["issue_keys"] = sorted(action["issue_keys"], key=cycle_key_tuple)
    stop_reason = None
    if (record["receipt"]["states"]["preview_export"] != "passed" or not record["report"]["reviews"]["agent"]["full_image_viewed"]
            or record["receipt"]["states"]["agent_image_review"] not in {"passed", "failed"} or record["receipt"]["review_consensus"] == "conflict"):
        stop_reason = "incomplete_or_conflicting_review"
    elif not cycle_semantic_gate(baseline["assessment"]):
        stop_reason = "semantic_context_gate"
    elif not baseline["validation"]["valid"]:
        stop_reason = "input_validation_failed"
    for diagnostic in baseline["validation"]["diagnostics"]:
        if diagnostic["severity"] != "warning":
            continue
        subject = diagnostic.get("subject", {}); target_action = actions.get(subject.get("id")) if subject.get("kind") == "edge" else None
        code = diagnostic["code"]
        if (target_action is None or not (code.startswith("text/edge-label-") or target_action["intent"] == "reroute-edge"
                                         and (code.startswith("routing/") or code == "layout/main-path-zigzag"))):
            stop_reason = "unsupported_input_warning"
    if payload_error is not None:
        stop_reason = "unsupported_native_payload"
        rejected.append({"target": None, "issue_key": None, "reason": stop_reason, "diagnostic": payload_error})
    if state["terminal"]:
        stop_reason = "run_stopped"
    elif state["attempts"] and state["attempts"][-1]["marker"] is None:
        stop_reason = "attempt_pending"
    elif state["next_attempt"] > 2:
        stop_reason = "attempt_exhausted"
    if not actions and stop_reason is None:
        stop_reason = "no_eligible_actions"
    return {"repair_plan_version": 1, "run_id": initial["run"]["run_id"], "initial_prepared_sha256": initial["manifest"].sha256,
            "parent_record_sha256": record["manifest"].sha256, "parent_assessment_sha256": parent_assessment,
            "current_artifact_sha256": baseline["graph"].sha256, "current_context_sha256": baseline["context"].sha256 if baseline["context"] else None,
            "authorization_sha256": auth_input.sha256, "tool_version": contracts.TOOL_VERSION, "repair_rule_version": review_preservation.REPAIR_RULE_VERSION,
            "attempt_index": state["next_attempt"], "ledger_root_sha256": state["root"].sha256 if state["root"] else None,
            "can_repair": bool(actions) and stop_reason is None, "actions": [actions[sid] for sid in sorted(actions)],
            "rejected": sorted(rejected, key=lambda item: (str(item["target"]), str(item["issue_key"]), item["reason"])),
            "baseline_issue_keys": [issue_keys[key] for key in sorted(issue_keys)], "input_validation": baseline["validation"],
            "context_assessment": baseline["assessment"], "stop_reason": stop_reason}


def cycle_plan_inputs(args):
    cycle_required(args, {"prepared", "expected_prepared_sha256", "parent_prepared", "expected_parent_prepared_sha256", "record", "expected_record_sha256",
                          "authorization", "expected_authorization_sha256", "input", "expected_input_sha256", "output"})
    initial = cycle_initial(args)
    auth, auth_input, root = cycle_authorization(args, initial)
    parent, record, baseline, state, parent_assessment, extra = cycle_parent(args, initial, root)
    plan = cycle_plan_data(initial, auth, auth_input, parent, record, baseline, state, parent_assessment)
    inputs = review_workflow.review_unique_inputs([*initial["inputs"], auth_input, *parent["inputs"], *record["inputs"], *baseline["inputs"], *state["inputs"], *extra])
    return initial, auth_input, root, parent, record, baseline, state, plan, inputs


def cycle_plan(args):
    initial, auth_input, root, parent, record, baseline, state, plan, inputs = cycle_plan_inputs(args)
    members = [review_workflow.review_member("plan.json", review_workflow.review_json_bytes(plan)),
               review_workflow.review_member("authorization.json", auth_input.raw), review_workflow.review_member("prepared.json", initial["manifest"].raw),
               review_workflow.review_member("record.json", record["manifest"].raw)]
    delivery = review_state.state_package_publish(args.output, "plan", plan["run_id"], members, inputs)
    return {"operation": "review", "action": "plan", "valid": True, "plan": plan, "delivery": delivery}, 0


def cycle_context_rebind(baseline, candidate):
    if baseline["context"] is None:
        return None, None, True
    before = semantic_context.decode_context(baseline["context"].raw).data
    artifact = {"sha256": context_bundle.bundle_hash(candidate["raw"]), "schema_version": "3", "model_hash_version": contracts.MODEL_HASH_VERSION,
                "model_hash": document.find_pool(candidate["tree"]).get(contracts.DATA_MODEL_HASH)}
    changes = {"context_changes_version": 1, "expected_input_sha256": baseline["graph"].sha256,
               "expected_context_sha256": baseline["context"].sha256, "mode": "geometry-only"}
    after = provenance.transition_context(before, changes, context_native.original_model(baseline["tree"]),
                context_native.original_model(candidate["tree"]), context_sha256=baseline["context"].sha256, artifact=artifact)
    differences = provenance.audit_context_preservation(before, after, changes)
    if differences or {k:v for k,v in before.items() if k != "artifact"} != {k:v for k,v in after.items() if k != "artifact"}:
        cycle_failure("review/protected-change", "Geometry repair did not preserve all context/source/history declarations")
    raw = review_workflow.review_json_bytes(after)
    assessment = semantic_context.assess_context(semantic_context.decode_context(raw), candidate["tree"], artifact["sha256"], candidate["validation"], strict=True)
    return raw, assessment, True


def cycle_repair(args):
    cycle_required(args, {"plan", "expected_plan_sha256"})
    initial, auth_input, root, parent, record, baseline, state, expected_plan, inputs = cycle_plan_inputs(args)
    if state["attempts"] and state["attempts"][-1]["marker"] is None:
        cycle_failure("review/attempt-pending", "The existing attempt must be assessed or stopped first")
    if state["next_attempt"] > 2:
        cycle_failure("review/attempt-exhausted", "Review run has consumed both attempts")
    if state["terminal"]:
        cycle_failure("review/state-conflict", "Review run has already stopped")
    _, plan_manifest, members = review_state.state_package_read(args.plan, "plan")
    review_workflow.review_input_expected(plan_manifest, args.expected_plan_sha256, "plan")
    plan = review_state.state_payload(members["plan.json"])
    review_evidence.review_object(plan, CYCLE_PLAN_FIELDS)
    if (review_workflow.review_json_bytes(plan) != review_workflow.review_json_bytes(expected_plan)
            or members["authorization.json"].raw != auth_input.raw or members["prepared.json"].raw != initial["manifest"].raw
            or members["record.json"].raw != record["manifest"].raw):
        cycle_failure("review/plan-mismatch", "Plan differs from independent current input/permission reconstruction")
    if not plan["can_repair"]:
        cycle_failure("review/candidate-ineligible", "Plan has no eligible repair under current evidence", reason=plan["stop_reason"])
    inputs = review_workflow.review_unique_inputs([*inputs, plan_manifest, *members.values()])
    context_bundle.bundle_new_directory(args.output)
    output_path = context_bundle.bundle_absolute(args.output)
    ledger_path = review_state.state_ledger_path(initial["manifest"], root["run_id"])
    if output_path == ledger_path or ledger_path in output_path.parents:
        cycle_failure("delivery/path-unsafe", "Candidate output cannot occupy the fixed review state tree")
    claim = {"claim_version": 1, "run_id": plan["run_id"], "attempt_index": plan["attempt_index"], "root_manifest_sha256": plan["ledger_root_sha256"],
             "parent_assessment_sha256": plan["parent_assessment_sha256"], "parent_record_sha256": record["manifest"].sha256,
             "input_artifact_sha256": baseline["graph"].sha256, "input_context_sha256": baseline["context"].sha256 if baseline["context"] else None,
             "plan_sha256": plan_manifest.sha256, "authorization_sha256": auth_input.sha256, "tool_version": contracts.TOOL_VERSION,
             "repair_rule_version": review_preservation.REPAIR_RULE_VERSION,
             "action_fingerprint": cycle_action_fingerprint(plan)}
    try:
        actual_claim, claim_input, claim_delivery, latest = review_state.claim_review_attempt(initial["manifest"], root, claim, inputs)
    except contracts.DiagramError as exc:
        failed_delivery = exc.evidence.get("delivery", {})
        is_claim = failed_delivery.get("directory") == str(ledger_path / ("attempt-" + str(plan["attempt_index"])))
        exc.evidence.update(attempt_consumed=is_claim and bool(failed_delivery.get("committed")),
                            claim_delivery=failed_delivery if is_claim else None, state_delivery=None if is_claim else failed_delivery)
        raise
    inputs = review_workflow.review_unique_inputs([*inputs, *latest["inputs"]])
    slot = latest["attempts"][-1]["directory"]
    result = {"attempt_result_version": 1, "run_id": plan["run_id"], "attempt_index": plan["attempt_index"], "claim_sha256": claim_input.sha256,
              "plan_sha256": plan_manifest.sha256, "status": "technical_failed", "candidate_sha256": None, "candidate_artifact_sha256": None,
              "candidate_context_sha256": None, "diagnostics": [], "delivery": None}
    try:
        candidate = review_repair.generate_repair_candidate(baseline["graph"].raw, plan["actions"])
        result["status"] = candidate["status"]
        if candidate["status"] != "technical_passed":
            result["diagnostics"] = candidate["validation"]["diagnostics"]
            if not candidate["protected_projection"]["preserved"]:
                result["diagnostics"].append(contracts.make_diagnostic("review/protected-change", "error", "Candidate did not preserve protected XML"))
            delivery, _, _ = review_state.state_single_publish(slot / "result", "result", plan["run_id"], "result.json", result, inputs)
            return {"operation": "review", "action": "repair", "valid": False, "attempt_consumed": True, "claim_sha256": claim_input.sha256,
                    "result": result, "claim_delivery": claim_delivery, "result_delivery": delivery}, 1
        context_raw, assessment, source_preserved = cycle_context_rebind(baseline, candidate)
        if not cycle_semantic_gate(assessment):
            cycle_failure("review/candidate-ineligible", "Candidate semantic/group gate did not pass")
        candidate_sha = context_bundle.bundle_hash(candidate["raw"])
        metadata = {"candidate_version": 1, "run_id": plan["run_id"], "attempt_index": plan["attempt_index"], "claim_sha256": claim_input.sha256,
                    "plan_sha256": plan_manifest.sha256, "authorization_sha256": auth_input.sha256, "parent_record_sha256": record["manifest"].sha256,
                    "before_artifact_sha256": baseline["graph"].sha256, "before_context_sha256": baseline["context"].sha256 if baseline["context"] else None,
                    "artifact_sha256": candidate_sha, "artifact_bytes": len(candidate["raw"]), "context_sha256": context_bundle.bundle_hash(context_raw) if context_raw else None,
                    "actions": plan["actions"], "metrics": candidate["metrics"], "validation": candidate["validation"], "semantic_context": assessment,
                    "source_preserved": source_preserved, "protected_projection": candidate["protected_projection"], "technical_status": "passed", "accepted": False}
        output_members = [review_workflow.review_member("artifact/diagram.drawio", candidate["raw"], limit=review_workflow.REVIEW_GRAPH_BYTES),
                          review_workflow.review_member("candidate.json", review_workflow.review_json_bytes(metadata))]
        if context_raw is not None:
            submanifest = {"bundle_version": 1, "members": [{"role": "diagram", "name": "diagram.drawio", "sha256": candidate_sha, "bytes": len(candidate["raw"])},
                           {"role": "context", "name": "context.json", "sha256": context_bundle.bundle_hash(context_raw), "bytes": len(context_raw)}]}
            output_members.extend([review_workflow.review_member("artifact/context.json", context_raw),
                                   review_workflow.review_member("artifact/completion.json", review_workflow.review_json_bytes(submanifest))])
        delivery = review_state.state_package_publish(args.output, "candidate", plan["run_id"], output_members, inputs)
        result.update(status="technical_passed", candidate_sha256=delivery["manifest"]["sha256"], candidate_artifact_sha256=candidate_sha,
                      candidate_context_sha256=metadata["context_sha256"], delivery=delivery)
        result_delivery, _, _ = review_state.state_single_publish(slot / "result", "result", plan["run_id"], "result.json", result, inputs)
        return {"operation": "review", "action": "repair", "valid": True, "attempt_consumed": True, "claim_sha256": claim_input.sha256,
                "claim_delivery": claim_delivery, "candidate": metadata, "delivery": delivery, "result_delivery": result_delivery}, 0
    except Exception as exc:
        if isinstance(exc, contracts.DiagramError):
            diagnostic = copy.deepcopy(exc.diagnostic()); result["delivery"] = copy.deepcopy(exc.evidence.get("delivery"))
        else:
            diagnostic = contracts.make_diagnostic("internal/unexpected", "error", "Candidate execution failed", evidence={"exception_type": type(exc).__name__})
        result.update(status="delivery_failed" if result["delivery"] else "technical_failed", candidate_sha256=None,
                      candidate_artifact_sha256=None, candidate_context_sha256=None, diagnostics=[diagnostic])
        recovery = None
        try:
            recovery, _, _ = review_state.state_single_publish(slot / "result", "result", plan["run_id"], "result.json", result, inputs)
        except Exception as record_exc:
            recovery = {"complete": False, "exception_type": type(record_exc).__name__}
        if isinstance(exc, contracts.DiagramError) and (exc.code.startswith(("routing/", "provenance/")) or exc.code in {"review/candidate-ineligible", "review/protected-change"}):
            return {"operation": "review", "action": "repair", "valid": False, "attempt_consumed": True, "claim_sha256": claim_input.sha256,
                    "result": result, "claim_delivery": claim_delivery, "result_delivery": recovery}, 1
        if isinstance(exc, contracts.DiagramError):
            exc.evidence.update(attempt_consumed=True, claim_sha256=claim_input.sha256, claim_delivery=claim_delivery, attempt_result=result, result_delivery=recovery)
            raise
        return {"operation": "review", "action": "repair", "valid": False, "attempt_consumed": True, "claim_sha256": claim_input.sha256,
                "result": result, "claim_delivery": claim_delivery, "result_delivery": recovery}, 3


def cycle_candidate_root(initial, authorization_sha256):
    return {"state_version": 1, "run_id": initial["run"]["run_id"], "prepared_sha256": initial["manifest"].sha256,
            "prepared_path_sha256": context_bundle.bundle_hash(str(initial["manifest"].path).encode("utf-8")),
            "original": initial["run"]["original"], "authorization_sha256": authorization_sha256,
            "tool_version": contracts.TOOL_VERSION, "repair_rule_version": review_preservation.REPAIR_RULE_VERSION, "maximum_attempts": 2}


def cycle_candidate_metadata(metadata, header, manifest, baseline):
    review_evidence.review_object(metadata, CYCLE_CANDIDATE_FIELDS)
    cycle_candidate_shapes(metadata, baseline)
    review_evidence.review_version(metadata["candidate_version"])
    review_evidence.review_integer(metadata["attempt_index"], minimum=1)
    if metadata["attempt_index"] > 2:
        cycle_failure("review/attempt-exhausted", "Candidate exceeds the fixed attempt limit")
    for key in ("claim_sha256", "plan_sha256", "authorization_sha256", "parent_record_sha256", "before_artifact_sha256", "before_context_sha256", "artifact_sha256", "context_sha256"):
        review_evidence.review_digest(metadata[key], nullable=key in {"before_context_sha256", "context_sha256"})
    review_evidence.review_integer(metadata["artifact_bytes"])
    if (metadata["run_id"] != header["run_id"] or metadata["artifact_sha256"] != baseline["graph"].sha256
            or metadata["artifact_bytes"] != len(baseline["graph"].raw)
            or metadata["context_sha256"] != (baseline["context"].sha256 if baseline["context"] else None)
            or metadata["technical_status"] != "passed" or metadata["accepted"] is not False):
        cycle_failure("review/baseline-mismatch", "Candidate metadata differs from actual immutable graph/context")
    if (review_workflow.review_json_bytes(metadata["validation"]) != review_workflow.review_json_bytes(baseline["validation"])
            or not baseline["validation"]["valid"] or baseline["validation"]["warnings"]
            or metadata["source_preserved"] is not True or not cycle_semantic_gate(baseline["assessment"])):
        cycle_failure("review/candidate-ineligible", "Candidate no longer meets its technical gates")
    expected_assessment = baseline["assessment"]
    if review_workflow.review_json_bytes(metadata["semantic_context"]) != review_workflow.review_json_bytes(expected_assessment):
        cycle_failure("review/baseline-mismatch", "Candidate context receipt differs from its actual binding")


def cycle_read_candidate(path, expected):
    header, manifest, members = review_state.state_package_read(path, "candidate")
    review_workflow.review_input_expected(manifest, expected, "candidate")
    baseline = review_workflow.review_read_baseline(members["artifact/diagram.drawio"].path,
            members["artifact/context.json"].path if "artifact/context.json" in members else None,
            members["artifact/completion.json"].path if "artifact/completion.json" in members else None)
    metadata = review_state.state_payload(members["candidate.json"])
    cycle_candidate_metadata(metadata, header, manifest, baseline)
    return {"header": header, "manifest": manifest, "members": members, "metadata": metadata, "baseline": baseline,
            "inputs": review_workflow.review_unique_inputs([manifest, *members.values(), *baseline["inputs"]])}


def cycle_candidate_run(baseline, metadata, candidate_manifest, initial_manifest):
    run = review_workflow.review_initial_run(baseline, metadata["run_id"])
    initial = review_state.state_payload(initial_manifest)
    review_evidence.review_object(initial, {"review_bundle_version", "kind", "run_id", "members"})
    if initial["review_bundle_version"] != 1 or initial["kind"] != "prepared" or initial["run_id"] != metadata["run_id"]:
        cycle_failure("review/baseline-mismatch", "Candidate root reference is not its original prepared run")
    original_entries = {entry["name"]: entry for entry in initial["members"]}
    if "original/diagram.drawio" not in original_entries:
        cycle_failure("review/baseline-mismatch", "Candidate root reference lacks its original graph identity")
    run.update(review_contract_version=2, run_format_version=2,
               original={"artifact_sha256": original_entries["original/diagram.drawio"]["sha256"],
                         "context_sha256": original_entries.get("original/context.json", {}).get("sha256")},
               round={"index": metadata["attempt_index"], "kind": "candidate", "parent_record_sha256": metadata["parent_record_sha256"],
                      "repair_attempt": metadata["attempt_index"], "claim_sha256": metadata["claim_sha256"], "candidate_sha256": candidate_manifest.sha256},
               repair={"support": "explicit_authorization", "maximum_attempts": 2, "attempts_started": metadata["attempt_index"],
                       "authorization_sha256": metadata["authorization_sha256"]})
    run.pop("last_accepted"); run.pop("last_good")
    run["pointer_authority"] = "ledger-assessment"
    return run


def cycle_read_candidate_prepared(path):
    header, manifest, members = review_state.state_package_read(path, "candidate-prepared")
    baseline = review_workflow.review_read_baseline(members["candidate/diagram.drawio"].path,
            members["candidate/context.json"].path if "candidate/context.json" in members else None,
            members["candidate/completion.json"].path if "candidate/completion.json" in members else None)
    metadata = review_state.state_payload(members["candidate.json"])
    candidate_manifest = members["candidate-manifest.json"]
    candidate_header = review_state.state_payload(candidate_manifest)
    review_evidence.review_object(candidate_header, {"review_bundle_version", "kind", "run_id", "members"})
    if candidate_header["review_bundle_version"] != 2 or candidate_header["kind"] != "candidate" or candidate_header["run_id"] != header["run_id"]:
        cycle_failure("review/baseline-mismatch", "Candidate prepared reference has the wrong package kind or run")
    actual_references = {"candidate.json": members["candidate.json"]}
    for key in ("diagram.drawio", "context.json", "completion.json"):
        if "candidate/" + key in members:
            actual_references["artifact/" + key] = members["candidate/" + key]
    expected_entries = [{"name": name, "sha256": item.sha256, "bytes": len(item.raw)} for name, item in sorted(actual_references.items())]
    if candidate_header["members"] != expected_entries:
        cycle_failure("review/baseline-mismatch", "Candidate prepared snapshots differ from original candidate completion")
    cycle_candidate_metadata(metadata, candidate_header, candidate_manifest, baseline)
    run = cycle_candidate_run(baseline, metadata, candidate_manifest, members["initial-prepared.json"])
    index = review_evidence.review_object_index(baseline["tree"], baseline["validation"])
    if (review_workflow.review_json_bytes(run) != review_workflow.review_json_bytes(review_state.state_payload(members["run.json"]))
            or review_workflow.review_json_bytes(index) != review_workflow.review_json_bytes(review_state.state_payload(members["objects.json"]))):
        cycle_failure("review/baseline-mismatch", "Candidate prepared index/state differs from original candidate snapshots")
    return {"run": run, "index": index, "manifest": manifest, "members": members,
            "inputs": review_workflow.review_unique_inputs([manifest, *members.values(), *baseline["inputs"]])}


def cycle_matching_attempt(initial, root, attempt_index, claim_sha256, *, allow_partial_result=False):
    review_evidence.review_integer(attempt_index, minimum=1)
    review_evidence.review_digest(claim_sha256)
    state = review_state.inspect_review_state(initial["manifest"], root, allow_partial_result=allow_partial_result)
    if attempt_index > 2:
        cycle_failure("review/attempt-exhausted", "Review cannot exceed two attempts")
    attempts = [item for item in state["attempts"] if item["index"] == attempt_index]
    if not attempts or attempts[0]["claim_input"].sha256 != claim_sha256:
        cycle_failure("review/state-conflict", "Requested attempt does not identify its actual durable claim")
    return state, attempts[0]


def cycle_candidate_prepare(args):
    cycle_required(args, {"prepared", "expected_prepared_sha256", "candidate", "expected_candidate_sha256", "attempt", "claim_sha256",
                          "input", "expected_input_sha256", "output"})
    initial = cycle_initial(args)
    candidate = cycle_read_candidate(args.candidate, args.expected_candidate_sha256)
    metadata = candidate["metadata"]
    root = cycle_candidate_root(initial, metadata["authorization_sha256"])
    state, attempt = cycle_matching_attempt(initial, root, args.attempt, args.claim_sha256)
    result = attempt["result"]
    if (metadata["run_id"] != initial["run"]["run_id"] or metadata["claim_sha256"] != args.claim_sha256
            or metadata["attempt_index"] != args.attempt or result is None or result["status"] != "technical_passed"
            or result["candidate_sha256"] != candidate["manifest"].sha256 or attempt["marker"] is not None
            or attempt["claim"]["parent_record_sha256"] != metadata["parent_record_sha256"]):
        cycle_failure("review/state-conflict", "Candidate is not the pending technical result of this claim")
    baseline = review_workflow.review_read_baseline(args.input, args.context, args.input_completion_manifest)
    review_workflow.review_input_expected(baseline["graph"], args.expected_input_sha256, "input")
    if baseline["context"]:
        review_workflow.review_input_expected(baseline["context"], args.expected_context_sha256, "context")
    if (baseline["graph"].sha256 != metadata["artifact_sha256"] or (baseline["context"].sha256 if baseline["context"] else None) != metadata["context_sha256"]):
        cycle_failure("review/baseline-mismatch", "Explicit candidate input differs from its package")
    run = cycle_candidate_run(baseline, metadata, candidate["manifest"], initial["manifest"])
    index = review_evidence.review_object_index(baseline["tree"], baseline["validation"])
    members = [review_workflow.review_member("run.json", review_workflow.review_json_bytes(run)),
               review_workflow.review_member("objects.json", review_workflow.review_json_bytes(index)),
               review_workflow.review_member("initial-prepared.json", initial["manifest"].raw),
               review_workflow.review_member("candidate-manifest.json", candidate["manifest"].raw),
               review_workflow.review_member("candidate.json", candidate["members"]["candidate.json"].raw)]
    for key in ("diagram.drawio", "context.json", "completion.json"):
        if "artifact/" + key in candidate["members"]:
            item = candidate["members"]["artifact/" + key]
            members.append(review_workflow.review_member("candidate/" + key, item.raw, limit=review_workflow.REVIEW_GRAPH_BYTES if key.endswith("drawio") else semantic_context.MAX_BYTES))
    inputs = review_workflow.review_unique_inputs([*initial["inputs"], *candidate["inputs"], *state["inputs"], *baseline["inputs"]])
    delivery = review_state.state_package_publish(args.output, "candidate-prepared", run["run_id"], members, inputs)
    return {"operation": "review", "action": "prepare", "valid": True, "run_id": run["run_id"], "round": run["round"],
            "states": run["validation"], "semantic_context": baseline["assessment"], "object_count": len(index["objects"]), "delivery": delivery}, 0


def cycle_candidate_record(args):
    cycle_required(args, {"prepared", "expected_prepared_sha256", "input", "expected_input_sha256", "output", "evidence_dir", "report", "expected_report_sha256"})
    prepared = cycle_read_candidate_prepared(args.prepared)
    review_workflow.review_input_expected(prepared["manifest"], args.expected_prepared_sha256, "prepared")
    baseline = review_workflow.review_read_baseline(args.input, args.context, args.input_completion_manifest)
    review_workflow.review_input_expected(baseline["graph"], args.expected_input_sha256, "input")
    if baseline["context"]:
        review_workflow.review_input_expected(baseline["context"], args.expected_context_sha256, "context")
    if (baseline["graph"].sha256 != prepared["run"]["artifact"]["sha256"]
            or (baseline["context"].sha256 if baseline["context"] else None) != prepared["run"]["context"]["sha256"]):
        cycle_failure("review/baseline-mismatch", "Candidate record input differs from its prepared baseline")
    evidence_dir = context_bundle.bundle_absolute(args.evidence_dir)
    if context_bundle.bundle_absolute(args.report) != evidence_dir / "report.json":
        cycle_failure("delivery/path-unsafe", "Review report must be report.json inside the explicit evidence directory")
    report, report_input = review_workflow.review_read_json(args.report)
    review_workflow.review_input_expected(report_input, args.expected_report_sha256, "report")
    receipt = cycle_assess_report(report, prepared, report_input.sha256)
    source_members = {"report.json": report_input, "prepared.json": prepared["manifest"]}
    for preview in report["previews"]:
        source_members[preview["name"]] = review_workflow.review_read_evidence(evidence_dir / preview["name"], max_bytes=preview_png.PNG_MAX_BYTES)
    if report["export"]:
        for key in ("stdout", "stderr"):
            declaration = report["export"][key]
            if declaration:
                source_members[declaration["name"]] = review_workflow.review_read_evidence(evidence_dir / declaration["name"], max_bytes=review_workflow.REVIEW_LOG_BYTES)
    # The prospective receipt is validated separately; images are independently decoded from the actual bytes.
    checks = cycle_verify_images(report, dict(source_members, **{"receipt.json": None}))
    if sum(item["bytes"] for item in checks) > preview_png.PNG_TOTAL_BYTES:
        cycle_failure("review/resource-limit", "Total PNG byte budget exceeded")
    members = [review_workflow.review_member(name, item.raw, limit=preview_png.PNG_MAX_BYTES if name.endswith("png") else
                review_workflow.REVIEW_LOG_BYTES if name.endswith("txt") else semantic_context.MAX_BYTES) for name, item in source_members.items()]
    members.append(review_workflow.review_member("receipt.json", review_workflow.review_json_bytes(receipt)))
    inputs = review_workflow.review_unique_inputs([*prepared["inputs"], *baseline["inputs"], *source_members.values()])
    delivery = review_state.state_package_publish(args.output, "candidate-record", prepared["run"]["run_id"], members, inputs)
    return {"operation": "review", "action": "record", "valid": True, "review": receipt, "semantic_context": baseline["assessment"],
            "png_checks": checks, "delivery": delivery}, 0


def run_review_cycle(args):
    cycle_argument_scope(args)
    if args.context is None and (args.expected_context_sha256 is not None or args.input_completion_manifest is not None):
        cycle_failure("input/invalid", "Context digest/manifest require their explicit input")
    if args.action == "plan":
        return cycle_plan(args)
    if args.action == "repair":
        return cycle_repair(args)
    if args.action == "prepare":
        return cycle_candidate_prepare(args)
    if args.action == "record":
        return cycle_candidate_record(args)
    if args.action == "assess":
        return cycle_assess(args)
    cycle_failure("review/action-unsupported", "Unsupported review action")


def cycle_assessment_publish(args, initial, root, state, attempt, assessment, inputs):
    # Recheck the durable parent before publishing, then again before appending its marker.
    latest, actual = cycle_matching_attempt(initial, root, args.attempt, args.claim_sha256, allow_partial_result=args.stop_reason is not None)
    if actual["marker"] is not None:
        cycle_failure("review/state-conflict", "An assessment marker already exists for this attempt")
    inputs = review_workflow.review_unique_inputs([*inputs, *latest["inputs"]])
    delivery = review_state.state_package_publish(args.output, "assessment", initial["run"]["run_id"],
                    [review_workflow.review_member("assessment.json", review_workflow.review_json_bytes(assessment))], inputs)
    marker_delivery = None
    try:
        latest, actual = cycle_matching_attempt(initial, root, args.attempt, args.claim_sha256, allow_partial_result=args.stop_reason is not None)
        if actual["marker"] is not None:
            cycle_failure("review/state-conflict", "Another assessment committed this attempt first")
        _, am, amembers = review_state.state_package_read(context_bundle.bundle_absolute(args.output) / "completion.json", "assessment")
        marker = {"assessment_marker_version": 1, "run_id": assessment["run_id"], "attempt_index": args.attempt,
                  "claim_sha256": args.claim_sha256, "assessment_sha256": am.sha256, "decision": assessment["decision"],
                  "continue": assessment["continue"], "original": assessment["original"], "last_accepted": assessment["last_accepted"],
                  "last_good": assessment["last_good"]}
        marker_delivery, _, _ = review_state.state_single_publish(actual["directory"] / "assessment", "assessment-marker", assessment["run_id"],
                    "assessment.json", marker, review_workflow.review_unique_inputs([*inputs, *latest["inputs"], am, *amembers.values()]))
        review_state.inspect_review_state(initial["manifest"], root, allow_partial_result=args.stop_reason is not None)
    except contracts.DiagramError as exc:
        actual_marker = marker_delivery or exc.evidence.get("delivery")
        exc.evidence.update(assessment_delivery=delivery, marker_delivery=actual_marker,
                            ledger_marker_committed=bool(actual_marker and actual_marker.get("committed")),
                            ledger_advanced=False, attempt_consumed=True, claim_sha256=args.claim_sha256)
        raise
    return {"operation": "review", "action": "assess", "valid": True, "assessment": assessment,
            "delivery": delivery, "marker_delivery": marker_delivery, "ledger_advanced": True}, 0


def cycle_assessment_base(initial, state, attempt):
    return {"review_assessment_version": 1, "run_id": initial["run"]["run_id"], "attempt_index": attempt["index"],
            "claim_sha256": attempt["claim_input"].sha256, "parent_record_sha256": attempt["claim"]["parent_record_sha256"],
            "candidate_sha256": None, "candidate_record_sha256": None, "resolutions_sha256": None,
            "checks": {key: "not_assessed" for key in CYCLE_CHECKS}, "decision": "stopped", "stop_reason": "incomplete-attempt", "continue": False,
            "visual_status": "not_assessed", "remaining_issues": [], "metrics": [], "original": copy.deepcopy(initial["run"]["original"]),
            "last_accepted": copy.deepcopy(state["last_accepted"]), "last_good": copy.deepcopy(state["last_good"]),
            "attempts_started": len(state["attempts"]), "maximum_attempts": 2, "trust": "externally_supplied",
            "acceptance_basis": "technical_gates_and_declared_visual_improvement", "remaining_coverage": "unavailable"}


def cycle_stop_assess(args):
    cycle_required(args, {"prepared", "expected_prepared_sha256", "authorization", "expected_authorization_sha256", "attempt", "claim_sha256", "output"})
    review_evidence.review_enum(args.stop_reason, {"incomplete-attempt"}, code="input/invalid")
    forbidden = {"input", "context", "input_completion_manifest", "expected_input_sha256", "expected_context_sha256", "parent_prepared",
                 "expected_parent_prepared_sha256", "parent_record", "expected_parent_record_sha256", "parent_assessment", "expected_assessment_sha256",
                 "plan", "expected_plan_sha256", "before", "expected_before_sha256", "before_context", "expected_before_context_sha256",
                 "before_completion_manifest", "candidate", "expected_candidate_sha256", "candidate_prepared", "expected_candidate_prepared_sha256",
                 "record", "expected_record_sha256", "resolutions", "expected_resolutions_sha256", "report", "evidence_dir", "expected_report_sha256"}
    if any(getattr(args, key, None) is not None for key in forbidden):
        cycle_failure("input/invalid", "Stop recovery cannot be mixed with candidate acceptance inputs")
    initial = cycle_initial(args)
    _, auth_input, root = cycle_authorization(args, initial)
    state, attempt = cycle_matching_attempt(initial, root, args.attempt, args.claim_sha256, allow_partial_result=True)
    if attempt["marker"] is not None or args.attempt != len(state["attempts"]):
        cycle_failure("review/state-conflict", "Only the unassessed final claim can be stopped")
    assessment = cycle_assessment_base(initial, state, attempt)
    inputs = review_workflow.review_unique_inputs([*initial["inputs"], auth_input, *state["inputs"]])
    context_bundle.bundle_new_directory(args.output)
    return cycle_assessment_publish(args, initial, root, state, attempt, assessment, inputs)


def cycle_resolution_region(item, report, index):
    previews = {preview["id"]: preview for preview in report["previews"]}
    preview = previews.get(item["preview_id"])
    if preview is None or item["preview_sha256"] != preview["sha256"] or item["preview_id"] not in report["reviews"]["agent"]["viewed_previews"]:
        cycle_failure("review/reference-invalid", "Resolution does not identify an actually declared viewed candidate image")
    review_evidence.review_rect(item["region"])
    if not review_evidence.review_inside(item["region"], preview["width"], preview["height"]):
        cycle_failure("review/mapping-invalid", "Resolution region is outside the actual image")
    full = previews.get("full")
    mapping = full["mapping"] if full else None
    if mapping is None:
        return False
    region = dict(item["region"])
    if preview["kind"] == "cutout":
        transform = preview["mapping"]
        region = {"x": region["x"] / transform["scale_x"] + transform["crop"]["x"],
                  "y": region["y"] / transform["scale_y"] + transform["crop"]["y"],
                  "width": region["width"] / transform["scale_x"], "height": region["height"] / transform["scale_y"]}
    objects = {review_evidence.review_target_key(obj["target"]): obj for obj in index["objects"]}
    for target in item["issue_key"]["targets"]:
        key = review_evidence.review_target_key(target)
        if key not in objects:
            cycle_failure("review/reference-invalid", "Resolution target is absent from the candidate")
        if not review_evidence.review_target_intersects(objects[key], mapping, region, objects):
            cycle_failure("review/mapping-invalid", "Resolution region does not intersect the projected candidate target")
    return True


def cycle_issue_groups(receipt, *, blocking_only=True, reviewer=None):
    result = {}
    for issue in receipt["issues"]:
        if blocking_only and issue["severity"] not in {"blocker", "warning"} or reviewer and issue["reviewer_kind"] != reviewer:
            continue
        result.setdefault(cycle_key_tuple(cycle_issue_key(issue)), []).append(issue)
    return result


def cycle_review_agreement(record):
    if record["receipt"]["review_consensus"] == "conflict":
        return False
    reviews = record["report"]["reviews"]
    if reviews["human"]["state"] not in {"passed", "failed"}:
        return True
    # Stable typed keys also catch a conflict concealed by renaming issue IDs between reviewers.
    summaries = []
    for kind in ("agent", "human"):
        groups = cycle_issue_groups(record["receipt"], reviewer=kind)
        summaries.append({key: sorted(issue["severity"] for issue in issues) for key, issues in groups.items()})
    return summaries[0] == summaries[1]


def cycle_resolution_checks(data, initial, attempt, parent, record, prepared, plan, before_tree, after_tree):
    review_evidence.review_object(data, {"issue_resolution_version", "run_id", "claim_sha256", "parent_record_sha256", "candidate_record_sha256", "reviewer_kind", "items"})
    review_evidence.review_version(data["issue_resolution_version"])
    if (data["run_id"] != initial["run"]["run_id"] or data["claim_sha256"] != attempt["claim_input"].sha256
            or data["parent_record_sha256"] != parent["manifest"].sha256 or data["candidate_record_sha256"] != record["manifest"].sha256
            or data["reviewer_kind"] != "agent"):
        cycle_failure("review/resolution-invalid", "Resolution sidecar differs from the actual claim and review records")
    items = contracts.require_list(data["items"], "resolution items")
    if len(items) > 128:
        cycle_failure("review/resource-limit", "Too many issue resolutions")
    previous = cycle_issue_groups(parent["receipt"])
    current = cycle_issue_groups(record["receipt"])
    agent_all = cycle_issue_groups(record["receipt"], blocking_only=False, reviewer="agent")
    planned = {cycle_key_tuple(key): [action for action in plan["actions"] if key in action["issue_keys"]]
               for action in plan["actions"] for key in action["issue_keys"]}
    seen, metrics = set(), []
    resolver = review_evidence.ReviewValidator()
    complete, spatial, non_regressing, improvement, uncertain = True, True, True, False, False
    rank = {"note": 0, "warning": 1, "blocker": 2}
    for key, issues in current.items():
        if key not in previous or max(rank[i["severity"]] for i in issues) > max(rank[i["severity"]] for i in previous[key]):
            non_regressing = False
    for item in items:
        review_evidence.review_object(item, {"issue_key", "previous_issue_ids", "disposition", "candidate_issue_ids", "preview_id", "preview_sha256", "region", "observation", "uncertainty"})
        review_evidence.review_object(item["issue_key"], {"code", "targets"})
        review_evidence.review_text(item["issue_key"]["code"], maximum=128)
        targets = contracts.require_list(item["issue_key"]["targets"], "resolution targets")
        if not targets or len(targets) > 32:
            cycle_failure("review/resolution-invalid", "Resolution must have bounded typed targets")
        target_keys = [resolver.target(target) for target in targets]
        if len(set(target_keys)) != len(target_keys):
            cycle_failure("schema/duplicate", "Resolution targets are duplicated")
        key = cycle_key_tuple(item["issue_key"])
        if key in seen or key not in previous:
            cycle_failure("review/resolution-invalid", "Resolution key is duplicated or outside the actual prior issues")
        seen.add(key)
        review_evidence.review_enum(item["disposition"], {"resolved", "improved", "unchanged", "uncertain"}, code="review/resolution-invalid")
        review_evidence.review_text(item["observation"])
        review_evidence.review_text(item["uncertainty"], nullable=True)
        resolver.sid(item["preview_id"])
        review_evidence.review_digest(item["preview_sha256"])
        for field in ("previous_issue_ids", "candidate_issue_ids"):
            ids = contracts.require_list(item[field], field)
            for sid in ids:
                resolver.sid(sid)
            if len(set(ids)) != len(ids):
                cycle_failure("schema/duplicate", "Resolution issue aliases are duplicated")
        if set(item["previous_issue_ids"]) != {issue["issue_id"] for issue in previous[key]}:
            cycle_failure("review/resolution-invalid", "Resolution does not preserve all actual prior issue aliases")
        expected_ids = {issue["issue_id"] for issue in agent_all.get(key, [])}
        if set(item["candidate_issue_ids"]) != expected_ids:
            cycle_failure("review/resolution-invalid", "Resolution candidate aliases do not exactly match that typed issue key")
        spatial = cycle_resolution_region(item, record["report"], prepared["index"]) and spatial
        disposition = item["disposition"]
        if disposition == "uncertain":
            if item["uncertainty"] is None:
                cycle_failure("review/resolution-invalid", "Uncertain resolution requires an explicit uncertainty")
            uncertain = True
            continue
        if item["uncertainty"] is not None:
            uncertain = True
        if disposition in {"unchanged", "improved"}:
            old_severity = max(rank[i["severity"]] for i in previous[key])
            if not current.get(key) or max(rank[i["severity"]] for i in current[key]) != old_severity or not expected_ids:
                complete = False
        if disposition in {"resolved", "improved"} and key not in planned:
            complete = False
        evaluations = []
        for action in planned.get(key, []):
            intent = CYCLE_ISSUE_INTENTS[item["issue_key"]["code"]]
            before_metric = review_preservation.repair_native_metric(before_tree, action["target"]["id"], intent)
            after_metric = review_preservation.repair_native_metric(after_tree, action["target"]["id"], intent)
            comparison = review_preservation.repair_metric_comparison(before_metric, after_metric, item["issue_key"]["code"])
            metrics.append({"issue_key": copy.deepcopy(item["issue_key"]), "target": action["target"], "intent": intent,
                            "before": before_metric, "after": after_metric, "comparison": comparison})
            evaluations.append(comparison)
            non_regressing = non_regressing and comparison["non_regressing"]
        if disposition == "resolved":
            resolved = not current.get(key) and bool(evaluations) and all(value["resolved"] for value in evaluations)
            complete = complete and resolved
            improvement = improvement or resolved and any(value["improved"] for value in evaluations)
        elif disposition == "improved":
            improved = bool(evaluations) and all(value["non_regressing"] for value in evaluations) and any(value["improved"] for value in evaluations)
            complete = complete and improved
            improvement = improvement or improved
    if seen != set(previous):
        cycle_failure("review/resolution-incomplete", "Resolution sidecar must explicitly account for every prior blocker/warning key")
    # All action metrics are independently checked even if their issue was declared unchanged or uncertain.
    for action in plan["actions"]:
        before_metric = review_preservation.repair_native_metric(before_tree, action["target"]["id"], action["intent"])
        after_metric = review_preservation.repair_native_metric(after_tree, action["target"]["id"], action["intent"])
        for key in action["issue_keys"]:
            if CYCLE_ISSUE_INTENTS[key["code"]] == action["intent"]:
                non_regressing = non_regressing and review_preservation.repair_metric_comparison(before_metric, after_metric, key["code"])["non_regressing"]
    return {"complete": complete, "spatial": spatial, "non_regressing": non_regressing,
            "improvement": improvement, "uncertain": uncertain, "metrics": metrics}


def cycle_source_comparison(before, after):
    if (before["context"] is None) != (after["context"] is None):
        return False
    if before["context"] is None:
        return True
    original = semantic_context.decode_context(before["context"].raw).data
    candidate = semantic_context.decode_context(after["context"].raw).data
    changes = {"context_changes_version": 1, "expected_input_sha256": before["graph"].sha256,
               "expected_context_sha256": before["context"].sha256, "mode": "geometry-only"}
    return ({key: value for key, value in original.items() if key != "artifact"} == {key: value for key, value in candidate.items() if key != "artifact"}
            and not provenance.audit_context_preservation(original, candidate, changes))


def cycle_assess(args):
    if args.stop_reason is not None:
        return cycle_stop_assess(args)
    cycle_required(args, {"prepared", "expected_prepared_sha256", "parent_prepared", "expected_parent_prepared_sha256", "parent_record", "expected_parent_record_sha256",
                          "plan", "expected_plan_sha256", "authorization", "expected_authorization_sha256", "before", "expected_before_sha256", "attempt", "claim_sha256",
                          "candidate", "expected_candidate_sha256", "candidate_prepared", "expected_candidate_prepared_sha256", "record", "expected_record_sha256",
                          "resolutions", "expected_resolutions_sha256", "output"})
    if args.before_context is None and (args.expected_before_context_sha256 is not None or args.before_completion_manifest is not None):
        cycle_failure("input/invalid", "Before context digest/manifest require their explicit input")
    initial = cycle_initial(args)
    auth, auth_input, root = cycle_authorization(args, initial)
    state, attempt = cycle_matching_attempt(initial, root, args.attempt, args.claim_sha256)
    if attempt["marker"] is not None or args.attempt != len(state["attempts"]):
        cycle_failure("review/state-conflict", "Only the pending final attempt can be assessed")
    parent_args = copy.copy(args)
    parent_args.input, parent_args.expected_input_sha256 = args.before, args.expected_before_sha256
    parent_args.context, parent_args.expected_context_sha256 = args.before_context, args.expected_before_context_sha256
    parent_args.input_completion_manifest = args.before_completion_manifest
    parent_args.record, parent_args.expected_record_sha256 = args.parent_record, args.expected_parent_record_sha256
    parent, parent_record, before, _, parent_assessment, extra = cycle_parent(parent_args, initial, root)
    _, plan_manifest, plan_members = review_state.state_package_read(args.plan, "plan")
    review_workflow.review_input_expected(plan_manifest, args.expected_plan_sha256, "plan")
    plan = review_state.state_payload(plan_members["plan.json"])
    review_evidence.review_object(plan, CYCLE_PLAN_FIELDS)
    if (plan["ledger_root_sha256"] is None and args.attempt != 1 or plan["ledger_root_sha256"] not in {None, state["root"].sha256}):
        cycle_failure("review/plan-mismatch", "Plan root reference is not this durable run")
    prior_state = dict(state, next_attempt=args.attempt, terminal=False, attempts=state["attempts"][:-1],
                       root=state["root"] if plan["ledger_root_sha256"] else None)
    expected_plan = cycle_plan_data(initial, auth, auth_input, parent, parent_record, before, prior_state, parent_assessment)
    claim = attempt["claim"]
    if (review_workflow.review_json_bytes(plan) != review_workflow.review_json_bytes(expected_plan) or not plan["can_repair"]
            or plan_members["authorization.json"].raw != auth_input.raw or plan_members["prepared.json"].raw != initial["manifest"].raw
            or plan_members["record.json"].raw != parent_record["manifest"].raw or claim["plan_sha256"] != plan_manifest.sha256
            or claim["action_fingerprint"] != cycle_action_fingerprint(plan)
            or claim["parent_record_sha256"] != parent_record["manifest"].sha256 or claim["input_artifact_sha256"] != before["graph"].sha256
            or claim["input_context_sha256"] != (before["context"].sha256 if before["context"] else None)):
        cycle_failure("review/plan-mismatch", "Independent plan/input/permission reconstruction differs from this actual claim")
    candidate = cycle_read_candidate(args.candidate, args.expected_candidate_sha256)
    metadata, after = candidate["metadata"], candidate["baseline"]
    result = attempt["result"]
    if (result is None or result["status"] != "technical_passed" or result["candidate_sha256"] != candidate["manifest"].sha256
            or metadata["claim_sha256"] != args.claim_sha256 or metadata["plan_sha256"] != plan_manifest.sha256
            or metadata["authorization_sha256"] != auth_input.sha256 or metadata["actions"] != plan["actions"]
            or metadata["before_artifact_sha256"] != before["graph"].sha256
            or metadata["before_context_sha256"] != (before["context"].sha256 if before["context"] else None)
            or metadata["parent_record_sha256"] != parent_record["manifest"].sha256 or metadata["attempt_index"] != args.attempt):
        cycle_failure("review/state-conflict", "Candidate is not the actual result attached to this claim and plan")
    prepared = cycle_read_candidate_prepared(args.candidate_prepared)
    review_workflow.review_input_expected(prepared["manifest"], args.expected_candidate_prepared_sha256, "candidate-prepared")
    if (prepared["run"]["round"]["candidate_sha256"] != candidate["manifest"].sha256
            or prepared["members"]["initial-prepared.json"].raw != initial["manifest"].raw
            or prepared["run"]["round"]["claim_sha256"] != args.claim_sha256):
        cycle_failure("review/baseline-mismatch", "Actual candidate prepared package belongs to another claim")
    record = cycle_read_record(args.record, args.expected_record_sha256, prepared)
    resolutions, resolution_input = review_workflow.review_read_json(args.resolutions)
    review_workflow.review_input_expected(resolution_input, args.expected_resolutions_sha256, "resolutions")
    before_tree = review_preservation.preservation_parse(before["graph"].raw)
    after_tree = review_preservation.preservation_parse(after["graph"].raw)
    protection = review_preservation.protected_repair_comparison(before_tree, after_tree, plan["actions"])
    source_preserved = cycle_source_comparison(before, after)
    semantic_preserved = context_native.original_model(before_tree) == context_native.original_model(after_tree)
    # original_model includes geometry-independent business facts only.
    resolution = cycle_resolution_checks(resolutions, initial, attempt, parent_record, record, prepared, plan, before_tree, after_tree)
    pexport, cexport = parent_record["report"]["export"], record["report"]["export"]
    environment = pexport is not None and cexport is not None and pexport["renderer"] == cexport["renderer"]
    parent_full = next((p for p in parent_record["report"]["previews"] if p["kind"] == "full"), None)
    current_full = next((p for p in record["report"]["previews"] if p["kind"] == "full"), None)
    full_coverage = (record["receipt"]["states"]["preview_export"] == "passed" and record["report"]["reviews"]["agent"]["full_image_viewed"]
                     and record["receipt"]["states"]["agent_image_review"] in {"passed", "failed"}
                     and current_full is not None and current_full["mapping"] is not None and resolution["spatial"]
                     and (parent_full is None or current_full["sha256"] != parent_full["sha256"]))
    agreement = cycle_review_agreement(parent_record) and cycle_review_agreement(record)
    assessment = cycle_assessment_base(initial, state, attempt)
    booleans = {"technical": after["validation"]["valid"] and not after["validation"]["warnings"], "protected_projection": protection["preserved"],
                "semantic_context": semantic_preserved and cycle_semantic_gate(before["assessment"]) and cycle_semantic_gate(after["assessment"]),
                "source_preservation": source_preserved, "environment": environment, "full_and_target_coverage": full_coverage,
                "resolutions": resolution["complete"] and not resolution["uncertain"], "no_regression": resolution["non_regressing"] and agreement,
                "metric_improvement": resolution["improvement"] and after["graph"].sha256 != before["graph"].sha256}
    remaining = [issue for issue in record["receipt"]["issues"] if issue["severity"] in {"blocker", "warning"}]
    assessment.update(candidate_sha256=candidate["manifest"].sha256, candidate_record_sha256=record["manifest"].sha256, resolutions_sha256=resolution_input.sha256,
                      checks={key: "passed" if value else "failed" for key, value in booleans.items()}, remaining_issues=remaining,
                      metrics=resolution["metrics"], remaining_coverage="complete", visual_status=record["receipt"]["states"]["agent_image_review"])
    if all(booleans.values()):
        pointer = {"artifact_sha256": after["graph"].sha256, "context_sha256": after["context"].sha256 if after["context"] else None,
                   "candidate_sha256": candidate["manifest"].sha256, "record_sha256": record["manifest"].sha256}
        assessment.update(decision="accepted", last_accepted=pointer, stop_reason="scope_complete")
        if not remaining and assessment["visual_status"] == "passed":
            assessment["last_good"] = pointer
        if remaining:
            next_state = dict(state, next_attempt=args.attempt + 1, terminal=False, attempts=[], root=state["root"])
            next_plan = cycle_plan_data(initial, auth, auth_input, prepared, record, after, next_state, "0" * 64)
            assessment["continue"] = args.attempt < 2 and next_plan["can_repair"]
            assessment["stop_reason"] = None if assessment["continue"] else "budget_exhausted" if args.attempt == 2 else "unrepairable_remaining"
    else:
        stopped = not environment or not full_coverage or resolution["uncertain"] or not agreement
        assessment["decision"] = "stopped" if stopped else "rejected"
        assessment["stop_reason"] = ("environment_mismatch" if not environment else "incomplete_visual_coverage" if not full_coverage else
                "uncertain_resolution" if resolution["uncertain"] else "conflicting_review" if not agreement else
                next(key + "_failed" for key in CYCLE_CHECKS if not booleans[key]))
    inputs = review_workflow.review_unique_inputs([*initial["inputs"], auth_input, *state["inputs"], *parent["inputs"], *parent_record["inputs"],
                *before["inputs"], *extra, plan_manifest, *plan_members.values(), *candidate["inputs"], *prepared["inputs"], *record["inputs"], resolution_input])
    context_bundle.bundle_new_directory(args.output)
    return cycle_assessment_publish(args, initial, root, state, attempt, assessment, inputs)


def cycle_argument_scope(args):
    common = {"prepared", "expected_prepared_sha256", "output"}
    graph = {"input", "expected_input_sha256", "context", "expected_context_sha256", "input_completion_manifest"}
    auth = {"authorization", "expected_authorization_sha256"}
    parent = {"parent_prepared", "expected_parent_prepared_sha256", "record", "expected_record_sha256", "parent_assessment", "expected_assessment_sha256"}
    plan = {"plan", "expected_plan_sha256"}
    attempt = {"attempt", "claim_sha256"}
    candidate = {"candidate", "expected_candidate_sha256"}
    report = {"evidence_dir", "report", "expected_report_sha256"}
    allowed = {"plan": common | graph | auth | parent, "repair": common | graph | auth | parent | plan,
               "prepare": common | graph | attempt | candidate, "record": common | graph | report,
               "assess": common | auth | attempt | {"stop_reason"} if args.stop_reason is not None else common | auth | attempt | plan | candidate |
                   {"parent_prepared", "expected_parent_prepared_sha256", "parent_record", "expected_parent_record_sha256", "parent_assessment", "expected_assessment_sha256",
                    "before", "expected_before_sha256", "before_context", "expected_before_context_sha256", "before_completion_manifest", "candidate_prepared",
                    "expected_candidate_prepared_sha256", "record", "expected_record_sha256", "resolutions", "expected_resolutions_sha256"}}
    if args.action not in allowed:
        cycle_failure("review/action-unsupported", "Unsupported review action")
    unused = sorted(key for key, value in vars(args).items() if value is not None and key not in allowed[args.action] | {"command", "action", "func"})
    if unused:
        cycle_failure("input/invalid", "Arguments do not apply to this explicit review action", arguments=unused)


def cycle_metric_schema(metric, resolver):
    contracts.require_mapping(metric, "native repair metric")
    review_evidence.review_enum(metric.get("kind"), {"edge-label", "edge-route"})
    base = {"metric_version", "kind", "target"}
    extra = ({"bounds_quality", "label_position", "node_overlap_count", "label_overlap_count", "unrelated_edge_overlap_count", "carrier_usable", "outside_container", "conflict_count", "conflicts"}
             if metric["kind"] == "edge-label" else {"bend_count", "manhattan_length", "terminal_clearance", "minimum_terminal_clearance", "clearance_violation", "node_crossing_count", "crossed_node_ids"})
    review_evidence.review_object(metric, base | extra)
    review_evidence.review_version(metric["metric_version"])
    if resolver.target(metric["target"])[0] != "edge":
        cycle_failure("schema/type", "Native repair metrics require typed edge targets")
    if metric["kind"] == "edge-label":
        review_evidence.review_enum(metric["bounds_quality"], {"estimated"})
        position = contracts.require_list(metric["label_position"], "label position")
        if len(position) != 2:
            cycle_failure("schema/type", "Label position requires exactly two finite coordinates")
        for value in position:
            review_evidence.review_number(value)
        for key in ("node_overlap_count", "label_overlap_count", "unrelated_edge_overlap_count", "conflict_count"):
            review_evidence.review_integer(metric[key])
        for key in ("carrier_usable", "outside_container"):
            cycle_boolean(metric[key], key)
        review_evidence.review_object(metric["conflicts"], {"blocking_node_ids", "blocking_label_ids", "blocking_edge_ids", "outside_container"})
        cycle_boolean(metric["conflicts"]["outside_container"], "outside_container")
        for key in ("blocking_node_ids", "blocking_label_ids", "blocking_edge_ids"):
            identifiers = contracts.require_list(metric["conflicts"][key], key)
            for sid in identifiers:
                resolver.sid(sid)
            if len(set(identifiers)) != len(identifiers):
                cycle_failure("schema/duplicate", "Metric conflict identities are duplicated")
    else:
        for key in ("bend_count", "node_crossing_count"):
            review_evidence.review_integer(metric[key])
        for key in ("manhattan_length", "terminal_clearance", "minimum_terminal_clearance"):
            review_evidence.review_number(metric[key])
            if metric[key] < 0:
                cycle_failure("schema/type", "Route metric distances cannot be negative")
        cycle_boolean(metric["clearance_violation"], "clearance_violation")
        ids = contracts.require_list(metric["crossed_node_ids"], "crossed nodes")
        for sid in ids:
            resolver.sid(sid)
        if len(set(ids)) != len(ids):
            cycle_failure("schema/duplicate", "Metric crossed node IDs are duplicated")


def cycle_candidate_shapes(metadata, baseline):
    resolver = review_evidence.ReviewValidator()
    actions = contracts.require_list(metadata["actions"], "candidate actions")
    if not 1 <= len(actions) <= 32:
        cycle_failure("review/resource-limit", "Candidate requires one to 32 declared actions")
    targets = set()
    for action in actions:
        review_evidence.review_object(action, {"intent", "target", "issue_keys", "before_metric", "strategy_id"})
        review_evidence.review_enum(action["intent"], review_preservation.REPAIR_INTENTS)
        target = resolver.target(action["target"])
        if target[0] != "edge" or target in targets:
            cycle_failure("schema/duplicate", "Candidate actions require unique typed edges")
        targets.add(target)
        expected_strategy = "bounded-native-route-v1" if action["intent"] == "reroute-edge" else "native-label-reflow-v1"
        if action["strategy_id"] != expected_strategy:
            cycle_failure("review/action-not-authorized", "Candidate strategy differs from its supported intent")
        keys = contracts.require_list(action["issue_keys"], "action issue keys")
        if not keys or len(keys) > 128:
            cycle_failure("review/resource-limit", "Candidate action requires bounded issue keys")
        seen = set()
        for key in keys:
            review_evidence.review_object(key, {"code", "targets"})
            review_evidence.review_enum(key["code"], set(CYCLE_ISSUE_INTENTS))
            key_targets = contracts.require_list(key["targets"], "issue targets")
            if not key_targets or len(key_targets) > 32:
                cycle_failure("review/resource-limit", "Candidate issue requires bounded targets")
            identities = [resolver.target(value) for value in key_targets]
            if len(set(identities)) != len(identities) or target not in identities:
                cycle_failure("review/reference-invalid", "Candidate issue key does not contain its action target exactly once")
            normalized = cycle_key_tuple(key)
            if normalized in seen:
                cycle_failure("schema/duplicate", "Candidate action issue keys are duplicated")
            seen.add(normalized)
        cycle_metric_schema(action["before_metric"], resolver)
        if action["before_metric"]["target"] != action["target"] or action["before_metric"]["kind"] != ("edge-label" if action["intent"] == "reposition-edge-label" else "edge-route"):
            cycle_failure("review/baseline-mismatch", "Action baseline metric differs from its target or intent")
    metrics = contracts.require_list(metadata["metrics"], "candidate metrics")
    if len(metrics) != len(actions):
        cycle_failure("review/baseline-mismatch", "Candidate metrics must cover every action exactly once")
    measured = set()
    action_map = {review_evidence.review_target_key(action["target"]): action for action in actions}
    for metric in metrics:
        review_evidence.review_object(metric, {"target", "intent", "before", "after"})
        key = resolver.target(metric["target"])
        if key not in action_map or key in measured or metric["intent"] != action_map[key]["intent"]:
            cycle_failure("review/baseline-mismatch", "Candidate metric target/intent differs from its actions")
        measured.add(key)
        for name in ("before", "after"):
            cycle_metric_schema(metric[name], resolver)
            if metric[name]["target"] != metric["target"]:
                cycle_failure("review/baseline-mismatch", "Native metric has a different target")
        actual = review_preservation.repair_native_metric(baseline["tree"], metric["target"]["id"], metric["intent"])
        if (review_workflow.review_json_bytes(metric["after"]) != review_workflow.review_json_bytes(actual)
                or review_workflow.review_json_bytes(metric["before"]) != review_workflow.review_json_bytes(action_map[key]["before_metric"])):
            cycle_failure("review/baseline-mismatch", "Candidate metrics differ from actual geometry or declared action baseline")
    projection = metadata["protected_projection"]
    review_evidence.review_object(projection, {"rule_version", "preserved", "differences", "reason"}, {"rule_version", "preserved", "differences"})
    cycle_boolean(projection["preserved"], "preserved")
    if projection["rule_version"] != review_preservation.REPAIR_RULE_VERSION:
        cycle_failure("review/version-unsupported", "Candidate protection rule is unsupported")
    differences = contracts.require_list(projection["differences"], "projection differences")
    for difference in differences:
        review_evidence.review_text(difference)
    if not projection["preserved"] or differences or "reason" in projection:
        cycle_failure("review/protected-change", "A technical candidate must report a clean protected projection")
    cycle_boolean(metadata["source_preserved"], "source_preserved")
    if metadata["semantic_context"] is not None:
        contracts.require_mapping(metadata["semantic_context"], "candidate context assessment")


def cycle_boolean(value, field):
    if type(value) is not bool:
        cycle_failure("schema/type", "Expected a boolean", field=field)


def cycle_action_fingerprint(plan):
    return context_bundle.bundle_hash(review_workflow.review_json_bytes({"artifact": plan["current_artifact_sha256"], "context": plan["current_context_sha256"],
            "actions": [{"target": action["target"], "intent": action["intent"], "strategy_id": action["strategy_id"]} for action in plan["actions"]],
            "rule_version": plan["repair_rule_version"]}))


def cycle_assessment_schema(assessment):
    review_evidence.review_object(assessment, CYCLE_ASSESS_FIELDS)
    review_evidence.review_version(assessment["review_assessment_version"])
    for key in ("attempt_index", "attempts_started", "maximum_attempts"):
        review_evidence.review_integer(assessment[key], minimum=1)
    if not 1 <= assessment["attempt_index"] <= assessment["attempts_started"] <= assessment["maximum_attempts"] == 2:
        cycle_failure("review/state-conflict", "Assessment attempt accounting exceeds its durable budget")
    for key in ("claim_sha256", "parent_record_sha256", "candidate_sha256", "candidate_record_sha256", "resolutions_sha256"):
        review_evidence.review_digest(assessment[key], nullable=key in {"candidate_sha256", "candidate_record_sha256", "resolutions_sha256"})
    review_evidence.review_object(assessment["checks"], set(CYCLE_CHECKS))
    for value in assessment["checks"].values():
        review_evidence.review_enum(value, {"passed", "failed", "not_assessed"})
    review_evidence.review_enum(assessment["decision"], {"accepted", "rejected", "stopped"})
    cycle_boolean(assessment["continue"], "continue")
    review_evidence.review_text(assessment["stop_reason"], nullable=True)
    for key in ("last_accepted", "last_good"):
        review_state.state_pointer(assessment[key])
    if (assessment["trust"] != "externally_supplied" or assessment["acceptance_basis"] != "technical_gates_and_declared_visual_improvement"
            or assessment["decision"] == "accepted" and (any(value != "passed" for value in assessment["checks"].values())
                or any(assessment[key] is None for key in ("candidate_sha256", "candidate_record_sha256", "resolutions_sha256", "last_accepted")))):
        cycle_failure("review/state-conflict", "Assessment contradicts its acceptance basis or gates")
    contracts.require_list(assessment["remaining_issues"], "remaining issues")
    contracts.require_list(assessment["metrics"], "assessment metrics")
    review_evidence.review_enum(assessment["remaining_coverage"], {"complete", "unavailable"})
