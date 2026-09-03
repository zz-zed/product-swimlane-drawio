# Contributing

Contributions that improve deterministic layout, routing quality, validation, portability, documentation, or test coverage are welcome.

## Development setup

Requirements:

- Python 3.10 or later
- Node.js and `npx` for local Agent Skill discovery checks

Run the tests:

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tools/release_check.py
```

Distribute the complete Skill directory, including its adjacent `swimlane_core` modules. Keep the explicit inventories in `release-files.json` and `tools/release_check.py` synchronized when adding a file. Before delivery, create and extract an archive containing exactly the release inventory, then run the full test command and `python3 -B tools/release_check.py --export` from that extraction. The extracted package must remain unchanged and contain no bytecode caches. Child Python commands must also pass `-B`; the parent flag is not inherited.

Check Skill discovery:

```bash
npx skills add . --list
```

## Pull requests

Before submitting a change:

1. Keep the actual Skill package limited to essential instructions, references, metadata, and scripts.
2. Use neutral fixtures and placeholders. Do not include user data, organization names, proprietary terminology, credentials, or domain-specific sample flows.
3. Preserve stable semantic IDs and geometry-preserving patch behavior unless the change intentionally revises the compatibility contract.
4. Add or update tests for new behavior and expected failures.
5. Run strict validation for every generated test diagram.
6. Document compatibility or behavior changes in `CHANGELOG.md`.

## Commit scope

Do not commit generated `.drawio` files, task-specific or draft previews, local task inputs, virtual environments, caches, or editor metadata. Curated PNG assets referenced by public documentation or fictional examples are allowed.
