#!/usr/bin/env python3
"""Check the actual Skill release inventory without scanning local caches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


SKILL_PREFIX = "skills/product-swimlane-drawio"
EXPECTED_SKILL_FILES = frozenset({
    "SKILL.md", "agents/openai.yaml", "references/schema.json",
    "references/schema.md", "scripts/drawio_swimlane.py",
    "scripts/swimlane_core/__init__.py", "scripts/swimlane_core/contracts.py",
    "scripts/swimlane_core/geometry.py",
    "scripts/swimlane_core/document.py", "scripts/swimlane_core/metadata.py",
})
EXPECTED_RELEASE_FILES = frozenset(json.loads(
    (Path(__file__).resolve().parents[1] / "release-files.json").read_text(encoding="utf-8")
))


def release_inventory(root: Path, *, source: bool | None = None) -> set[str]:
    """Git source: tracked + nonignored candidates; export: every actual file.

    A tracked cache is a release error even if it matches .gitignore. An
    exported package has no cache exemptions. Never fall back after Git fails.
    """
    root = root.resolve()
    if source is None:
        source = (root / ".git").exists()
    if source:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"],
            check=True, capture_output=True,
        )
        return {name for name in result.stdout.decode("utf-8").split("\0") if name}
    return {path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() or path.is_symlink()}


def skill_inventory(root: Path, *, source: bool | None = None) -> set[str]:
    prefix = SKILL_PREFIX + "/"
    return {name[len(prefix):] for name in release_inventory(root, source=source)
            if name.startswith(prefix)}


def check_release(root: Path, *, source: bool | None = None) -> dict:
    release_files = release_inventory(root, source=source)
    prefix = SKILL_PREFIX + "/"
    files = {name[len(prefix):] for name in release_files if name.startswith(prefix)}
    errors = []
    for name in sorted(release_files - EXPECTED_RELEASE_FILES):
        errors.append(f"Unexpected release file: {name}")
    for name in sorted(EXPECTED_RELEASE_FILES - release_files):
        errors.append(f"Missing release file: {name}")
    for name in sorted(release_files & EXPECTED_RELEASE_FILES):
        path = root / name
        parts = Path(name).parts
        if any(root.joinpath(*parts[:index]).is_symlink() for index in range(1, len(parts) + 1)) or not path.is_file():
            errors.append(f"Not a regular release file: {name}")
    for name in sorted(files - EXPECTED_SKILL_FILES):
        errors.append(f"Unexpected packaged file: {name}")
    for name in sorted(EXPECTED_SKILL_FILES - files):
        errors.append(f"Missing packaged file: {name}")
    # Only known text files may be decoded. Unexpected binaries still fail
    # inventory validation, rather than disappearing behind suffix filters.
    for name in sorted(files & EXPECTED_SKILL_FILES):
        path = root / SKILL_PREFIX / name
        parts = (Path(SKILL_PREFIX) / name).parts
        linked = any(root.joinpath(*parts[:index]).is_symlink()
                     for index in range(1, len(parts) + 1))
        if linked or not path.is_file():
            errors.append(f"Not a regular packaged file: {name}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"Invalid UTF-8 packaged text: {name}")
            continue
        if re.search(r"/Users/|/home/|[A-Za-z]:\\Users\\", content):
            errors.append(f"Absolute user path in packaged text: {name}")
    return {"valid": not errors, "files": sorted(files),
            "release_files": sorted(release_files), "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--export", action="store_true", help="Check every file, without Git ignore rules")
    args = parser.parse_args()
    result = check_release(args.root, source=False if args.export else None)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
