"""Pure port validation and explicit per-run port allocation."""

from __future__ import annotations

from . import contracts, geometry as core_geometry
from . import routing_policy


PORT_OFFSETS = (0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9, 0.275, 0.725)
PORT_SIDES = {"top", "bottom", "left", "right"}

def validate_side(side: str, field: str) -> str:
    if side not in PORT_SIDES:
        raise contracts.DiagramError(f"Unsupported {field}: {side}")
    return side

def validate_offset(offset, field: str) -> float:
    value = float(offset)
    if not 0.05 <= value <= 0.95:
        raise contracts.DiagramError(f"{field} must be between 0.05 and 0.95")
    return value

class PortAllocator:
    def __init__(self) -> None:
        self.occupied: dict[tuple[str, str, float], list[str]] = {}

    @staticmethod
    def key(node_id: str, side: str, offset: float) -> tuple[str, str, float]:
        return node_id, side, round(float(offset), 4)

    def reserve(
        self,
        node_id: str,
        side: str,
        offset: float,
        edge_id: str,
        *,
        allow_reuse: bool = False,
        fail_on_conflict: bool = True,
    ) -> float:
        side = validate_side(side, "port side")
        offset = validate_offset(offset, "port offset")
        key = self.key(node_id, side, offset)
        if key in self.occupied and not allow_reuse and fail_on_conflict:
            used_by = ", ".join(self.occupied[key])
            raise contracts.DiagramError(
                f"Port {node_id}:{side}@{contracts.number(offset)} is already used by {used_by}; "
                "choose another port or set allow_port_reuse"
            )
        self.occupied.setdefault(key, []).append(edge_id)
        return offset

    def choose(
        self,
        node_id: str,
        side: str,
        edge_id: str,
        requested=None,
        *,
        allow_reuse: bool = False,
    ) -> float:
        side = validate_side(side, "port side")
        if requested is not None:
            return self.reserve(
                node_id, side, requested, edge_id, allow_reuse=allow_reuse
            )
        for offset in PORT_OFFSETS:
            if allow_reuse or self.key(node_id, side, offset) not in self.occupied:
                return self.reserve(
                    node_id, side, offset, edge_id, allow_reuse=allow_reuse
                )
        raise contracts.DiagramError(f"No free {side} port remains on node {node_id}")

    def available(
        self,
        node_id: str,
        side: str,
        offset: float,
        *,
        allow_reuse: bool = False,
        minimum_offset_gap: float = 0.0,
    ) -> bool:
        if allow_reuse:
            return True
        if self.key(node_id, side, offset) in self.occupied:
            return False
        return all(
            occupied_node != node_id
            or occupied_side != side
            or abs(occupied_offset - float(offset)) >= minimum_offset_gap
            for occupied_node, occupied_side, occupied_offset in self.occupied
        )

def candidate_port_offsets(
    bounds: dict[str, float],
    side: str,
    other_bounds: dict[str, float],
    other_side: str,
) -> list[float]:
    candidates = list(PORT_OFFSETS)
    if side in {"left", "right"} and other_side in {"left", "right"}:
        other_center_y = other_bounds["top"] + other_bounds["height"] / 2
        candidates.append((other_center_y - bounds["top"]) / bounds["height"])
    elif side in {"top", "bottom"} and other_side in {"top", "bottom"}:
        other_center_x = other_bounds["left"] + other_bounds["width"] / 2
        candidates.append((other_center_x - bounds["left"]) / bounds["width"])
    return sorted(
        {
            round(value, 6)
            for value in candidates
            if 0.05 <= value <= 0.95
        },
        key=lambda value: (abs(value - 0.5), value),
    )


def port_side_length(bounds: dict[str, float], side: str) -> float:
    """Return the physical length represented by offsets on one node side."""
    validate_side(side, "port side")
    dimension = "height" if side in {"left", "right"} else "width"
    length = float(bounds[dimension])
    if length <= 0.0:
        raise contracts.DiagramError(f"Port side length must be positive for {side}")
    return length


