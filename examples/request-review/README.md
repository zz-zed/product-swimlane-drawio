# Request review example

This fictional, domain-neutral example demonstrates the complete workflow:

```text
prompt.md → confirmed semantic structure → process.json → local process.drawio → strict validation → preview.png
```

The diagram uses the v3 `approval-loop` pattern with four vertical lanes, a confirmed main path, one decision, a compact rework loop, a long-form spacing profile, and three phases presented in a navigation rail. The repository keeps the semantic source and PNG preview; the rebuild command produces the native, locally editable Draw.io file.

## Rebuild

From the repository root:

```bash
python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  build --spec examples/request-review/process.json \
  --output /tmp/request-review.drawio --strict

python3 skills/product-swimlane-drawio/scripts/drawio_swimlane.py \
  validate --input /tmp/request-review.drawio --strict
```

Repeated builds from the committed specification should produce byte-for-byte identical `.drawio` files. Generated `.drawio` files are intentionally excluded from the repository.
