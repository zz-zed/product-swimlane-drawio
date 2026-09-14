# Provenance bundle example

This fictional, domain-neutral example builds a provenance-only context. Its
source is an inert declaration: the tool does not read the reference, fetch a
URL, verify the named reviewer, or authenticate the confirmed facts.

Run this from the repository root. `provenance-bundle` must be a new directory;
the fixed member names are part of the delivery contract.

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  build --spec examples/provenance/process.json --strict \
  --context examples/provenance/context-template.json \
  --output provenance-bundle/diagram.drawio \
  --context-output provenance-bundle/context.json \
  --completion-manifest provenance-bundle/completion.json

python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  validate --input provenance-bundle/diagram.drawio --strict \
  --context provenance-bundle/context.json \
  --completion-manifest provenance-bundle/completion.json
```

The template is unbound; build writes a bound context only after producing the
diagram. The final `completion.json` is the delivery commit point. A missing or
mismatched manifest makes a provenance bundle unreadable. See the packaged
[provenance contract](../../skills/product-swimlane-drawio/references/provenance.md).
