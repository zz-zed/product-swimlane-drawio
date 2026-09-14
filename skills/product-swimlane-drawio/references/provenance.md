# Provenance context v1

Use provenance only when the requester wants a declared, local record of which
source assertion supports specific diagram fields. It is opt-in. The runtime
does not open source references, read source content, make network calls,
authenticate an actor, or establish that a business statement is true.

Provenance is a component of semantic context. Read [semantic context v1](semantic-context.md)
first for artifact binding and pattern/group checks.

## Build a complete bundle

Build accepts an unbound template with `context_template_version: 1`; it must
not contain `artifact`. A template can contain only `provenance`, or it can
also contain the existing `patterns` and `group_contracts` components. The
writer binds the context to the generated diagram after serialization.

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" \
  build --spec "<spec.json>" --strict \
  --context "<template.json>" \
  --output "<new-dir>/diagram.drawio" \
  --context-output "<new-dir>/context.json" \
  --completion-manifest "<new-dir>/completion.json"
```

All three paths must be in one new directory and use those exact names. The
diagram and context are immutable members, but they are not jointly atomic.
`completion.json` is written last and is the only completion point. It records
the SHA-256 and byte length of both members. An existing destination, unsafe
path, output alias, or missing/mismatched manifest is rejected; failed delivery
can retain an incomplete residue and must not be treated as a bundle.

A bound provenance context always needs its explicit completion manifest when
read:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" \
  inspect --input "<new-dir>/diagram.drawio" \
  --context "<new-dir>/context.json" \
  --completion-manifest "<new-dir>/completion.json"
```

`validate` uses the same read arguments. No command discovers a nearby context
or completion file. Existing pattern/group contexts without provenance remain
readable as standalone explicit context files.

## Declared data

The bound context keeps its existing `context_version`, `artifact`, optional
`patterns`, and optional `group_contracts`, then adds this component:

```json
{
  "provenance": {
    "version": 1,
    "sources": [],
    "facts": [],
    "bindings": [],
    "history": []
  }
}
```

All arrays are required. Context limits remain 2 MiB, depth 32, and 20,000 ID
references; each provenance collection has at most 1,024 records and history
at most 128 events. Unknown fields, versions, types, duplicate IDs, and invalid
digests are refused. The context's raw SHA-256 is separate from the diagram's
existing schema and semantic-model hash.

| Record | Required fields and meaning |
| --- | --- |
| Source | `{id, kind, version, digest, reference?}`. `digest` is lower-case SHA-256. A reference is either a neutral label or a safe HTTP(S) URL; it is inert text, never opened or fetched. |
| Fact | `{id, status, source_refs, confirmation}`. Status is `confirmed`, `assumption`, or `unresolved`. A confirmed fact needs source support plus `{asserted_by, basis}`. This is a declaration-only assertion, not identity or truth verification. |
| Binding | `{id, fact_id, target, fields, source_snapshots, validity}`. Targets are a node, edge, or decision-scoped outcome. `fields` snapshots actual supported fields: node `label`, `owner`, `type`; edge `label`, `from`, `to`, `outcome`; outcome `label`, `decision_label`, `owner`. |
| History | Append-only previous source, fact, and binding records with the exact earlier artifact and raw-context digests. Records are retained rather than silently removed. |

The following is the complete shape of one binding. `source_snapshots` has
exactly one `{source_id, version, digest}` record for each `fact.source_refs`
ID. Its version and digest may differ from the current source on an explicitly
stale binding or in retained history. A source reference is optional; when supplied it is either a
neutral label matching `[A-Za-z0-9][A-Za-z0-9._ -]{0,255}`, or an HTTP(S) URL
of at most 512 characters with a hostname and without userinfo, query,
fragment, backslash, or control characters. Filesystem paths, UNC/drive paths,
and other URL schemes are refused.

```json
{
  "id": "binding-edge",
  "fact_id": "fact-edge",
  "target": {"kind": "edge", "id": "next"},
  "fields": {"label": "", "from": "start", "to": "finish", "outcome": null},
  "source_snapshots": [
    {"source_id": "source-1", "version": "1", "digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
  ],
  "validity": "current"
}
```

Node targets are `{ "kind": "node", "id": "<node-id>" }` with `label`,
`owner`, and `type` snapshots. Edge targets use `label`, `from`, `to`, and
`outcome`; an empty label is a real snapshot and a missing edge outcome is
`null`. An outcome target is
`{ "kind": "outcome", "decision_id": "<decision-id>", "outcome_id": "<outcome-id>" }`
with `label`, `decision_label`, and `owner` snapshots. Outcome identity is
scoped to its decision, never inferred from a visible label.

Each history item is exactly an earlier identity plus full replaced records;
its record arrays do not contain nested history:

```json
{
  "artifact_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "context_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "records": {"sources": [], "facts": [], "bindings": []}
}
```

Binding validity is independent of a fact's declared status. It is `current`,
`stale`, `missing`, or `deleted`. An `assumption` or `unresolved` fact may bind
an actual field without a source; that binding is missing evidence, not an
invented source. A current bound read whose projected field, target, source
version, or source digest has drifted is refused rather than adopted. Controlled
patches produce readable `stale` or `deleted` history and a source gate that is
incomplete.

