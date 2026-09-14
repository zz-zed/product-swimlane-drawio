# Semantic context v1

Use semantic context only when the requester wants an explicit, read-only check of a managed v3 diagram's declared process pattern or group relationship. It supplements the diagram; it does not infer business facts from labels, geometry, or the main path.

The standalone pattern/group context contract supports only `validate` and
`inspect`. It does not write the diagram or the context. Provenance uses its
own explicit bundle workflow; visual review remains outside both contracts.

For declared source, fact, and field-binding records that travel with an
explicitly delivered bundle, read the [provenance context contract](provenance.md).
That opt-in workflow adds context-aware build, patch, compare, and migration
paths; it does not alter this standalone pattern/group read contract.

## Invoke a pattern check

Pass the context explicitly. The tool never discovers a neighboring file.

```bash
python3 "<skill-root>/scripts/drawio_swimlane.py" \
  validate --input "<diagram.drawio>" --context "<context.json>" --strict

python3 "<skill-root>/scripts/drawio_swimlane.py" \
  inspect --input "<diagram.drawio>" --context "<context.json>"
```

Without `--context`, the existing command behavior is unchanged. With it, `validate` first runs its ordinary checks and then evaluates the semantic-context gate. `--strict` still controls the ordinary warning gate; it cannot bypass an incomplete or failed pattern check. `inspect` remains read-only and can return a valid inspection receipt even when the semantic gate is incomplete or failed.

## Context format

The file is UTF-8 JSON. This is a field skeleton, not an executable context: `scopes` must contain a complete declared scope and every placeholder must be replaced with values from the inspected diagram. The packaged Skill does not require repository examples; use the inspected diagram's identity when creating a context.

```json
{
  "context_version": 1,
  "artifact": {
    "sha256": "<64 lowercase hex characters>",
    "schema_version": "3",
    "model_hash_version": "<diagram value>",
    "model_hash": "<diagram value>"
  },
  "patterns": {
    "version": 1,
    "scopes": ["<complete scope>"]
  }
}
```

`artifact` must match the actual input bytes and its managed semantic identity. The input must be a managed v3 diagram with a current model hash. Legacy v1/v2 inputs and a missing model hash are unsupported for this opt-in check; an artifact mismatch is rejected as stale binding. The tool does not migrate either case.

`patterns.scopes` is nonempty. Every scope contains `id`, `role`, `pattern`, `nodes`, `edges`, `excluded_edges`, and `declarations`. `role` is `primary` or `local`, and there is exactly one `primary` scope. `pattern` is one of `linear`, `approval-loop`, `request-response`, `fork-join`, `fan-in`, `lifecycle`, or `custom`. `nodes` contains at least one existing node. `edges` name existing semantic edges and both endpoints of each edge must be in `nodes`. `excluded_edges` contains `{ "edge_id", "reason" }` entries; it is the only way to leave a scope-related edge outside the pattern check.

Scope edges and excluded edges cannot overlap, and every edge connected to a scope node must be in one of those two sets. An exclusion limits only this pattern check; ordinary integrity and strict validation still apply. The primary scope's pattern must equal the diagram's `behavior_pattern`. A primary scope can be a partial subgraph. It is whole-diagram coverage only when it includes every node and edge and has no exclusions.

Reject unknown fields, duplicate JSON keys or IDs, empty IDs, non-finite numbers, boolean values in place of version integers, and unsupported component versions. The context is limited to 2 MiB, nesting depth 32, 128 scopes, 128 group declarations, and 20,000 total ID references; oversized input is rejected rather than truncated. The pattern/group-only command form above does not carry a complete provenance bundle. When `provenance` is supplied, follow [the provenance contract](provenance.md) and provide its explicit completion manifest. An empty object or array is not a valid provenance component.

## Pattern declarations

The checks use only the directed multigraph formed by `scope.edges`; parallel edges retain their IDs. They never use node text, rank, or geometry as evidence.

| Pattern | Required declarations and check |
| --- | --- |
| `linear` | `entry` and `exit`. Every scope node is on one acyclic path from entry to exit; entry has degree 0/1, exit 1/0, and intermediate nodes 1/1. |
| `approval-loop` | `forward_edges`, `retry_edges`, and `terminal_rejections`. Each terminal rejection is `{rejected_edge, termination_path}`. A retry has a forward-only return path from its target to its source. A termination path is an ordered, continuous edge-ID path from the rejected edge's target to an existing end. Both retry and terminal-rejection lists may be empty when the declared forward edges explain every scope control edge. |
| `request-response` | `requests`. Each request gives `request_edge`, `response_required`, `response_edges`, and `completion_paths` entries of `{response_edge, path}`. Required requests need a response; response owners pair with the request target/source owners, and the completion path reaches the response source. Optional requests may have no response. |
| `fork-join` | `fork`, `join`, and at least two `branches`. Each branch is `{branch_id, entry_edge, required, join_path}` with optional `branch_nodes`. `join_path` is required only for a required branch; an optional branch may omit it or give an empty path. Required branches reach the declared join; every edge leaving the fork is declared or excluded. When present, `branch_nodes` must exactly equal the internal nodes proven by the branch: its entry target plus nodes traversed by `join_path`, excluding the fork and join. |
| `fan-in` | `target` and `inputs`, with at least two different sources. Each input is `{input_id, source, required, path}`. `path` is required only for a required input; an optional input may omit it or give an empty path. Required paths reach target. |
| `lifecycle` | `states`, `node_states`, and `allowed_transitions` entries of `{from, to}`. Each scope node has one declared state and every scope edge has an explicitly allowed state pair, including same-state edges. |
| `custom` | An empty `declarations` object. It reports `not_applicable` for pattern-specific rules while all ordinary and scope checks continue. |

