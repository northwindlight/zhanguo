"""Global grouping and explicit budget fallback over public observations."""
import unittest
from unittest.mock import patch
from ruleai.v11plus import RuleState
from ruleai.v11plus import grouping
from ruleai.v11plus.economy import supply_capacity


class TestRuleStrategy(unittest.TestCase):
    def setup(self):
        armies=[{'id':i+1,'kind':'native:infantry','x':x,'y':0,'owner':'scenario:qin','engaged':False}
                for i,x in enumerate((0,1,8,9))]
        view=[{'position':[x,y],'owner':None,'terrain':'native:plain'} for x in range(10) for y in range(2)]
        catalog={'native:infantry':{'move_cost':{'native:plain':1}}}
        knowledge={'view':view,'allies':{'scenario:qin'},'unit_catalog':catalog}
        return armies,view,catalog,knowledge

    def test_global_groups_use_nearby_pairs(self):
        armies,_,_,knowledge=self.setup()
        targets=[grouping.Candidate((0,1),2),grouping.Candidate((9,1),2)]
        assignments,exact=grouping._solve(knowledge,targets,armies,20000,2)
        self.assertTrue(exact)
        self.assertEqual(assignments,{1:(0,1),2:(0,1),3:(9,1),4:(9,1)})

    def test_budget_fallback_assigns_every_unit_and_persists_honest_flag(self):
        armies,view,catalog,_=self.setup()
        class PublicPlayer:
            actor='scenario:qin'
            def call_api(self,capability=None,method=None,arguments=None,action_label=None,**extra):
                if (capability,method)==('native:army','unit_catalog'):return catalog
                raise AssertionError((capability,method))
        obs={'actor':'scenario:qin','facts':{'view':view,'army':armies,'diplomacy':{'alliances':[],'wars':[]}}}
        state=RuleState()
        with patch.object(grouping,'V11_SOLVER_NODE_CAP',0):
            grouping.regroup(PublicPlayer(),obs,state,armies,forecast=lambda p,c,u: {"known":True,"winnable":len(u)>=2})
        self.assertEqual(set(state.targets),{1,2,3,4});self.assertFalse(state.exact)
        self.assertEqual(RuleState.from_save(state.to_save()),state)

    def test_supply_capacity_comes_from_the_published_building_catalog(self):
        catalog={'native:supply-factory':{'outputs':{'native:supply':3}},
                 'native:farm':{'outputs':{'native:food':1}}}
        buildings=[{'kind':'native:supply-factory','remaining':0},
                   {'kind':'native:supply-factory','remaining':0},
                   {'kind':'native:supply-factory','remaining':1},
                   {'kind':'native:farm','remaining':0}]
        self.assertEqual(supply_capacity(catalog,buildings),6)
        self.assertEqual(supply_capacity({},buildings),0)


if __name__=='__main__':unittest.main()
