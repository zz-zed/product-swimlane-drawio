# Semantic context example

This fictional, domain-neutral example shows the smallest complete declared
`linear` pattern check. It contains a semantic specification and the matching
context; generated `.drawio` files remain outside the repository.

From the repository root, build to a new temporary path, then validate with the
context:

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  build --spec examples/semantic-context/process.json \
  --output /tmp/semantic-context.drawio --strict

python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  validate --input /tmp/semantic-context.drawio \
  --context examples/semantic-context/pattern-context.json --strict
```

The second command exits 0 and returns `semantic_context.gate: "passed"`.
The context binds to the exact generated file bytes and semantic-model hash, so
editing either the diagram or context-relevant process semantics requires a new
context. See the [semantic context contract](../../skills/product-swimlane-drawio/references/semantic-context.md) for the complete format and the other declared patterns.

`group-branch.process.json` and `group-branch.context.json` show an opt-in,
lane-local branch group. The `approved` outcome deliberately triggers both
`record` and `notify`; the unrelated `declined` outcome is not a group member.

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  build --spec examples/semantic-context/group-branch.process.json \
  --output /tmp/group-branch.drawio --strict

python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  validate --input /tmp/group-branch.drawio \
  --context examples/semantic-context/group-branch.context.json --strict
```

This check exits 0 with `semantic_context.group_gate: "passed"`.
