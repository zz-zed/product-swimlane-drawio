"""Independent saved-XML assertions for incremental label and route updates."""
from pathlib import Path
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'skills/product-swimlane-drawio/scripts/drawio_swimlane.py'
sys.path.insert(0, str(ROOT / 'tools'))
from swimlane_loader import load_skill_modules
from evidence_cases import corpus, linear_spec


class SavedEditContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_skill_modules(TOOL, module_name='saved_edit_contracts')
        cls.t, cls.d, cls.c = cls.m.tool, cls.m.document, cls.m.contracts

    def edge(self, tree, edge_id='e0'):
        return self.d.edge_records(self.d.graph_root(tree))[edge_id]

    def route(self, edge):
        # Native XML comparison: do not compact or call compare/route planning.
        arrays = edge.findall("./mxGeometry/Array[@as='points']")
        return (edge.get('source'), edge.get('target'), edge.get('style'),
                tuple(ET.tostring(item) for item in arrays),
                tuple(sorted((key, value) for key, value in edge.attrib.items()
                             if key.startswith(('data-waypoints', 'data-exit', 'data-entry')))))

    def saved_retry(self):
        tree = self.t.build_tree(corpus()['request-response-retry'])
        edge = self.edge(tree, 'retry')
        pts = edge.findall("./mxGeometry/Array[@as='points']/mxPoint")
        for a, b in zip(pts, pts[1:]):
            if a.get('x') == b.get('x') and a.get('y') != b.get('y'):
                a.set('x', '454'); b.set('x', '454'); break
        return tree

    def test_t01_saved_automatic_retry_label_only_preserves_native_route(self):
        before = self.saved_retry(); after = copy.deepcopy(before)
        changes = {'update_edges': [{'id': 'retry', 'label': 'Recheck'}]}
        saved = self.route(self.edge(before, 'retry'))
        result = self.t.patch_tree(after, changes, False)
        self.assertEqual(self.route(self.edge(after, 'retry')), saved)
        self.assertIn((454., 492.), self.d.edge_waypoints(self.edge(after, 'retry')))
        self.assertEqual(result['label_updated_edges'], ['retry'])
        self.assertEqual(result['rerouted_edges'], [])
        self.assertEqual(result['saved_routes'], {'checked': 7, 'preserved': True, 'changed_edges': []})
        self.assertTrue(self.m.validation.validate_tree(after)['quality_gate_passed'])
        self.assertTrue(self.t.compare_trees(before, after, changes)['preserved'])

    def test_t02_label_only_preserves_repeated_collinear_and_empty_explicit_points(self):
        for points in ([], [(120, 142), (120, 142), (120, 151)]):
            with self.subTest(points=points):
                tree = self.t.build_tree(linear_spec())
                edge = self.edge(tree)
                self.d.set_edge_points(edge, points, action='replace_explicit')
                edge.set('data-waypoints-origin', 'explicit')
                saved = self.route(edge)
                self.t.patch_tree(tree, {'update_edges': [{'id': 'e0', 'label': ''}]}, False)
                self.assertEqual(self.route(self.edge(tree)), saved)

    def test_t03_same_label_false_reroute_and_same_routing_fields_are_noops(self):
        for update in ({'label': 'Next'}, {'reroute': False}, {'route': 'forward'}, {'exit_side': 'bottom'}):
            tree = self.t.build_tree(linear_spec()); edge = self.edge(tree)
            if 'route' in update:
                update['route'] = edge.get('data-route')
            saved = ET.tostring(edge)
            result = self.t.patch_tree(tree, {'update_edges': [{'id': 'e0', **update}]}, False)
            self.assertEqual(ET.tostring(self.edge(tree)), saved)
            self.assertEqual(result['label_repositioned_edges'], [])
            self.assertEqual(result['rerouted_edges'], [])

    def test_t04_shorter_text_keeps_saved_relative_geometry(self):
        tree = self.t.build_tree(linear_spec()); edge = self.edge(tree)
        geom = edge.find('mxGeometry')
        # An equivalent real native placement with nonzero x, not a cache edit.
        lanes, nodes = self.d.lane_node_records(self.d.graph_root(tree), self.d.find_pool(tree))
        measured = self.d.edge_label_measurement(edge, scene={'lanes': lanes, 'nodes': nodes, 'pool': self.d.find_pool(tree)})
        geom.set('x', '0.25'); geom.set('y', '2')
        anchor, _ = self.m.labels.native_label_anchor(measured['path'], x=.25, y=2)
        offset = geom.find("./mxPoint[@as='offset']")
        offset.set('x', str(measured['position'][0]-anchor[0])); offset.set('y', str(measured['position'][1]-anchor[1]))
        saved = ET.tostring(geom)
        result = self.t.patch_tree(tree, {'update_edges': [{'id': 'e0', 'label': 'Go'}]}, False)
        self.assertEqual(ET.tostring(self.edge(tree).find('mxGeometry')), saved)
        self.assertEqual(result['label_repositioned_edges'], [])

    def test_t05_overlap_repositions_only_label_on_saved_path(self):
        tree = self.t.build_tree(linear_spec()); edge = self.edge(tree)
        geom = edge.find('mxGeometry'); offset = geom.find("./mxPoint[@as='offset']")
        offset.set('x', '0'); offset.set('y', '60')
        saved = self.route(edge)
        result = self.t.patch_tree(tree, {'update_edges': [{'id': 'e0', 'label': 'Go'}]}, False)
        self.assertEqual(self.route(self.edge(tree)), saved)
        self.assertEqual(result['label_repositioned_edges'], ['e0'])
        self.assertTrue(self.m.validation.validate_tree(tree)['quality_gate_passed'])

    def test_t06_unplaceable_label_leaves_cli_input_and_output_unchanged(self):
        tree = self.t.build_tree(linear_spec())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); before = root/'before.drawio'; output = root/'after.drawio'; patch = root/'patch.json'
            self.d.write_tree(tree, before); original=before.read_bytes(); output.write_bytes(b'sentinel')
            patch.write_text(json.dumps({'update_edges': [{'id': 'e0', 'label': '测量范围' * 80}]}))
            result = subprocess.run([sys.executable,'-B',str(TOOL),'patch','--input',str(before),'--expected-input-sha256',hashlib.sha256(original).hexdigest(),'--changes',str(patch),'--output',str(output),'--strict','--force'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('text/edge-label-no-clear-span',result.stdout)
            self.assertEqual(before.read_bytes(),original); self.assertEqual(output.read_bytes(),b'sentinel')
            self.assertEqual(list(root.glob('.*.candidate')),[])

    def test_t07_mixed_labels_and_reroute_are_deterministic(self):
        before = self.saved_retry()
        changes = {'update_edges': [{'id': 'e0', 'label': 'Go', 'reroute': True}, {'id': 'retry', 'label': 'Recheck'}, {'id': 'e1', 'label': 'Pass'}]}
        a,b=copy.deepcopy(before),copy.deepcopy(before)
        for tree in (a,b):
            self.t.patch_tree(tree,changes,False)
            self.assertEqual(self.route(self.edge(tree,'retry')),self.route(self.edge(before,'retry')))
        self.assertEqual(ET.tostring(a.getroot()),ET.tostring(b.getroot()))

    def test_t08_t15_frozen_native_label_unavailable_blocks_added_route(self):
        tree = self.t.build_tree(linear_spec()); self.edge(tree).set('style',self.edge(tree).get('style')+'rotation=30;')
        saved=ET.tostring(self.edge(tree))
        with self.assertRaises(self.c.DiagramError) as caught:
            self.t.patch_tree(tree,{'edges':[{'id':'extra','from':'n1','to':'n3'}]},False)
        self.assertEqual(caught.exception.code,'text/edge-label-geometry-unavailable')
        self.assertEqual(ET.tostring(self.edge(tree)),saved)

    def test_t15_new_route_avoids_actual_frozen_label_and_preserves_old_xml(self):
        tree = self.t.build_tree(linear_spec())
        edge = self.edge(tree, 'e2')
        cache = {key: value for key, value in edge.attrib.items() if key.startswith('data-label-')}
        offset = edge.find("mxGeometry/mxPoint[@as='offset']")
        offset.set('x', '-90'); offset.set('y', '0')
        old_edges = {edge_id: ET.tostring(cell)
                     for edge_id, cell in self.d.edge_records(self.d.graph_root(tree)).items()}
        lanes, nodes = self.d.lane_node_records(self.d.graph_root(tree), self.d.find_pool(tree))
        measured = self.d.edge_label_measurement(edge, scene={'lanes': lanes, 'nodes': nodes,
                                                            'pool': self.d.find_pool(tree)})
        self.assertEqual(measured['bounds']['left'], 17.5)
        self.assertEqual(measured['bounds']['right'], 42.5)
        self.assertTrue(self.m.validation.validate_tree(tree)['quality_gate_passed'])
        self.t.patch_tree(tree, {'edges': [{'id': 'bypass', 'from': 'n1', 'to': 'n3',
                              'exit_side': 'left', 'entry_side': 'left',
                              'exit_offset': .5, 'entry_offset': .5}]}, True)
        for edge_id, saved in old_edges.items():
            self.assertEqual(ET.tostring(self.edge(tree, edge_id)), saved)
        self.assertEqual({key: value for key, value in self.edge(tree, 'e2').attrib.items()
                          if key.startswith('data-label-')}, cache)
        lanes, nodes = self.d.lane_node_records(self.d.graph_root(tree), self.d.find_pool(tree))
        # The existing x=8 candidate clears the saved x=17.5..42.5 label;
        # the previously chosen x=30 candidate crossed it at y=348.
        path = self.d.edge_polyline(self.edge(tree, 'bypass'), lanes, nodes)
        self.assertEqual(path, [(54., 204.), (8., 204.), (8., 396.), (54., 396.)])
        self.assertTrue(self.m.validation.validate_tree(tree)['quality_gate_passed'])

    def test_t15_no_clear_candidate_and_explicit_conflict_leave_cli_files_unchanged(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as td:
                spec = linear_spec()
                # The 222px label spans all bounded candidate corridors at
                # y=348, while remaining valid on its own saved carrier.
                if not explicit:
                    spec['edges'][2]['label'] = '中' * 20
                tree = self.t.build_tree(spec)
                geometry = self.edge(tree, 'e2').find('mxGeometry')
                offset = geometry.find("mxPoint[@as='offset']")
                if offset is None:
                    offset = ET.SubElement(geometry, 'mxPoint', {'as': 'offset'})
                offset.set('x', '-90' if explicit else '0'); offset.set('y', '0')
                self.assertTrue(self.m.validation.validate_tree(tree)['quality_gate_passed'])
                new_edge = {'id': 'bypass', 'from': 'n1', 'to': 'n3',
                            'exit_side': 'left', 'entry_side': 'left',
                            'exit_offset': .5, 'entry_offset': .5}
                if explicit:
                    new_edge['waypoints'] = [{'x': 30, 'y': 204}, {'x': 30, 'y': 396}]
                root = Path(td); before = root/'before.drawio'; output = root/'after.drawio'; patch = root/'patch.json'
                self.d.write_tree(tree, before); original = before.read_bytes(); output.write_bytes(b'sentinel')
                patch.write_text(json.dumps({'edges': [new_edge]}))
                result = subprocess.run([sys.executable, '-B', str(TOOL), 'patch', '--input', str(before),
                                         '--expected-input-sha256', hashlib.sha256(original).hexdigest(),
                                         '--changes', str(patch), '--output', str(output), '--strict', '--force'],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                expected = 'text/edge-label-edge-overlap' if explicit else 'routing/no-safe-route'
                self.assertIn(expected, result.stdout)
                if not explicit:
                    self.assertIn('frozen_label_edges', result.stdout)
                    self.assertIn('e2', result.stdout)
                self.assertEqual(before.read_bytes(), original)
                self.assertEqual(output.read_bytes(), b'sentinel')
                self.assertEqual(list(root.glob('.*.candidate')), [])

    def test_t15_legacy_route_fallback_cannot_cross_saved_label(self):
        tree = self.t.build_tree(linear_spec())
        offset = self.edge(tree, 'e2').find("mxGeometry/mxPoint[@as='offset']")
        offset.set('x', '-66'); offset.set('y', '0')
        root, pool = self.d.graph_root(tree), self.d.find_pool(tree)
        lanes, nodes = self.d.lane_node_records(root, pool)
        context = {}
        self.m.routing_adapter.seed_routing_context(context, self.d.edge_records(root), lanes, nodes, pool=pool)
        saved_context = copy.deepcopy(context)
        new_edge = {'id': 'bypass', 'from': 'n1', 'to': 'n3',
                    'exit_side': 'left', 'entry_side': 'left', 'exit_offset': .5, 'entry_offset': .5}
        # Exercise the compatibility API's unsafe-base fallback deliberately:
        # it may bypass its legacy node gate, never a saved-label obstacle.
        with mock.patch.object(self.t.routing, 'route_candidates', return_value=[]):
            with self.assertRaises(self.c.DiagramError) as caught:
                self.t.routing.route_edge(new_edge, self.d.routing_lane_views(lanes),
                                          self.d.routing_node_views(nodes), self.t.ports.PortAllocator(), context)
        self.assertIn('saved labels', str(caught.exception))
        self.assertEqual(context, saved_context)

    def test_t09_valid_node_motion_preserves_saved_points_invalid_requires_route(self):
        tree = self.t.build_tree(linear_spec()); saved=self.route(self.edge(tree))
        self.t.patch_tree(tree,{'update_nodes':[{'id':'n1','y':178}]},True)
        self.assertEqual(self.route(self.edge(tree)),saved)
        tree = self.t.build_tree(linear_spec())
        with self.assertRaises(self.c.DiagramError) as caught:
            self.t.patch_tree(tree,{'update_nodes':[{'id':'n1','x':45}]},True)
        self.assertEqual(caught.exception.code,'patch/route-update-required')
        self.assertIn('e0',caught.exception.evidence['edges'])
        # Native unrounded endpoints expose a subpixel diagonal when the
        # planner serializes a nonexact fraction; do not round it into support.
        tree = self.t.build_tree(linear_spec())
        with self.assertRaises(self.c.DiagramError) as unavailable:
            self.t.patch_tree(tree, {'update_nodes':[{'id':'n1','x':45}],
                                    'update_edges':[{'id':'e0','reroute':True},
                                                    {'id':'e1','reroute':True}]}, True)
        self.assertEqual(unavailable.exception.code, 'routing/no-safe-route')
        self.assertIn('editor_router_additional_turns_required', str(unavailable.exception.evidence))
        # An explicit aligned port request on an exactly representable offset
        # moves the node and preserves a strict-valid vertical main path.
        tree = self.t.build_tree(linear_spec())
        self.t.patch_tree(tree, {'update_nodes':[{'id':'n1','x':21}], 'update_edges':[
            {'id':'e0','reroute':True,'exit_offset':0.5,'entry_offset':0.75},
            {'id':'e1','reroute':True,'exit_offset':0.75,'entry_offset':0.5}]}, True)
        self.assertTrue(self.m.validation.validate_tree(tree)['quality_gate_passed'])

    def test_moved_nonincident_note_invalidates_frozen_route(self):
        tree = self.t.build_tree(linear_spec())
        self.t.patch_tree(tree, {'nodes': [{'id':'memo','lane':'lane-a','rank':1,'type':'note',
                                          'label':'Memo','x':10,'y':72,'width':45,'height':24}]}, False)
        before = copy.deepcopy(tree)
        saved = self.route(self.edge(tree))
        with self.assertRaises(self.c.DiagramError) as caught:
            self.t.patch_tree(tree, {'update_nodes': [{'id':'memo','x':102,'y':140}]}, True)
        self.assertEqual(caught.exception.code, 'patch/route-update-required')
        self.assertIn('e0', caught.exception.evidence['edges'])
        self.assertEqual(self.route(self.edge(tree)), saved)

    def test_t10_saved_native_port_automatic_is_preserved_without_reroute(self):
        tree=self.t.build_tree(linear_spec()); edge=self.edge(tree,'e1')
        self.d.set_style_option(edge,'exitX','0.4'); self.d.set_style_option(edge,'entryX','0.4')
        saved=self.route(edge)
        self.t.patch_tree(tree,{'update_nodes':[{'id':'n1','label':'Revised'}]},False)
        self.assertEqual(self.route(self.edge(tree,'e1')),saved)

    def test_t14_inspect_validate_read_only_and_cache_independent(self):
        tree=self.t.build_tree(linear_spec()); before=self.t.inspect_tree(tree)['edges'][0]['label_geometry']
        self.edge(tree).set('data-label-left','-9999');self.edge(tree).set('data-label-segment','99')
        saved=ET.tostring(tree.getroot())
        after=self.t.inspect_tree(tree)['edges'][0]['label_geometry'];self.m.validation.validate_tree(tree)
        self.assertEqual(before,after);self.assertEqual(saved,ET.tostring(tree.getroot()))

    def test_hidden_label_text_update_preserves_geometry_and_not_applicable(self):
        tree = self.t.build_tree(linear_spec()); edge = self.edge(tree)
        edge.set('style', edge.get('style')+'noLabel=1;')
        geometry = ET.tostring(edge.find('mxGeometry')); route = self.route(edge)
        receipt = self.t.patch_tree(tree, {'update_edges': [{'id':'e0','label':'Accepted'}]}, False)
        self.assertEqual(self.edge(tree).get('value'), 'Accepted')
        self.assertEqual(self.route(self.edge(tree)), route)
        self.assertEqual(ET.tostring(self.edge(tree).find('mxGeometry')), geometry)
        self.assertEqual(receipt['label_repositioned_edges'], [])
        self.assertEqual(self.t.inspect_tree(tree)['edges'][0]['label_geometry']['status'], 'not_applicable')

    def test_saved_route_guard_rejects_direct_path_mutation_without_replay(self):
        before=self.saved_retry();after=copy.deepcopy(before)
        self.edge(after,'retry').find("./mxGeometry/Array/mxPoint").set('x','450')
        with self.assertRaises(self.c.DiagramError) as caught:
            self.t.saved_edge_preservation_guard(before,after,{'update_edges':[{'id':'retry','label':'Recheck'}]})
        self.assertEqual(caught.exception.code,'patch/preservation-violation')


if __name__=='__main__': unittest.main()
