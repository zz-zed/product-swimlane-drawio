"""Pure contracts for bounded batch port planning."""

import copy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/product-swimlane-drawio/scripts"
ORIGINAL_MODULES = {name: module for name, module in sys.modules.items() if name == "swimlane_core" or name.startswith("swimlane_core.")}
for name in ORIGINAL_MODULES:
    sys.modules.pop(name, None)
sys.path.insert(0, str(SCRIPTS))
try:
    from swimlane_core import port_planner, ports
finally:
    for name in list(sys.modules):
        if name == "swimlane_core" or name.startswith("swimlane_core."):
            sys.modules.pop(name, None)
    sys.modules.update(ORIGINAL_MODULES)
    sys.path.remove(str(SCRIPTS))


def bounds(left, top, width=100, height=100):
    return {"left": float(left), "top": float(top), "width": float(width), "height": float(height), "right": float(left + width), "bottom": float(top + height)}


class PortPlannerTests(unittest.TestCase):
    def setUp(self):
        self.nodes = {"hub": bounds(0, 100), "top": bounds(250, 0), "mid": bounds(250, 100), "bottom": bounds(250, 200)}

    def collect(self, edges, *, mutable=None, main=(), locked=None, sides=None, gap=16):
        if sides is None:
            sides = {edge["id"]: ("right", "left") for edge in edges}
        return port_planner.collect_port_requests(edges, self.nodes, sides, mutable_edge_ids=mutable, main_axis_edge_ids=main, locked_offsets=locked, minimum_gap_px=gap)

    def test_collects_both_directions_and_preserves_explicit_locked_derived_sources(self):
        edges = [{"id": "out", "from": "hub", "to": "bottom", "exit_offset": .65}, {"id": "in", "from": "top", "to": "hub"}]
        original = copy.deepcopy((edges, self.nodes))
        requests = self.collect(edges, mutable={"out"}, locked={"in": (.5, .35)}, sides={"out": ("right", "left"), "in": ("right", "right")})
        shared = [endpoint for request in requests for endpoint in (request.exit, request.entry) if (endpoint.node_id, endpoint.side) == ("hub", "right")]
        self.assertEqual([(endpoint.edge_id, endpoint.endpoint, endpoint.offset_source) for endpoint in shared], [("out", "exit", "explicit"), ("in", "entry", "locked")])
        self.assertEqual((edges, self.nodes), original)

    def test_fan_is_monotonic_centered_and_stable_through_permutation_and_rename(self):
        edges = [{"id": "a", "from": "hub", "to": "top"}, {"id": "b", "from": "hub", "to": "mid"}, {"id": "c", "from": "hub", "to": "bottom"}]
        expected = None
        for ordered in (edges, list(reversed(edges)), [edges[1], edges[2], edges[0]]):
            plan = port_planner.plan_port_requests(self.collect(ordered, main={"b"}))
            self.assertEqual(plan.status, port_planner.PLAN_COMPLETE, plan.issues)
            values = {edge_id: assignment.exit.offset for edge_id, assignment in plan.by_edge().items()}
            self.assertLess(values["a"], values["b"])
            self.assertLess(values["b"], values["c"])
            self.assertEqual(values["b"], .5)
            expected = values if expected is None else expected
            self.assertEqual(values, expected)
            self.assertEqual([assignment.edge_id for assignment in plan.assignments], ["a", "b", "c"])
        renamed = [{"id": "z3", "from": "hub", "to": "top"}, {"id": "z1", "from": "hub", "to": "mid"}, {"id": "z2", "from": "hub", "to": "bottom"}]
        planned = port_planner.plan_port_requests(self.collect(renamed, main={"z1"})).by_edge()
        self.assertEqual([planned[key].exit.offset for key in ("z3", "z1", "z2")], [expected[key] for key in ("a", "b", "c")])

    def test_locked_order_exception_and_explicit_overlap_contract(self):
        edges = [{"id": "high", "from": "hub", "to": "top"}, {"id": "low", "from": "hub", "to": "bottom"}]
        locked = port_planner.plan_port_requests(self.collect(edges, mutable=set(), locked={"high": (.8, .5), "low": (.2, .5)}, gap=0))
        self.assertEqual(locked.status, port_planner.PLAN_COMPLETE)
        self.assertEqual([issue.code for issue in locked.issues], ["routing/port-order-locked"])
        conflict = port_planner.plan_port_requests(self.collect(edges, mutable=set(), locked={"high": (.5, .5), "low": (.5, .5)}))
        self.assertEqual(conflict.status, port_planner.PLAN_CONSTRAINT_CONFLICT)
        reused = port_planner.plan_port_requests(self.collect([{**edges[0], "allow_port_reuse": True}, edges[1]], mutable=set(), locked={"high": (.5, .5), "low": (.5, .5)}))
        self.assertEqual(reused.status, port_planner.PLAN_COMPLETE)
        mismatch = port_planner.plan_port_requests(self.collect(
            [{"id": "explicit", "from": "hub", "to": "top", "exit_offset": .35}],
            locked={"explicit": (.5, .5)},
        ))
        self.assertEqual(mismatch.status, port_planner.PLAN_CONSTRAINT_CONFLICT)

    def test_reuse_is_order_independent_but_zero_gap_never_implies_reuse(self):
        edges = [{"id": "reuse", "from": "hub", "to": "top", "allow_port_reuse": True}, {"id": "plain", "from": "hub", "to": "bottom"}]
        locks = {"reuse": (.5, .5), "plain": (.5, .5)}
        observed = []
        for ordered in (edges, list(reversed(edges))):
            plan = port_planner.plan_port_requests(self.collect(ordered, mutable=set(), locked=locks, gap=0))
            self.assertEqual(plan.status, port_planner.PLAN_COMPLETE, plan.issues)
            observed.append({assignment.edge_id: assignment.exit.offset for assignment in plan.assignments})
        self.assertEqual(observed[0], observed[1])
        renamed = [{**edges[0], "id": "z"}, {**edges[1], "id": "a"}]
        renamed_plan = port_planner.plan_port_requests(self.collect(renamed, mutable=set(), locked={"z": (.5, .5), "a": (.5, .5)}, gap=0))
        self.assertEqual(renamed_plan.status, port_planner.PLAN_COMPLETE)
        no_reuse = port_planner.plan_port_requests(self.collect(
            [{"id": "one", "from": "hub", "to": "top"}, {"id": "two", "from": "hub", "to": "bottom"}],
            mutable=set(), locked={"one": (.5, .5), "two": (.5, .5)}, gap=0,
        ))
        self.assertEqual(no_reuse.status, port_planner.PLAN_CONSTRAINT_CONFLICT)

    def test_mixed_fanin_fanout_and_shared_pair_are_monotonic_and_aligned(self):
        nodes = {
            "hub": bounds(0, 100), "in-high": bounds(-200, 0), "in-low": bounds(-200, 50),
            "out-high": bounds(250, 0), "out-mid": bounds(250, 100), "out-low": bounds(250, 200),
        }
        edges = [
            {"id": "in-high", "from": "in-high", "to": "hub"}, {"id": "in-low", "from": "in-low", "to": "hub"},
            {"id": "out-high", "from": "hub", "to": "out-high"}, {"id": "out-mid", "from": "hub", "to": "out-mid"}, {"id": "out-low", "from": "hub", "to": "out-low"},
        ]
        sides = {"in-high": ("right", "right"), "in-low": ("right", "right"), "out-high": ("right", "left"), "out-mid": ("right", "left"), "out-low": ("right", "left")}
        plan = port_planner.plan_port_requests(port_planner.collect_port_requests(edges, nodes, sides, minimum_gap_px=10))
        self.assertEqual(plan.status, port_planner.PLAN_COMPLETE, plan.issues)
        shared = [plan.by_edge()[edge_id].entry.offset if edge_id.startswith("in") else plan.by_edge()[edge_id].exit.offset for edge_id in ("in-high", "out-high", "in-low", "out-mid", "out-low")]
        self.assertEqual(shared, sorted(shared))
        self.assertEqual(len(set(shared)), 5)

        shared_bounds = (0.0, 0.0, 100.0, 100.0)
        requests = (
            port_planner.EdgePortRequest("a", port_planner.EndpointRequest("a", "source", "exit", "right", shared_bounds, 10), port_planner.EndpointRequest("a", "target", "entry", "left", shared_bounds, 10)),
            port_planner.EdgePortRequest("b", port_planner.EndpointRequest("b", "source", "exit", "right", shared_bounds, 90), port_planner.EndpointRequest("b", "target", "entry", "left", shared_bounds, 90)),
        )
        paired = port_planner.plan_port_requests(requests)
        self.assertEqual(paired.status, port_planner.PLAN_COMPLETE, paired.issues)
        self.assertLess(paired.by_edge()["a"].exit.offset, paired.by_edge()["b"].exit.offset)
        self.assertLess(paired.by_edge()["a"].entry.offset, paired.by_edge()["b"].entry.offset)

    def test_independent_intervals_and_outside_lock_radius_do_not_misreport_capacity(self):
        edges = [{"id": "upper", "from": "hub", "to": "top"}, {"id": "lower", "from": "hub", "to": "bottom"}]
        requests = port_planner.collect_port_requests(
            edges, self.nodes, {edge["id"]: ("right", "left") for edge in edges},
            offset_limits={"upper": {"exit": {"min": .05, "max": .25}}, "lower": {"exit": {"min": .75, "max": .95}}},
        )
        plan = port_planner.plan_port_requests(requests)
        self.assertEqual(plan.status, port_planner.PLAN_COMPLETE, plan.issues)
        self.assertLessEqual(plan.by_edge()["upper"].exit.offset, .25)
        self.assertGreaterEqual(plan.by_edge()["lower"].exit.offset, .75)
        self.assertEqual(ports.continuous_port_capacity(.05, .95, [0.0], .1), 9)

    def test_prepared_linked_components_expose_local_budget_receipts(self):
        edges = [{"id": "forward", "from": "hub", "to": "top"}, {"id": "return", "from": "mid", "to": "bottom"}]
        preparation = port_planner.prepare_port_plan(self.collect(edges), linked_edge_pairs=[("forward", "return")])
        initial = port_planner.initial_port_plan(preparation)
        replay = port_planner.replan_port_plan(preparation)
        self.assertEqual(initial.status, port_planner.PLAN_COMPLETE, initial.issues)
        self.assertEqual(initial.assignments, replay.assignments)
        self.assertEqual(initial.components_solved, 1)
        self.assertEqual(initial.component_assignment_key("forward"), initial.component_assignment_key("return"))
        self.assertLessEqual(initial.max_component_attempts, initial.budget.max_attempts)

    def test_replan_skips_rejected_signature_and_accumulates_to_exhaustion(self):
        request = port_planner.EdgePortRequest(
            "edge",
            port_planner.EndpointRequest("edge", "source", "exit", "right", (0, 0, 100, 100), 20),
            port_planner.EndpointRequest("edge", "target", "entry", "left", (200, 0, 100, 100), 20),
        )
        preparation = port_planner.prepare_port_plan((request,), budget=port_planner.PlannerBudget(3, 6, 256))
        first = port_planner.initial_port_plan(preparation)
        component = first.component_assignment_key("edge")
        rejected = [first.component_assignment_signature(component)]
        second = port_planner.replan_port_plan(preparation, component, rejected_assignment_signatures=rejected, previous_plan=first)
        self.assertEqual(second.status, port_planner.PLAN_COMPLETE, second.issues)
        self.assertNotEqual(second.component_assignment_signature(component), rejected[0])
        rejected.append(second.component_assignment_signature(component))
        prior = second
        while True:
            next_plan = port_planner.replan_port_plan(preparation, component, rejected_assignment_signatures=rejected, previous_plan=prior)
            if next_plan.status != port_planner.PLAN_COMPLETE:
                self.assertEqual(next_plan.status, port_planner.PLAN_CANDIDATE_EXHAUSTED)
                break
            signature = next_plan.component_assignment_signature(component)
            self.assertNotIn(signature, rejected)
            rejected.append(signature)
            prior = next_plan

    def test_replan_keeps_other_component_objects_and_replans_linked_reciprocal_pair(self):
        edges = [
            {"id": "forward", "from": "hub", "to": "top"},
            {"id": "return", "from": "mid", "to": "bottom"},
            {"id": "unrelated", "from": "top", "to": "mid"},
        ]
        preparation = port_planner.prepare_port_plan(self.collect(edges), linked_edge_pairs=[("forward", "return")])
        first = port_planner.initial_port_plan(preparation)
        component = first.component_assignment_key("forward")
        rejected = first.component_assignment_signature(component)
        second = port_planner.replan_port_plan(preparation, component, rejected_assignment_signatures=[rejected], previous_plan=first)
        repeated = port_planner.replan_port_plan(preparation, component, rejected_assignment_signatures=[rejected], previous_plan=first)
        self.assertEqual(second.status, port_planner.PLAN_COMPLETE, second.issues)
        self.assertNotEqual(second.component_assignment_signature(component), rejected)
        self.assertEqual(repeated.assignments, second.assignments)
        self.assertIs(second.by_edge()["unrelated"], first.by_edge()["unrelated"])
        bad_component = port_planner.replan_port_plan(preparation, (("missing", "right"),), previous_plan=first)
        self.assertEqual(bad_component.status, port_planner.PLAN_CONSTRAINT_CONFLICT)
        bad_signature = port_planner.replan_port_plan(preparation, component, rejected_assignment_signatures=[(("missing",),)], previous_plan=first)
        self.assertEqual(bad_signature.status, port_planner.PLAN_CONSTRAINT_CONFLICT)

    def test_linked_three_edge_replan_prunes_rejected_edge_before_component_leaf(self):
        edges = [
            {"id": "first", "from": "hub", "to": "top"},
            {"id": "second", "from": "mid", "to": "bottom"},
            {"id": "third", "from": "top", "to": "mid"},
        ]
        preparation = port_planner.prepare_port_plan(
            self.collect(edges),
            budget=port_planner.PlannerBudget(3, 6, 4),
            linked_edge_pairs=[("first", "second"), ("second", "third")],
        )
        first = port_planner.initial_port_plan(preparation)
        self.assertEqual(first.status, port_planner.PLAN_COMPLETE, first.issues)
        component = first.component_assignment_key("first")
        replan = port_planner.replan_port_plan(
            preparation,
            component,
            rejected_assignment_keys=[first.by_edge()["first"].assignment_key],
            previous_plan=first,
        )
        self.assertEqual(replan.status, port_planner.PLAN_COMPLETE, replan.issues)
        self.assertNotEqual(replan.by_edge()["first"].assignment_key, first.by_edge()["first"].assignment_key)
        self.assertEqual(replan.max_component_attempts, 4)

    def test_replan_feedback_shapes_are_strict_component_contracts(self):
        edges = [{"id": "forward", "from": "hub", "to": "top"}, {"id": "return", "from": "mid", "to": "bottom"}]
        preparation = port_planner.prepare_port_plan(self.collect(edges), linked_edge_pairs=[("forward", "return")])
        first = port_planner.initial_port_plan(preparation)
        component = first.component_assignment_key("forward")
        forward_key = first.by_edge()["forward"].assignment_key
        return_key = first.by_edge()["return"].assignment_key
        malformed_keys = [
            ("forward",),
            (forward_key[0], "wrong-node", *forward_key[2:]),
            (*forward_key[:2], "bottom", *forward_key[3:]),
            (*forward_key[:3], float("nan"), *forward_key[4:]),
            (*forward_key[:3], forward_key[3] + .0000001, *forward_key[4:]),
        ]
        for key in malformed_keys:
            with self.subTest(key=key):
                rejected = port_planner.replan_port_plan(preparation, component, rejected_assignment_keys=[key], previous_plan=first)
                self.assertEqual(rejected.status, port_planner.PLAN_CONSTRAINT_CONFLICT)
                self.assertEqual(rejected.issues[0].code, "routing/port-plan-conflict")
        malformed_signatures = [
            (forward_key,),
            (forward_key, return_key, forward_key),
            (forward_key, forward_key),
            (("forward",), return_key),
        ]
        for signature in malformed_signatures:
            with self.subTest(signature=signature):
                rejected = port_planner.replan_port_plan(preparation, component, rejected_assignment_signatures=[signature], previous_plan=first)
                self.assertEqual(rejected.status, port_planner.PLAN_CONSTRAINT_CONFLICT)
                self.assertEqual(rejected.issues[0].code, "routing/port-plan-conflict")

    def test_capacity_candidate_and_conflict_group_budget_are_distinct_and_atomic(self):
        nodes = {"hub": bounds(0, 0, height=40)}
        edges = []
        sides = {}
        for index in range(4):
            target = f"target-{index}"
            nodes[target] = bounds(200, index * 80, height=40)
            edges.append({"id": f"edge-{index}", "from": "hub", "to": target})
            sides[f"edge-{index}"] = ("right", "left")
        capacity = port_planner.plan_port_requests(port_planner.collect_port_requests(edges, nodes, sides))
        self.assertEqual(capacity.status, port_planner.PLAN_CAPACITY_EXHAUSTED)
        candidates = port_planner.plan_port_requests(self.collect([{"id": "a", "from": "hub", "to": "top"}, {"id": "b", "from": "hub", "to": "bottom"}]), budget=port_planner.PlannerBudget(1, 6, 256))
        self.assertEqual(candidates.status, port_planner.PLAN_CANDIDATE_EXHAUSTED)
        exhausted = port_planner.plan_port_requests(self.collect([{"id": "a", "from": "hub", "to": "top"}, {"id": "b", "from": "hub", "to": "bottom"}]), budget=port_planner.PlannerBudget(12, 6, 1))
        self.assertEqual(exhausted.status, port_planner.PLAN_BUDGET_EXHAUSTED)
        self.assertEqual(exhausted.assignments, ())

    def test_500_independent_pairs_do_not_spend_one_global_attempt_budget(self):
        nodes = {}
        edges = []
        sides = {}
        for index in range(500):
            source, target = f"s{index}", f"t{index}"
            nodes[source] = bounds(index * 300, 0)
            nodes[target] = bounds(index * 300 + 150, 0)
            edges.append({"id": f"e{index}", "from": source, "to": target})
            sides[f"e{index}"] = ("right", "left")
        plan = port_planner.plan_port_requests(port_planner.collect_port_requests(edges, nodes, sides))
        self.assertEqual(plan.status, port_planner.PLAN_COMPLETE, plan.issues)
        self.assertEqual(len(plan.assignments), 500)
        self.assertGreater(plan.attempts, 256)
        self.assertEqual(plan.components_solved, 500)
        self.assertLessEqual(plan.max_component_attempts, plan.budget.max_attempts)
        component = plan.component_assignment_key("e0")
        replan = port_planner.replan_port_plan(
            port_planner.prepare_port_plan(port_planner.collect_port_requests(edges, nodes, sides)),
            component,
            rejected_assignment_keys=[plan.by_edge()["e0"].assignment_key],
            previous_plan=plan,
        )
        self.assertEqual(replan.status, port_planner.PLAN_COMPLETE, replan.issues)
        self.assertEqual(replan.components_solved, 500)
        self.assertLessEqual(replan.max_component_attempts, replan.budget.max_attempts)
        self.assertIs(replan.by_edge()["e499"], plan.by_edge()["e499"])

    def test_port_primitives_keep_search_finite_and_measure_physical_gap(self):
        node = bounds(0, 0, width=200, height=100)
        self.assertEqual(ports.port_side_length(node, "right"), 100.0)
        self.assertEqual(ports.port_side_length(node, "top"), 200.0)
        candidates = ports.finite_port_offsets([.5], locked_offsets=[.5], minimum_gap=.16, limit=12)
        self.assertIn(.34, candidates)
        self.assertIn(.66, candidates)
        self.assertEqual(ports.continuous_port_capacity(.05, .95, [.5], .16), 4)

    def test_mandatory_candidates_survive_center_order_or_exhaust_the_finite_budget(self):
        mandatory = ports.finite_port_offsets([.5], minimum=.05, maximum=.95, limit=1, mandatory_offsets=[.05, .2, .95])
        self.assertEqual(mandatory, [.05, .2, .95])
        shared_bounds = (0.0, 0.0, 100.0, 100.0)
        request = port_planner.EdgePortRequest(
            "edge",
            port_planner.EndpointRequest("edge", "source", "exit", "right", shared_bounds, 20),
            port_planner.EndpointRequest("edge", "target", "entry", "left", shared_bounds, 20),
        )
        exhausted = port_planner.plan_port_requests((request,), budget=port_planner.PlannerBudget(1, 6, 256))
        self.assertEqual(exhausted.status, port_planner.PLAN_CANDIDATE_EXHAUSTED)


if __name__ == "__main__":
    unittest.main()
