"""Stable low-level contracts shared by swimlane runtime modules."""

from __future__ import annotations

import re


SCHEMA_VERSION = "2"
V3_SCHEMA_VERSION = "3"
STRUCTURED_SCHEMA_VERSIONS = {SCHEMA_VERSION, V3_SCHEMA_VERSION}
TOOL_VERSION = "0.6.5"
MODEL_HASH_VERSION = "1"

GROUP_KINDS = {"parallel", "branch", "merge", "exception", "support"}
GROUP_FIELDS = {"id", "label", "lane", "kind", "nodes"}
GROUP_UPDATE_FIELDS = GROUP_FIELDS

# These names are the persisted XML wire contract.  Keep them centralized so
# document/metadata extraction can use the same spelling without importing the
# CLI or inferring attributes from business-level schemas.
DATA_KIND = "data-kind"
DATA_SEMANTIC_ID = "data-semantic-id"
DATA_SCHEMA_VERSION = "data-schema-version"
DATA_TOOL_VERSION = "data-tool-version"
DATA_MODEL_HASH_VERSION = "data-model-hash-version"
DATA_MODEL_HASH = "data-model-hash"
DATA_LANE_ORDER = "data-lane-order"
DATA_MAIN_PATH = "data-main-path"
DATA_GROUPS = "data-groups"
DATA_GROUP_ID = "data-group-id"
DATA_ALLOW_PORT_REUSE = "data-allow-port-reuse"
DATA_ANCHOR = "data-anchor"
DATA_BEHAVIOR_PATTERN = "data-behavior-pattern"
DATA_BOTTOM_PADDING = "data-bottom-padding"
DATA_BRANCH = "data-branch"
DATA_EDGE_TYPE = "data-edge-type"
DATA_ENTRY_OFFSET = "data-entry-offset"
DATA_ENTRY_OFFSET_EXPLICIT = "data-entry-offset-explicit"
DATA_ENTRY_SIDE = "data-entry-side"
DATA_ENTRY_SIDE_EXPLICIT = "data-entry-side-explicit"
DATA_EXIT_OFFSET = "data-exit-offset"
DATA_EXIT_OFFSET_EXPLICIT = "data-exit-offset-explicit"
DATA_EXIT_SIDE = "data-exit-side"
DATA_EXIT_SIDE_EXPLICIT = "data-exit-side-explicit"
DATA_FILL_COLOR = "data-fill-color"
DATA_FLOW_ROLE = "data-flow-role"
DATA_FROM = "data-from"
DATA_FROM_RANK = "data-from-rank"
DATA_LABEL_HEIGHT = "data-label-height"
DATA_LABEL_LEFT = "data-label-left"
DATA_LABEL_SEGMENT = "data-label-segment"
DATA_LABEL_TOP = "data-label-top"
DATA_LABEL_WIDTH = "data-label-width"
DATA_LANE_HEADER_HEIGHT = "data-lane-header-height"
DATA_LANE_ID = "data-lane-id"
DATA_LAYOUT_PROFILE = "data-layout-profile"
DATA_MAX_RANK = "data-max-rank"
DATA_NODE_TYPE = "data-node-type"
DATA_OUTCOME = "data-outcome"
DATA_PHASE_PRESENTATION = "data-phase-presentation"
DATA_PHASE_RAIL_WIDTH = "data-phase-rail-width"
DATA_PRESENTATION = "data-presentation"
DATA_RANK = "data-rank"
DATA_ROUTE = "data-route"
DATA_ROW_GAP = "data-row-gap"
DATA_SLOT = "data-slot"
DATA_TITLE_HEIGHT = "data-title-height"
DATA_TO = "data-to"
DATA_TO_RANK = "data-to-rank"
DATA_TOP_PADDING = "data-top-padding"
DATA_WAYPOINTS_ORIGIN = "data-waypoints-origin"
MANAGED_KINDS = frozenset({"pool", "lane", "node", "phase", "edge"})


def number(value) -> str:
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


