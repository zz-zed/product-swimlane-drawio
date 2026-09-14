# Visual review evidence v1

Use visual review when a requester needs an immutable record of strict
validation, an exported preview, and declared agent or human image review for
one managed v3 diagram. The review workflow does not modify the diagram or its
context. It does not export an image, call a model, open a URL, execute a
command found in a report, create a repair candidate, or grant permission for a
repair.

## Prepare an immutable snapshot

`review prepare` accepts a single-page, fully managed v3 diagram and its
reviewed SHA-256. It copies the original input and writes a new prepared
directory; it does not render an image. If a context is supplied, it is bound
to its reviewed raw SHA-256. A bound provenance context also requires its input
completion manifest.

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" \
  review prepare --input "<diagram.drawio>" \
  --expected-input-sha256 "<reviewed-diagram-sha>" \
  --output "<new-prepared-directory>"
```

The final `completion.json` is the only completion point. Its immutable members
include `run.json`, `objects.json`, and `original/diagram.drawio`; an explicit
context and input completion file are copied under `original/` when supplied.
The object index uses absolute `mxgraph-root` coordinates, keeping native
bounds, edge paths, and label bounds distinct. It is an identity and geometry
snapshot, not a rendered preview.

## Record externally supplied evidence

Export or view an image outside this command, then prepare a bounded evidence
directory containing the report and only its declared PNG/log members. The
record command validates, copies, and binds those supplied bytes to the prepared
snapshot; it does not run the export command declared by the report.

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" \
  review record --prepared "<prepared>/completion.json" \
  --expected-prepared-sha256 "<prepared-completion-sha>" \
  --input "<diagram.drawio>" --expected-input-sha256 "<reviewed-diagram-sha>" \
  --evidence-dir "<evidence-directory>" \
  --report "<evidence-directory>/report.json" \
  --expected-report-sha256 "<report-sha>" \
  --output "<new-record-directory>"
```

If a context was present during prepare, record also needs that same context,
its reviewed raw SHA-256, and its input completion manifest where applicable.
The input diagram and context must still match the prepared snapshot. A changed
byte requires a new prepare; a record never replaces an earlier record.

The engine computes `strict_validation` from the prepared artifact. The report
declares only `preview_export`, `agent_image_review`, and `human_review`, each
as `passed`, `failed`, `not_run`, or `not_available`. A format-valid record can
preserve a failed or unavailable export/review and still return a successful
record operation. That records the evidence honestly; it does not establish a
visual pass. A successful export does not establish that any agent or human
viewed the image.

The record receipt keeps `report_validation` separate from these states and
includes `trust: "externally_supplied"`. The command's outer `png_checks`
field, rather than `receipt.json`, lists actual PNG byte checks.
A bound issue receives `spatial_bound` only after its calibrated region
intersects every named target; otherwise it is identity-bound or ambiguous. An
issue remains an observation even when its report format and spatial mapping
are valid.

### Minimum report

This complete report records that no export or viewing took place. Replace the
identity values with values from `run.json` and the prepared completion receipt.

```json
{
  "visual_report_version": 1,
  "run_id": "00000000-0000-4000-8000-000000000000",
  "prepared_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "artifact_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "context_sha256": null,
  "round_index": 0,
  "export": null,
  "previews": [],
  "reviews": {
    "agent": {"state": "not_run", "reason": null, "reviewer": null, "full_image_viewed": false, "viewed_previews": [], "issues": []},
    "human": {"state": "not_run", "reason": null, "reviewer": null, "full_image_viewed": false, "viewed_previews": [], "issues": []}
  }
}
```

For a successful export, `export` has exactly `state`, `reason`, `renderer`,
`command`, `exit_code`, `input_before_sha256`, `input_after_sha256`,
`full_preview_sha256`, `stdout`, `stderr`, and `calibration`. A passed export
has exit code 0, matching before/after artifact digests, a known renderer name,
version, and profile ID, and a matching full preview. `renderer` is
`{name,version,profile_id,fonts}`; command is inert argument data. `stdout` and
`stderr` are null or `{name,sha256,bytes}` for the fixed export-log names.

Each full preview is `{id:"full",kind:"full",name:"full.png",sha256,bytes,
width,height,mapping}`. A non-null full mapping is
`{mapping_version:1,kind:"axis_aligned",scale_x,scale_y,translate_x,
translate_y,calibration_id}`. Its calibration is
`{calibration_version:1,id,profile_id,artifact_sha256,preview_sha256,method:
"externally_observed_anchors",anchors,tolerance_px:2.0}`. An anchor is
`{target,anchor,pixel}`, where anchor is a bounds corner or center and pixel is
`{x,y}`. At least three non-collinear anchors across two targets are required.

