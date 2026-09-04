"""Deterministic, bounded planning of paired attachment ports.

This module deliberately knows nothing about XML, route construction, or the
CLI.  Callers provide resolved sides and post-layout bounds; they retain the
authority to decide which edges may later be routed or written.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import contracts, geometry as core_geometry, ports


PLAN_COMPLETE = "complete"
PLAN_CONSTRAINT_CONFLICT = "constraint_conflict"
PLAN_CAPACITY_EXHAUSTED = "capacity_exhausted"
PLAN_CANDIDATE_EXHAUSTED = "candidate_exhausted"
PLAN_BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class PlannerBudget:
    """Finite operation limits for one connected port-conflict group."""

    max_endpoint_candidates: int = 12
    max_backtracks_per_group: int = 6
    max_attempts: int = 256

    def __post_init__(self) -> None:
        if min(self.max_endpoint_candidates, self.max_backtracks_per_group, self.max_attempts) <= 0:
            raise contracts.DiagramError("Port planner budgets must be positive")


@dataclass(frozen=True)
class EndpointRequest:
    edge_id: str
    node_id: str
    endpoint: str
    side: str
    bounds: tuple[float, float, float, float]
    remote_coordinate: float
    mutable: bool = True
    main_axis: bool = False
    allow_reuse: bool = False
    explicit_offset: float | None = None
    locked_offset: float | None = None
    minimum_offset: float = 0.05
    maximum_offset: float = 0.95
    minimum_gap_px: float = 16.0

    @property
    def hard_offset(self) -> float | None:
        return self.explicit_offset if self.explicit_offset is not None else self.locked_offset

    @property
    def offset_source(self) -> str:
        if self.explicit_offset is not None:
            return "explicit"
        if self.locked_offset is not None:
            return "locked"
        return "derived"

    @property
    def side_length(self) -> float:
        return self.bounds[3] if self.side in {"left", "right"} else self.bounds[2]

    @property
    def tangent_start(self) -> float:
        return self.bounds[1] if self.side in {"left", "right"} else self.bounds[0]

    @property
    def normalized_remote(self) -> float:
        if self.side_length <= 0.0:
            return 0.5
        return min(0.95, max(0.05, (self.remote_coordinate - self.tangent_start) / self.side_length))


@dataclass(frozen=True)
class EdgePortRequest:
    edge_id: str
    exit: EndpointRequest
    entry: EndpointRequest


@dataclass(frozen=True)
class PlannedEndpoint:
    node_id: str
    side: str
    offset: float
    source: str


@dataclass(frozen=True)
class EdgePortAssignment:
    edge_id: str
    exit: PlannedEndpoint
    entry: PlannedEndpoint

    @property
    def assignment_key(self) -> tuple:
        return _assignment_key(self.edge_id, self.exit, self.entry)


@dataclass(frozen=True)
class PortPlanIssue:
    code: str
    message: str
    edge_ids: tuple[str, ...] = ()
    node_id: str | None = None
    side: str | None = None


@dataclass(frozen=True)
class ComponentPlan:
    """A solved or attempted conflict component for a later routing batch."""

    key: tuple[tuple[str, str], ...]
    edge_ids: tuple[str, ...]
    attempts: int
    assignment_signature: tuple = ()


@dataclass(frozen=True)
class PortPlanPreparation:
    """Pure routing handoff object; it neither reserves nor writes ports."""

    edge_requests: tuple[EdgePortRequest, ...]
    budget: PlannerBudget
    linked_edge_pairs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PortPlan:
    status: str
    assignments: tuple[EdgePortAssignment, ...]
    issues: tuple[PortPlanIssue, ...]
    attempts: int
    candidate_pairs: int
    budget: PlannerBudget
    components: tuple[ComponentPlan, ...] = ()
    max_component_attempts: int = 0
    components_solved: int = 0

    def by_edge(self) -> dict[str, EdgePortAssignment]:
        return {assignment.edge_id: assignment for assignment in self.assignments}

    @property
    def total_attempts(self) -> int:
        return self.attempts

    def component_assignment_key(self, edge_id: str) -> tuple[tuple[str, str], ...]:
        for component in self.components:
            if edge_id in component.edge_ids:
                return component.key
        raise KeyError(edge_id)

    def component_assignment_signature(self, component_identifier) -> tuple:
        for component in self.components:
            if component.key == component_identifier:
                return component.assignment_signature
        raise KeyError(component_identifier)


def prepare_port_plan(
    edge_requests,
    *,
    budget: PlannerBudget | None = None,
    linked_edge_pairs=(),
) -> PortPlanPreparation:
    """Capture a deterministic routing batch before initial planning."""
    return PortPlanPreparation(
        tuple(edge_requests),
        budget or PlannerBudget(),
        tuple(tuple(pair) for pair in linked_edge_pairs),
    )


def initial_port_plan(preparation: PortPlanPreparation) -> PortPlan:
    """Create the first pure plan for a prepared routing batch."""
    return plan_port_requests(
        preparation.edge_requests,
        budget=preparation.budget,
        linked_edge_pairs=preparation.linked_edge_pairs,
    )


def replan_port_plan(
    preparation: PortPlanPreparation,
    component_identifier=None,
    *,
    rejected_assignment_keys=(),
    rejected_assignment_signatures=(),
    previous_plan: PortPlan | None = None,
    edge_requests=None,
    linked_edge_pairs=None,
) -> PortPlan:
    """Replan one feedback-rejected component without disturbing its peers."""
    prior = previous_plan or initial_port_plan(preparation)
    return _plan_port_requests(
        preparation.edge_requests if edge_requests is None else edge_requests,
        budget=preparation.budget,
        linked_edge_pairs=(preparation.linked_edge_pairs if linked_edge_pairs is None else linked_edge_pairs),
        component_identifier=component_identifier,
        rejected_assignment_keys=tuple(rejected_assignment_keys),
        rejected_assignment_signatures=tuple(rejected_assignment_signatures),
        previous_plan=prior,
    )


def collect_port_requests(
    edges,
    node_bounds: dict[str, dict[str, float]],
    endpoint_sides: dict[str, tuple[str, str]],
    *,
    mutable_edge_ids=None,
    main_axis_edge_ids=(),
    locked_offsets: dict[str, tuple[float | None, float | None]] | None = None,
    offset_limits: dict[str, dict[str, dict[str, float]]] | None = None,
    minimum_gap_px: float = 16.0,
) -> tuple[EdgePortRequest, ...]:
    """Collect input provenance without changing the input edges or geometry."""
    edge_list = tuple(edges)
    mutable = {edge["id"] for edge in edge_list} if mutable_edge_ids is None else set(mutable_edge_ids)
    main_axis = set(main_axis_edge_ids)
    frozen = locked_offsets or {}
    all_limits = offset_limits or {}

    def bounds_for(node_id: str) -> tuple[float, float, float, float]:
        value = node_bounds[node_id]
        return (float(value["left"]), float(value["top"]), float(value["width"]), float(value["height"]))

    def tangent_center(value: tuple[float, float, float, float], side: str) -> float:
        return value[1] + value[3] / 2 if side in {"left", "right"} else value[0] + value[2] / 2

    requests = []
    for edge in edge_list:
        edge_id = edge["id"]
        exit_side, entry_side = endpoint_sides[edge_id]
        ports.validate_side(exit_side, "exit_side")
        ports.validate_side(entry_side, "entry_side")
        source = bounds_for(edge["from"])
        target = bounds_for(edge["to"])
        locked_exit, locked_entry = frozen.get(edge_id, (None, None))
        common = {
            "mutable": edge_id in mutable,
            "main_axis": edge_id in main_axis,
            "allow_reuse": bool(edge.get("allow_port_reuse", False)),
            "minimum_gap_px": float(minimum_gap_px),
        }
        exit_limits = all_limits.get(edge_id, {}).get("exit", {})
        entry_limits = all_limits.get(edge_id, {}).get("entry", {})
        requests.append(EdgePortRequest(
            edge_id,
            EndpointRequest(
                edge_id, edge["from"], "exit", exit_side, source, tangent_center(target, exit_side),
                explicit_offset=float(edge["exit_offset"]) if edge.get("exit_offset") is not None else None,
                locked_offset=float(locked_exit) if locked_exit is not None else None,
                minimum_offset=float(exit_limits.get("min", 0.05)),
                maximum_offset=float(exit_limits.get("max", 0.95)),
                **common,
            ),
            EndpointRequest(
                edge_id, edge["to"], "entry", entry_side, target, tangent_center(source, entry_side),
                explicit_offset=float(edge["entry_offset"]) if edge.get("entry_offset") is not None else None,
                locked_offset=float(locked_entry) if locked_entry is not None else None,
                minimum_offset=float(entry_limits.get("min", 0.05)),
                maximum_offset=float(entry_limits.get("max", 0.95)),
                **common,
            ),
        ))
    return tuple(requests)


def plan_port_requests(
    edge_requests,
    *,
    budget: PlannerBudget | None = None,
    linked_edge_pairs=(),
) -> PortPlan:
    return _plan_port_requests(
        edge_requests,
        budget=budget,
        linked_edge_pairs=linked_edge_pairs,
    )


def _plan_port_requests(
    edge_requests,
    *,
    budget: PlannerBudget | None = None,
    linked_edge_pairs=(),
    component_identifier=None,
    rejected_assignment_keys=(),
    rejected_assignment_signatures=(),
    previous_plan: PortPlan | None = None,
) -> PortPlan:
    """Plan complete pairs atomically, with budgets local to conflict groups."""
    requests = tuple(edge_requests)
    active_budget = budget or PlannerBudget()
    edge_ids = [request.edge_id for request in requests]
    if len(edge_ids) != len(set(edge_ids)):
        return _failure(PLAN_CONSTRAINT_CONFLICT, active_budget, "routing/port-plan-conflict", "Edge IDs in one port-planning batch must be unique")
    links = tuple(tuple(pair) for pair in linked_edge_pairs)
    for pair in links:
        if len(pair) != 2 or pair[0] not in edge_ids or pair[1] not in edge_ids:
            return _failure(PLAN_CONSTRAINT_CONFLICT, active_budget, "routing/port-plan-conflict", "linked_edge_pairs must reference two batch edge IDs")
    endpoints = [endpoint for request in requests for endpoint in (request.exit, request.entry)]
    for request in requests:
        if request.exit.edge_id != request.edge_id or request.entry.edge_id != request.edge_id:
            return _failure(PLAN_CONSTRAINT_CONFLICT, active_budget, "routing/port-plan-conflict", f"Endpoint edge IDs do not match {request.edge_id}", (request.edge_id,))
    invalid = _validation_issue(endpoints)
    if invalid is not None:
        return PortPlan(PLAN_CONSTRAINT_CONFLICT, (), (invalid,), 0, 0, active_budget)

    groups: dict[tuple[str, str], list[EndpointRequest]] = {}
    for endpoint in endpoints:
        groups.setdefault((endpoint.node_id, endpoint.side), []).append(endpoint)
    for group in groups.values():
        group.sort(key=_endpoint_order)
    conflict = _hard_conflict(groups)
    if conflict is not None:
        return PortPlan(PLAN_CONSTRAINT_CONFLICT, (), (conflict,), 0, 0, active_budget)
    capacity = _capacity_issue(groups)
    if capacity is not None:
        return PortPlan(PLAN_CAPACITY_EXHAUSTED, (), (capacity,), 0, 0, active_budget)

    hard = {_endpoint_key(endpoint): endpoint.hard_offset for endpoint in endpoints if endpoint.hard_offset is not None}
    center_owners = _center_owners(groups)
    ideals = _ordered_ideals(groups, hard)
    pairs: dict[str, list[tuple[float, float]]] = {}
    for request in requests:
        exit_values = _endpoint_candidates(request.exit, groups, center_owners, active_budget)
        entry_values = _endpoint_candidates(request.entry, groups, center_owners, active_budget)
        if exit_values is None or entry_values is None:
            pairs[request.edge_id] = []
            continue
        candidates = [(exit_value, entry_value) for exit_value in exit_values for entry_value in entry_values]
        candidates.sort(key=lambda pair: _pair_preference(request, pair, ideals))
        pairs[request.edge_id] = candidates
    candidate_pairs = sum(len(values) for values in pairs.values())
    empty = tuple(sorted(edge_id for edge_id, values in pairs.items() if not values))
    if empty:
        return _failure(PLAN_CANDIDATE_EXHAUSTED, active_budget, "routing/port-plan-exhausted", "At least one edge has no finite compatible port pair", empty, candidate_pairs=candidate_pairs)

    components = _conflict_components(requests, groups, links)
    component_keys = {_component_key(component) for component in components}
    has_rejections = bool(rejected_assignment_keys or rejected_assignment_signatures)
    if component_identifier is None and has_rejections:
        return _failure(PLAN_CONSTRAINT_CONFLICT, active_budget, "routing/port-plan-conflict", "A component identifier is required for rejected assignments")
    if component_identifier is not None and component_identifier not in component_keys:
        return _failure(PLAN_CONSTRAINT_CONFLICT, active_budget, "routing/port-plan-conflict", "Unknown port-plan component identifier")
    if component_identifier is not None:
        target_component = next(component for component in components if _component_key(component) == component_identifier)
        rejection_issue = _rejection_issue(target_component, rejected_assignment_keys, rejected_assignment_signatures)
        if rejection_issue is not None:
            return PortPlan(PLAN_CONSTRAINT_CONFLICT, (), (rejection_issue,), 0, candidate_pairs, active_budget)
    prior_components = {component.key: component for component in (previous_plan.components if previous_plan else ())}
    prior_assignments = previous_plan.by_edge() if previous_plan else {}
    if previous_plan is not None and component_identifier is not None:
        if set(prior_components) != component_keys or set(prior_assignments) != set(edge_ids):
            return _failure(PLAN_CONSTRAINT_CONFLICT, active_budget, "routing/port-plan-conflict", "Previous plan does not match the prepared component topology")

    selected = dict(hard)
    total_attempts = 0
    component_receipts = []
    for component in components:
        component_key = _component_key(component)
        if previous_plan is not None and component_identifier is not None and component_key != component_identifier:
            result = _selected_from_assignments(component, prior_assignments)
            prior_component = prior_components[component_key]
            attempts, status = prior_component.attempts, PLAN_COMPLETE
        else:
            result, attempts, status = _search_component(
                component,
                pairs,
                groups,
                selected,
                active_budget,
                rejected_assignment_keys if component_key == component_identifier else (),
                rejected_assignment_signatures if component_key == component_identifier else (),
            )
        total_attempts += attempts
        signature = () if result is None else _component_signature(component, result)
        component_receipts.append(ComponentPlan(component_key, tuple(sorted(request.edge_id for request in component)), attempts, signature))
        if result is None:
            code = "routing/port-plan-exhausted"
            message = "No complete port plan: finite candidates were exhausted"
            if status == PLAN_BUDGET_EXHAUSTED:
                message = "No complete port plan: deterministic conflict-group budget was exhausted"
            return _failure(status, active_budget, code, message, tuple(sorted(request.edge_id for request in component)), attempts=total_attempts, candidate_pairs=candidate_pairs, components=tuple(component_receipts), components_solved=len(component_receipts) - 1)
        selected.update(result)

    issues = _locked_order_issues(groups, selected)
    assignments = tuple(sorted((
        prior_assignments[request.edge_id]
        if previous_plan is not None and component_identifier is not None and _component_key(next(component for component in components if request in component)) != component_identifier
        else EdgePortAssignment(request.edge_id, _planned(request.exit, selected), _planned(request.entry, selected))
        for request in requests
    ), key=lambda assignment: _request_key(next(request for request in requests if request.edge_id == assignment.edge_id))))
    return PortPlan(PLAN_COMPLETE, assignments, tuple(issues), total_attempts, candidate_pairs, active_budget, tuple(component_receipts), max((component.attempts for component in component_receipts), default=0), len(component_receipts))


def _failure(status, budget, code, message, edge_ids=(), *, attempts=0, candidate_pairs=0, components=(), components_solved=0) -> PortPlan:
    component_values = tuple(components)
    return PortPlan(status, (), (PortPlanIssue(code, message, tuple(edge_ids)),), attempts, candidate_pairs, budget, component_values, max((component.attempts for component in component_values), default=0), components_solved)


def _endpoint_key(endpoint: EndpointRequest) -> tuple[str, str, str]:
    return endpoint.edge_id, endpoint.endpoint, endpoint.node_id


def _endpoint_order(endpoint: EndpointRequest) -> tuple[float, int, str, str]:
    return (round(endpoint.remote_coordinate, 6), 0 if endpoint.main_axis else 1, endpoint.edge_id, endpoint.endpoint)


def _request_key(request: EdgePortRequest) -> tuple:
    """Use IDs only after semantic geometry has exhausted the tie-breakers."""
    return (
        request.exit.bounds, request.exit.side, round(request.exit.remote_coordinate, 6),
        request.entry.bounds, request.entry.side, round(request.entry.remote_coordinate, 6),
        0 if request.exit.main_axis or request.entry.main_axis else 1,
        request.edge_id,
    )


def _component_key(component) -> tuple[tuple[str, str], ...]:
    return tuple(sorted({
        (endpoint.node_id, endpoint.side)
        for request in component for endpoint in (request.exit, request.entry)
    }))


def _assignment_key(edge_id: str, exit_endpoint: PlannedEndpoint, entry_endpoint: PlannedEndpoint) -> tuple:
    return (
        edge_id,
        exit_endpoint.node_id, exit_endpoint.side, round(exit_endpoint.offset, 6),
        entry_endpoint.node_id, entry_endpoint.side, round(entry_endpoint.offset, 6),
    )


def _component_signature(component, selected) -> tuple:
    return tuple(sorted(
        _assignment_key(
            request.edge_id,
            _planned(request.exit, selected),
            _planned(request.entry, selected),
        )
        for request in component
    ))


def _request_assignment_key(request, selected) -> tuple:
    return _assignment_key(
        request.edge_id,
        _planned(request.exit, selected),
        _planned(request.entry, selected),
    )


def _selected_from_assignments(component, assignments) -> dict[tuple[str, str, str], float]:
    selected = {}
    for request in component:
        assignment = assignments[request.edge_id]
        if (
            assignment.exit.node_id != request.exit.node_id
            or assignment.exit.side != request.exit.side
            or assignment.entry.node_id != request.entry.node_id
            or assignment.entry.side != request.entry.side
        ):
            raise contracts.DiagramError("Previous port-plan assignment does not match component endpoints")
        selected[_endpoint_key(request.exit)] = assignment.exit.offset
        selected[_endpoint_key(request.entry)] = assignment.entry.offset
    return selected


def _rejection_issue(component, assignment_keys, assignment_signatures) -> PortPlanIssue | None:
    requests_by_id = {request.edge_id: request for request in component}
    edge_ids = set(requests_by_id)

    def invalid(message):
        return PortPlanIssue("routing/port-plan-conflict", message)

    def valid_key(key):
        if not isinstance(key, tuple) or len(key) != 7:
            return False
        edge_id, exit_node, exit_side, exit_offset, entry_node, entry_side, entry_offset = key
        if not isinstance(edge_id, str):
            return False
        request = requests_by_id.get(edge_id)
        if request is None:
            return False
        if (
            exit_node != request.exit.node_id
            or exit_side != request.exit.side
            or entry_node != request.entry.node_id
            or entry_side != request.entry.side
        ):
            return False
        for offset in (exit_offset, entry_offset):
            if isinstance(offset, bool) or not isinstance(offset, (int, float)):
                return False
            if not -float("inf") < float(offset) < float("inf"):
                return False
            if not 0.05 <= float(offset) <= 0.95 or round(float(offset), 6) != float(offset):
                return False
        return True

    for key in assignment_keys:
        if not valid_key(key):
            return invalid("Rejected assignment key does not exactly match the selected component")
    for signature in assignment_signatures:
        if not isinstance(signature, tuple) or len(signature) != len(component):
            return invalid("Rejected assignment signature does not exactly match the selected component")
        if not all(valid_key(key) for key in signature):
            return invalid("Rejected assignment signature does not exactly match the selected component")
        if {key[0] for key in signature} != edge_ids:
            return invalid("Rejected assignment signature does not exactly match the selected component")
    return None


def _reuse_allowed(first: EndpointRequest, second: EndpointRequest) -> bool:
    """A user-authored reuse permission is symmetric, never traversal-based."""
    return first.allow_reuse or second.allow_reuse


def _pair_offsets_compatible(first, first_value, second, second_value, tolerance) -> bool:
    same_port = abs(first_value - second_value) <= tolerance
    if same_port:
        return _reuse_allowed(first, second)
    distance = abs(first_value - second_value) * first.side_length
    return distance + core_geometry.GEOMETRY_TOLERANCE >= max(first.minimum_gap_px, second.minimum_gap_px)


def _validation_issue(endpoints) -> PortPlanIssue | None:
    for endpoint in endpoints:
        if endpoint.endpoint not in {"exit", "entry"} or endpoint.side not in ports.PORT_SIDES:
            return PortPlanIssue("routing/port-plan-conflict", f"Unsupported endpoint for edge {endpoint.edge_id}", (endpoint.edge_id,), endpoint.node_id, endpoint.side)
        if (
            endpoint.side_length <= 0.0
            or endpoint.minimum_gap_px < 0.0
            or not 0.05 <= endpoint.minimum_offset <= endpoint.maximum_offset <= 0.95
        ):
            return PortPlanIssue("routing/port-plan-conflict", f"Invalid port bounds for {endpoint.node_id}:{endpoint.side}", (endpoint.edge_id,), endpoint.node_id, endpoint.side)
        if endpoint.explicit_offset is not None and endpoint.locked_offset is not None and abs(endpoint.explicit_offset - endpoint.locked_offset) > core_geometry.GEOMETRY_TOLERANCE / 100:
            return PortPlanIssue("routing/port-plan-conflict", f"Explicit and locked offsets disagree for edge {endpoint.edge_id}", (endpoint.edge_id,), endpoint.node_id, endpoint.side)
        if endpoint.hard_offset is not None and not endpoint.minimum_offset <= endpoint.hard_offset <= endpoint.maximum_offset:
            return PortPlanIssue("routing/port-plan-conflict", f"Locked offset is outside the allowed interval for edge {endpoint.edge_id}", (endpoint.edge_id,), endpoint.node_id, endpoint.side)
        if not endpoint.mutable and endpoint.hard_offset is None:
            return PortPlanIssue("routing/port-plan-conflict", f"Frozen endpoint for edge {endpoint.edge_id} has no locked offset", (endpoint.edge_id,), endpoint.node_id, endpoint.side)
    return None


def _hard_conflict(groups) -> PortPlanIssue | None:
    tolerance = core_geometry.GEOMETRY_TOLERANCE / 100
    for (node_id, side), group in sorted(groups.items()):
        hard = [endpoint for endpoint in group if endpoint.hard_offset is not None]
        for index, first in enumerate(hard):
            for second in hard[index + 1:]:
                same_port = abs(first.hard_offset - second.hard_offset) <= tolerance
                explicitly_reused = same_port and _reuse_allowed(first, second)
                required = max(first.minimum_gap_px, second.minimum_gap_px)
                distance = abs(first.hard_offset - second.hard_offset) * first.side_length
                if (same_port and not explicitly_reused) or (
                    distance + core_geometry.GEOMETRY_TOLERANCE < required and not explicitly_reused
                ):
                    return PortPlanIssue("routing/port-plan-conflict", f"Hard ports on {node_id}:{side} are closer than {contracts.number(required)}px", tuple(sorted((first.edge_id, second.edge_id))), node_id, side)
    return None


def _capacity_issue(groups) -> PortPlanIssue | None:
    for (node_id, side), group in sorted(groups.items()):
        variable = [endpoint for endpoint in group if endpoint.hard_offset is None and not endpoint.allow_reuse]
        if not variable:
            continue
        # Prove only what the continuous model can prove.  Each endpoint keeps
        # its own interval; incompatible ranges therefore fall through to the
        # finite planner instead of being falsely rejected by a global overlap.
        for endpoint in variable:
            locks = [
                other.hard_offset for other in group
                if other.hard_offset is not None and not _reuse_allowed(endpoint, other)
            ]
            capacity = ports.continuous_port_capacity(
                endpoint.minimum_offset,
                endpoint.maximum_offset,
                locks,
                endpoint.minimum_gap_px / endpoint.side_length,
            )
            if capacity <= 0:
                return PortPlanIssue("routing/port-capacity", f"{node_id}:{side} has no continuous interval for {endpoint.edge_id}", (endpoint.edge_id,), node_id, side)
        common_interval = len({(endpoint.minimum_offset, endpoint.maximum_offset, endpoint.minimum_gap_px) for endpoint in variable}) == 1
        if not common_interval:
            continue
        first = variable[0]
        locks = [other.hard_offset for other in group if other.hard_offset is not None and not _reuse_allowed(first, other)]
        capacity = ports.continuous_port_capacity(first.minimum_offset, first.maximum_offset, locks, first.minimum_gap_px / first.side_length)
        if capacity < len(variable):
            return PortPlanIssue("routing/port-capacity", f"{node_id}:{side} needs {len(variable)} additional ports but its locked intervals fit {capacity}", tuple(sorted({endpoint.edge_id for endpoint in group})), node_id, side)
    return None


def _center_owners(groups) -> dict[tuple[str, str], tuple[str, str, str]]:
    owners = {}
    tolerance = core_geometry.GEOMETRY_TOLERANCE / 100
    for key, group in groups.items():
        if any(endpoint.hard_offset is not None and abs(endpoint.hard_offset - 0.5) <= tolerance for endpoint in group):
            continue
        candidates = [endpoint for endpoint in group if endpoint.main_axis and endpoint.hard_offset is None]
        if not candidates and len(group) > 1:
            candidates = [endpoint for endpoint in group if endpoint.hard_offset is None]
        if candidates:
            owners[key] = _endpoint_key(min(candidates, key=lambda endpoint: (abs(endpoint.normalized_remote - 0.5), _endpoint_order(endpoint))))
    return owners


def _ordered_ideals(groups, hard) -> dict[tuple[str, str, str], float]:
    ideals = dict(hard)
    for group in groups.values():
        variable = [endpoint for endpoint in group if endpoint.hard_offset is None]
        values = sorted(ports.PORT_OFFSETS, key=lambda value: (abs(value - 0.5), value))
        if len(variable) <= len(values):
            for endpoint, value in zip(variable, sorted(values[:len(variable)])):
                ideals[_endpoint_key(endpoint)] = value
        else:
            for endpoint in variable:
                ideals[_endpoint_key(endpoint)] = endpoint.normalized_remote
    return ideals


def _endpoint_candidates(endpoint, groups, center_owners, budget) -> list[float] | None:
    if endpoint.hard_offset is not None:
        return [round(endpoint.hard_offset, 6)]
    group = groups[(endpoint.node_id, endpoint.side)]
    locks = [item for item in group if item.hard_offset is not None]
    gap = endpoint.minimum_gap_px / endpoint.side_length
    mandatory = [endpoint.normalized_remote, endpoint.minimum_offset, endpoint.maximum_offset]
    for item in locks:
        lock_gap = max(endpoint.minimum_gap_px, item.minimum_gap_px) / endpoint.side_length
        if _reuse_allowed(endpoint, item):
            mandatory.append(item.hard_offset)
        else:
            mandatory.extend((item.hard_offset - lock_gap, item.hard_offset + lock_gap))
    values = ports.finite_port_offsets(
        [*ports.PORT_OFFSETS, endpoint.normalized_remote],
        minimum=endpoint.minimum_offset,
        maximum=endpoint.maximum_offset,
        locked_offsets=[item.hard_offset for item in locks],
        minimum_gap=gap,
        limit=budget.max_endpoint_candidates,
        mandatory_offsets=mandatory,
    )
    if len(values) > budget.max_endpoint_candidates:
        return None
    tolerance = core_geometry.GEOMETRY_TOLERANCE / 100
    values = [value for value in values if all(
        _pair_offsets_compatible(endpoint, value, item, item.hard_offset, tolerance)
        for item in locks
    )]
    return values


def _pair_preference(request, pair, ideals):
    exit_offset, entry_offset = pair
    exit_ideal = ideals[_endpoint_key(request.exit)]
    entry_ideal = ideals[_endpoint_key(request.entry)]
    main_cost = (abs(exit_offset - 0.5) if request.exit.main_axis else 0.0) + (abs(entry_offset - 0.5) if request.entry.main_axis else 0.0)
    order_cost = abs(exit_offset - exit_ideal) + abs(entry_offset - entry_ideal)
    aligned = ((request.exit.side in {"left", "right"} and request.entry.side in {"left", "right"}) or (request.exit.side in {"top", "bottom"} and request.entry.side in {"top", "bottom"}))
    alignment = 0.0
    if aligned:
        alignment = abs((request.exit.tangent_start + exit_offset * request.exit.side_length) - (request.entry.tangent_start + entry_offset * request.entry.side_length))
    return (main_cost, order_cost, alignment, abs(exit_offset - request.exit.normalized_remote) + abs(entry_offset - request.entry.normalized_remote), abs(exit_offset - .5) + abs(entry_offset - .5), exit_offset, entry_offset)


def _conflict_components(requests, groups, linked_edge_pairs=()):
    parent = {request.edge_id: request.edge_id for request in requests}

    def find(edge_id):
        while parent[edge_id] != edge_id:
            parent[edge_id] = parent[parent[edge_id]]
            edge_id = parent[edge_id]
        return edge_id

    def join(first, second):
        first, second = find(first), find(second)
        if first != second:
            parent[second] = first

    for group in groups.values():
        for endpoint in group[1:]:
            join(group[0].edge_id, endpoint.edge_id)
    for first, second in linked_edge_pairs:
        join(first, second)
    components = {}
    for request in requests:
        components.setdefault(find(request.edge_id), []).append(request)
    def component_order(component):
        return min(_request_key(request) for request in component)
    return tuple(
        tuple(sorted(component, key=_request_key))
        for component in sorted(components.values(), key=component_order)
    )


def _search_component(
    component,
    pairs,
    groups,
    hard,
    budget,
    rejected_assignment_keys=(),
    rejected_assignment_signatures=(),
):
    # Fully hard pairs were validated before search and are already present in
    # ``hard``.  Replaying them would make a later non-reusing endpoint appear
    # to reject an already-authorized explicit overlap merely due to traversal
    # order.
    ordered = sorted(
        [request for request in component if request.exit.hard_offset is None or request.entry.hard_offset is None],
        key=lambda request: (len(pairs[request.edge_id]), 0 if request.exit.main_axis or request.entry.main_axis else 1, _endpoint_order(request.exit), _endpoint_order(request.entry), request.edge_id),
    )
    attempts = 0
    backtracks: dict[tuple[str, str], int] = {}
    budget_hit = False
    rejected_keys = set(rejected_assignment_keys)
    rejected_signatures = set(rejected_assignment_signatures)

    def search(index, selected):
        nonlocal attempts, budget_hit
        if index >= len(ordered):
            signature = _component_signature(component, selected)
            if signature in rejected_signatures:
                return None
            if rejected_keys and any(key in rejected_keys for key in signature):
                return None
            return dict(selected)
        request = ordered[index]
        for exit_offset, entry_offset in pairs[request.edge_id]:
            if attempts >= budget.max_attempts:
                budget_hit = True
                return None
            attempts += 1
            trial = dict(selected)
            trial[_endpoint_key(request.exit)] = exit_offset
            trial[_endpoint_key(request.entry)] = entry_offset
            # Route feasibility feedback may reject one edge's paired ports.
            # Prune immediately once that pair is complete; waiting for the
            # component leaf wastes bounded attempts on unrelated linked edges.
            if _request_assignment_key(request, trial) in rejected_keys:
                continue
            if not _partial_valid((request.exit, request.entry), groups, trial):
                continue
            result = search(index + 1, trial)
            if result is not None or budget_hit:
                return result
            for key in {(request.exit.node_id, request.exit.side), (request.entry.node_id, request.entry.side)}:
                if len(groups[key]) > 1:
                    backtracks[key] = backtracks.get(key, 0) + 1
                    if backtracks[key] > budget.max_backtracks_per_group:
                        budget_hit = True
                        return None
        return None

    result = search(0, dict(hard))
    return result, attempts, PLAN_BUDGET_EXHAUSTED if budget_hit else PLAN_CANDIDATE_EXHAUSTED


def _partial_valid(endpoints, groups, selected) -> bool:
    tolerance = core_geometry.GEOMETRY_TOLERANCE / 100
    for endpoint in endpoints:
        value = selected[_endpoint_key(endpoint)]
        for other in groups[(endpoint.node_id, endpoint.side)]:
            other_key = _endpoint_key(other)
            if other_key == _endpoint_key(endpoint) or other_key not in selected:
                continue
            other_value = selected[other_key]
            same_port = abs(value - other_value) <= tolerance
            if not _pair_offsets_compatible(endpoint, value, other, other_value, tolerance):
                return False
            if same_port:
                continue
            if abs(endpoint.remote_coordinate - other.remote_coordinate) <= core_geometry.GEOMETRY_TOLERANCE:
                continue
            # Two immutable endpoints may already be reversed in an edited
            # diagram.  Preserve and report that exception; a derived endpoint
            # is still required to respect every hard neighbour where possible.
            if endpoint.hard_offset is not None and other.hard_offset is not None:
                continue
            if endpoint.remote_coordinate < other.remote_coordinate and value > other_value + tolerance:
                return False
            if endpoint.remote_coordinate > other.remote_coordinate and value < other_value - tolerance:
                return False
    return True


def _locked_order_issues(groups, selected) -> list[PortPlanIssue]:
    issues = []
    tolerance = core_geometry.GEOMETRY_TOLERANCE / 100
    for (node_id, side), group in sorted(groups.items()):
        exceptions = set()
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                if first.remote_coordinate >= second.remote_coordinate - core_geometry.GEOMETRY_TOLERANCE:
                    continue
                if selected[_endpoint_key(first)] > selected[_endpoint_key(second)] + tolerance and (first.hard_offset is not None or second.hard_offset is not None):
                    exceptions.update((first.edge_id, second.edge_id))
        if exceptions:
            issues.append(PortPlanIssue("routing/port-order-locked", f"Locked ports preserve a remote-order exception on {node_id}:{side}", tuple(sorted(exceptions)), node_id, side))
    return issues


def _planned(endpoint, selected) -> PlannedEndpoint:
    return PlannedEndpoint(endpoint.node_id, endpoint.side, selected[_endpoint_key(endpoint)], endpoint.offset_source)
