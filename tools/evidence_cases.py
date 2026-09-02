"""Small fictional topology corpus; no editor evidence is synthesized here."""

from __future__ import annotations

import copy
import hashlib
import json


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def linear_spec(edge_count: int = 4, *, version: str = "3") -> dict:
    if edge_count < 2:
        raise ValueError("at least two edges are required")
    nodes = [{"id": f"n{i}", "lane": "lane-a", "rank": i + 1,
              "type": "start" if i == 0 else "end" if i == edge_count else "process",
              "label": "" if i in (0, edge_count) else f"Step {i}"}
             for i in range(edge_count + 1)]
    spec = {
        "schema_version": version, "title": "Neutral sequence",
        "lanes": [{"id": "lane-a", "label": "Lane A", "width": 240}],
        "nodes": nodes,
        "edges": [{"id": f"e{i}", "from": f"n{i}", "to": f"n{i + 1}",
                   "label": "Next"} for i in range(edge_count)],
        "main_path": [node["id"] for node in nodes],
    }
    if version == "1":
        del spec["schema_version"]
    elif version == "3":
        spec.update(behavior_pattern="linear", layout={"profile": "review"})
    return spec


def corpus() -> dict[str, dict]:
    decision = linear_spec()
    decision["behavior_pattern"] = "approval-loop"
    decision["nodes"][2].update(type="decision", label="Continue?")
    decision["edges"][2].update(branch="positive", outcome="continue", flow_role="main")
    decision["edges"].append({"id": "retry", "from": "n2", "to": "n1", "type": "retry",
                              "branch": "negative", "outcome": "retry", "route": "back"})
    decision["phases"] = [{"id": "phase-a", "label": "Phase A", "from_rank": 1, "to_rank": 3},
                          {"id": "phase-b", "label": "Phase B", "from_rank": 4, "to_rank": 5}]
    decision["layout"]["phase_presentation"] = "rail"
    exchange = linear_spec(6)
    exchange["behavior_pattern"] = "request-response"
    exchange["lanes"].append({"id": "lane-b", "label": "Lane B", "width": 240})
    exchange["nodes"][2]["lane"] = "lane-b"
    exchange["nodes"][4]["lane"] = "lane-b"
    exchange["edges"][1].update(type="call", flow_role="main")
    exchange["edges"][2].update(type="return", flow_role="response")
    exchange["edges"].append({"id": "retry", "from": "n4", "to": "n2", "type": "retry",
                              "route": "back", "flow_role": "retry", "label": "Retry"})
    locked = copy.deepcopy(decision)
    # An explicit hard conflict must be diagnosed, not allowed via an xfail.
    locked["edges"].append({"id": "locked", "from": "n1", "to": "n3",
                             "exit_side": "bottom", "entry_side": "top",
                             "exit_offset": 0.5, "entry_offset": 0.5})
    return {"decision-retry-phases": decision, "request-response-retry": exchange,
            "explicit-port-conflict": locked}
