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
    initial=tuple(tuple((50,h,False,cover,0) for h in row) for row in hp)
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

    def test_multi_unit_and_partial_retreat_cover_use_fractional_python_fallback(self):
        if solver.FAST is None:self.skipTest('Optional C extension not built')
        for hp,cover in [(((100,100),(80,)),100),(((100,),(100,)),50)]:
            with self.subTest(hp=hp,cover=cover):
                result=solver.solve(*example(hp,cover),max_states=1,max_rounds=1)
                self.assertEqual(result['backend'],'python')
                self.assertIn('equal',result['fallback'])

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

    def test_research_factory_state_restores_and_decisions_match(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        host=GameHost.new(self.cfg());actor='research:q'
        x,y=host.world.territory.owned(actor)[0].tile.position
        unit=host.world.military.seed(actor,'native:infantry',x,y)[0]
        with TemporaryDirectory() as folder:
            file=Path(folder)/'synthetic.json';host.save(file);loaded=GameHost.load(file,self.cfg())
            self.assertTrue(loaded.clients[actor].research)
            self.assertEqual(host.to_save(),loaded.to_save())
            self.assertEqual(host.clients[actor]._research('assess',[unit.id],x,y),loaded.clients[actor]._research('assess',[unit.id],x,y))


if __name__=='__main__':unittest.main()
