"""阶段 A 的玩家调动契约；仅使用合成世界与临时存档。"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import mp


class TestMoveBaseline(unittest.TestCase):
    def world(self):
        w=mp.World(size=16,seed=5,nations=['秦','楚'],
                   starts={'秦':(5,5),'楚':(12,12)})
        w.units.clear()
        w.tiles.pop((6,5),None)
        w.units.guard_once.add((6,5))  # 合成已清掉守卫的野地；读档不重新出生
        w.units.spawn('秦','步',5,5)
        w.begin_turn()
        return w

    def move(self,w,tool=False,x=6,y=5):
        if tool:
            return w.call_tool('秦','移动',{'army_id':1,'x':x+1,'y':y+1})
        return w.move('秦',1,x,y)[1]

    def test_wild_move_queries_do_not_materialize_or_conquer(self):
        w=self.world();before=w.state_version
        self.assertIn('未占地',self.move(w))
        state=w.player_state('秦')
        self.assertEqual(state['state_version'],before+1)
        unit=state['current']['armies'][0]
        self.assertEqual((unit['x'],unit['y'],unit['tile_owner']),(7,6,None))
        self.assertFalse(unit['action_available'])
        self.assertNotIn((6,5),w.tiles)
        self.assertIn('状态版本',w.decision_overview('秦'))
        self.assertIn('状态版本',w.call_tool('秦','query',{'panel':'overview'}))
        state['current']['armies'][0]['hp']=1
        self.assertEqual(w.units[0].hp,100)
        self.assertEqual(w.state_version,before+1)  # 查询无副作用

    def test_tool_and_direct_move_match_and_reject_other_phases(self):
        direct,tool=self.world(),self.world()
        self.assertEqual(self.move(direct),self.move(tool,tool=True))
        self.assertEqual(direct.units.to_save(),tool.units.to_save())
        self.assertEqual(direct.state_version,tool.state_version)
        for phase in ('已结算','slot.06','结算失败'):
            for tool_entry in (False,True):
                w=self.world();w.phase=phase
                before=deepcopy(w.units.to_save());version=w.state_version
                with self.subTest(phase=phase,tool=tool_entry),self.assertRaisesRegex(ValueError,'仅行动阶段'):
                    self.move(w,tool=tool_entry)
                self.assertEqual(w.units.to_save(),before)
                self.assertEqual(w.state_version,version)

    def test_failed_move_revision_depends_on_actual_allowance_change(self):
        for visible in (True,False):
            w=self.world();before=w.state_version
            target=(6,5) if visible else (15,15)
            w.tiles[target]=w._new_tile(*target,'楚')
            self.assertEqual(w.visible_to('秦',*target),visible)
            ok,msg=w.move('秦',1,*target)
            self.assertFalse(ok,msg)
            self.assertEqual((w.units[0].x,w.units[0].y),(5,5))
            self.assertEqual(w.state_version,before+(0 if visible else 1))
            self.assertEqual(w.units[0].moved_turn,-1 if visible else w.turn)

    def test_wild_move_save_load_continuation_matches(self):
        w=self.world();self.move(w)
        with TemporaryDirectory() as directory:
            a,b=Path(directory)/'synthetic-a.json',Path(directory)/'synthetic-b.json'
            w.save(a);loaded=mp.World.load(a);loaded.save(b)
            self.assertEqual(a.read_bytes(),b.read_bytes())
            self.assertEqual(w.player_state('秦'),loaded.player_state('秦'))
            w.resolve_turn();loaded.resolve_turn()
            w.begin_turn();loaded.begin_turn()
            self.assertEqual(w.move('秦',1,5,5),loaded.move('秦',1,5,5))
            w.save(a);loaded.save(b)
            self.assertEqual(a.read_bytes(),b.read_bytes())
