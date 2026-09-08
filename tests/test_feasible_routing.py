"""Neutral coordinator regressions for bounded native-aware route planning."""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from evidence_cases import linear_spec
from swimlane_loader import load_skill_modules


class FeasibleRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_skill_modules(ROOT / "skills/product-swimlane-drawio/scripts/drawio_swimlane.py", module_name="feasible_routing_tests")
        cls.routing = cls.loaded.routing
        cls.planner = cls.loaded.port_planner

    def captured(self, spec=None):
        original = self.routing.plan_route_batch
        calls = []
        def capture(*args, **kwargs):
            calls.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
            return original(*args, **kwargs)
        with mock.patch.object(self.routing, "plan_route_batch", side_effect=capture):
            self.loaded.tool.build_tree(spec or linear_spec(3, version="2"))
        self.assertEqual(len(calls), 1)
        return calls[0]

    def replay(self, args, kwargs, **changes):
        options = dict(kwargs, **changes)
        return self.routing.plan_route_batch(*args, **options)

    def test_actual_adapter_profiles_enter_new_branch_and_preserve_inputs(self):
        args, kwargs = self.captured()
        before = copy.deepcopy((args, kwargs))
        with mock.patch.object(self.routing, "_plan_feasible_route_batch", wraps=self.routing._plan_feasible_route_batch) as branch:
            result = self.replay(args, kwargs)
        self.assertEqual(branch.call_count, 1)
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual((args, kwargs), before)
        self.assertGreater(result.planning["candidate_evaluations"], 0)
        self.assertLessEqual(result.batch_replays, 64)
        self.assertTrue(all(count <= 6 for count in result.component_replans.values()))
        self.assertEqual(set(result.label_choices), {edge["id"] for edge in args[0]})

    def test_side_domain_respects_explicit_offsets_locked_and_waypoints(self):
        args, _ = self.captured()
        edges, lanes, nodes = args
        edge = {"id": "return", "from": "n2", "to": "n1", "route": "back"}
        default = ("right", "right")
        cases = [({"exit_side": "right"}, (None, None), 0),
                 ({"exit_offset": .35}, (None, None), 0),
                 ({"entry_offset": .65}, (None, None), 1),
                 ({}, (.5, None), 0), ({}, (None, .5), 1)]
        for fields, locked, fixed in cases:
            with self.subTest(fields=fields, locked=locked):
                values = self.routing.side_pair_candidates(dict(edge, **fields), default, lanes, nodes, locked=locked)
                self.assertEqual(values[0], default)
                self.assertTrue(all(pair[fixed] == default[fixed] for pair in values))
                self.assertGreater(len(values), 1)
                self.assertLessEqual(len(values), 16)
        for points in ([], [(120, 150), (120, 150)]):
            self.assertEqual(self.routing.side_pair_candidates(dict(edge, waypoints=points), default, lanes, nodes), (default,))
        self.assertEqual(self.routing.side_pair_candidates(edge, default, lanes, nodes, main=True), (default,))

    def test_explicit_waypoints_roundtrip_through_actual_batch(self):
        spec = linear_spec(3, version="2")
        points = [(120, 150), (120, 150)]
        spec["edges"][0]["waypoints"] = points
        args, kwargs = self.captured(spec)
        result = self.replay(args, kwargs)
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        decision = next(item for item in result.decisions if item.edge_id == "e0")
        self.assertEqual(decision.routed["points"], points)
        self.assertEqual(args[0][0]["waypoints"], points)

    def test_supported_center_domain_exposes_capacity_instead_of_offcenter_fallback(self):
        def bounds(x, y):
            return {"left": x, "top": y, "right": x+100, "bottom": y+100, "width": 100, "height": 100}
        nodes = {"hub": bounds(0, 0), "a": bounds(300, 0), "b": bounds(300, 200)}
        edges = [{"id": "one", "from": "hub", "to": "a"}, {"id": "two", "from": "hub", "to": "b"}]
        sides = dict.fromkeys(("one", "two"), ("right", "left"))
        domain = {edge["id"]: {"exit": (.5,)} for edge in edges}
        requests = self.planner.collect_port_requests(edges, nodes, sides, supported_offsets=domain)
        plan = self.planner.plan_port_requests(requests)
        self.assertNotEqual(plan.status, self.planner.PLAN_COMPLETE)
        self.assertTrue(plan.issues)
        self.assertEqual(set(plan.issues[0].edge_ids), {"one", "two"})
        # An explicit incompatible value remains an input conflict, never .5.
        explicit = [dict(edges[0], exit_offset=.35)]
        requests = self.planner.collect_port_requests(explicit, nodes, sides, supported_offsets=domain)
        plan = self.planner.plan_port_requests(requests)
        self.assertNotEqual(plan.status, self.planner.PLAN_COMPLETE)
        self.assertEqual(requests[0].exit.hard_offset, .35)

    def test_prospective_side_joins_only_direct_port_component(self):
        def bounds(x, y):
            return {"left": x, "top": y, "right": x+100, "bottom": y+100, "width": 100, "height": 100}
        nodes = {name: bounds(index*200, 0) for index, name in enumerate(("hub", "a", "b", "u", "v"))}
        edges = [{"id": "moving", "from": "hub", "to": "a"}, {"id": "coupled", "from": "hub", "to": "b"}, {"id": "unrelated", "from": "u", "to": "v"}]
        sides = {"moving": ("right", "left"), "coupled": ("top", "left"), "unrelated": ("top", "left")}
        requests = self.planner.collect_port_requests(edges, nodes, sides)
        self.assertEqual(self.routing._port_dependency_closure({"moving"}, requests, ()), {"moving"})
        closure = self.routing._port_dependency_closure({"moving"}, requests, (), prospective={"moving": ("top", "left")})
        self.assertEqual(closure, {"moving", "coupled"})

    def test_candidate_and_path_budgets_fail_explicitly_without_input_changes(self):
        args, kwargs = self.captured()
        before = copy.deepcopy((args, kwargs))
        for field, expected in (("max_candidate_evaluations", "candidate_evaluations"), ("max_path_candidates", "path_candidates")):
            with self.subTest(field=field):
                budget = self.routing.RouteSearchBudget(**{field: 1})
                result = self.replay(args, kwargs, route_budget=budget)
                self.assertEqual(result.status, self.routing.ROUTE_FAILED)
                self.assertEqual(result.failure.code, "routing/route-search-budget")
                self.assertEqual(result.failure.evidence["planning"]["budget"], expected)
                self.assertEqual((args, kwargs), before)

    def test_replay_and_component_budgets_are_shared_by_offset_and_side_repairs(self):
        args, kwargs = self.captured()
        # Remove main-path privilege so the finite side domain is available.
        options = dict(kwargs, main_path=[])
        context = copy.deepcopy(options["routing_context"])
        context["main_path_pairs"] = set()
        options["routing_context"] = context
        seen = []
        def blocked(edge, assignment, lanes, nodes, context, **unused):
            seen.append((assignment.exit.side, assignment.entry.side, assignment.exit.offset, assignment.entry.offset))
            return self.routing.RouteFailure("routing/no-safe-route", edge["id"], "Controlled geometric obstruction", evidence={"planning": {"reason": "candidate_space_exhausted"}})
        for budget, expected, maximum in ((self.routing.RouteSearchBudget(max_batch_replays=1), "batch_replays", 1),
                                         (self.routing.RouteSearchBudget(max_component_replans=1), "component_replans", 2)):
            with self.subTest(expected=expected), mock.patch.object(self.routing, "route_edge_at_ports", side_effect=blocked):
                result = self.replay(args, options, route_budget=budget)
                self.assertEqual(result.status, self.routing.ROUTE_FAILED)
                self.assertEqual(result.failure.code, "routing/route-search-budget")
                self.assertEqual(result.failure.evidence["planning"]["budget"], expected)
                self.assertLessEqual(result.batch_replays, maximum)
                self.assertTrue(all(count <= budget.max_component_replans for count in result.component_replans.values()))

    def test_frozen_routes_remain_obstacles_without_rerouting_or_mutation(self):
        args, kwargs = self.captured()
        original = self.replay(args, kwargs)
        self.assertEqual(original.status, self.routing.ROUTE_COMPLETE)
        context = copy.deepcopy(kwargs["routing_context"])
        by_id = {edge["id"]: edge for edge in args[0]}
        for decision in original.decisions:
            self.routing._record_trial_decision(context, by_id[decision.edge_id], decision)
        frozen = {"e0", "e1"}
        locks = {item.edge_id: (item.assignment.exit.offset, item.assignment.entry.offset) for item in original.decisions if item.edge_id in frozen}
        options = dict(kwargs, routing_context=context, mutable_edge_ids={"e2"}, locked_offsets=locks)
        before = copy.deepcopy((args, options))
        with mock.patch.object(self.routing, "route_edge_at_ports", wraps=self.routing.route_edge_at_ports) as route:
            result = self.replay(args, options)
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual({item.edge_id for item in result.decisions}, {"e2"})
        self.assertEqual({call.args[0]["id"] for call in route.call_args_list}, {"e2"})
        self.assertEqual((args, options), before)

    def test_missing_frozen_path_fails_before_trials(self):
        args, kwargs = self.captured()
        with mock.patch.object(self.routing, "route_edge_at_ports", wraps=self.routing.route_edge_at_ports) as route:
            result = self.replay(args, kwargs, mutable_edge_ids={"e2"})
        self.assertEqual(result.failure.code, "routing/frozen-route-missing")
        self.assertEqual(route.call_count, 0)

    def test_constraint_values_survive_actual_batch_repair_attempts(self):
        args, kwargs = self.captured()
        original_edge = args[0][1]
        cases = [({"exit_side": "bottom"}, {}, "exit", "side", "bottom"),
                 ({"exit_offset": .35}, {}, "exit", "offset", .35),
                 ({}, {"e1": (.35, None)}, "exit", "offset", .35),
                 ({}, {"e1": (None, .65)}, "entry", "offset", .65)]
        for fields, locks, endpoint, attribute, value in cases:
            with self.subTest(fields=fields, locks=locks):
                edge = dict(original_edge, **fields)
                local_args = ([edge], args[1], args[2])
                options = dict(kwargs, main_path=[], mutable_edge_ids={"e1"}, locked_offsets=locks)
                observed = []
                def blocked(candidate, assignment, lanes, nodes, context, **unused):
                    observed.append(assignment)
                    return self.routing.RouteFailure("routing/no-safe-route", candidate["id"], "Controlled obstruction")
                with mock.patch.object(self.routing, "route_edge_at_ports", side_effect=blocked):
                    result = self.replay(local_args, options)
                self.assertEqual(result.status, self.routing.ROUTE_FAILED)
                self.assertTrue(observed)
                self.assertTrue(all(getattr(getattr(item, endpoint), attribute) == value for item in observed))
                if attribute == "offset":
                    expected_side = "bottom" if endpoint == "exit" else "top"
                    self.assertTrue(all(getattr(item, endpoint).side == expected_side for item in observed))

    def test_side_change_rebuilds_rejection_against_current_component(self):
        args, kwargs = self.captured()
        args = ([args[0][1]], args[1], args[2])
        options = dict(kwargs, main_path=[], mutable_edge_ids={"e1"})
        observed, rejections = [], []
        original_replan = self.planner.replan_port_plan
        def blocked(edge, assignment, lanes, nodes, context, **unused):
            observed.append((assignment.exit.side, assignment.entry.side))
            return self.routing.RouteFailure("routing/no-safe-route", edge["id"], "Controlled obstruction")
        def replan(preparation, *positional, **named):
            requests = {request.edge_id: request for request in preparation.edge_requests}
            for key in named.get("rejected_assignment_keys", ()):
                request = requests[key[0]]
                self.assertEqual((key[1], key[2], key[4], key[5]),
                                 (request.exit.node_id, request.exit.side, request.entry.node_id, request.entry.side))
                rejections.append(key)
            return original_replan(preparation, *positional, **named)
        with mock.patch.object(self.routing, "route_edge_at_ports", side_effect=blocked), mock.patch.object(self.planner, "replan_port_plan", side_effect=replan):
            result = self.replay(args, options)
        self.assertEqual(result.status, self.routing.ROUTE_FAILED)
        self.assertGreater(len(set(observed)), 1)
        self.assertTrue(rejections)
        self.assertLessEqual(result.batch_replays, 64)
        self.assertTrue(all(count <= 6 for count in result.component_replans.values()))

    def test_unrelated_accepted_route_is_not_reselected_during_side_repairs(self):
        args, kwargs = self.captured()
        options = dict(kwargs, main_path=[])
        before = copy.deepcopy((args, options))
        original_route = self.routing.route_edge_at_ports
        observed = {}
        def block_middle(edge, assignment, lanes, nodes, context, **named):
            observed.setdefault(edge["id"], []).append(assignment)
            if edge["id"] == "e1":
                return self.routing.RouteFailure("routing/no-safe-route", edge["id"], "Controlled obstruction")
            return original_route(edge, assignment, lanes, nodes, context, **named)
        with mock.patch.object(self.routing, "route_edge_at_ports", side_effect=block_middle):
            result = self.replay(args, options)
        self.assertEqual(result.status, self.routing.ROUTE_FAILED)
        self.assertGreater(len(observed["e1"]), 1)
        self.assertEqual(len(observed["e0"]), 1)
        self.assertEqual((args, options), before)

    def test_failed_batch_does_not_return_partial_deliverable_decisions(self):
        args, kwargs = self.captured()
        before = copy.deepcopy((args, kwargs))
        original = self.routing.route_edge_at_ports
        def fail_second(edge, assignment, lanes, nodes, context, **options):
            if edge["id"] == "e1":
                return self.routing.RouteFailure("routing/no-safe-route", edge["id"], "Fixed obstruction", locked=True)
            return original(edge, assignment, lanes, nodes, context, **options)
        with mock.patch.object(self.routing, "route_edge_at_ports", side_effect=fail_second):
            result = self.replay(args, kwargs)
        self.assertEqual(result.status, self.routing.ROUTE_FAILED)
        self.assertEqual(result.decisions, ())
        self.assertEqual(result.label_choices, {})
        self.assertEqual((args, kwargs), before)

    def test_local_rejections_consume_candidate_budget_before_geometry_checks(self):
        args, kwargs = self.captured()
        routes = self.routing
        unsafe = [[(120., 100.), (float(index), 100.), (float(index), 300.), (120., 300.)]
                  for index in range(200)]
        original = routes.automatic_polyline_is_safe
        with mock.patch.object(routes, "route_candidates", return_value=unsafe), \
             mock.patch.object(routes, "automatic_polyline_is_safe", wraps=original) as checks:
            result = self.replay(args, kwargs, route_budget=routes.RouteSearchBudget(
                max_path_candidates=1, max_candidate_evaluations=1))
        self.assertEqual(result.failure.code, "routing/route-search-budget")
        self.assertEqual(checks.call_count, 1)
        self.assertEqual(result.planning["candidate_evaluations"], 1)

    def test_path_rejection_is_scoped_to_native_assignment_and_all_obstacles(self):
        args, kwargs = self.captured()
        result = self.replay(args, kwargs)
        decision = result.decisions[0]
        profile = kwargs["routing_context"]["native_label_profiles"]
        make = self.routing._route_rejection_key
        params = (decision.edge_id, decision.assignment, decision.routed["points"], profile,
                  {"n": {"left": 1.}}, {"other": [(0., 0.), (10., 0.)]}, {})
        before = copy.deepcopy(params)
        first = make(*params)
        self.assertEqual(first, make(*copy.deepcopy(params)))
        for index, change in ((3, {"different": True}), (4, {}),
                              (5, {"other": [(0., 1.), (10., 1.)]}), (6, {"label": {"left": 4.}})):
            changed = list(copy.deepcopy(params)); changed[index] = change
            self.assertNotEqual(first, make(*changed))
        self.assertEqual(params, before)

    def test_missing_native_profile_fails_instead_of_legacy_fallback(self):
        args, kwargs = self.captured()
        for profiles in ({}, {key: value for key, value in kwargs["routing_context"]["native_label_profiles"].items()
                              if key != "e0"}):
            options = copy.deepcopy(kwargs)
            options["routing_context"]["native_label_profiles"] = profiles
            result = self.replay(args, options)
            self.assertEqual(result.status, self.routing.ROUTE_FAILED)
            self.assertEqual(result.failure.evidence["planning"]["reason"], "native_profile_unavailable")
            self.assertEqual(result.decisions, ())
            self.assertEqual(result.label_choices, {})

    def test_singleton_offset_exhaustion_still_tries_permitted_sides(self):
        spec = linear_spec(2, version="2")
        spec["nodes"] = [spec["nodes"][0], spec["nodes"][-1]]
        spec["edges"] = [{"id": "direct", "from": "n0", "to": "n2", "label": ""}]
        spec["main_path"] = ["n0", "n2"]
        args, kwargs = self.captured(spec)
        calls = []
        original = self.routing.route_edge_at_ports
        def fail_default(edge, assignment, *args, **kwargs):
            sides = (assignment.exit.side, assignment.entry.side)
            calls.append(sides)
            if sides == ("bottom", "top"):
                return self.routing.RouteFailure("routing/no-safe-route", edge["id"], "Controlled default-side obstacle")
            return original(edge, assignment, *args, **kwargs)
        with mock.patch.object(self.routing, "route_edge_at_ports", side_effect=fail_default):
            result = self.replay(args, kwargs, main_path=[])
        self.assertEqual(result.status, self.routing.ROUTE_COMPLETE)
        self.assertEqual(calls[0], ("bottom", "top"))
        self.assertTrue(any(pair != calls[0] for pair in calls))
        self.assertLessEqual(result.batch_replays, 64)

    def test_same_lane_downward_decision_uses_free_bottom_without_overriding_explicit_or_main(self):
        args, _ = self.captured()
        _, lanes, nodes = copy.deepcopy(args)
        nodes["n1"]["semantic"]["type"] = "decision"
        edge = {"id": "branch", "from": "n1", "to": "n2", "branch": "negative"}
        def sides(item, reserved=()):
            return self.routing.preferred_sides(item, "forward", nodes["n1"], nodes["n2"], lanes,
                        v3_semantics=True, outgoing_counts={"n1": 2}, bottom_reserved_sources=set(reserved))
        self.assertEqual(sides(edge), ("bottom", "top"))
        self.assertEqual(sides(dict(edge, exit_side="right")), ("right", "top"))
        self.assertEqual(sides(edge, {"n1"}), ("left", "top"))

    def test_cross_lane_return_prefers_outer_pair_before_opposite_side_detour(self):
        args, _ = self.captured()
        _, lanes, nodes = copy.deepcopy(args)
        lanes["right"] = copy.deepcopy(next(iter(lanes.values())))
        nodes["n2"]["lane"] = "right"
        edge = {"id": "return", "from": "n2", "to": "n1", "route": "back"}
        pairs = self.routing.side_pair_candidates(edge, ("left", "left"), lanes, nodes)
        self.assertLess(pairs.index(("right", "right")), pairs.index(("left", "right")))
        fixed = self.routing.side_pair_candidates(dict(edge, exit_side="left"), ("left", "left"), lanes, nodes)
        self.assertTrue(all(pair[0] == "left" for pair in fixed))

    def test_cross_lane_elbow_beats_staircase_without_target_corridor_penalty(self):
        common = dict(route_class="back", is_main_path=False, same_lane_down=False,
            target_lane={"x": 100, "width": 200}, target_bounds={"left": 150, "right": 250},
            entry_side="left", existing_segments=[], reciprocal_segments=[], label_choice=None,
            has_label=False, prefer_target_lane_corridor=False)
        elbow = [(50, 200), (50, 100), (150, 100)]
        stair = [(50, 200), (50, 150), (120, 150), (120, 100), (150, 100)]
        self.assertEqual(self.routing.candidate_score(elbow, **common), 232)
        self.assertEqual(self.routing.candidate_score(stair, **common), 296)

    def test_neutral_three_lane_return_builds_one_elbow_from_original_auto_fields(self):
        ids = ["begin", "submit", "check", "choice", "record", "end", "revise"]
        lanes = ["a", "a", "b", "b", "c", "c", "a"]
        ranks = [1, 2, 3, 4, 5, 6, 4]
        kinds = ["start", "process", "process", "decision", "process", "end", "process"]
        spec = dict(schema_version="3", title="Neutral revision", behavior_pattern="approval-loop",
            lanes=[{"id": x, "label": x.upper()} for x in ("a", "b", "c")],
            nodes=[{"id": i, "lane": lane, "rank": rank, "type": kind,
                    "label": "" if kind in {"start", "end"} else i}
                   for i, lane, rank, kind in zip(ids, lanes, ranks, kinds)],
            edges=[{"id": "main"+str(i), "from": a, "to": b, "flow_role": "main"}
                   for i, (a, b) in enumerate(zip(ids[:5], ids[1:6]))], main_path=ids[:6])
        spec["edges"][3].update(branch="positive", outcome="yes", label="Yes")
        spec["edges"].extend([
            {"id": "revise_branch", "from": "choice", "to": "revise", "route": "side",
             "flow_role": "branch", "branch": "negative", "outcome": "no", "label": "No"},
            {"id": "return", "from": "revise", "to": "check", "route": "back", "type": "retry", "flow_role": "retry"}])
        args, options = self.captured(spec)
        result = self.replay(args, options)
        decision = next(d for d in result.decisions if d.edge_id == "return")
        path = decision.routed["native_path"]
        self.assertEqual(len(path), 3)
        self.assertEqual(path[0][0], path[1][0])
        self.assertEqual(path[1][1], path[2][1])
        tree = self.loaded.tool.build_tree(spec)
        self.assertTrue(self.loaded.validation.validate_tree(tree)["quality_gate_passed"])


if __name__ == "__main__":
    unittest.main()
