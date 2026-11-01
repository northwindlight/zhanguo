"""Investment-level BC: learned projects, deterministic v13plus cash/material service.

All owned-site/catalog projects remain selectable, even if not executable. Only
padding is masked by the network. Formal eligibility is a feature/auxiliary
training target, never a policy override. No teacher choice runs in the learner.
"""
from collections import Counter
import random

import torch

from ruleai.v11plus import apimap
from ruleai.v13.economy import operating_targets, reserve_cash, SUPPLY
from ruleai.v13plus.economy import Planner
from ruleai.v13.entry import RuleClient as BaseClient
from .adapter import observation, label, RESOURCES, scale
from .model import collate


class InvestmentPlanner(Planner):
    def refresh(self):
        self.sites=apimap.query(self.player,'building_sites')
        self.military=self.ledger.query('econ')['preview'].get('military',{})

    def package(self,action,args,choice):
        buildings=self.buildings();military=dict(self.military)
        if action=='build':
            if choice['price'] is None or choice['reason'] is not None:return None
            costs=choice['price']['costs']
            buildings=buildings+[dict(site=args['site'],kind=args['kind'],remaining=1)]
        else:
            if not all(r['met'] for r in choice['requirements'] if 'resource' not in r):return None
            costs=choice['costs']
            unit=apimap.query(self.player,'unit_catalog')[args['kind']]
            military['need']=military.get('need',0)+unit['supply']
        recipe=dict(costs)
        for key,value in operating_targets(self.catalog,buildings,military,self.prices,self.profile.reserve).items():
            recipe[key]=recipe.get(key,0)+value
        reserve=reserve_cash(self.catalog,self.buildings(),self.military,self.buy,self.profile)
        return recipe,reserve

    def encode(self):
        self.refresh()
        encoded=observation(self.ledger,self.state,include_trades=False)
        plans={}
        for site in self.sites['sites']:
            query=dict(zip(('x','y'),site['position']))
            plans[site['id']]={
                'build':{r['kind']:r for r in self.player.call_api('native:buildings','planning',query)['buildings']},
                'recruit':{r['kind']:r for r in self.player.call_api('native:army','planning',query)}}
        packages=[None]
        for i,(action,args) in enumerate(encoded['actions'][1:],1):
            package=self.package(action,args,plans[args['site']][action][args['kind']])
            funding=self.funding(*package) if package else None
            encoded['legal'][i]=funding is not None
            features=encoded['candidates'][i]
            # Formal funding is an auxiliary supervision target (legal), not an
            # observation feature; the candidate keeps its public availability.
            if package:
                recipe,reserve=package
                features[59:66]=[scale(recipe.get(r,0)) for r in RESOURCES]
            packages.append(package)
        # Energy totals are public catalog facts; attempts/reserve are
        # rule/training-derived and stay out of the observation.
        encoded['context'][0,85:87]=[
            scale(sum(self.catalog[b['kind']][k] for b in self.buildings()))
            for k in ('energy_output','energy_use')]
        encoded['packages']=packages
        return encoded

    def execute(self,encoded,index):
        action,args=encoded['actions'][index]
        package=encoded['packages'][index]
        if not hasattr(self.ledger,'project_attempts'):self.ledger.project_attempts=Counter()
        self.ledger.project_attempts[(action,args['site'],args['kind'])]+=1
        if package is None or not self.fund(*package):return False
        return self.ledger.do(action,**args)


def sample(encoded,index):
    return dict(context=encoded['context'],candidates=encoded['candidates'],
                legal=encoded['legal'],limits=encoded['limits'],target=index,quantity=0.)


class DemonstrationPlanner(InvestmentPlanner):
    def __init__(self,*args,sink,**kwargs):
        super().__init__(*args,**kwargs);self.sink=sink

    def execute_project(self,action,site,kind,recipe,reserve):
        # Observe BEFORE financing. Recompute from current formal quotes and
        # current troops, sharing exactly the learner's execution semantics.
        encoded=self.encode();args=dict(site=site['id'],kind=kind)
        if action=='recruit':args['count']=1
        index=encoded['actions'].index((action,args))
        if not encoded['legal'][index]:return False
        row=sample(encoded,index)
        if not self.execute(encoded,index):raise RuntimeError('Project preflight/execution diverged')
        self.sink(row)
        return True


class RecordingProjects(BaseClient):
    version='economy-project-teacher'
    def __init__(self,*args,sink,**kwargs):
        super().__init__(*args,**kwargs);self.sink=sink

    def run_economy(self,ledger):
        ledger.economy_market=ledger.query('market')
        planner=DemonstrationPlanner(ledger,self.state,sink=self.sink)
        try:
            planner.operations();planner.recruit();planner.invest()
            self.sink(sample(planner.encode(),0))
        finally:planner.finish()


class CorrectingProjects(RecordingProjects):
    version='economy-project-mixture'
    def __init__(self,*args,model,teacher_probability=.5,seed=0,**kwargs):
        super().__init__(*args,**kwargs)
        self.model=model;self.teacher_probability=teacher_probability
        self.rng=random.Random(seed);self.max_economy_actions=96
        self.truncated=0;self.economic_actions=Counter();self.project_failures=0
        self.teacher_turns=0;self.student_turns=0

    def run_economy(self,ledger):
        if self.rng.random()<self.teacher_probability:
            self.teacher_turns+=1;super().run_economy(ledger)
        else:
            self.student_turns+=1;LearnedProjects.run_economy(self,ledger)


class LearnedProjects(BaseClient):
    version='economy-project-bc'
    def __init__(self,*args,model,max_economy_actions=96,**kwargs):
        super().__init__(*args,**kwargs)
        self.model=model;self.max_economy_actions=max_economy_actions
        self.truncated=0;self.economic_actions=Counter();self.project_failures=0

    def run_economy(self,ledger):
        ledger.economy_market=ledger.query('market')
        planner=InvestmentPlanner(ledger,self.state)
        try:
            planner.operations()
            with torch.inference_mode():
                for _ in range(self.max_economy_actions):
                    if ledger.full():break
                    encoded=planner.encode();logits,_=self.model(collate([encoded]))
                    index=int(logits[0].argmax());action,_=encoded['actions'][index]
                    if action=='finish':break
                    if planner.execute(encoded,index):self.economic_actions[action]+=1
                    else:self.project_failures+=1
                else:self.truncated+=1
        finally:planner.finish()