## Gates and coverage

The receipt keeps the pattern/group `gate` separate from `source_gate`. A
provenance-only context reports the former as `not_applicable`; it does not
infer a process pattern. `source_results` reports each fact's declared status,
confirmation, `verification: "declaration_only"`, validity,
`effective_confirmed`, and field bindings.

`source_gate: "passed"` requires every declared fact to be effectively
confirmed, and every diagram node, edge, and decision-scoped outcome to have
at least one confirmed, current field binding. It only proves the declared
fields. `source_coverage` therefore reports object coverage and field coverage
separately: `whole_diagram` says every object has a supported field, while
`field_extent: "all_fields"` is needed to cover every supported field. One
label binding does not cover an object's other fields.

`inspect` returns 0 for a readable incomplete source gate. `validate` returns
1 when the source, pattern, group, ordinary, or strict gate fails or is
incomplete. Invalid context, raw binding, delivery, or manifest input is a
safe refusal with exit 2.

| Command | Provenance-aware use |
| --- | --- |
| `build` | Bind an unbound template and publish a new bundle. |
| `inspect` | Read an explicit bound context and its completion manifest; readable incomplete evidence returns 0. |
| `validate` | Read the same bundle and return 1 for an incomplete source gate. |
| `patch` | Require both reviewed raw input digests, a declared context transition, and input/output manifests. |
| `compare` | Read both bundles and independently prove the declared context transition and record preservation. |
| `migrate` | Read-only dry-run reads the explicit input bundle; an eligible write only rebinds metadata through a new bundle. |

## Update and compare

For a normal bound update, patch requires the diagram and raw-context digests,
the input manifest, an explicit transition, and a fresh bundle directory:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" \
  patch --input "<old>/diagram.drawio" --context "<old>/context.json" \
  --input-completion-manifest "<old>/completion.json" \
  --changes "<changes.json>" --context-changes "<context-changes.json>" \
  --expected-input-sha256 "<reviewed-diagram-sha>" \
  --expected-context-sha256 "<reviewed-context-sha>" \
  --output "<new>/diagram.drawio" --context-output "<new>/context.json" \
  --completion-manifest "<new>/completion.json"
```

`context_changes` declares `context_changes_version: 1`, both original digests,
and a `semantic`, `geometry-only`, or `migration` mode. Semantic changes to a
bound field or source version/digest invalidate affected bindings. A new or
renewed confirmed assertion needs an explicit confirmation action; a declared
downgrade to assumption or unresolved with confirmation cleared does not.
Geometry-only rebinding requires proof that semantic and bound field projections
did not change, and does not create a confirmation.

This is a structurally complete semantic patch transition. Replace the two
example digests with the reviewed raw input bytes. `confirmation_actions` is an
array of `{fact_id, asserted_by, basis}` and is required when a confirmed
assertion is added or renewed; it may be empty when no assertion is renewed.

```json
{
  "context_changes_version": 1,
  "expected_input_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_context_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "mode": "semantic",
  "confirmation_actions": []
}
```

An existing diagram can receive its first provenance bundle through a semantic
no-op patch with a template, exact input/template digests, matching full
provenance addition, and explicit assertions for current confirmed facts. This
is `provenance_operation: "initialize"` with
`prior_context_preserved: false`; it does not claim an unknown earlier sidecar
was preserved. Later bound updates report preserved prior context.

Use `compare` with both diagram/context pairs, both manifests, the graph
changes, and the same context transition. It independently checks the declared
context transition, retained records, history prefix, binding invalidation, and
unauthorized status upgrades. A graph-only comparison never proves provenance
preservation:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" \
  compare --before "<old>/diagram.drawio" --after "<new>/diagram.drawio" \
  --changes "<changes.json>" \
  --before-context "<old>/context.json" --after-context "<new>/context.json" \
  --before-completion-manifest "<old>/completion.json" \
  --after-completion-manifest "<new>/completion.json" \
  --context-changes "<context-changes.json>"
```

`migrate --dry-run` remains read-only and, with provenance, receives the bound
context and its input manifest but no output or context-change flags:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" \
  migrate --input "<old>/diagram.drawio" --dry-run \
  --context "<old>/context.json" \
  --input-completion-manifest "<old>/completion.json"
```

An eligible metadata write adds the migration transition, both reviewed digests,
and a new output bundle. It is a context-aware variant of the existing
same-schema migration gate, not a source or confirmation update:

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" \
  migrate --input "<old>/diagram.drawio" \
  --context "<old>/context.json" \
  --input-completion-manifest "<old>/completion.json" \
  --context-changes "<migration-context-changes.json>" \
  --expected-input-sha256 "<reviewed-diagram-sha>" \
  --expected-context-sha256 "<reviewed-context-sha>" \
  --output "<new>/diagram.drawio" --context-output "<new>/context.json" \
  --completion-manifest "<new>/completion.json"
```

Context-aware migration is v3-only. Legacy v1/v2 diagrams continue through the
existing no-context migration path and do not gain provenance from that command.