Every target is `{kind,id}` with kind `node`, `edge`, `lane`, `pool`, or `phase`, or is
`{kind:"outcome",decision_id,outcome_id}`. An outcome always carries both its
decision and outcome IDs. The permitted anchor literals are `top_left`,
`top_right`, `bottom_left`, `bottom_right`, and `center`.

A cutout preview is `{id,kind:"cutout",name:"cutouts/<id>.png",sha256,bytes,
width,height,mapping}`. Its mapping is
`{mapping_version:1,kind:"cutout",full_preview_sha256,crop,scale_x,scale_y}`
where crop is `{x,y,width,height}` with positive dimensions wholly inside the
full image. Cutout dimensions must match crop dimensions multiplied by scale to
within one pixel. A cutout does not substitute for the required full preview.

An actual review has
`{state,reason,reviewer,full_image_viewed,viewed_previews,issues}`. Reviewer is
`{kind:"agent"|"human",name,host,model,tool_receipt_ids}`; unknown name, host,
or model is null. Each issue is
`{issue_id,code,severity,targets,preview_id,preview_sha256,region,observation,
suggested_intent,binding,uncertainty}`. Supported codes are
`visual/edge-label-collision`, `visual/arrowhead-hidden`,
`visual/excessive-detour`, `visual/node-text-clipped`,
`visual/main-path-unclear`, `visual/crowding`, and `visual/uneven-spacing`.
Severity is `blocker`, `warning`, or `note`; suggested intent is null,
`reposition-edge-label`, `reroute-edge`, or `manual-review`. `binding` is
`bound` or `ambiguous`; ambiguous issues require a nonempty `uncertainty`.
`region` is `{x,y,width,height}` within the named preview. A passed agent or
human review declares the full image viewed and has no blocker or warning.

For `export.state: "failed"`, `reason` is nonblank; a real nonzero exit code
may be retained and no damaged PNG may be presented as a passed preview. For
`export.state: "not_available"`, `reason` is nonblank and command, exit code,
previews, and calibration are absent or null: do not invent an invocation or
PNG. `export: null` is the distinct `not_run` case.

## Preview and spatial limits

The report may declare a full PNG and up to 16 named cutouts. The checker
accepts a bounded, non-interlaced 8-bit RGB/RGBA PNG subset and verifies byte
identity, PNG structure, size limits, and report references. This proves a
supported image file was supplied; it does not prove the image came from the
diagram or that its pixels were understood.

Report JSON is limited to 2 MiB, depth 32, and 20,000 ID references, with at
most 2,048 issues and 4,096 indexed objects. Each PNG is at most 32 MiB,
16,384 by 16,384 pixels, and 32,000,000 pixels; all PNG input is at most 128
MiB, with at most 128 MiB decoded scanline data. These are file-safety limits,
not evidence of visual quality.

An optional axis-aligned calibration maps absolute native coordinates to pixels.
It uses at least three externally observed, non-collinear anchors across two
objects and a fixed two-pixel tolerance. The result is only
`declared_anchors_consistent`: it is not image recognition or exporter
authentication. Without calibration, an issue can remain identity-bound but its
spatial relation is unverified. With calibration, each bound target must
intersect the issue region: nodes, lanes, and pools use their projected bounds;
edges use their actual path segments rather than a whole-edge bounding box.
Outcome geometry can be explicitly derived from its decision and outgoing edge.
The receipt marks derived outcome geometry rather than inventing an independent
outcome rectangle. A calibrated non-intersection is refused; it is not silently
weakened to a bound issue.

Issue suggestions are observations only. `reposition-edge-label`, `reroute-edge`,
and `manual-review` never change the diagram in this read-only workflow. An
explicitly authorized candidate workflow is documented separately in
[authorized visual-repair candidates](visual-repair.md); it requires separate
prepared, record, authorization, and candidate inputs.

## Delivery and trust boundary

Prepared and record packages use new directories and a final no-clobber
completion manifest. The record copies the original report and supplied files,
then adds an engine receipt with `trust: "externally_supplied"`. Export command
text, renderer metadata, reviewer names, and model strings are declarations;
they do not authenticate a renderer or reviewer or prove an external invocation.

The workflow preserves an optional semantic-context assessment separately.
Pattern/group/source gates may be incomplete while a review snapshot or record
remains readable. It does not turn a source declaration into a confirmed fact,
and it does not provide iterative repair, acceptance, or a final good version.
Candidate records use `visual_report_version: 2` and add their claim,
candidate, and parent-record digests while retaining this report's evidence and
trust rules. Read [authorized visual-repair candidates](visual-repair.md) for
the separate authorization, candidate, and assessment inputs.
