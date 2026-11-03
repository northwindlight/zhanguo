"""DAgger purity, PPO identity/credit, and measured stage/rollback gates."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_economy_bc import cfg,torch
from host import GameHost
from ruleai.v11plus.ledger import Ledger


@unittest.skipIf(torch is None,'Optional training dependencies')
class OnlineChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):torch.set_num_threads(1)

    def encoded(self):
        from rl.economy.projects import InvestmentPlanner
        h=GameHost.new(cfg());p=h.player(h.actors()[0]);l=Ledger(p)
        l.do('plan',text='独立师生查询');planner=InvestmentPlanner(l,h.clients[p.actor].state)
        return h,planner,planner.encode()

    def test_dagger_oracle_does_not_execute_actions_or_change_memory(self):
        from rl.economy.online import expert_index
        h,p,e=self.encoded();before=h.runtime.save_mod_state(h.world)
        acts=copy.deepcopy(p.ledger.acts);memory=copy.deepcopy(p.state.market);callback=p.ledger.on_result
        index=expert_index(p,e)
        self.assertNotEqual(e['actions'][index][0],'finish')
        self.assertTrue(e['legal'][index])
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        self.assertEqual(p.ledger.acts,acts);self.assertEqual(p.state.market,memory)
        self.assertIs(p.ledger.on_result,callback);p.finish()

    def test_dagger_deduplicates_identical_noop_rejections_without_teacher_execution(self):
        from rl.economy.online import OnlineClient
        class Invalid(torch.nn.Module):
            def forward(self,batch,**kwargs):
                logits=torch.full_like(batch['limits'],-1000.)
                index=(batch['candidates'][:,:,7]>0).float().argmax(-1)
                logits[torch.arange(len(index)),index]=0
                return logits,torch.zeros(len(index)),None
        h=GameHost.new(cfg());actor=h.actors()[0];ledger=Ledger(h.player(actor))
        ledger.do('plan',text='无副作用拒绝只记录一次')
        c=OnlineClient(state=h.clients[actor].state,model=Invalid(),mode='dagger')
        c.run_economy(ledger)
        self.assertEqual(c.project_failures,1);self.assertEqual(len(c.rows),1)
        self.assertFalse(any(r['action']in ('build','recruit') for r in ledger.acts))

    def test_returns_never_mix_countries(self):
        from rl.economy.online import monte_carlo
        a=[dict(reward=.2),dict(reward=.3)];b=[dict(reward=5.),dict(reward=7.)]
        result=monte_carlo([a,b])
        self.assertEqual([s['return']for s in result],[.5,.3,12.,7.])

    def test_ppo_noop_reuse_preserves_every_sample_and_world(self):
        from rl.economy.model import Policy
        from rl.economy.online import OnlineClient,with_critic
        torch.manual_seed(123);model=with_critic(Policy(action_mode='projects')).eval()
        original=model.forward;calls=[]
        def biased(batch,**kwargs):
            calls.append(1)
            logits,value,execution=original(batch,**kwargs)
            logits=logits*0-1000
            index=(batch['candidates'][:,:,7]>0).float().argmax(-1)
            logits[torch.arange(len(index)),index]=0
            return logits,value,execution
        model.forward=biased
        outcomes=[];counts=[]
        for reuse in (False,True):
            h=GameHost.new(cfg());actor=h.actors()[0];ledger=Ledger(h.player(actor))
            ledger.do('plan',text='相同PPO采样')
            c=OnlineClient(state=h.clients[actor].state,model=model,mode='ppo',reuse_noop=reuse)
            calls.clear();torch.manual_seed(777);c.run_economy(ledger)
            counts.append(len(calls))
            outcomes.append((h.runtime.save_mod_state(h.world),ledger.acts,c.project_failures,c.truncated,
                [(s['action'],s['logp'],s['value'])for s in c.steps]))
        self.assertEqual(outcomes[0],outcomes[1]);self.assertEqual(counts,[96,1])
        self.assertEqual(len(outcomes[1][-1]),96)

    def test_critic_transfer_preserves_policy_and_ppo_has_actual_gradients(self):
        from rl.economy.model import Policy,collate
        from rl.economy.online import with_critic,ppo_update
        from rl.economy.projects import sample
        h,p,e=self.encoded();torch.manual_seed(1)
        actor=Policy(action_mode='projects').eval();model=with_critic(actor)
        row=sample(e,0);b=collate([row])
        with torch.no_grad():
            original=actor(b)[0];logits,value,_=model(b,return_value=True)
            torch.testing.assert_close(logits,original)
            self.assertEqual(float(value[0]),0.)
            logp=float(logits.log_softmax(-1)[0,0])
            same=model(collate([row,row]),return_value=True)[0].log_softmax(-1)[:,0]
            torch.testing.assert_close((same-logp).exp(),torch.ones(2),atol=1e-5,rtol=1e-5)
        streams=[[dict(row=row,action=0,logp=logp,value=0.,reward=x,period=0)]for x in (.1,.2,.3,.4)]
        result=ppo_update(model,streams,actor,epochs=1)
        self.assertGreater(result['updates'],0)
        self.assertIsNotNone(model.score[-1].weight.grad)
        self.assertGreater(float(model.critic[-1].weight.detach().abs().sum()),0.)
        p.finish()

    def test_value_encoder_detach_keeps_actor_fixed_during_value_only_update(self):
        from rl.economy.model import Policy,collate
        from rl.economy.online import with_critic,ppo_update
        from rl.economy.projects import sample
        h,p,e=self.encoded();torch.manual_seed(31)
        anchor=Policy(action_mode='projects').eval();model=with_critic(anchor)
        row=sample(e,0)
        with torch.no_grad():
            logits,value,_=model(collate([row]),return_value=True)
            logp=float(logits.log_softmax(-1)[0,0])
        before={k:v.clone()for k,v in model.state_dict().items()}
        streams=[[dict(row=row,action=0,logp=logp,value=0.,reward=x,period=20)]for x in (.1,.2,.3,.4)]
        result=ppo_update(model,streams,anchor,epochs=2,policy_coef=0.,execution_coef=0.,anchor_coef=0.,
            entropy_coef=0.,detach_value_encoder=True,weight_decay=0.)
        self.assertGreater(result['updates'],0)
        for k,v in model.state_dict().items():
            if not k.startswith('critic.'):self.assertTrue(torch.equal(v,before[k]),k)
        self.assertFalse(torch.equal(model.critic[-1].weight,before['critic.2.weight']))
        p.finish()

    def test_gradient_component_probe_measures_shared_encoder_without_accumulating_gradients(self):
        from rl.economy.model import Policy,collate
        from rl.economy.online import with_critic
        from rl.economy.ppo_diagnostics import component_gradients
        h,p,e=self.encoded();model=with_critic(Policy(action_mode='projects'))
        with torch.no_grad():model.critic[-1].weight.fill_(.1)
        b=collate([e]);logits,value,_=model(b,return_value=True)
        report=component_gradients(model,dict(policy=logits[:,0].mean(),value=value.square().mean()))
        self.assertGreater(report['value']['shared_encoder'],0.)
        self.assertTrue(all(q.grad is None for q in model.parameters()))
        logits,value,_=model(b,return_value=True,detach_value_encoder=True)
        detached=component_gradients(model,dict(policy=logits[:,0].mean(),value=value.square().mean()))
        self.assertEqual(detached['value']['shared_encoder'],0.)
        self.assertGreater(detached['value']['critic'],0.)
        p.finish()

    def gate(self,consumption=100.,tail=10.,**changes):
        rows=[dict(actor=f'scenario:n{i}',seed=7101,swap=False,
                   consumption=consumption/2,tail_consumption=tail/2) for i in (1,3)]
        result=dict(early_rate=1.,failure_rate=0.,truncated=0,shortfall=0,
                    consumption=consumption,tail_consumption=tail,countries=rows)
        result.update(changes)
        return result

    def test_measured_stage_selection_and_regression_rejection(self):
        from rl.economy.overnight import choose_stage,accept
        good=self.gate()
        self.assertEqual(choose_stage(self.gate(early_rate=.75),good,1.),'bc')
        self.assertEqual(choose_stage(good,good,.7),'dagger')
        self.assertEqual(choose_stage(good,good,.95),'ppo')
        self.assertTrue(accept(good,good)[0])
        self.assertFalse(accept(self.gate(early_rate=.75),good)[0])
        self.assertFalse(accept(self.gate(consumption=95.),good)[0])
        self.assertFalse(accept(self.gate(consumption=99.5),good)[0])
        self.assertFalse(accept(self.gate(failure_rate=.1),good)[0])
        collapsed=self.gate()
        collapsed['countries']=copy.deepcopy(good['countries'])
        collapsed['countries'][0]['consumption']=1.
        self.assertFalse(accept(collapsed,good)[0])

    def test_training_deadline_checked_between_batches(self):
        from rl.economy.online import checked_batches,Deadline
        with self.assertRaises(Deadline):next(checked_batches([object()],0))

    def test_all_production_curriculum_worlds_have_at_least_150_turns(self):
        from rl.economy.overnight import curriculum_turns
        self.assertEqual([curriculum_turns(i) for i in (1,3,4,7,8,100)],[150,150,200,200,250,250])
        self.assertTrue(all(curriculum_turns(i)>=150 for i in range(1,101)))

    def test_storage_budget_reserves_room_without_deleting_evidence(self):
        from rl.economy.overnight import storage_guard
        from rl.economy.online import Deadline
        with TemporaryDirectory() as directory:
            root=Path(directory);model=root/'best.pt';model.write_bytes(b'x'*64)
            self.assertEqual(storage_guard(root,budget=100,headroom=16,reserve=0)['run_bytes'],64)
            with self.assertRaises(Deadline):storage_guard(root,budget=70,headroom=16,reserve=0)
            self.assertEqual(model.read_bytes(),b'x'*64)
