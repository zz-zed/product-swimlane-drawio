# Authorized visual-repair candidates

Use this opt-in workflow only after a managed v3 diagram has an immutable
visual-review prepared package and an externally supplied record with a
spatially bound, supported edge issue. It produces a bounded candidate for one
of two explicitly authorized intents:

- `reposition-edge-label` for `visual/edge-label-collision`;
- `reroute-edge` for `visual/excessive-detour` or
  `visual/arrowhead-hidden`.

It never infers permission from a diagram, a review report, a suggested fix,
or an `automatic` marker. It does not render an image, call a model, inspect
an image, authenticate an exporter or reviewer, or alter the original diagram.
Candidate review evidence is still an external declaration. A candidate becomes
an accepted version only through the explicit assessment described below.

## Create an authorization declaration

Write an independent JSON file before planning. It binds the exact original
prepared package, the original diagram and optional context, and at most two
attempts. The canonical prepared-path digest is supplied by the caller; do not
put an absolute path in the declaration.

```json
{
  "review_authorization_version": 1,
  "run_id": "00000000-0000-4000-8000-000000000000",
  "prepared_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "prepared_path_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "original_artifact_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "original_context_sha256": null,
  "maximum_attempts": 2,
  "asserted_by": "neutral requester declaration",
  "basis": "explicit task authorization",
  "actions": [
    {
      "target": {"kind": "edge", "id": "edge-approval"},
      "intents": ["reposition-edge-label"]
    }
  ]
}
```

Each target is a unique typed edge; the action list contains one to 32 targets
and each target's intent list is nonempty and unique. This is a caller
assertion, not identity authentication. Changing the authorization bytes or a
bound input requires a new plan and cannot reset an existing attempt ledger.

## Plan and construct a candidate

All paths and digests are explicit. Each `--output` directory must be new. In
the first round, `--parent-prepared` can be the initial prepared package. The
plan does not change a diagram or consume an attempt.

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" review plan \
  --prepared "<initial-prepared>/completion.json" \
  --expected-prepared-sha256 "<initial-prepared-sha>" \
  --parent-prepared "<parent-prepared>/completion.json" \
  --expected-parent-prepared-sha256 "<parent-prepared-sha>" \
  --record "<parent-record>/completion.json" \
  --expected-record-sha256 "<parent-record-sha>" \
  --input "<current.drawio>" --expected-input-sha256 "<current-sha>" \
  --authorization "<authorization.json>" \
  --expected-authorization-sha256 "<authorization-sha>" \
  --output "<new-plan-directory>"
```

When the prepared baseline contains context, add `--context`,
`--expected-context-sha256`, and, for a bound provenance context,
`--input-completion-manifest`. The plan receipt contains `can_repair`, explicit
actions, rejected issues, baseline issue keys, and input checks.
`can_repair: false` is a valid plan result, not a repair or visual pass.

Use the resulting plan completion digest to construct a candidate:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" review repair \
  --prepared "<initial-prepared>/completion.json" \
  --expected-prepared-sha256 "<initial-prepared-sha>" \
  --parent-prepared "<parent-prepared>/completion.json" \
  --expected-parent-prepared-sha256 "<parent-prepared-sha>" \
  --record "<parent-record>/completion.json" \
  --expected-record-sha256 "<parent-record-sha>" \
  --input "<current.drawio>" --expected-input-sha256 "<current-sha>" \
  --authorization "<authorization.json>" \
  --expected-authorization-sha256 "<authorization-sha>" \
  --plan "<plan>/completion.json" --expected-plan-sha256 "<plan-sha>" \
  --output "<new-candidate-directory>"
```

The command independently rechecks all inputs. Once it has committed its
immutable claim, the attempt is consumed before any candidate is written. A
successful candidate package has `review_bundle_version: 2`, kind `candidate`,
and final `completion.json`; it contains `artifact/diagram.drawio` and
`candidate.json`. A context, when present, is supplied with both
`artifact/context.json` and `artifact/completion.json`.

