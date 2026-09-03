"""Managed semantic metadata, identity, and versioned model hashing."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

from . import contracts, document


def managed_groups_attribute(
    pool: ET.Element,
    lanes: dict[str, dict],
    nodes: dict[str, dict],
) -> list[dict]:
    groups = document.json_attribute(pool, contracts.DATA_GROUPS, list, [])
    group_ids: set[str] = set()
    member_to_group: dict[str, str] = {}
    for index, group in enumerate(groups):
        try:
            contracts.validate_group_object(group, f"managed group[{index}]")
        except contracts.DiagramError as exc:
            raise document.managed_metadata_error(
                "Managed group metadata is invalid",
                attribute=contracts.DATA_GROUPS,
                evidence={"index": index, "cause": exc.code},
            ) from exc
        group_id = group["id"]
        if group_id in group_ids:
            raise document.managed_metadata_error(
                "Managed group IDs must be unique",
                attribute=contracts.DATA_GROUPS,
                evidence={"group_id": group_id},
            )
        group_ids.add(group_id)
        if group["lane"] not in lanes:
            raise document.managed_metadata_error(
                "Managed group references a missing lane",
                attribute=contracts.DATA_GROUPS,
                evidence={"group_id": group_id, "lane": group["lane"]},
            )
        for node_id in group["nodes"]:
            if node_id not in nodes:
                raise document.managed_metadata_error(
                    "Managed group references a missing node",
                    attribute=contracts.DATA_GROUPS,
                    evidence={"group_id": group_id, "node": node_id},
                )
            if nodes[node_id]["lane"] != group["lane"]:
                raise document.managed_metadata_error(
                    "Managed group contains a node from another lane",
                    attribute=contracts.DATA_GROUPS,
                    evidence={"group_id": group_id, "node": node_id},
                )
            if node_id in member_to_group:
                raise document.managed_metadata_error(
                    "Managed node belongs to more than one group",
                    attribute=contracts.DATA_GROUPS,
                    evidence={
                        "node": node_id,
                        "groups": [member_to_group[node_id], group_id],
                    },
                )
            member_to_group[node_id] = group_id

    for node_id, record in nodes.items():
        mirrored_group = record["cell"].attrib.get(contracts.DATA_GROUP_ID)
        expected_group = member_to_group.get(node_id)
        if mirrored_group != expected_group:
            raise document.managed_metadata_error(
                "Node group metadata does not match the managed group model",
                attribute=contracts.DATA_GROUP_ID,
                evidence={
                    "node": node_id,
                    "expected_group": expected_group,
                    "actual_group": mirrored_group,
                },
            )
    return groups


def semantic_model_document(tree: ET.ElementTree) -> dict:
    pool = document.find_pool(tree)
    root = document.graph_root(tree)
    lanes, nodes = document.lane_node_records(root, pool)
    edges = document.edge_records(root)
    phases = document.phase_records(root, pool)
    schema_version = pool.attrib.get(contracts.DATA_SCHEMA_VERSION, "1")

    lane_order = document.managed_id_list_attribute(pool, contracts.DATA_LANE_ORDER, None)
    if lane_order is None:
        lane_order = [
            cell.attrib[contracts.DATA_SEMANTIC_ID]
            for cell in list(root)
            if cell.attrib.get(contracts.DATA_KIND) == "lane"
            and cell.attrib.get(contracts.DATA_SEMANTIC_ID)
        ]
    if len(lane_order) != len(set(lane_order)) or set(lane_order) != set(lanes):
        raise contracts.DiagramError(
            "Managed lane order does not match the diagram lanes",
            code="integrity/schema-composition-mismatch",
            subject={"kind": "pool", "id": "main"},
            evidence={"lane_order": lane_order, "lane_ids": sorted(lanes)},
            supported_fixes=["restore-lane-order", "controlled-rebuild"],
        )

    lane_model = [
        {
            "id": lane_id,
            "label": lanes[lane_id]["cell"].attrib.get("value", ""),
        }
        for lane_id in lane_order
    ]
    node_model = []
    for node_id, record in sorted(nodes.items()):
        cell = record["cell"]
        try:
            rank = int(cell.attrib.get(contracts.DATA_RANK, "0"))
        except ValueError as exc:
            raise contracts.DiagramError(
                f"Node {node_id} has invalid rank metadata",
                code="integrity/schema-composition-mismatch",
                subject={"kind": "node", "id": node_id},
                evidence={"rank": cell.attrib.get(contracts.DATA_RANK)},
                supported_fixes=["restore-node-metadata", "controlled-rebuild"],
            ) from exc
        item = {
            "id": node_id,
            "lane": record["lane"],
            "rank": rank,
            "type": cell.attrib.get(contracts.DATA_NODE_TYPE, "process"),
            "label": cell.attrib.get("value", ""),
        }
        if cell.attrib.get(contracts.DATA_SLOT):
            item["slot"] = cell.attrib[contracts.DATA_SLOT]
        if cell.attrib.get(contracts.DATA_ANCHOR):
            item["anchor"] = document.json_attribute(cell, contracts.DATA_ANCHOR, dict, {})
        node_model.append(item)

    edge_model = []
    for edge_id, cell in sorted(edges.items()):
        item = {
            "id": edge_id,
            "from": cell.attrib.get(contracts.DATA_FROM, ""),
            "to": cell.attrib.get(contracts.DATA_TO, ""),
            "type": cell.attrib.get(contracts.DATA_EDGE_TYPE, "flow"),
            "label": cell.attrib.get("value", ""),
            "route": cell.attrib.get(contracts.DATA_ROUTE, "auto"),
        }
        for attribute, field in (
            (contracts.DATA_BRANCH, "branch"),
            (contracts.DATA_FLOW_ROLE, "flow_role"),
            (contracts.DATA_OUTCOME, "outcome"),
        ):
            if cell.attrib.get(attribute):
                item[field] = cell.attrib[attribute]
        edge_model.append(item)

    try:
        phase_model = [
            {
                "id": phase_id,
                "label": cell.attrib.get("value", ""),
                "from_rank": int(cell.attrib.get(contracts.DATA_FROM_RANK, "0")),
                "to_rank": int(cell.attrib.get(contracts.DATA_TO_RANK, "0")),
            }
            for phase_id, cell in sorted(phases.items())
        ]
    except ValueError as exc:
        raise contracts.DiagramError(
            "Phase rank metadata is invalid",
            code="integrity/schema-composition-mismatch",
            subject={"kind": "phase"},
            supported_fixes=["restore-phase-metadata", "controlled-rebuild"],
        ) from exc
    model = {
        "model_hash_version": contracts.MODEL_HASH_VERSION,
        "schema_version": schema_version,
        "title": pool.attrib.get("value", ""),
        "lanes": lane_model,
        "nodes": node_model,
        "edges": edge_model,
        "main_path": document.managed_id_list_attribute(pool, contracts.DATA_MAIN_PATH, []),
        "phases": phase_model,
    }
    if schema_version == contracts.V3_SCHEMA_VERSION:
        model["behavior_pattern"] = pool.attrib.get(contracts.DATA_BEHAVIOR_PATTERN, "")
        model["layout"] = {
            "profile": pool.attrib.get(contracts.DATA_LAYOUT_PROFILE, "review"),
            "phase_presentation": pool.attrib.get(contracts.DATA_PHASE_PRESENTATION, "bands"),
        }
        groups = managed_groups_attribute(pool, lanes, nodes)
        model["groups"] = sorted(
            (
                {
                    **{key: value for key, value in group.items() if key != "nodes"},
                    "nodes": sorted(group.get("nodes", [])),
                }
                for group in groups
            ),
            key=lambda group: group.get("id", ""),
        )
    return model


def semantic_model_hash(tree: ET.ElementTree) -> str:
    payload = json.dumps(
        semantic_model_document(tree),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def managed_artifact_summary(tree: ET.ElementTree) -> dict:
    pool = document.find_pool(tree)
    stored_hash = pool.attrib.get(contracts.DATA_MODEL_HASH)
    computed_hash = semantic_model_hash(tree)
    matches = stored_hash == computed_hash if stored_hash else None
    return {
        "tool_version": pool.attrib.get(contracts.DATA_TOOL_VERSION),
        "model_hash_version": pool.attrib.get(contracts.DATA_MODEL_HASH_VERSION),
        "stored_model_hash": stored_hash,
        "computed_model_hash": computed_hash,
        "model_hash_matches": matches,
    }


def refresh_managed_metadata(tree: ET.ElementTree) -> None:
    pool = document.find_pool(tree)
    root = document.graph_root(tree)
    if contracts.DATA_LANE_ORDER not in pool.attrib:
        pool.attrib[contracts.DATA_LANE_ORDER] = json.dumps(
            [
                cell.attrib[contracts.DATA_SEMANTIC_ID]
                for cell in list(root)
                if cell.attrib.get(contracts.DATA_KIND) == "lane"
                and cell.attrib.get(contracts.DATA_SEMANTIC_ID)
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
    pool.attrib[contracts.DATA_TOOL_VERSION] = contracts.TOOL_VERSION
    pool.attrib[contracts.DATA_MODEL_HASH_VERSION] = contracts.MODEL_HASH_VERSION
    pool.attrib[contracts.DATA_MODEL_HASH] = semantic_model_hash(tree)