class DiagramError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "input/invalid",
        subject: dict | None = None,
        evidence: dict | None = None,
        supported_fixes: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.subject = subject
        self.evidence = evidence or {}
        self.supported_fixes = supported_fixes or []

    def diagnostic(self) -> dict:
        return make_diagnostic(
            self.code,
            "error",
            str(self),
            subject=self.subject,
            evidence=self.evidence,
            supported_fixes=self.supported_fixes,
        )


def make_diagnostic(
    code: str,
    severity: str,
    message: str,
    *,
    subject: dict | None = None,
    evidence: dict | None = None,
    supported_fixes: list[str] | None = None,
) -> dict:
    diagnostic = {
        "code": code,
        "severity": severity,
        "message": message,
        "evidence": evidence or {},
        "supported_fixes": supported_fixes or [],
    }
    if subject:
        diagnostic["subject"] = subject
    return diagnostic


def reject_unknown_fields(value: dict, allowed: set[str], subject: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DiagramError(
            f"Unknown field(s) in {subject}: {', '.join(unknown)}",
            code="schema/unknown-field",
            subject={"kind": subject},
            evidence={"fields": unknown},
            supported_fixes=["remove-unknown-fields"],
        )


def require_mapping(value, subject: str) -> dict:
    if not isinstance(value, dict):
        raise DiagramError(
            f"{subject} must be an object",
            code="schema/type",
            subject={"kind": subject},
            evidence={"expected": "object", "actual": type(value).__name__},
        )
    return value


def require_list(value, subject: str) -> list:
    if not isinstance(value, list):
        raise DiagramError(
            f"{subject} must be an array",
            code="schema/type",
            subject={"kind": subject},
            evidence={"expected": "array", "actual": type(value).__name__},
        )
    return value


def require_string(value, subject: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise DiagramError(
            f"{subject} must be {qualifier}",
            code="schema/type",
            subject={"kind": subject},
            evidence={"expected": qualifier},
        )
    return value


def validate_semantic_id(value, subject: str) -> str:
    value = require_string(value, subject)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise DiagramError(
            f"{subject} must contain only ASCII letters, digits, underscores, or hyphens",
            code="schema/id-format",
            subject={"kind": subject, "id": value},
            supported_fixes=["replace-semantic-id"],
        )
    return value


def validate_id_list(values, subject: str) -> list[str]:
    values = require_list(values, subject)
    result: list[str] = []
    for index, value in enumerate(values):
        result.append(validate_semantic_id(value, f"{subject}[{index}]"))
    if len(result) != len(set(result)):
        raise DiagramError(
            f"{subject} must not contain duplicate IDs",
            code="schema/duplicate",
            subject={"kind": subject},
        )
    return result


def validate_group_object(group: dict, subject: str, *, update: bool = False) -> None:
    require_mapping(group, subject)
    reject_unknown_fields(group, GROUP_UPDATE_FIELDS if update else GROUP_FIELDS, subject)
    required = ("id",) if update else ("id", "lane", "kind", "nodes")
    for field in required:
        if field not in group:
            raise DiagramError(
                f"Missing required field in {subject}: {field}",
                code="schema/required",
                subject={"kind": subject},
                evidence={"field": field},
            )
    validate_semantic_id(group["id"], f"{subject}.id")
    if "lane" in group:
        validate_semantic_id(group["lane"], f"{subject}.lane")
    if "label" in group:
        require_string(group["label"], f"{subject}.label", allow_empty=True)
    kind = require_string(group["kind"], f"{subject}.kind") if "kind" in group else None
    if kind is not None and kind not in GROUP_KINDS:
        raise DiagramError(
            f"Unsupported group kind: {kind}",
            code="schema/enum",
            subject={"kind": "group", "id": group.get("id")},
            evidence={"field": "kind", "allowed": sorted(GROUP_KINDS)},
        )
    nodes = validate_id_list(group["nodes"], f"{subject}.nodes") if "nodes" in group else None
    if nodes is not None and not nodes:
        raise DiagramError(
            f"{subject}.nodes must contain at least one node",
            code="schema/min-items",
            subject={"kind": "group", "id": group.get("id")},
        )
