# Contributing

Contributions that improve deterministic layout, routing quality, validation, portability, documentation, or test coverage are welcome.

## Development setup

Requirements:

- Python 3.10 or later; CI covers Python 3.10 through 3.15
- Node.js 24 and `npx` for Agent Skill discovery checks

Run the tests:

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tools/release_check.py --versions
```

Distribute the complete Skill directory, including its adjacent `swimlane_core` modules. `release-files.json` is the single reviewed file inventory; the Skill subset is derived from it. Add or remove an entry when intentionally changing the public package. Unknown files, tracked caches, symlinks, missing files, and absolute user paths still fail validation.

The runtime `TOOL_VERSION` in `swimlane_core/contracts.py` is the current-version source. `--versions` checks it against the Codex plugin, Claude marketplace, and latest dated changelog release. Keep historical compatibility fixtures and historical baseline versions explicit; they are not aliases for the current version.

Check Skill discovery:

```bash
npx --yes skills@1.5.22 add . --list
```

## CI and release verification

The repository keeps one workflow with the following scope:

| Event | Checks |
| --- | --- |
| Pull request | Full tests on Python 3.10 and 3.15, release inventory/version checks, Skill discovery |
| Push to `main` | Full tests on Python 3.10–3.15, release inventory/version checks, Skill discovery |
| Push of a `v*` tag | Tag/main-history and version checks, actual archive/extraction verification on Python 3.12, discovery from the extraction |
| Manual run on a branch | Full Python matrix, discovery, and actual archive/extraction verification |

Ordinary development-branch pushes do not duplicate PR runs. New runs cancel superseded checks for the same PR or branch; in-progress tag verification is not cancelled. Python and release-package jobs have a ten-minute limit, while discovery has a three-minute limit. These limits apply to executing jobs, not runner queue time.

Retaining Python 3.10 is a project compatibility commitment even though its upstream maintenance has ended. Change the minimum version explicitly before removing that coverage. Performance probes remain manual and are not part of every PR.

Before tagging a release, wait for the full `main` matrix to pass on the release commit. Tags must refer to a commit in `main` history and match the runtime version exactly as `v<version>`. The ancestry check alone does not prove that a prior workflow passed. Tag verification does not publish a GitHub Release or upload assets.

Run the same actual-package verification locally:

```bash
python3 -B tools/verify_release.py --discover-skill
```

To verify a particular release tag, also pass `--tag '<release-tag>'`. The verifier checks the source inventory and versions, creates a ZIP from exactly the reviewed inventory in a temporary directory, extracts it, and runs the complete test suite from the extracted root. It checks the export before and after the tests and discovery, and rejects changed file bytes or additional files. The temporary archive and extraction are cleaned up automatically.

The extracted package must remain unchanged and contain no bytecode caches. Child Python commands must also pass `-B`; the parent flag is not inherited. For an already unpacked distribution, run `python3 -B tools/release_check.py --root '<export-root>' --export --versions`.

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
