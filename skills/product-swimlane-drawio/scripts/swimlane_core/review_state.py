"""Confined immutable review packages and a durable two-slot claim ledger."""
from __future__ import annotations

import os
import stat
import uuid

from . import context_bundle, contracts, preview_png, review_evidence, review_workflow, semantic_context

STATE_PACKAGE_MEMBERS = {
    "state": ({"state.json"}, set()), "claim": ({"claim.json"}, set()),
    "result": ({"result.json"}, set()), "assessment-marker": ({"assessment.json"}, set()),
    "plan": ({"plan.json", "authorization.json", "prepared.json", "record.json"}, set()),
    "candidate": ({"artifact/diagram.drawio", "candidate.json"}, {"artifact/context.json", "artifact/completion.json"}),
    "candidate-prepared": ({"run.json", "objects.json", "initial-prepared.json", "candidate-manifest.json", "candidate.json", "candidate/diagram.drawio"},
                           {"candidate/context.json", "candidate/completion.json"}),
    "candidate-record": ({"report.json", "receipt.json", "prepared.json"}, {"full.png", "export.stdout.txt", "export.stderr.txt"}),
    "assessment": ({"assessment.json"}, set()),
}
STATE_ROOT_FIELDS = {"state_version", "run_id", "prepared_sha256", "prepared_path_sha256", "original", "authorization_sha256", "tool_version", "repair_rule_version", "maximum_attempts"}
STATE_CLAIM_FIELDS = {"claim_version", "run_id", "attempt_index", "root_manifest_sha256", "parent_assessment_sha256", "parent_record_sha256", "input_artifact_sha256", "input_context_sha256", "plan_sha256", "authorization_sha256", "tool_version", "repair_rule_version", "action_fingerprint"}
STATE_RESULT_FIELDS = {"attempt_result_version", "run_id", "attempt_index", "claim_sha256", "plan_sha256", "status", "candidate_sha256", "candidate_artifact_sha256", "candidate_context_sha256", "diagnostics", "delivery"}
STATE_MARKER_FIELDS = {"assessment_marker_version", "run_id", "attempt_index", "claim_sha256", "assessment_sha256", "decision", "continue", "original", "last_accepted", "last_good"}


def state_failure(code, message, **evidence):
    review_evidence.review_failure(code, message, **evidence)