def finite_port_offsets(
    candidates,
    *,
    minimum: float = 0.05,
    maximum: float = 0.95,
    locked_offsets=(),
    minimum_gap: float = 0.0,
    limit: int = 16,
    mandatory_offsets=(),
) -> list[float]:
    """Return a bounded candidate set, including useful hard-lock edges only.

    This deliberately does not manufacture an unbounded uniform distribution.
    The batch planner stays a finite search over established offsets, remote
    projections, and the immediately feasible positions next to hard locks.
    """
    lower = validate_offset(minimum, "minimum port offset")
    upper = validate_offset(maximum, "maximum port offset")
    if lower > upper + core_geometry.GEOMETRY_TOLERANCE / 100:
        raise contracts.DiagramError("minimum port offset must not exceed maximum")
    if minimum_gap < 0.0:
        raise contracts.DiagramError("minimum port gap must not be negative")
    if limit <= 0:
        raise contracts.DiagramError("port candidate limit must be positive")
    values = [*candidates, lower, upper]
    for value in locked_offsets:
        locked = float(value)
        values.extend((locked - minimum_gap, locked + minimum_gap))
    tolerance = core_geometry.GEOMETRY_TOLERANCE / 100
    mandatory = {
        round(float(value), 6)
        for value in mandatory_offsets
        if lower - tolerance <= float(value) <= upper + tolerance
    }
    filtered = {
        round(float(value), 6)
        for value in values
        if lower - tolerance <= float(value) <= upper + tolerance
    }
    # Mandatory values deliberately bypass center preference.  A caller that
    # needs a strict cap can detect ``len(result) > limit`` and report a finite
    # candidate failure instead of silently dropping a hard geometric option.
    required = sorted(mandatory)
    optional = sorted(filtered - mandatory, key=lambda value: (abs(value - 0.5), value))
    return required + optional[:max(0, limit - len(required))]


def continuous_port_capacity(
    minimum: float,
    maximum: float,
    locked_offsets,
    minimum_gap: float,
) -> int:
    """Prove capacity across continuous intervals split by hard locks."""
    lower = validate_offset(minimum, "minimum port offset")
    upper = validate_offset(maximum, "maximum port offset")
    if lower > upper + core_geometry.GEOMETRY_TOLERANCE / 100:
        return 0
    gap = float(minimum_gap)
    if gap < 0.0:
        raise contracts.DiagramError("minimum port gap must not be negative")
    tolerance = core_geometry.GEOMETRY_TOLERANCE / 100
    if gap <= tolerance:
        return 1_000_000_000
    # A lock can constrain this interval even when its own offset falls just
    # outside it: its exclusion radius still reaches into the usable range.
    locks = sorted(
        float(value) for value in locked_offsets
        if float(value) + gap >= lower - tolerance
        and float(value) - gap <= upper + tolerance
    )
    capacity = 0
    cursor = lower
    for locked in locks:
        interval_end = min(upper, locked - gap)
        while cursor <= interval_end + tolerance:
            capacity += 1
            cursor += gap
        cursor = max(cursor, locked + gap)
    while cursor <= upper + tolerance:
        capacity += 1
        cursor += gap
    return capacity

