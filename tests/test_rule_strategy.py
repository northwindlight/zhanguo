"""Global grouping and explicit budget fallback over public observations."""
import unittest
from unittest.mock import patch
from ruleai.v11plus import RuleState
from ruleai.v11plus import grouping


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
        assignments,exact=grouping._solve(knowledge,'scenario:qin',targets,armies,None,{},20000,2)
        self.assertTrue(exact)
        self.assertEqual(assignments,{1:(0,1),2:(0,1),3:(9,1),4:(9,1)})

    def test_budget_fallback_assigns_every_unit_and_persists_honest_flag(self):
        armies,view,catalog,_=self.setup()
        class PublicPlayer:
            actor='scenario:qin'
            def query(self,name,args=None):
                if name=='unit_catalog':return catalog
                if name=='battle_estimate':return {'known':True,'winnable':len(args['ids'])>=2}
                raise AssertionError(name)
        obs={'actor':'scenario:qin','facts':{'view':view,'army':armies,'diplomacy':{'alliances':[],'wars':[]}}}
        state=RuleState()
        with patch.object(grouping,'V11_SOLVER_NODE_CAP',0):
            grouping.regroup(PublicPlayer(),obs,state,armies)
        self.assertEqual(set(state.targets),{1,2,3,4});self.assertFalse(state.exact)
        self.assertEqual(RuleState.from_save(state.to_save()),state)


if __name__=='__main__':unittest.main()
