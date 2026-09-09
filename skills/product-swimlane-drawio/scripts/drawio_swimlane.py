#!/usr/bin/env python3
"""Build, inspect, patch, validate, and compare editable Draw.io swimlane diagrams."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

from swimlane_core import build, contracts, document, roundtrip, validation as core_validation


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def command_build(args: argparse.Namespace) -> None:
    document.ensure_output_available(args.output, args.force)
    spec = load_json(args.spec)
    tree = build.build_tree(spec)
    result = core_validation.validate_tree(tree)
    strict_failed = bool(args.strict and result["warnings"])
    if not result["valid"] or strict_failed:
        raise contracts.DiagramError(
            "Generated diagram failed strict validation"
            if strict_failed
            else "Generated diagram failed validation",
            code="delivery/strict-validation-failed"
            if strict_failed
            else "delivery/validation-failed",
            evidence={"strict": args.strict, "diagnostics": result["diagnostics"]},
        )
    document.write_tree(
        tree, args.output,
        candidate_check=lambda candidate: result.update(
            roundtrip._check_delivery_candidate(tree, candidate, args.strict)
        ),
    )
    result.update(
        {
            "operation": "build",
            "strict_mode": args.strict,
            "output": document.file_receipt(args.output),
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_patch(args: argparse.Namespace) -> None:
    document.ensure_different(args.input, args.output)
    document.ensure_output_available(args.output, args.force)
    input_receipt = document.file_receipt(args.input)
    if not args.expected_input_sha256:
        raise contracts.DiagramError(
            "Patch requires the SHA-256 digest returned by inspect",
            code="delivery/input-sha256-required",
            supported_fixes=["inspect-latest-input", "supply-expected-input-sha256"],
        )
    if (
        input_receipt["sha256"] != args.expected_input_sha256
    ):
        raise contracts.DiagramError(
            "Patch input does not match the reviewed SHA-256 baseline",
            code="delivery/input-sha256-mismatch",
            evidence={
                "expected": args.expected_input_sha256,
                "actual": input_receipt["sha256"],
            },
            supported_fixes=["inspect-latest-input", "update-expected-input-sha256"],
        )
    tree = document.read_tree(args.input)
    input_validation = core_validation.validate_tree(tree)
    integrity_errors = [
        diagnostic
        for diagnostic in input_validation["diagnostics"]
        if diagnostic["severity"] == "error"
        and diagnostic["code"].startswith("integrity/")
    ]
    if integrity_errors:
        raise contracts.DiagramError(
            "Patch input has unsafe managed metadata",
            code="delivery/input-integrity-failed",
            evidence={"diagnostics": integrity_errors},
            supported_fixes=["restore-semantic-metadata", "controlled-rebuild"],
        )
    if (
        input_validation.get("model_hash_matches") is False
        and not args.accept_model_drift
    ):
        raise contracts.DiagramError(
            "Patch input semantic metadata changed after its managed hash was written",
            code="integrity/model-hash-mismatch",
            evidence={
                "stored": input_validation.get("stored_model_hash"),
                "computed": input_validation.get("computed_model_hash"),
            },
            supported_fixes=["review-semantic-drift", "use-accept-model-drift"],
        )
    before = copy.deepcopy(tree)
    changes = load_json(args.changes)
    patch_receipt = roundtrip.patch_tree(tree, changes, args.allow_geometry_updates)
    patch_receipt.update(
        {
            "input_sha256": input_receipt["sha256"],
            "input_bytes": input_receipt["bytes"],
            "input_tool_version": input_validation.get("tool_version"),
            "input_managed_state": input_validation.get("managed_state"),
            "input_model_hash_matches": input_validation.get("model_hash_matches"),
            "accepted_model_drift": bool(
                args.accept_model_drift
                and input_validation.get("model_hash_matches") is False
            ),
        }
    )
    result = core_validation.validate_tree(tree)
    strict_failed = bool(args.strict and result["warnings"])
    if not result["valid"] or strict_failed:
        raise contracts.DiagramError(
            "Patched diagram failed strict validation"
            if strict_failed
            else "Patched diagram failed validation",
            code="delivery/strict-validation-failed"
            if strict_failed
            else "delivery/validation-failed",
            evidence={"strict": args.strict, "diagnostics": result["diagnostics"]},
        )
    document.write_tree(
        tree, args.output,
        candidate_check=lambda candidate: result.update(
            roundtrip._check_delivery_candidate(tree, candidate, args.strict, before=before, changes=changes)
        ),
    )
    result.update(
        {
            "operation": "patch",
            "strict_mode": args.strict,
            "manual_waypoints_preserved": patch_receipt["manual_waypoints_preserved"],
            "manual_waypoints_checked": patch_receipt["manual_waypoints_checked"],
            "patch_receipt": patch_receipt,
            "output": document.file_receipt(args.output),
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_validate(args: argparse.Namespace) -> None:
    result = core_validation.validate_tree(document.read_tree(args.input))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"] or (args.strict and result["warnings"]):
        raise SystemExit(1)


def command_compare(args: argparse.Namespace) -> None:
    changes = load_json(args.changes) if args.changes else None
    result = roundtrip.compare_trees(document.read_tree(args.before), document.read_tree(args.after), changes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["preserved"]:
        raise SystemExit(1)


def command_inspect(args: argparse.Namespace) -> None:
    result = roundtrip.inspect_tree(document.read_tree(args.input))
    result["input"] = document.file_receipt(args.input)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build a new editable Draw.io file")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument(
        "--strict",
        action="store_true",
        help="Do not write output when quality warnings are present",
    )
    build.add_argument("--force", action="store_true", help="Replace an existing output file")
    build.set_defaults(func=command_build)

    patch = subparsers.add_parser("patch", help="Incrementally patch an existing generated Draw.io file")
    patch.add_argument("--input", type=Path, required=True)
    patch.add_argument("--changes", type=Path, required=True)
    patch.add_argument("--output", type=Path, required=True)
    patch.add_argument(
        "--strict",
        action="store_true",
        help="Do not write output when quality warnings are present",
    )
    patch.add_argument("--allow-geometry-updates", action="store_true")
    patch.add_argument(
        "--expected-input-sha256",
        help="Fail unless the input matches this reviewed SHA-256 digest",
    )
    patch.add_argument(
        "--accept-model-drift",
        action="store_true",
        help="Rebaseline reviewed semantic edits when the stored model hash differs",
    )
    patch.add_argument("--force", action="store_true", help="Replace an existing output file")
    patch.set_defaults(func=command_patch)

    validate = subparsers.add_parser("validate", help="Validate structure and visual routing heuristics")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--strict", action="store_true", help="Fail when quality warnings are present")
    validate.set_defaults(func=command_validate)

    compare = subparsers.add_parser("compare", help="Prove that all existing semantic cells were preserved")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--changes", type=Path, help="Allow cells named in a patch file to change")
    compare.set_defaults(func=command_compare)

    inspect = subparsers.add_parser("inspect", help="Inspect compatible semantic metadata and geometry")
    inspect.add_argument("--input", type=Path, required=True)
    inspect.set_defaults(func=command_inspect)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except contracts.DiagramError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": [str(exc)],
                    "warnings": [],
                    "diagnostics": [exc.diagnostic()],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except (json.JSONDecodeError, ET.ParseError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if isinstance(exc, json.JSONDecodeError):
            code = "input/json-invalid"
        elif isinstance(exc, ET.ParseError):
            code = "input/drawio-xml-invalid"
        elif isinstance(exc, OSError):
            code = "delivery/io-error"
        else:
            code = "input/invalid"
        diagnostic = contracts.make_diagnostic(code, "error", str(exc))
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": [str(exc)],
                    "warnings": [],
                    "diagnostics": [diagnostic],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except Exception as exc:
        message = "Unexpected internal error"
        diagnostic = contracts.make_diagnostic(
            "internal/unexpected",
            "error",
            message,
            evidence={"exception_type": type(exc).__name__},
            supported_fixes=["report-bug"],
        )
        print("error: Unexpected internal error", file=sys.stderr)
        print(
            json.dumps(
                {
                    "valid": False,
                    "errors": [message],
                    "warnings": [],
                    "diagnostics": [diagnostic],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