def state_file_exists(path, *, directory=False):
    absolute = context_bundle.bundle_absolute(path)
    parent, _ = context_bundle.bundle_directory(absolute.parent)
    try:
        try:
            entry = os.stat(absolute.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (directory and not stat.S_ISDIR(entry.st_mode)) or (not directory and not stat.S_ISREG(entry.st_mode)):
            state_failure("delivery/path-unsafe", "Review state entry has an unsafe file type")
        if not directory and entry.st_nlink != 1:
            state_failure("delivery/path-unsafe", "Review state entry has a hard-link alias")
        return True
    finally:
        os.close(parent)


def state_package_read(path, kind=None):
    try:
        data, manifest = review_workflow.review_read_json(path)
    except contracts.DiagramError as exc:
        if exc.code == "delivery/path-unsafe" and exc.evidence.get("reason") == "FileNotFoundError":
            state_failure("delivery/bundle-incomplete", "Explicit review completion is missing")
        raise
    review_evidence.review_object(data, {"review_bundle_version", "kind", "run_id", "members"})
    review_evidence.review_version(data["review_bundle_version"], 2)
    review_evidence.review_enum(data["kind"], set(STATE_PACKAGE_MEMBERS), code="review/version-unsupported")
    if kind is not None and data["kind"] != kind:
        state_failure("delivery/bundle-invalid", "Review package kind differs from the requested input")
    if manifest.path.name != "completion.json":
        state_failure("delivery/bundle-invalid", "Review package requires the fixed completion.json name")
    try:
        run_id = uuid.UUID(data["run_id"])
        if str(run_id) != data["run_id"] or run_id.version != 4:
            raise ValueError("not UUID4")
    except (ValueError, TypeError, AttributeError):
        state_failure("schema/type", "Review run identity must be canonical UUID4")
    entries = contracts.require_list(data["members"], "review members")
    if len(entries) > 24:
        state_failure("review/resource-limit", "Review package member budget exceeded")
    names = []
    for entry in entries:
        review_evidence.review_object(entry, {"name", "sha256", "bytes"})
        review_evidence.review_text(entry["name"], maximum=512)
        review_evidence.review_digest(entry["sha256"])
        review_evidence.review_integer(entry["bytes"])
        names.append(entry["name"])
    required, optional = STATE_PACKAGE_MEMBERS[data["kind"]]
    allowed = required | optional
    if data["kind"] == "candidate-record":
        for name in names:
            if name.startswith("cutouts/") and name.endswith(".png"):
                contracts.validate_semantic_id(name[8:-4], "cutout ID")
                allowed.add(name)
    if names != sorted(set(names)) or not required <= set(names) <= allowed:
        state_failure("delivery/bundle-invalid", "Review package members are not its exact sorted allowlist")
    for prefix in ("artifact", "candidate"):
        if (prefix + "/context.json" in names) != (prefix + "/completion.json" in names):
            state_failure("delivery/bundle-invalid", "Context and its E3 completion must be supplied together")
    members, total = {}, 0
    for entry in entries:
        name = entry["name"]
        limit = (preview_png.PNG_MAX_BYTES if name.endswith(".png") else review_workflow.REVIEW_LOG_BYTES if name.endswith(".txt")
                 else review_workflow.REVIEW_GRAPH_BYTES if name.endswith(".drawio") else semantic_context.MAX_BYTES)
        item = review_workflow.review_read_evidence(manifest.path.parent / name, max_bytes=limit, member=True)
        if item.sha256 != entry["sha256"] or len(item.raw) != entry["bytes"]:
            state_failure("delivery/bundle-incomplete", "Completed review member bytes do not match", name=name)
        if name.endswith(".png"):
            total += len(item.raw)
        members[name] = item
    if total > preview_png.PNG_TOTAL_BYTES:
        state_failure("review/resource-limit", "Total PNG byte budget exceeded")
    payload_names = {"state": "state.json", "claim": "claim.json", "result": "result.json", "assessment-marker": "assessment.json",
                     "plan": "plan.json", "candidate": "candidate.json", "candidate-prepared": "run.json", "candidate-record": "report.json", "assessment": "assessment.json"}
    payload = state_payload(members[payload_names[data["kind"]]])
    if payload.get("run_id") != data["run_id"]:
        state_failure("review/state-conflict", "Review package header differs from its actual payload run identity")
    all_inputs = [manifest, *members.values()]
    context_bundle.bundle_check_distinct(all_inputs)
    for item in all_inputs:
        item.verify()
    return data, manifest, members


def state_verify_published(path, written):
    _, manifest, members = state_package_read(path)
    if {item.path: item.raw for item in written} != {item.path: item.raw for item in members.values()}:
        state_failure("delivery/candidate-preservation-failed", "Actual package differs from its checked candidate")
    return manifest


def state_package_publish(directory, kind, run_id, members, inputs):
    return context_bundle.bundle_publish_members(directory, sorted(members, key=lambda item: item["name"]),
            {"review_bundle_version": 2, "kind": kind, "run_id": run_id}, review_workflow.review_unique_inputs(inputs), state_verify_published)


def state_single_publish(directory, kind, run_id, name, data, inputs):
    delivery = state_package_publish(directory, kind, run_id,
                [review_workflow.review_member(name, review_workflow.review_json_bytes(data))], inputs)
    _, manifest, members = state_package_read(context_bundle.bundle_absolute(directory) / "completion.json", kind)
    return delivery, manifest, members[name]


def state_payload(item):
    data = semantic_context.decode_context(item.raw, validate=False).data
    return contracts.require_mapping(data, "visual review object")


def state_pointer(value):
    if value is None:
        return
    review_evidence.review_object(value, {"artifact_sha256", "context_sha256", "candidate_sha256", "record_sha256"})
    for key in value:
        review_evidence.review_digest(value[key], nullable=key == "context_sha256")


def state_list_allowed(directory, allowed):
    descriptor, _ = context_bundle.bundle_directory(directory)
    try:
        names = set(os.listdir(descriptor))
        unknown = names - set(allowed)
        if any(name.startswith("attempt-") and name not in {"attempt-1", "attempt-2"} for name in unknown):
            state_failure("review/attempt-exhausted", "Review run cannot have more than two attempt slots")
        if unknown:
            state_failure("review/state-incomplete", "Review state contains unrecognized or incomplete entries", entries=sorted(unknown))
        return names
    finally:
        os.close(descriptor)


def state_ledger_path(prepared, run_id):
    return prepared.path.parent.parent / (".review-state-" + run_id)


def state_claim_schema(claim):
    review_evidence.review_object(claim, STATE_CLAIM_FIELDS)
    review_evidence.review_version(claim["claim_version"])
    review_evidence.review_integer(claim["attempt_index"], minimum=1)
    if claim["attempt_index"] > 2:
        state_failure("review/attempt-exhausted", "At most two repair claims are supported")
    for key in STATE_CLAIM_FIELDS - {"claim_version", "run_id", "attempt_index", "tool_version", "repair_rule_version"}:
        review_evidence.review_digest(claim[key], nullable=key in {"parent_assessment_sha256", "input_context_sha256"})
    for key in ("run_id", "tool_version", "repair_rule_version"):
        review_evidence.review_text(claim[key], maximum=128)
    if (claim["parent_assessment_sha256"] is None) != (claim["attempt_index"] == 1):
        state_failure("review/state-conflict", "Claim parent assessment does not match its round")


def inspect_review_state(prepared, expected_root, *, allow_partial_result=False):
    directory = state_ledger_path(prepared, expected_root["run_id"])
    state = {"directory": directory, "root": None, "inputs": [], "attempts": [], "last_accepted": None,
             "last_good": None, "terminal": False, "next_attempt": 1, "incomplete": False}
    if not state_file_exists(directory, directory=True):
        return state
    if not state_file_exists(directory / "completion.json"):
        state_failure("review/state-incomplete", "An existing state root has no complete manifest")
    names = state_list_allowed(directory, {"state.json", "completion.json", "attempt-1", "attempt-2"})
    _, root_manifest, root_members = state_package_read(directory / "completion.json", "state")
    root = state_payload(root_members["state.json"])
    review_evidence.review_object(root, STATE_ROOT_FIELDS)
    if review_workflow.review_json_bytes(root) != review_workflow.review_json_bytes(expected_root):
        state_failure("review/state-conflict", "State root differs from original authorization or prepared identity")
    state.update(root=root_manifest, inputs=[root_manifest, *root_members.values()])
    if "attempt-2" in names and "attempt-1" not in names:
        state_failure("review/state-incomplete", "Attempt slots are not contiguous")
    previous_marker = None
    for index in (1, 2):
        slot = directory / ("attempt-" + str(index))
        if slot.name not in names:
            break
        if previous_marker is not None and (previous_marker["decision"] != "accepted" or not previous_marker["continue"]):
            state_failure("review/state-conflict", "A stopped run has an additional attempt")
        slot_names = state_list_allowed(slot, {"claim.json", "completion.json", "result", "assessment"})
        if "completion.json" not in slot_names:
            state_failure("review/state-incomplete", "An attempt slot was reserved without a complete claim")
        _, claim_manifest, members = state_package_read(slot / "completion.json", "claim")
        claim_item = members["claim.json"]; claim = state_payload(claim_item)
        state_claim_schema(claim)
        if (claim["run_id"] != root["run_id"] or claim["attempt_index"] != index or claim["root_manifest_sha256"] != root_manifest.sha256
                or claim["authorization_sha256"] != root["authorization_sha256"] or claim["tool_version"] != root["tool_version"]
                or claim["repair_rule_version"] != root["repair_rule_version"]
                or claim["parent_assessment_sha256"] != (previous_marker["assessment_sha256"] if previous_marker else None)):
            state_failure("review/state-conflict", "Attempt claim does not match its fixed parent chain")
        before_pointer = previous_marker["last_accepted"] if previous_marker else root["original"]
        if (claim["input_artifact_sha256"] != before_pointer["artifact_sha256"]
                or claim["input_context_sha256"] != before_pointer["context_sha256"]
                or (previous_marker and claim["parent_record_sha256"] != before_pointer["record_sha256"])):
            state_failure("review/state-conflict", "Claim before identity is not its accepted parent")
        state["inputs"].extend([claim_manifest, claim_item])
        attempt = {"index": index, "directory": slot, "claim": claim, "claim_input": claim_item,
                   "result": None, "marker": None}
        state["attempts"].append(attempt)
        if "result" in slot_names:
            if not state_file_exists(slot / "result" / "completion.json"):
                if not allow_partial_result:
                    state_failure("review/state-incomplete", "Attempt result delivery is incomplete")
                state["incomplete"] = True
            else:
                state_list_allowed(slot / "result", {"result.json", "completion.json"})
                _, rm, ritems = state_package_read(slot / "result" / "completion.json", "result")
                result = state_payload(ritems["result.json"])
                review_evidence.review_object(result, STATE_RESULT_FIELDS)
                review_evidence.review_version(result["attempt_result_version"])
                review_evidence.review_enum(result["status"], {"technical_passed", "no_change", "technical_failed", "delivery_failed"})
                if (result["run_id"] != root["run_id"] or type(result["attempt_index"]) is not int or result["attempt_index"] != index
                        or result["claim_sha256"] != claim_item.sha256 or result["plan_sha256"] != claim["plan_sha256"]):
                    state_failure("review/state-conflict", "Attempt result is attached to another claim")
                for key in ("candidate_sha256", "candidate_artifact_sha256", "candidate_context_sha256"):
                    review_evidence.review_digest(result[key], nullable=key == "candidate_context_sha256" or result["status"] != "technical_passed")
                    if result["status"] != "technical_passed" and result[key] is not None:
                        state_failure("review/state-conflict", "Failed result cannot claim a qualified candidate")
                if result["status"] == "technical_passed" and ((result["candidate_context_sha256"] is None) != (claim["input_context_sha256"] is None)
                        or result["candidate_artifact_sha256"] == claim["input_artifact_sha256"]):
                    state_failure("review/state-conflict", "Qualified result did not change the graph or preserve context presence")
                contracts.require_list(result["diagnostics"], "attempt diagnostics")
                if result["delivery"] is not None:
                    contracts.require_mapping(result["delivery"], "attempt delivery")
                attempt["result"] = result
                state["inputs"].extend([rm, *ritems.values()])
        if "assessment" in slot_names:
            state_list_allowed(slot / "assessment", {"assessment.json", "completion.json"})
            _, am, aitems = state_package_read(slot / "assessment" / "completion.json", "assessment-marker")
            marker = state_payload(aitems["assessment.json"])
            review_evidence.review_object(marker, STATE_MARKER_FIELDS)
            review_evidence.review_version(marker["assessment_marker_version"])
            review_evidence.review_digest(marker["assessment_sha256"])
            review_evidence.review_enum(marker["decision"], {"accepted", "rejected", "stopped"})
            if (marker["run_id"] != root["run_id"] or type(marker["attempt_index"]) is not int or marker["attempt_index"] != index
                    or marker["claim_sha256"] != claim_item.sha256 or type(marker["continue"]) is not bool
                    or marker["original"] != root["original"]):
                state_failure("review/state-conflict", "Assessment marker is not bound to this claim")
            for key in ("last_accepted", "last_good"):
                state_pointer(marker[key])
            if marker["decision"] == "accepted":
                result = attempt["result"]
                if result is None or result["status"] != "technical_passed" or marker["last_accepted"] is None:
                    state_failure("review/state-conflict", "An accepted marker lacks its qualified candidate")
                pointer = marker["last_accepted"]
                if (pointer["artifact_sha256"] != result["candidate_artifact_sha256"]
                        or pointer["context_sha256"] != result["candidate_context_sha256"] or pointer["candidate_sha256"] != result["candidate_sha256"]):
                    state_failure("review/state-conflict", "Accepted pointer differs from actual attempt result identity")
            elif marker["continue"] or marker["last_accepted"] != state["last_accepted"] or marker["last_good"] != state["last_good"]:
                state_failure("review/state-conflict", "An unaccepted attempt changed preserved pointers")
            if index == 2 and marker["continue"]:
                state_failure("review/attempt-exhausted", "Second attempt cannot continue")
            if marker["last_good"] not in (state["last_good"], marker["last_accepted"]):
                state_failure("review/state-conflict", "Last-good pointer is not a prior good or current accepted candidate")
            attempt["marker"] = marker
            state["inputs"].extend([am, *aitems.values()])
            state.update(last_accepted=marker["last_accepted"], last_good=marker["last_good"], terminal=not marker["continue"])
            previous_marker = marker
        elif index == 1 and "attempt-2" in names:
            state_failure("review/state-incomplete", "Second attempt lacks a complete preceding assessment")
    state["next_attempt"] = len(state["attempts"]) + 1
    for item in state["inputs"]:
        item.verify()
    return state


def claim_review_attempt(prepared, expected_root, claim, inputs):
    state_claim_schema(dict(claim, root_manifest_sha256=claim.get("root_manifest_sha256") or "0" * 64))
    state = inspect_review_state(prepared, expected_root)
    if state["attempts"] and state["attempts"][-1]["marker"] is None:
        state_failure("review/attempt-pending", "Previous attempt is awaiting a result or assessment")
    if state["next_attempt"] > 2:
        state_failure("review/attempt-exhausted", "Review run has consumed its two attempts")
    if state["terminal"]:
        state_failure("review/state-conflict", "Review run has already stopped")
    if claim["attempt_index"] != state["next_attempt"]:
        state_failure("review/state-conflict", "Plan refers to an outdated attempt slot")
    all_inputs = review_workflow.review_unique_inputs([*inputs, *state["inputs"]])
    if state["root"] is None:
        _, root_manifest, _ = state_single_publish(state["directory"], "state", expected_root["run_id"], "state.json", expected_root, all_inputs)
        state = inspect_review_state(prepared, expected_root)
        all_inputs = review_workflow.review_unique_inputs([*inputs, *state["inputs"]])
    claim = dict(claim, root_manifest_sha256=state["root"].sha256)
    delivery, manifest, item = state_single_publish(state["directory"] / ("attempt-" + str(claim["attempt_index"])),
                "claim", expected_root["run_id"], "claim.json", claim, all_inputs)
    try:
        latest = inspect_review_state(prepared, expected_root)
    except contracts.DiagramError as exc:
        exc.evidence.update(delivery=delivery, attempt_consumed=True, claim_sha256=item.sha256)
        raise
    return claim, item, delivery, latest
