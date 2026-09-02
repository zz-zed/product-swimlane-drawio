#!/usr/bin/env python3
"""Print reproducible CLI contract fingerprints; never update baselines in place."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from evidence_cases import corpus, digest, linear_spec

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"
BASELINE_TOOL_VERSIONS = {"0.5.0", "0.5.1"}


def normalize(value, replacements, *, tool_version=None, input_version=None, path=()):
    """Only valid stamps matching the actual corresponding artifact may vary."""
    if isinstance(value, dict):
        return {key: normalize(item, replacements, tool_version=tool_version,
                               input_version=input_version, path=(*path, key))
                for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item, replacements, tool_version=tool_version,
                          input_version=input_version, path=(*path, index))
                for index, item in enumerate(value)]
    expected = None
    if path in {("result", "tool_version"), ("result", "validation", "tool_version")}:
        expected = tool_version
    elif path == ("result", "patch_receipt", "input_tool_version"):
        expected = input_version
    if isinstance(value, str):
        if expected in BASELINE_TOOL_VERSIONS and value == expected:
            return "<tool-version>"
        for old, new in replacements.items():
            value = value.replace(old, new)
    return value


def capture(tool: Path = TOOL) -> dict:
    records = {}
    for name, spec in {**{f"linear-v{v}": linear_spec(version=v) for v in ("1", "2", "3")},
                       **corpus()}.items():
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "spec.json"
            output = directory / "before.drawio"
            after = directory / "after.drawio"
            changes = directory / "patch.json"
            source.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            changes.write_text(json.dumps({"update_nodes": [{"id": "n1", "label": "Revised"}]}), encoding="utf-8")
            replacements = {str(directory): "<workdir>"}
            versions = {}

            def run(*args):
                result = subprocess.run([sys.executable, "-B", str(tool), *map(str, args)],
                                        text=True, capture_output=True, timeout=30, check=False)
                return {"exit_code": result.returncode, "result": json.loads(result.stdout), "stderr": result.stderr}

            build = run("build", "--spec", source, "--output", output)
            record = {"spec_sha256": digest(spec), "commands": {}}
            commands = {"build": build}
            if output.exists():
                def artifact(path):
                    raw = path.read_bytes()
                    raw_hash = hashlib.sha256(raw).hexdigest()
                    match = re.search(rb'data-tool-version="([^"]+)"', raw)
                    versions[path.name] = match.group(1).decode() if match else None
                    canonical = re.sub(rb'data-tool-version="[^"]+"', b'data-tool-version="<tool-version>"', raw)
                    canonical_hash = hashlib.sha256(canonical).hexdigest()
                    replacements[raw_hash] = canonical_hash
                    return {"bytes": len(raw), "canonical_sha256": canonical_hash}

                record["before"] = artifact(output)
                commands["inspect"] = run("inspect", "--input", output)
                commands["validate-strict"] = run("validate", "--input", output, "--strict")
                commands["patch"] = run("patch", "--input", output, "--changes", changes,
                                         "--output", after, "--expected-input-sha256",
                                         hashlib.sha256(output.read_bytes()).hexdigest(), "--strict")
                if after.exists():
                    record["after"] = artifact(after)
                    commands["compare"] = run("compare", "--before", output, "--after", after, "--changes", changes)
                rejected = directory / "rejected.drawio"
                commands["reject-stale-input"] = run("patch", "--input", output, "--changes", changes,
                                                     "--output", rejected, "--expected-input-sha256", "0" * 64, "--strict")
                record["stale_output_exists"] = rejected.exists()
                record["input_unchanged"] = artifact(output) == record["before"]
            strict_output = directory / "strict.drawio"
            commands["build-strict"] = run("build", "--spec", source, "--output", strict_output, "--strict")
            record["strict_output_exists"] = strict_output.exists()
            record["candidate_files"] = sorted(p.name for p in directory.glob(".*.candidate"))
            for command, result in commands.items():
                normalized = normalize(result, replacements,
                                       tool_version=versions.get(after.name if command == "patch" else output.name),
                                       input_version=versions.get(output.name))
                record["commands"][command] = {"exit_code": result["exit_code"], "sha256": digest(normalized)}
            report = commands.get("validate-strict", build)["result"]
            record["diagnostics"] = report.get("diagnostics", [])
            records[name] = record
    return {"contract_version": 1, "normalization": "Only temporary directory, tool-version metadata and file hashes derived from that metadata.",
            "cases": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", type=Path, default=TOOL)
    args = parser.parse_args()
    print(json.dumps(capture(args.tool), ensure_ascii=False, indent=2))