The engine, rather than the caller, writes the immutable claim package. Its
`claim.json` binds `run_id`, attempt index, root manifest, parent assessment
(null in round one), parent record, input artifact/context, plan,
authorization, tool/rule versions, and an action fingerprint. The ledger has
only `attempt-1` and `attempt-2`; incomplete or conflicting entries fail
closed. A candidate-prepared run declares `pointer_authority:
"ledger-assessment"`: no candidate run file is a mutable source of
`last_accepted` or `last_good`.

The candidate receipt reports strict validation, semantic-context assessment,
source preservation, exact protected-projection evidence, and before/after
native metrics. It changes only the precise, authorized edge geometry carrier
or automatic route fields. It preserves semantic fields, labels, non-target
objects, unknown XML payload, element ordering, text, and tails. Namespace
declarations, unsupported point payload, explicit or unknown waypoint origin,
and unsupported native layouts are refused rather than rewritten.

A route or label search may fail after claim creation. In that case `review
repair` exits 1 with `attempt_consumed: true` and preserves the immutable claim
and result receipt. It does not produce a qualified candidate or treat a
failed search as visual improvement.

## Prepare and record fresh candidate evidence

Candidate preparation uses the original prepared package, candidate package,
claim digest, and the actual candidate artifact. The resulting package is kind
`candidate-prepared`; its required immutable members include `run.json`,
`objects.json`, `initial-prepared.json`, `candidate-manifest.json`,
`candidate.json`, and `candidate/diagram.drawio`. Context members occur as a
pair when the candidate has context.

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" review prepare \
  --prepared "<initial-prepared>/completion.json" \
  --expected-prepared-sha256 "<initial-prepared-sha>" \
  --candidate "<candidate>/completion.json" \
  --expected-candidate-sha256 "<candidate-sha>" \
  --attempt 1 --claim-sha256 "<claim-sha>" \
  --input "<candidate>/artifact/diagram.drawio" \
  --expected-input-sha256 "<candidate-artifact-sha>" \
  --output "<new-candidate-prepared-directory>"
```

Supply the candidate context and its completion manifest when applicable.
Export and view a fresh candidate image outside this command, then create the
version-2 report and evidence directory using the constraints in
[visual review evidence](visual-review.md). Record the supplied bytes against
the candidate prepared package:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" review record \
  --prepared "<candidate-prepared>/completion.json" \
  --expected-prepared-sha256 "<candidate-prepared-sha>" \
  --input "<candidate>/artifact/diagram.drawio" \
  --expected-input-sha256 "<candidate-artifact-sha>" \
  --evidence-dir "<candidate-evidence-directory>" \
  --report "<candidate-evidence-directory>/report.json" \
  --expected-report-sha256 "<candidate-report-sha>" \
  --output "<new-candidate-record-directory>"
```

The candidate record binds the claim, candidate, and parent record identities
to its report and supplied preview bytes. It validates their structure and
declared calibration; it does not establish that an external exporter,
reviewer, or model performed the declared work.

## Assess a candidate or stop an incomplete attempt

Assessment receives every before, parent, candidate, evidence, and resolution
input explicitly. In round one, the parent prepared package is the initial
prepared package. In a later round it must be the prepared package of the
previous accepted candidate, with its parent assessment. A run ID or a pointer
inside a JSON document never discovers an omitted file.

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" review assess \
  --prepared "<initial-prepared>/completion.json" \
  --expected-prepared-sha256 "<initial-prepared-sha>" \
  --authorization "<authorization.json>" \
  --expected-authorization-sha256 "<authorization-sha>" \
  --attempt 1 --claim-sha256 "<claim-sha>" \
  --parent-prepared "<parent-prepared>/completion.json" \
  --expected-parent-prepared-sha256 "<parent-prepared-sha>" \
  --parent-record "<parent-record>/completion.json" \
  --expected-parent-record-sha256 "<parent-record-sha>" \
  --plan "<plan>/completion.json" --expected-plan-sha256 "<plan-sha>" \
  --before "<before.drawio>" --expected-before-sha256 "<before-sha>" \
  --candidate "<candidate>/completion.json" \
  --expected-candidate-sha256 "<candidate-sha>" \
  --candidate-prepared "<candidate-prepared>/completion.json" \
  --expected-candidate-prepared-sha256 "<candidate-prepared-sha>" \
  --record "<candidate-record>/completion.json" \
  --expected-record-sha256 "<candidate-record-sha>" \
  --resolutions "<resolutions.json>" \
  --expected-resolutions-sha256 "<resolutions-sha>" \
  --output "<new-assessment-directory>"
