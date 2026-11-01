"""Meaningful bound-API, trajectory and learning checks for the economic fork."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from host import GameHost
from ruleai.v11plus.ledger import Ledger
from ruleai.v13.entry import RuleState
from ruleai.v13plus.economy import Planner


def cfg():
    return {'map_size':16,'seed':73,'nations':[dict(id=f'scenario:n{i}',name=str(i),
        client='rule',rule_ai='v13plus',rule_strategy=('balanced','merchant','buffer','integrated')[i])
        for i in range(4)]}


class TeacherChecks(unittest.TestCase):
    def test_missing_recruit_materials_remain_fundable(self):
        h=GameHost.new(cfg());p=h.player(h.actors()[0]);l=Ledger(p)
        l.do('plan',text='按完整征兵配方备料')
        site=p.call_api('native:buildings','sites')['sites'][0]
        h.world.buildings.seed(site['id'],'native:barracks')
        for resource in ('native:food','native:equipment'):
            h.world.resources._stock(p.actor,resource).set_amount(0)
        planner=Planner(l,h.clients[p.actor].state)
        self.assertTrue(planner.can_recruit(site,'native:infantry'))
        planner.recruit();planner.finish()
        self.assertTrue(all(r['ok'] for r in l.acts))
        self.assertEqual(sum(r['action']=='recruit' for r in l.acts),1)

    def test_prior_grid_failure_blocks_recruitment_before_buying_its_recipe(self):
        h=GameHost.new(cfg());p=h.player(h.actors()[0]);l=Ledger(p)
        l.do('plan',text='先修复供电再征兵')
        site=p.call_api('native:buildings','sites')['sites'][0]['id']
        h.world.buildings.seed(site,'native:barracks')
        h.world.military._grid_short[p.actor]=True
        planner=Planner(l,h.clients[p.actor].state)
        self.assertFalse(planner.can_recruit(planner.sites['sites'][0],'native:infantry'))
        planner.recruit()
        self.assertEqual([r['action'] for r in l.acts],['plan'])
        planner.invest();planner.finish()
        self.assertTrue(all(r['ok'] for r in l.acts))
        self.assertTrue(any(r['action']=='build' for r in l.acts))

    def test_financing_is_pure_and_complete_then_never_sells_bought_inputs(self):
        h=GameHost.new(cfg());p=h.player(h.actors()[0]);l=Ledger(p)
        l.do('plan',text='先保完整材料再投资')
        h.world.resources._stock(p.actor,'native:gold').set_amount(0)
        h.world.resources._stock(p.actor,'native:food').set_amount(300)
        l._queries.clear();planner=Planner(l,RuleState())
        targets={'native:gold':20,'native:ore':30}
        before=h.runtime.save_mod_state(h.world)
        self.assertIsNotNone(planner.funding(targets))
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        self.assertTrue(planner.fund(targets))
        self.assertGreaterEqual(h.world.resources.balance(p.actor,'native:gold'),20)
        self.assertGreaterEqual(h.world.resources.balance(p.actor,'native:ore'),30)
        planner.finish()
        buys={r['data']['resource'] for r in l.acts if r['action']=='buy'}
        sells={r['data']['resource'] for r in l.acts if r['action']=='sell'}
        self.assertFalse(buys&sells)
        self.assertTrue(all(r['ok'] for r in l.acts))

    def test_unavailable_package_and_used_quota_are_not_candidates(self):
        h=GameHost.new(cfg());p=h.player(h.actors()[0]);l=Ledger(p)
        l.do('plan',text='使用正式容量和整单库存')
        planner=Planner(l,RuleState());before=h.runtime.save_mod_state(h.world)
        self.assertIsNone(planner.funding({'native:ore':1000000}))
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        site=planner.sites['sites'][0]
        self.assertTrue(l.do('build',site=site['id'],kind='native:wood-power-plant'))
        planner.sites=p.call_api('native:buildings','sites')
        self.assertFalse(any(s['id']==site['id'] for s,k,c in planner.candidates()))
        planner.finish()

    def test_teacher_roundtrip_keeps_version_profile_and_actual_continuation(self):
        h=GameHost.new(cfg())
        for _ in range(3):h.cycle()
        with TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.json';h.save(path)
            other=GameHost.load(path,cfg());self.assertEqual(h.to_save(),other.to_save())
            h.cycle();other.cycle();self.assertEqual(h.to_save(),other.to_save())


try:
    import torch
    import numpy as np
    from rl.economy.adapter import observation,label,decode
    from rl.economy.client import RecordingTeacher,CorrectingTeacher
    from rl.economy.data import Writer,Dataset
    from rl.economy.model import Policy,collate
except ImportError:
    torch=None


@unittest.skipIf(torch is None,'Optional economic training dependencies are not installed')
class LearningChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):torch.set_num_threads(1)

    def test_recording_preserves_full_multicountry_trajectory_and_stop_labels(self):
        h=GameHost.new(cfg());control=GameHost.new(cfg());rows=[]
        for actor,c in list(h.clients.items()):
            h.clients[actor]=RecordingTeacher(state=c.state,sink=rows.append)
        for _ in range(4):h.cycle();control.cycle()
        # Independent new hosts intentionally have different session UUIDs.
        self.assertEqual(h.runtime.save_mod_state(h.world),control.runtime.save_mod_state(control.world))
        self.assertEqual({a:c.to_save() for a,c in h.clients.items()},
                         {a:c.to_save() for a,c in control.clients.items()})
        self.assertEqual(sum(r['target']==0 for r in rows),16)
        self.assertTrue(all(r['legal'][r['target']] for r in rows))
        self.assertTrue(all('actions' not in r for r in rows))

    def test_bound_observation_is_pure_and_does_not_require_teacher_for_quantities(self):
        h=GameHost.new(cfg());p=h.player(h.actors()[0]);l=Ledger(p);l.do('plan',text='独立候选')
        before=h.runtime.save_mod_state(h.world)
        encoded=observation(l,h.clients[p.actor].state)
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        i,_=label(encoded,'buy',dict(resource='native:wood',quantity=137))
        action,args=decode(encoded,i,np.log1p(137))
        self.assertEqual((action,args),('buy',dict(resource='native:wood',quantity=137)))
        self.assertTrue(l.do(action,**args))
        after=observation(l,h.clients[p.actor].state)
        with self.assertRaises(ValueError):label(after,'sell',dict(resource='native:wood',quantity=1))
        # Quoted but unaffordable/structurally unavailable projects remain visible.
        self.assertTrue(any(not ok and a[0]=='build' for ok,a in zip(encoded['legal'],encoded['actions'])))

    def test_corrective_collection_labels_only_teacher_turns_after_student_waits(self):
        class WaitingPolicy(torch.nn.Module):
            def forward(self,batch):
                logits=torch.zeros_like(batch['limits']);logits[:,0]=1
                return logits,torch.zeros_like(logits)
        h=GameHost.new(cfg());rows=[];model=WaitingPolicy()
        for actor,c in list(h.clients.items()):
            h.clients[actor]=CorrectingTeacher(state=c.state,sink=rows.append,
                model=model,teacher_probability=.75,seed=actor)
        for _ in range(8):h.cycle()
        teacher=sum(c.teacher_turns for c in h.clients.values())
        student=sum(c.student_turns for c in h.clients.values())
        self.assertGreater(student,0);self.assertEqual(teacher+student,32)
        self.assertEqual(sum(r['target']==0 for r in rows),teacher)
        self.assertTrue(all(r['legal'][r['target']] for r in rows))
        self.assertTrue(any(r['candidates'][r['target'],7]>0 for r in rows))

    def test_padding_invariance_real_gradients_and_compressed_data_roundtrip(self):
        torch.manual_seed(73)
        h=GameHost.new(cfg());p=h.player(h.actors()[0]);l=Ledger(p);l.do('plan',text='训练验证')
        e=observation(l,h.clients[p.actor].state);e.pop('actions');e.update(target=0,quantity=0.)
        more={**e,'context':np.concatenate((e['context'],e['context'])),
            'candidates':np.concatenate((e['candidates'],e['candidates'])),
            'legal':np.concatenate((e['legal'],e['legal'])),
            'limits':np.concatenate((e['limits'],e['limits']))}
        model=Policy();model.eval()
        self.assertGreater(sum(p.numel() for p in model.parameters()),1000000)
        with torch.inference_mode():
            a=model(collate([e]))[0][0];b=model(collate([e,more]))[0][0,:len(a)]
        torch.testing.assert_close(a,b,atol=1e-5,rtol=1e-5)
        optimizer=torch.optim.AdamW(model.parameters(),lr=.001)
        batch=collate([e]);initial=float(torch.nn.functional.cross_entropy(model(batch)[0],batch['target']).detach())
        for _ in range(5):
            optimizer.zero_grad();loss=torch.nn.functional.cross_entropy(model(batch)[0],batch['target'])
            loss.backward();optimizer.step()
        self.assertIsNotNone(model.context[0].weight.grad)
        self.assertLess(float(loss.detach()),initial)
        with TemporaryDirectory() as temp:
            path=Path(temp)/'world-73.npz';w=Writer();w.add(e);w.save(path,dict(seed=73))
            d=Dataset([path]);self.assertEqual(len(d),1)
            np.testing.assert_allclose(d[0]['context'],e['context'],atol=.001)
            np.testing.assert_array_equal(d[0]['legal'],e['legal'])


if __name__=='__main__':unittest.main()