def allocate_port_pair(
    allocator: PortAllocator,
    edge: dict,
    source_bounds: dict[str, float],
    target_bounds: dict[str, float],
    exit_side: str,
    entry_side: str,
    offset_limits: dict[str, dict[str, float]] | None = None,
    *,
    prefer_center_ports: bool = False,
) -> tuple[float, float]:
    """Allocate source and target ports together so simple routes stay aligned."""
    allow_reuse = bool(edge.get("allow_port_reuse", False))
    requested_exit = edge.get("exit_offset")
    requested_entry = edge.get("entry_offset")
    limits = offset_limits or {}

    def allowed(endpoint: str, offset: float) -> bool:
        endpoint_limits = limits.get(endpoint, {})
        return (
            offset >= endpoint_limits.get("min", 0.0) - core_geometry.GEOMETRY_TOLERANCE / 100
            and offset <= endpoint_limits.get("max", 1.0) + core_geometry.GEOMETRY_TOLERANCE / 100
        )

    if requested_exit is not None and requested_entry is not None:
        return (
            allocator.reserve(
                edge["from"], exit_side, requested_exit, edge["id"], allow_reuse=allow_reuse
            ),
            allocator.reserve(
                edge["to"], entry_side, requested_entry, edge["id"], allow_reuse=allow_reuse
            ),
        )

    exit_candidates = (
        [validate_offset(requested_exit, "exit_offset")]
        if requested_exit is not None
        else candidate_port_offsets(source_bounds, exit_side, target_bounds, entry_side)
    )
    entry_candidates = (
        [validate_offset(requested_entry, "entry_offset")]
        if requested_entry is not None
        else candidate_port_offsets(target_bounds, entry_side, source_bounds, exit_side)
    )

    pairs: list[tuple[float, float, float]] = []
    for exit_offset in exit_candidates:
        if not allowed("exit", exit_offset):
            continue
        exit_gap = (
            0.0
            if requested_exit is not None
            else routing_policy.NEAR_PARALLEL_CLEARANCE
            / max(
                source_bounds["height"]
                if exit_side in {"left", "right"}
                else source_bounds["width"],
                1.0,
            )
        )
        if not allocator.available(
            edge["from"],
            exit_side,
            exit_offset,
            allow_reuse=allow_reuse,
            minimum_offset_gap=exit_gap,
        ):
            continue
        source_point = core_geometry.port_point(source_bounds, exit_side, exit_offset)
        matched_entry = None
        if requested_entry is None:
            if exit_side in {"left", "right"} and entry_side in {"left", "right"}:
                matched_entry = (source_point[1] - target_bounds["top"]) / target_bounds["height"]
            elif exit_side in {"top", "bottom"} and entry_side in {"top", "bottom"}:
                matched_entry = (source_point[0] - target_bounds["left"]) / target_bounds["width"]
        dynamic_entries = list(entry_candidates)
        if matched_entry is not None and 0.05 <= matched_entry <= 0.95:
            dynamic_entries.append(round(matched_entry, 6))
        for entry_offset in sorted(set(dynamic_entries)):
            if not allowed("entry", entry_offset):
                continue
            entry_gap = (
                0.0
                if requested_entry is not None
                else routing_policy.NEAR_PARALLEL_CLEARANCE
                / max(
                    target_bounds["height"]
                    if entry_side in {"left", "right"}
                    else target_bounds["width"],
                    1.0,
                )
            )
            if not allocator.available(
                edge["to"],
                entry_side,
                entry_offset,
                allow_reuse=allow_reuse,
                minimum_offset_gap=entry_gap,
            ):
                continue
            target_point = core_geometry.port_point(target_bounds, entry_side, entry_offset)
            if exit_side in {"left", "right"} and entry_side in {"left", "right"}:
                alignment = abs(source_point[1] - target_point[1])
            elif exit_side in {"top", "bottom"} and entry_side in {"top", "bottom"}:
                alignment = abs(source_point[0] - target_point[0])
            else:
                alignment = 0.0
            center_cost = abs(exit_offset - 0.5) + abs(entry_offset - 0.5)
            if prefer_center_ports:
                # Centered side ports are the normal human-authored attachment
                # points.  Chasing endpoint alignment commonly pushes a long
                # return to 0.1/0.9 even when both center ports are free.  Keep
                # alignment as a secondary preference and move off center only
                # when the center port is occupied or explicitly overridden.
                pair_cost = center_cost * 10000.0 + alignment
            else:
                pair_cost = alignment * 100.0 + center_cost
            pairs.append((pair_cost, exit_offset, entry_offset))
    if not pairs:
        raise contracts.DiagramError(f"No compatible free port pair remains for edge {edge['id']}")
    _, exit_offset, entry_offset = min(pairs)
    allocator.reserve(
        edge["from"], exit_side, exit_offset, edge["id"], allow_reuse=allow_reuse
    )
    allocator.reserve(
        edge["to"], entry_side, entry_offset, edge["id"], allow_reuse=allow_reuse
    )
    return exit_offset, entry_offset