No general ignore or force-pass mechanism exists. Legal exceptions are limited to declared exclusions, optional responses, an approval scope with no retry or terminal rejection when forward edges fully explain it, optional fork/fan-in inputs, and `custom`.

## Group contracts

`group_contracts` is optional. When supplied, it has `version: 1` and a `groups`
array. Each entry is `{group_id, scope_id, declarations}`: `group_id` must name
an existing v3 group, and `scope_id` selects one declared pattern scope. The
group's existing members must all be in that scope's nodes. Group membership,
kind, lane, and the diagram's existing semantic hash remain in the diagram;
the context does not duplicate or alter them. Groups remain lane-local even
when a declared proof path uses edges through another lane.

The component can be empty. Omitting it leaves the earlier pattern-only receipt
unchanged with `group_contracts.status: "not_provided"`. Supplying
`{"version": 1, "groups": []}` reports `provided_empty`, no group results, a
`not_applicable` group gate, and zero requested groups. That is an explicit
empty request, not a claim that every group received a topology check.

| Group kind | Declared relationship |
| --- | --- |
| `parallel` | Root fields are `fork`, `join`, and `branches`; `fork` and `branches` are required, and `join` is required whenever any branch has `join_required: true`. There are at least two branch entries. A branch supplies `branch_id`, `entry_edge`, `members`, `branch_edges`, and `join_required`. Its proof graph uses only its declared branch edges: fork-linked edges, every branch entry edge, and edges leaving the join cannot prove membership or a join. Actual shared nodes before the join are allowed. |
| `branch` | Root fields are `decision` and `branches`, both required. Each branch entry is `{entry_edge, outcome, member, path}`. The edge originates at that decision and carries the declared outcome; its path reaches the declared group member. The same `(decision, outcome)` may legitimately trigger multiple actions. Unrelated decision outcomes are not inferred as required members. |
| `merge` | Root fields are `target` and `inputs`, both required. Target is a group member and there are at least two distinct inputs. An input is `{input_id, source, required, path}`. Required paths, and nonempty optional paths, must reach target. |
| `exception` / `support` | Use an empty declarations object. These kinds receive reference-only checks, not a topology assertion. |

For `parallel`, `branch`, and `merge`, a missing required declaration or an
unexplained group member is incomplete; a well-formed but contradictory
relationship is failed. `exception` and `support` can report
`not_applicable` while their coverage remains partial and
`topology_assessed` remains false. That status never means the group topology
was proven.

An empty component is distinct from an empty declaration. For a requested
`parallel`, `branch`, or `merge` group, missing core facts and group members
left unexplained by an empty list are incomplete; the receipt lists the missing
declarations. A complete declaration that violates a minimum relationship—for
example, too few distinct parallel branches or merge inputs—is failed.

Group results are sorted by group ID. Each has its kind, scope, gate, rules, and
coverage. Coverage separates the unique members actually proved by rules
(`member_count`), declared member coverage, typed-reference coverage, and the
group's total members. Top-level group coverage separately reports requested,
unrequested, and topology-assessed group counts. `all_groups` means all groups
were listed, not that every listed group received a successful topology proof.

## Results and exits

The `semantic_context` receipt records the context and artifact digests, component version, scope results, gate, and coverage. With group contracts it also records `group_results`, `group_gate`, and `group_coverage`; pattern coverage remains separate. Each rule result has a stable rule ID, scope ID, status, sorted subject IDs, structured evidence, and missing declarations. Group rule subjects distinguish group, node, edge, and `(decision, outcome)` identities. Results are sorted by scope ID/group ID and rule ID.

`passed` means the declared check succeeded. `violated` means the declared structure contradicts the diagram. `not_assessed` means a required declaration or prerequisite is missing. `not_applicable` is valid for a declared `custom` scope, an explicit empty group component, and `exception` or `support` groups that receive reference-only assessment. It never turns partial or reference-only coverage into a topology proof. A partial scope never proves the complete diagram's business meaning.

| Condition | `validate` | `inspect` |
| --- | ---: | ---: |
| Invalid parameter/context JSON, bad reference, unsupported component, or artifact-binding rejection | 2 | 2 |
| Ordinary validation fails, or its strict gate fails | 1 | existing inspection behavior |
| A pattern or group rule is violated, or a required declaration is missing | 1 | 0 |
| Ordinary gate passes and every requested rule passes or is validly `not_applicable` | 0 | 0 |

An invalid context fails closed before pattern rules run. An artifact hash or model-hash mismatch is a binding rejection, not an unsupported-artifact claim. A diagram that cannot meet ordinary prerequisites produces `not_assessed`; the tool does not derive business conclusions from a broken input.
