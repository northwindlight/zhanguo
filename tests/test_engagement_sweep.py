"""交战的挂点与清扫：撤退撤走之后，留守者不许再挂着「交战中」。

0.1 的结算是一段有序过程（战斗 → 撤离 → **脱离清扫** → 占领），0.2 拆成 stage 时
把中间的清扫整步丢了，于是：守军尽撤、城被接管，占领军却还挂着 engaged ——
下一回合 move 被拒、当回合不回血、次回合还多一条 0 伤害假战报。
"""
import unittest

from tests import test_world_domains as fixtures

Q, C, Z = fixtures.Q, fixtures.C, fixtures.Z


class TestEngagementSweep(unittest.TestCase):
    def board(self, *sites):
        t = fixtures.TestWorldDomains(); w = t.world()
        for ident, owner, x, y in sites: t.site(w, ident, owner, x, y)
        return t, w

    def unit(self, w, owner, aid):
        return w.units.by_id(aid, owner)

    def assert_no_phantom_engagement(self, w):
        self.assertEqual(w.military.engaged_without_foes(), ())

    def test_retreat_leaves_the_occupier_free_next_turn(self):
        """守军尽撤 → 占领 → 下回合可动，一条链跑通。"""
        t, w = self.board(('scenario:qc', Q, 1, 1), ('scenario:cc', C, 2, 1))
        atk = w.military.seed(Q, 'native:infantry', 1, 1)[0].id
        dfn = w.military.seed(C, 'native:infantry', 2, 1)[0].id
        t.cycle(w)                                        # 新兵当期不能行动
        t.declare(w)                                      # 秦楚交战
        self.assertEqual(t.call(w, Q, 'attack', ids=[atk], x=2, y=1).status, 'effective')
        self.assertEqual(t.call(w, C, 'retreat', aid=dfn, x=3, y=1).status, 'submitted')
        result = t.cycle(w)
        self.assertEqual(w.territory.owner(2, 1), Q)      # 守军尽撤，城归攻方
        self.assertIn('disengaged', result)               # 清扫真的跑过
        self.assert_no_phantom_engagement(w)
        # 占领军这一回合已经交代完，下回合必须能继续行动，而不是白挂一个回合。
        self.assertEqual(t.call(w, Q, 'move', aid=atk, x=2, y=2).status, 'effective')

    def test_sweep_releases_a_unit_whose_only_foe_left_the_tile(self):
        """清扫覆盖的另一半：格上已无活敌军的 engaged 军队就地解除交战。"""
        t, w = self.board(('scenario:qc', Q, 1, 1), ('scenario:cc', C, 2, 1))
        q = w.military.seed(Q, 'native:infantry', 1, 1)[0]
        dfn = w.military.seed(C, 'native:infantry', 2, 1)[0]
        t.declare(w)
        w.military.attack(Q, [q.id], 2, 1)
        w.military.retreat(C, dfn.id, 3, 1)                   # 守军提交撤退
        w.military.withdrawals()                              # 结算时撤走
        self.assertTrue(self.unit(w, Q, q.id).engaged)         # 攻方仍挂着交战
        self.assertEqual(w.military.clear_disengaged(), ((Q, q.id),))
        self.assertFalse(self.unit(w, Q, q.id).engaged)
        self.assert_no_phantom_engagement(w)

    def test_elimination_stops_wars_on_owned_land_but_not_barbarian_fights(self):
        """亡国当场停战，但只清站在国有地上的：打野人的仗不是「战争」。"""
        t, w = self.board(('scenario:qc', Q, 1, 1), ('scenario:cc', C, 2, 1), ('scenario:zc', Z, 5, 5))
        besieger = self.unit(w, Q, w.military.seed(Q, 'native:infantry', 2, 1)[0].id)   # 站在楚地上
        squatter = self.unit(w, Q, w.military.seed(Q, 'native:infantry', 6, 6)[0].id)   # 无主荒地
        w.military._guard(6, 6)
        guard = next(u for u in w.units if u.owner == '野人' and (u.x, u.y) == (6, 6))
        for unit, seq in ((besieger, 1), (squatter, 2), (guard, 3)): unit.update(engaged=True, engage_seq=seq)
        w.military.disengage([Q])
        self.assertFalse(besieger.engaged, '国有地上的军队应当停战')
        self.assertTrue(squatter.engaged, '无主荒地上的清野战不该被亡国停战掐掉')
        self.assertTrue(guard.engaged)


if __name__ == '__main__':
    unittest.main()
