"""Investment execution and the old RL stack's no-hard-legality-mask contract."""
import unittest
from unittest.mock import patch

from tests.test_economy_bc import cfg,torch
from host import GameHost
from ruleai.v11plus.ledger import Ledger


@unittest.skipIf(torch is None,'Optional training dependencies')
class ProjectChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):torch.set_num_threads(1)

    def planner(self):
        from rl.economy.projects import InvestmentPlanner
        h=GameHost.new(cfg());p=h.player(h.actors()[0]);ledger=Ledger(p)
        ledger.do('plan',text='完整投资项目')
        return h,InvestmentPlanner(ledger,h.clients[p.actor].state)

    def test_no_formal_legality_mask_but_padding_is_masked(self):
        from rl.economy.model import Policy,collate
        h,p=self.planner();e=p.encode();model=Policy(action_mode='projects')
        batch=collate([e])
        before=model(batch)[0].detach()
        self.assertTrue((~batch['legal']).any())
        self.assertTrue((before[~batch['legal']]>-1e8).all())
        batch['legal']=~batch['legal']
        torch.testing.assert_close(model(batch)[0],before)
        batch['candidate_mask'][0,-1]=False
        self.assertEqual(float(model(batch)[0][0,-1].detach()),-1e9)
        p.finish()

    def test_missing_recruit_materials_financed_in_one_selected_project(self):
        h,p=self.planner();site=p.sites['sites'][0]['id']
        h.world.buildings.seed(site,'native:barracks')
        for resource in ('native:food','native:equipment'):
            h.world.resources._stock(p.player.actor,resource).set_amount(0)
        p.ledger._queries.clear();before=h.runtime.save_mod_state(h.world)
        e=p.encode();self.assertEqual(h.runtime.save_mod_state(h.world),before)
        i=e['actions'].index(('recruit',dict(site=site,kind='native:infantry',count=1)))
        self.assertTrue(e['legal'][i]);self.assertTrue(p.execute(e,i))
        self.assertTrue(any(r['action']=='buy' for r in p.ledger.acts))
        self.assertEqual(p.ledger.acts[-1]['action'],'recruit')
        self.assertTrue(all(r['ok'] for r in p.ledger.acts));p.finish()

    def test_rejected_project_remains_selectable_and_exposes_attempt(self):
        h,p=self.planner();e=p.encode()
        i=next(i for i,(a,k) in enumerate(e['actions']) if a=='recruit')
        self.assertFalse(e['legal'][i]);before=h.runtime.save_mod_state(h.world)
        self.assertFalse(p.execute(e,i))
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        action,args=e['actions'][i]
        after=p.encode();self.assertEqual(after['actions'],e['actions'])
        self.assertEqual(p.ledger.project_attempts[(action,args['site'],args['kind'])],1);p.finish()

    def test_finish_policy_is_not_secretly_given_troops_or_buildings(self):
        from rl.economy.projects import LearnedProjects
        class Finish(torch.nn.Module):
            def forward(self,b):
                x=torch.zeros_like(b['limits']);x[:,0]=1
                return x,x
        h=GameHost.new(cfg())
        for a,c in list(h.clients.items()):h.clients[a]=LearnedProjects(state=c.state,model=Finish())
        for _ in range(4):h.cycle()
        for a,c in h.clients.items():
            facts=h.player(a).observe()['facts']
            self.assertFalse(any(u['owner']==a for u in facts['army']))
            self.assertEqual(len(facts['building_sites']['instances']),1)
            self.assertEqual(c.project_failures,0)
