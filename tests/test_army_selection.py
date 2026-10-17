"""Army selectors and atomic batches over synthetic worlds, without model calls."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from host import GameHost
from tests import test_world_domains as fixtures
Q=fixtures.Q


class TestArmySelection(unittest.TestCase):
    def world(self):
        fixture=fixtures.TestWorldDomains();w=fixture.world();fixture.site(w,'scenario:home',Q,2,2)
        w.military.seed(Q,'native:infantry',2,2,2)
        return w

    def test_every_tool_accepts_single_aid_ids_or_matching_both(self):
        for action in ('move','attack','retreat','disband','reachable'):
            for selector in ({'aid':1},{'ids':[1]},{'aid':1,'ids':[1]}):
                with self.subTest(action=action,selector=selector):
                    w=self.world();args=deepcopy(selector)
                    if action in ('move','attack','retreat'):args.update(x=3,y=2)
                    if action=='retreat':w.units.by_id(1,Q).update(engaged=True)
                    result=w.call_tool(Q,action,args)
                    if action=='reachable':self.assertIn(((2,2),0),result)
                    else:self.assertEqual(result.units,(1,))

    def test_invalid_selectors_leave_units_unchanged_for_every_tool(self):
        for action in ('move','attack','retreat','disband','reachable'):
            for selector in ({},{'ids':[]},{'ids':[1,1]},{'aid':1,'ids':[2]},
                             {'aid':True},{'ids':[True]},{'aid':0},{'ids':[-1]}, {'ids':[1,999]}):
                with self.subTest(action=action,selector=selector):
                    w=self.world();before=deepcopy(w.units.to_save());args=deepcopy(selector)
                    if action in ('move','attack','retreat'):args.update(x=3,y=2)
                    with self.assertRaises(ValueError):w.call_tool(Q,action,args)
                    self.assertEqual(w.units.to_save(),before)

    def test_batch_move_validates_all_units_before_moving_any(self):
        w=self.world();w.units.by_id(2,Q).update(moved_turn=w.government.period())
        before=deepcopy(w.units.to_save())
        with self.assertRaises(ValueError):w.call_tool(Q,'move',{'ids':[1,2],'site':'3 2'})
        self.assertEqual(w.units.to_save(),before)
        w=self.world();receipt=w.call_tool(Q,'move',{'ids':[1,2],'place':'3 2'})
        self.assertEqual(receipt.units,(1,2));self.assertTrue(receipt.position_changed)
        self.assertEqual([(u.x,u.y) for u in w.units.of(Q)],[(3,2),(3,2)])

    def test_batch_retreat_validates_all_units_before_marking_any(self):
        w=self.world();w.units.by_id(1,Q).update(engaged=True)
        before=deepcopy(w.units.to_save())
        with self.assertRaises(ValueError):w.call_tool(Q,'retreat',{'ids':[1,2],'x':3,'y':2})
        self.assertEqual(w.units.to_save(),before)
        w.units.by_id(2,Q).update(engaged=True)
        result=w.call_tool(Q,'retreat',{'ids':[1,2],'x':3,'y':2})
        self.assertEqual(result.units,(1,2));self.assertFalse(result.position_changed)
        for unit in w.units.of(Q):
            self.assertEqual((unit.x,unit.y),(2,2));self.assertEqual(tuple(unit.get('retreat_to')),(3,2))

    def test_bulk_reachable_groups_visible_routes_and_disband_removes_batch(self):
        w=self.world();before=deepcopy(w.units.to_save())
        rows=w.call_tool(Q,'reachable',{'ids':[1,2],'attack':True})
        self.assertEqual([r['aid'] for r in rows],[1,2])
        visible={t.position for t in w.runtime.object_call(w,'native:vision','tiles',Q)}
        for row in rows:
            self.assertTrue({pos for pos,cost in row['destinations']}<=visible)
        self.assertEqual(w.units.to_save(),before)
        result=w.call_tool(Q,'disband',{'ids':[1,2]})
        self.assertEqual([r.units for r in result],[(1,),(2,)])
        self.assertFalse(w.units.of(Q))

    def test_exposed_schemas_and_synthetic_save_roundtrip(self):
        cfg={'map_size':12,'seed':73,'nations':[{'id':Q,'name':'秦','client':'rule'}]}
        h=GameHost.new(cfg);p=h.player(Q)
        for schema in p.actions():
            if schema['function']['name'] not in ('move','attack','retreat','disband','reachable'):continue
            params=schema['function']['parameters']
            self.assertTrue({'aid','ids'}<=params['properties'].keys())
            self.assertFalse({'aid','ids'}&set(params['required']))
            self.assertEqual(params['anyOf'],[{'required':['aid']},{'required':['ids']}])
        with TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.json';h.save(path)
            self.assertEqual(GameHost.load(path,cfg).to_save(),h.to_save())


if __name__=='__main__':unittest.main()
