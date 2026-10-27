#!/usr/bin/env python3
"""Build a temporary release ZIP and verify its extracted contents in place."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from release_check import (
    EXPECTED_RELEASE_FILES, ROOT, check_release, check_versions, is_regular_file,
    load_release_manifest,
)


def file_hashes(root: Path) -> dict[str, str]:
    for name in EXPECTED_RELEASE_FILES:
        if not is_regular_file(root, name):
            raise ValueError(f"Not a regular release file: {name}")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sorted(EXPECTED_RELEASE_FILES)}


def run_checks(root: Path, *, discover_skill: bool, timeout: int) -> None:
    subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=root, check=True, timeout=timeout,
    )
    if discover_skill:
        subprocess.run(
            ["npx", "--yes", "skills@1.5.22", "add", ".", "--list"],
            cwd=root, check=True, timeout=60,
        )


def verify_release(root: Path = ROOT, *, tag: str | None = None,
                   discover_skill: bool = False, timeout: int = 480) -> dict:
    """Keep the source and extracted package unchanged, including on failure."""
    root = root.resolve()
    result = {"valid": False, "version": None, "file_count": len(EXPECTED_RELEASE_FILES),
              "checks_passed": False, "skill_discovery": "not_requested", "errors": []}
    errors = result["errors"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        errors.append("Test timeout must be a positive integer")
        return result
    try:
        errors.extend(check_release(root)["errors"])
        if errors:
            return result
        if load_release_manifest(root / "release-files.json") != EXPECTED_RELEASE_FILES:
            errors.append("Source manifest differs from the reviewed release inventory")
            return result
        versions = check_versions(root, tag=tag)
        result["version"] = versions["version"]
        errors.extend(versions["errors"])
        if errors:
            return result
        original_hashes = file_hashes(root)
        temporary_parent = Path(tempfile.gettempdir()).resolve()
        if temporary_parent.is_relative_to(root):
            errors.append("Temporary directory must be outside the source checkout")
            return result
        with tempfile.TemporaryDirectory(prefix="swimlane-release-") as temporary:
            directory = Path(temporary)
            archive = directory / "release.zip"
            extracted = directory / "extracted"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
                for name in sorted(EXPECTED_RELEASE_FILES):
                    if not is_regular_file(root, name):
                        raise ValueError(f"Not a regular release file: {name}")
                    package.writestr(name, (root / name).read_bytes())
            with zipfile.ZipFile(archive) as package:
                package.extractall(extracted)
            errors.extend(check_release(extracted, source=False)["errors"])
            errors.extend(check_versions(extracted, tag=tag)["errors"])
            if file_hashes(extracted) != original_hashes:
                errors.append("Extracted release bytes differ from the checked source")
            if errors:
                return result
            original_directories = {path.relative_to(extracted).as_posix()
                                    for path in extracted.rglob("*") if path.is_dir()}
            try:
                run_checks(extracted, discover_skill=discover_skill, timeout=timeout)
                result["checks_passed"] = True
                if discover_skill:
                    result["skill_discovery"] = "passed"
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                errors.append(f"Extracted release checks failed: {error}")
                if discover_skill:
                    result["skill_discovery"] = "not_completed"
            # Tests and discovery may fail after writing files. Always inspect
            # the extraction again rather than treating a zero exit as proof.
            errors.extend(check_release(extracted, source=False)["errors"])
            for name, expected_hash in original_hashes.items():
                path = extracted / name
                if (not is_regular_file(extracted, name)
                        or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash):
                    errors.append(f"Release file changed during verification: {name}")
            actual_directories = {path.relative_to(extracted).as_posix()
                                  for path in extracted.rglob("*") if path.is_dir()}
            if actual_directories != original_directories:
                errors.append("Release directories changed during verification")
            errors.extend(check_release(root)["errors"])
            if file_hashes(root) != original_hashes:
                errors.append("Source files changed during release verification")
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError,
            zipfile.BadZipFile) as error:
        errors.append(f"Release verification failed: {error}")
    result["valid"] = not errors
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tag", help="Require the exact v<version> release tag")
    parser.add_argument("--discover-skill", action="store_true",
                        help="Also run pinned Skill discovery from the extraction")
    parser.add_argument("--timeout", type=int, default=480,
                        help="Full-suite timeout in seconds (default: 480)")
    args = parser.parse_args()
    result = verify_release(args.root, tag=args.tag, discover_skill=args.discover_skill,
                            timeout=args.timeout)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
