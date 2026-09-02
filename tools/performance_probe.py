#!/usr/bin/env python3
"""Bounded, isolated same-topology timing/RSS probes (standard library only)."""

from __future__ import annotations

import argparse
import cProfile
import importlib.util
import json
import math
import platform
import pstats
try:
    import resource
except ImportError:  # Windows has no standard-library getrusage.
    resource = None
import subprocess
import sys
import time
from pathlib import Path

from evidence_cases import digest, linear_spec
from swimlane_loader import load_skill_modules

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py"
MAX_EDGES = 5000
MAX_TIMEOUT = 60


def check_limits(edges: int, timeout: float = 15) -> None:
    if not 2 <= edges <= MAX_EDGES:
        raise ValueError(f"edge count must be between 2 and {MAX_EDGES}")
    if not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be finite and between 0 (exclusive) and {MAX_TIMEOUT} seconds")


def replay_label_overlaps(tool, geometry, tree) -> dict:
    """Time the three validation overlap loops without XML/diagnostic overhead.

    This is a separately executed microbenchmark, NOT a disjoint slice of the
    validation wall time. Preparation is outside the timed region.
    """
    root = tool.document.graph_root(tree)
    lanes, nodes = tool.document.lane_node_records(root, tool.document.find_pool(tree))
    bounds = [tool.document.node_bounds_in_pool(node, lanes[node["lane"]]) for node in nodes.values()]
    segments, labels = {}, {}
    for edge_id, cell in tool.document.edge_records(root).items():
        points = tool.document.edge_polyline(cell, lanes, nodes)
        segments[edge_id] = list(zip(points, points[1:]))
        label = tool.effective_label_bounds(cell, points)
        if label is not None:
            labels[edge_id] = label
    started = time.perf_counter()
    hits = 0
    for edge_id, (carrier, box) in labels.items():
        hits += sum(geometry.bounds_overlap(box, node, gap=1.0) for node in bounds)
        for other_id, pieces in segments.items():
            for index, segment in enumerate(pieces):
                if edge_id == other_id and index == carrier:
                    continue
                if geometry.segment_intersects_box(segment, box, gap=1.0):
                    hits += 1
                    break
    label_ids = sorted(labels)
    for index, first in enumerate(label_ids):
        for second in label_ids[index + 1:]:
            hits += geometry.bounds_overlap(labels[first][1], labels[second][1], gap=2.0)
    return {"seconds": time.perf_counter() - started, "overlap_hits": hits,
            "labels": len(labels), "nodes": len(bounds), "edges": len(segments)}


def worker(edges: int) -> dict:
    check_limits(edges)
    loaded = load_skill_modules(TOOL, module_name="swimlane_probe")
    tool = loaded.tool
    spec = linear_spec(edges)
    profiler = cProfile.Profile()
    profiler.enable()
    started = time.perf_counter()
    tree = tool.build_tree(spec)
    built = time.perf_counter()
    result = tool.validate_tree(tree)
    finished = time.perf_counter()
    profiler.disable()
    stats = pstats.Stats(profiler).stats
    label_overlap = replay_label_overlaps(tool, loaded.geometry, tree)

    def function_cost(name):
        entries = [data for (_, _, function), data in stats.items() if function == name]
        return {"calls": sum(data[1] for data in entries),
                "self_seconds": sum(data[2] for data in entries),
                "inclusive_seconds": sum(data[3] for data in entries)} if entries else None

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource is not None else None
    return {"status": "completed", "edges": edges, "nodes": edges + 1,
            "spec_sha256": digest(spec), "tool_version": loaded.contracts.TOOL_VERSION,
            "build_seconds": built - started, "validation_seconds": finished - built,
            "elapsed_seconds": finished - started,
            "peak_rss_bytes": None if rss is None else int(rss if sys.platform == "darwin" else rss * 1024),
            "memory_source": "getrusage(RUSAGE_SELF)" if rss is not None else "not_available",
            "profile": {name: function_cost(name) for name in
                        ("route_edge", "choose_label_box", "reflow_automatic_edge_labels", "bounds_overlap", "segment_intersects_box")},
            "label_overlap_replay": label_overlap,
            "label_overlap_note": "Separately timed replay of label/node, label/edge, label/label checks; not a slice of validation_seconds.",
            "quality_gate_passed": result["quality_gate_passed"],
            "diagnostic_codes": [item["code"] for item in result["diagnostics"]]}


def probe(edges: int, timeout: float) -> dict:
    check_limits(edges, timeout)
    started = time.perf_counter()
    try:
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker", str(edges)],
                                capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "edges": edges, "spec_sha256": digest(linear_spec(edges)),
                "wall_seconds": time.perf_counter() - started, "limit_seconds": timeout,
                "build_seconds": None, "validation_seconds": None, "profile": None,
                "peak_rss_bytes": None, "quality_gate_passed": None}
    if result.returncode:
        return {"status": "error", "edges": edges, "exit_code": result.returncode,
                "stderr": result.stderr, "peak_rss_bytes": None}
    report = json.loads(result.stdout)
    report.update(wall_seconds=time.perf_counter() - started, limit_seconds=timeout)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--sizes", nargs="+", type=int, default=[60, 120, 250, 500])
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    try:
        for size in [args.worker] if args.worker is not None else args.sizes:
            check_limits(size, args.timeout)
        if not 1 <= args.repeats <= 10:
            raise ValueError("repeats must be between 1 and 10")
    except ValueError as exc:
        parser.error(str(exc))
    if args.worker is not None:
        print(json.dumps(worker(args.worker)))
        return
    print(json.dumps({"record": "environment", "python": platform.python_version(),
                      "platform": platform.platform(), "machine": platform.machine(),
                      "topology": "labeled-linear-v1", "profiler": "cProfile",
                      "repeats": args.repeats, "sizes": args.sizes,
                      "timeout_seconds": args.timeout,
                      "note": "Profile times overlap; do not add inclusive costs. RSS is worker process peak. Timeouts are missing measurements, not zeros."}), flush=True)
    for size in args.sizes:
        for repeat in range(args.repeats):
            print(json.dumps({"record": "sample", "repeat": repeat + 1, **probe(size, args.timeout)}), flush=True)


if __name__ == "__main__":
    main()