```

Supply `--before-context`, `--expected-before-context-sha256`, and
`--before-completion-manifest` when the before artifact has a bound provenance
context. In later rounds also supply `--parent-assessment` and
`--expected-assessment-sha256`.

The resolution JSON is a separate immutable input:

```json
{
  "issue_resolution_version": 1,
  "run_id": "00000000-0000-4000-8000-000000000000",
  "claim_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "parent_record_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "candidate_record_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "reviewer_kind": "agent",
  "items": [{
    "issue_key": {"code": "visual/edge-label-collision", "targets": [{"kind": "edge", "id": "edge-approval"}]},
    "previous_issue_ids": ["issue-before"],
    "disposition": "resolved",
    "candidate_issue_ids": [],
    "preview_id": "full",
    "preview_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "region": {"x": 10, "y": 20, "width": 30, "height": 40},
    "observation": "Neutral observed result.",
    "uncertainty": null
  }]
}
```

Items must cover every parent blocker or warning by its normalized code and
typed-target set. A missing later issue is not a resolution. `resolved`,
`improved`, `unchanged`, and `uncertain` each require the matching candidate
observation; `uncertain` stops automatic acceptance. New blocker/warning issues
or a regression reject the candidate.

Read `assessment.json` and its final completion manifest, rather than relying
on the process exit code. A complete assessment can return exit 0 with
`decision` `accepted`, `rejected`, or `stopped`. Accepted requires the recorded
technical, protected-projection, semantic-context, source-preservation,
environment, full-and-target-coverage, resolution, no-regression, and metric
improvement checks. `original`, `last_accepted`, and `last_good` are immutable
digest references with distinct meanings. `last_good` is set only for an
accepted candidate with an explicit passed agent review and no unresolved
blocker or warning; it can remain null after another accepted improvement.

The complete receipt has `review_assessment_version: 1` and includes the run,
attempt, claim, parent record, candidate, candidate record, and resolution
digests; all nine named checks; decision, stop reason, continuation, visual
status, remaining issues and metrics; original and pointer references; attempt
counts; `trust: "externally_supplied"`; the acceptance basis; and remaining
coverage. Each non-null `last_accepted` or `last_good` reference is exactly
`{artifact_sha256,context_sha256,candidate_sha256,record_sha256}`. `original`
is instead exactly `{artifact_sha256,context_sha256}`. Paths do not appear in
these references and cannot be followed to select later inputs.

A source gate may be incomplete without blocking a geometry-only candidate,
but the assessment still requires preservation of every source, fact, binding,
and history record. A pending, unresolved, stale, or merely declared source
fact is never promoted to confirmed by planning, repair, review, or assessment.

If an attempt has a committed claim but no qualified candidate/result, record a
truthful terminal stop without inventing evidence:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" review assess \
  --prepared "<initial-prepared>/completion.json" \
  --expected-prepared-sha256 "<initial-prepared-sha>" \
  --authorization "<authorization.json>" \
  --expected-authorization-sha256 "<authorization-sha>" \
  --attempt 1 --claim-sha256 "<claim-sha>" \
  --stop-reason incomplete-attempt \
  --output "<new-stop-assessment-directory>"
```

This writes `decision: "stopped"`, leaves checks `not_assessed`, preserves the
claim/result chain, and cannot set `last_accepted` or `last_good`. It cannot
reopen the consumed attempt.
