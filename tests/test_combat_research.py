"""Research probabilities, current overflow damage and private client grants."""
from copy import deepcopy
from itertools import product
import unittest
from unittest.mock import patch

from host import GameHost
from mods.experimental.combat_research import solver
from mods.native.army.objects import Unit
from mods.native.army.domain_objects import Military
from mods.native.army.parameters import COMBAT_DIE_MOD
from ruleai.v11plus import combat


def example(hp=((5,100),(100,)),cover=100):
    order=('left','right');enemies={'left':('right',),'right':('left',)}
    rows=[]
    for ri,row in enumerate(hp):
        # ★ `cover` 可以是**标量**（整方同一个掩护）或**每方一个元组**（逐兵掩护）
        covers=[cover]*len(row) if isinstance(cover,(int,float)) else list(cover[ri])
        rows.append(tuple((50,h,False,c,0) for h,c in zip(row,covers)))
    initial=tuple(rows)
    def power(sig):
        total=sum(k for k,r in sig)
        return tuple(max(1,total*(100+COMBAT_DIE_MOD[d])//100) for d in sorted(COMBAT_DIE_MOD))
    prototype=Unit('native:infantry',100,0,0,'left',1,1,'test')
    def spread(row,hits):
        units=[]
        for k,h,r,c in row:
            unit=deepcopy(prototype);unit.hp=h;unit.retreat_cover=c;units.append(unit)
        Military.spread_damage(hits,units)
        return tuple((row[i][0],u.hp,row[i][2],row[i][3]) for i,u in enumerate(units) if u.alive)
    return order,enemies,initial,power,spread


class TestCombatResearch(unittest.TestCase):
    def cfg(self,experimental=True):
        return {'mods':'mods.experimental.toml' if experimental else 'mods.toml',
                'map_size':12,'seed':71,'nations':[{'id':'research:q','name':'秦','client':'rule'}]}

    def test_current_spread_transfers_overflow_and_caps_losses_at_initial_hp(self):
        args=example();spread=args[-1];row=tuple(u[:4] for u in args[2][0])
        self.assertEqual(spread(row,50),((50,55,False,100),))
        # Old uniform split wasted 20 hp on the first unit, leaving the second at 75.
        odds=solver.solve(*args,use_fast=False)
        self.assertLessEqual(odds['expected_losses_partial']['left'],105)
        self.assertAlmostEqual(odds['p_win']['left']+odds['p_lose']['left']+odds['p_draw']+odds['unresolved'],1)

    def test_c_and_python_have_identical_complete_single_unit_integer_distributions(self):
        if solver.FAST is None:self.skipTest('Optional C extension not built')
        for hp,cover,states,rounds in [(((5,),(100,)),100,4000,64),
                                      (((100,),(80,)),100,4000,64),
                                      (((100,),(100,)),100,1,1),
                                      (((100,),(100,)),100,4000,4)]:
            with self.subTest(hp=hp,cover=cover,states=states):
                args=example(hp,cover);py=solver.solve(*args,max_states=states,max_rounds=rounds,use_fast=False)
                native=solver.solve(*args,max_states=states,max_rounds=rounds)
                self.assertEqual(native['backend'],'c')
                for key in py:
                    if key not in ('backend','fallback'):self.assertEqual(native[key],py[key],key)

    def test_c_and_python_agree_on_multi_unit_and_fractional_cover(self):
        """★★ **多支军 / 分数掩护**也走 C，且与 Python **逐位相同**。

        ★★ 2026-10-24 改：以前这两类**只能退回 Python**（C 的摊分是"整数均分 +
          余数按序号"⇒ 与 `equal_allocation` 的**水位填充**不是一回事）⇒
          **RL 的真实战斗（每方多支）根本用不上 C 扩展** ⇒ 训练慢一个数量级。
          ⇒ 现在 C 那边复刻了 Python 的摊分（血量改 double、掩护按比例）⇒
          这条闸门钉住"**多支军也走 C，且与 Python 逐位相同**"。
        """
        if solver.FAST is None:self.skipTest('Optional C extension not built')
        cases=[(((100,100),(80,)),100),          # 多支军
               (((100,),(100,)),50),              # 分数掩护（单支）
               (((100,60),(80,)),((100,50),(80,)))]  # 多支军 + 逐兵掩护
        for hp,cover in cases:
            with self.subTest(hp=hp,cover=cover):
                args=example(hp,cover)
                py=solver.solve(*args,max_states=4000,max_rounds=64,use_fast=False)
                native=solver.solve(*args,max_states=4000,max_rounds=64)
                self.assertEqual(native['backend'],'c',
                                 f'多支军/分数掩护没走 C ⇒ 训练用不上扩展：{native["fallback"]}')
                for key in py:
                    if key not in ('backend','fallback'):self.assertEqual(native[key],py[key],key)

    def test_symmetric_one_round_matches_exhaustive_dice_enumeration(self):
        odds=solver.solve(*example(((50,),(50,))),max_rounds=1,use_fast=False)
        wins=losses=draws=unresolved=0
        for left,right in product(COMBAT_DIE_MOD.values(),repeat=2):
            l=max(1,50*(100+left)//100);r=max(1,50*(100+right)//100)
            if l>=50 and r<50:wins+=1
            elif r>=50 and l<50:losses+=1
            elif l>=50 and r>=50:draws+=1
            else:unresolved+=1
        self.assertAlmostEqual(odds['p_win']['left'],wins/36)
        self.assertAlmostEqual(odds['p_lose']['left'],losses/36)
        self.assertAlmostEqual(odds['p_draw'],draws/36)
        self.assertAlmostEqual(odds['unresolved'],unresolved/36)

    def test_missing_or_failed_extension_reports_python_fallback(self):
        with patch.object(solver,'FAST',None),patch.object(solver,'FAST_ERROR','not compiled'):
            result=solver.solve(*example(((50,),(50,))))
            self.assertEqual((result['backend'],result['fallback']),('python','not compiled'))
        class Broken:
            def assess(self,*args):raise RuntimeError('synthetic failure')
        with patch.object(solver,'FAST',Broken()):
            result=solver.solve(*example(((50,),(50,))))
            self.assertEqual(result['backend'],'python');self.assertIn('synthetic failure',result['fallback'])

    def test_only_experimental_rule_client_receives_private_research_capability(self):
        native=GameHost.new(self.cfg(False));host=GameHost.new(self.cfg())
        self.assertNotIn('research:combat-probabilities',native.runtime.capabilities)
        self.assertFalse(getattr(native.clients['research:q'],'research',False))
        client=host.clients['research:q'];self.assertTrue(client.research)
        self.assertNotIn('combat',host.runtime.distribution['ClientFactories']['llm'].get('Services',{}))
        self.assertNotIn('combat',host.runtime.distribution['Session']['Services'])
        self.assertFalse(any('probab' in s['function']['name'] for s in host.player('research:q').actions()))
        with self.assertRaises(ValueError):host.player('research:q').query('combat_probabilities')
        self.assertEqual(host.runtime.units['experimental:combat-research'].mod.tools(),())

    def test_forecast_uses_visible_units_and_does_not_change_game_or_rng(self):
        host=GameHost.new(self.cfg());actor='research:q'
        x,y=host.world.territory.owned(actor)[0].tile.position
        unit=host.world.military.seed(actor,'native:infantry',x,y)[0]
        client=host.clients[actor];before=deepcopy(host.runtime.save_mod_state(host.world))
        known=client._research('assess',[unit.id],x,y)
        self.assertTrue(known['known']);self.assertEqual(known['probabilities']['p_win'][actor],1)
        hidden=next((x,y) for x in range(12) for y in range(12) if not host.runtime.object_call(host.world,'native:vision','visible',actor,x,y))
        unknown=client._research('assess',[unit.id],*hidden)
        self.assertFalse(unknown['known']);self.assertIsNone(unknown['probabilities'])
        self.assertEqual(host.runtime.save_mod_state(host.world),before)
        # An injected forecast must replace the old public query rather than call it.
        with patch.object(host.player(actor),'query',side_effect=AssertionError('unused')):
            result=combat.assess(None,(x,y),[{'id':unit.id}],forecast=client.forecast)
        self.assertTrue(result['research'])

    def test_rule_cycle_uses_research_and_never_calls_public_estimate(self):
        from unittest.mock import Mock
        from tests.test_parliament import CouncilBackend
        host=GameHost.new(self.cfg());actor='research:q'
        x,y=host.world.territory.owned(actor)[0].tile.position
        host.world.military.seed(actor,'native:infantry',x,y,count=2)
        host.services[actor]['council']._review_backend=CouncilBackend([True])
        client=host.clients[actor];client._research=Mock(wraps=client._research)
        with patch.object(host.world.military,'battle_estimate',side_effect=AssertionError('public estimate unused')):
            host.cycle()
        self.assertTrue(client._research.called)
        self.assertEqual(host.clock(),1)

    def test_forecast_memoizes_identical_battles_but_recomputes_changed_ones(self):
        """★ 记忆化（2026-10-24）：同一场仗**只算一次**；局面变了**必须重算**。

        ★ 为什么要这条闸门：`assess` 是**纯函数**（输入定 ⇒ 输出定）⇒ 可以记。
          但**签名少进一项** ⇒ 概率**静默过期**（**不报错**）⇒ 这条钉住三件事：
          ① 同一签名**不重算**（省时间）；
          ② 换了进攻编成 ⇒ 签名变了 ⇒ **必须重算**（否则就是过期概率）；
          ③ 缓存里那份必须是**干净的**（调用方改返回值改不坏它）。
        ★ 顺带钉住"为什么缓存是**模块级**"：`runtime.derived` 只在读视图里缓存
          能力对象 ⇒ 每次调用都重造 `Forecast` ⇒ 挂实例上的缓存**当场就死**
          （实测 738 次调用**零命中**）。
        """
        from mods.experimental.combat_research import mod as CRM
        host=GameHost.new(self.cfg());actor='research:q'
        x,y=host.world.territory.owned(actor)[0].tile.position
        unit=host.world.military.seed(actor,'native:infantry',x,y)[0]
        client=host.clients[actor]
        calls={'n':0};real=CRM.solve
        def counting(*a,**kw):
            calls['n']+=1
            return real(*a,**kw)
        CRM._MEMO.clear()
        with patch.object(CRM,'solve',counting):
            first=client._research('assess',[unit.id],x,y)
            self.assertEqual(calls['n'],1,'第一次没真算')
            second=client._research('assess',[unit.id],x,y)
            self.assertEqual(calls['n'],1,'★ 同一场仗又算了一遍 ⇒ 记忆化没生效')
            self.assertEqual(first,second,'缓存命中返回的结果不一样')
            # ★ 调用方改返回值 ⇒ **缓存不能被改坏**。
            second['probabilities']['p_win'][actor]=999
            third=client._research('assess',[unit.id],x,y)
            self.assertEqual(calls['n'],1)
            self.assertNotEqual(third['probabilities']['p_win'][actor],999,
                                '★ 返回的不是副本 ⇒ 调用方改坏了缓存')
            self.assertTrue(CRM._MEMO,'缓存是空的 ⇒ 上面那些命中从哪来')
            for value in CRM._MEMO.values():
                self.assertNotEqual(value['probabilities']['p_win'].get(actor),999,
                                    '★ 缓存里那份被调用方改坏了')
            # ★ 换了进攻编成 ⇒ 签名变了 ⇒ **必须重算**（不然就是过期概率）。
            #   ★ 只断言"**重算了**"（`calls`），**不断言结果变了** ——
            #   这一局只有一个国家 ⇒ 打谁都是赢 ⇒ 重算后的结果**本来就该一样**
            #   （结果变了才是 bug：说明编成不影响胜负）。
            other=host.world.military.seed(actor,'native:infantry',x,y)[0]
            client._research('assess',[unit.id,other.id],x,y)
            self.assertEqual(calls['n'],2,'★ 换了编成却吃了旧缓存 ⇒ 概率静默过期')

    def test_research_factory_state_restores_and_decisions_match(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from mods.experimental.combat_research import mod as CRM
        host=GameHost.new(self.cfg());actor='research:q'
        x,y=host.world.territory.owned(actor)[0].tile.position
        unit=host.world.military.seed(actor,'native:infantry',x,y)[0]
        with TemporaryDirectory() as folder:
            file=Path(folder)/'synthetic.json';host.save(file);loaded=GameHost.load(file,self.cfg())
            self.assertTrue(loaded.clients[actor].research)
            self.assertEqual(host.to_save(),loaded.to_save())
            # ★ 清缓存 ⇒ 比的是"**两次真算**"（不清的话两边都吃缓存 ⇒ 这条就废了）
            CRM._MEMO.clear()
            self.assertEqual(host.clients[actor]._research('assess',[unit.id],x,y),loaded.clients[actor]._research('assess',[unit.id],x,y))


if __name__=='__main__':unittest.main()
