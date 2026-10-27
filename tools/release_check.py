#!/usr/bin/env python3
"""Check the actual Skill release inventory without scanning local caches."""

from __future__ import annotations

import argparse
import ast
from datetime import date
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


SKILL_PREFIX = "skills/product-swimlane-drawio"
ROOT = Path(__file__).resolve().parents[1]
TOOL_VERSION_FILE = f"{SKILL_PREFIX}/scripts/swimlane_core/contracts.py"
# The reviewed manifest owns the complete inventory. These entrypoints must
# survive an accidental deletion from both the manifest and the working tree.
REQUIRED_RELEASE_FILES = frozenset({
    "release-files.json", "tools/release_check.py",
    f"{SKILL_PREFIX}/SKILL.md", f"{SKILL_PREFIX}/scripts/drawio_swimlane.py",
    f"{SKILL_PREFIX}/scripts/swimlane_core/__init__.py", TOOL_VERSION_FILE,
})


def load_release_manifest(path: Path) -> frozenset[str]:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list) or not values:
        raise ValueError("Release manifest must be a nonempty list of paths")
    files = set()
    for name in values:
        if (not isinstance(name, str) or not name or "\\" in name or ":" in name
                or any(ord(character) < 32 for character in name)):
            raise ValueError(f"Invalid release manifest path: {name!r}")
        path_parts = PurePosixPath(name)
        if (not path_parts.parts or path_parts.is_absolute() or path_parts.as_posix() != name
                or any(part in {".", "..", ".git", ".DS_Store", "__pycache__"} for part in path_parts.parts)
                or path_parts.suffix in {".pyc", ".pyo"}):
            raise ValueError(f"Invalid release manifest path: {name!r}")
        if name in files:
            raise ValueError(f"Duplicate release manifest path: {name}")
        files.add(name)
    missing = REQUIRED_RELEASE_FILES - files
    if missing:
        raise ValueError(f"Missing required release entrypoints: {', '.join(sorted(missing))}")
    return frozenset(files)


EXPECTED_RELEASE_FILES = load_release_manifest(ROOT / "release-files.json")
EXPECTED_SKILL_FILES = frozenset(
    name.removeprefix(SKILL_PREFIX + "/") for name in EXPECTED_RELEASE_FILES
    if name.startswith(SKILL_PREFIX + "/")
)


def read_tool_version(root: Path) -> str:
    """Read the literal runtime version without importing or executing code."""
    tree = ast.parse((root / TOOL_VERSION_FILE).read_text(encoding="utf-8"))
    values = []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
        )
        if any(isinstance(target, ast.Name) and target.id == "TOOL_VERSION" for target in targets):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                raise ValueError("TOOL_VERSION must be one literal string assignment")
            values.append(ast.literal_eval(node.value))
    if len(values) != 1 or not isinstance(values[0], str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
        r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?",
        values[0],
    ):
        raise ValueError("TOOL_VERSION must be one literal semantic version")
    return values[0]


def check_versions(root: Path, *, tag: str | None = None) -> dict:
    """Check release metadata separately from inventory-only fixture checks."""
    versions = {}
    errors = []
    try:
        versions["runtime"] = read_tool_version(root)
    except (OSError, UnicodeError, SyntaxError, ValueError) as error:
        errors.append(f"Invalid runtime version: {error}")
    for label, relative in (
        ("codex", ".codex-plugin/plugin.json"),
        ("claude", ".claude-plugin/marketplace.json"),
    ):
        try:
            data = json.loads((root / relative).read_text(encoding="utf-8"))
            if label == "claude":
                plugins = data["plugins"]
                if not isinstance(plugins, list):
                    raise ValueError("plugins must be a list")
                matches = [item for item in plugins if isinstance(item, dict)
                           and item.get("name") == "product-swimlane-drawio"]
                if len(matches) != 1:
                    raise ValueError("expected exactly one product-swimlane-drawio plugin")
                data = matches[0]
            version = data["version"]
            if not isinstance(version, str):
                raise ValueError("version must be a string")
            versions[label] = version
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
            errors.append(f"Invalid version in {relative}: {error}")
    try:
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        match = re.search(r"^## \[([^\]]+)\] - (\d{4}-\d{2}-\d{2})\s*$", changelog, re.MULTILINE)
        if match is None:
            raise ValueError("no dated release heading")
        date.fromisoformat(match[2])
        versions["changelog"] = match[1]
    except (OSError, UnicodeError, ValueError) as error:
        errors.append(f"Invalid version in CHANGELOG.md: {error}")
    expected = versions.get("runtime")
    if expected is not None:
        for label, version in versions.items():
            if version != expected:
                errors.append(f"Version mismatch: {label} is {version!r}, runtime is {expected!r}")
        if tag is not None and tag != f"v{expected}":
            errors.append(f"Tag mismatch: expected v{expected}, got {tag!r}")
    return {"valid": not errors, "version": expected, "versions": versions, "errors": errors}


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


def is_regular_file(root: Path, name: str) -> bool:
    parts = Path(name).parts
    return (not any(root.joinpath(*parts[:index]).is_symlink()
                    for index in range(1, len(parts) + 1))
            and (root / name).is_file())


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
        if not is_regular_file(root, name):
            errors.append(f"Not a regular release file: {name}")
    for name in sorted(files - EXPECTED_SKILL_FILES):
        errors.append(f"Unexpected packaged file: {name}")
    for name in sorted(EXPECTED_SKILL_FILES - files):
        errors.append(f"Missing packaged file: {name}")
    # Only known text files may be decoded. Unexpected binaries still fail
    # inventory validation, rather than disappearing behind suffix filters.
    for name in sorted(files & EXPECTED_SKILL_FILES):
        path = root / SKILL_PREFIX / name
        if not is_regular_file(root, f"{SKILL_PREFIX}/{name}"):
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
    parser.add_argument("--versions", action="store_true", help="Also check runtime and release metadata versions")
    parser.add_argument("--tag", help="Require the exact v<version> tag; implies --versions")
    args = parser.parse_args()
    result = check_release(args.root, source=False if args.export else None)
    if args.versions or args.tag is not None:
        result["versions"] = check_versions(args.root, tag=args.tag)
        result["errors"].extend(result["versions"]["errors"])
        result["valid"] = not result["errors"]
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
