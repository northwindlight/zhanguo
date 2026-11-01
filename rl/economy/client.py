"""Experimental local runner; only run_economy changes, military stays v11plus."""
from collections import Counter
import random
import torch

from ruleai.v13.entry import RuleClient as BaseClient
from ruleai.v13.market import MarketModel
from ruleai.v13plus.entry import RuleClient as TeacherClient
from .adapter import observation, label, decode
from .model import collate


class RecordingTeacher(TeacherClient):
    def __init__(self,*args,sink,**kwargs):
        super().__init__(*args,**kwargs);self.sink=sink

    def run_economy(self,ledger):
        ledger.economy_market=ledger.query('market')
        previous_market=self.state.market
        original_action,original_result=ledger.on_action,ledger.on_result
        pending=None
        def before(action,args):
            nonlocal pending
            encoded=observation(ledger,self.state)
            index,quantity=label(encoded,action,args)
            pending={k:v for k,v in encoded.items() if k!='actions'}
            pending.update(target=index,quantity=quantity)
            if original_action:original_action(action,args)
        def after(action,args,receipt):
            nonlocal pending
            if not receipt['ok']:raise RuntimeError(f'Invalid teacher demonstration: {receipt}')
            self.sink(pending);pending=None
            if original_result:original_result(action,args,receipt)
        ledger.on_action,ledger.on_result=before,after
        try:
            super().run_economy(ledger)
            # Finish is captured before next-period market memory is used.
            # The teacher updates memory in finish(); observation must retain
            # the same previous-period memory used throughout this turn.
            remembered=self.state.market
            try:
                self.state.market=previous_market
                before('finish',{})
            finally:
                self.state.market=remembered
            self.sink(pending)
        finally:
            ledger.on_action,ledger.on_result=original_action,original_result


class LearnedClient(BaseClient):
    version='economy-bc-experimental'
    def __init__(self,*args,model,max_economy_actions=96,**kwargs):
        super().__init__(*args,**kwargs)
        self.model=model;self.max_economy_actions=max_economy_actions
        self.truncated=0;self.economic_actions=Counter()

    def run_economy(self,ledger):
        market=ledger.query('market');old=MarketModel(market,self.state.market)
        ledger.economy_market=market
        own=Counter();count=0
        with torch.inference_mode():
            while count<self.max_economy_actions and not ledger.full():
                encoded=observation(ledger,self.state)
                logits,quantities=self.model(collate([encoded]))
                index=int(logits[0].argmax())
                action,args=decode(encoded,index,float(quantities[0,index]))
                if action=='finish':break
                if not ledger.do(action,**args):
                    raise RuntimeError(f'Formal API rejected a masked BC action: {ledger.acts[-1]}')
                self.economic_actions[action]+=1;count+=1
                if action in ('buy','sell'):
                    own[args['resource']]+=args['quantity']*(1 if action=='sell' else -1)
            else:
                self.truncated+=1
        self.state.market=old.remember(own)


class CorrectingTeacher(RecordingTeacher):
    """Collect expert turns after student mistakes; evaluation has no intervention.

This is BC on mixed-policy trajectories, not per-action DAgger or PPO. Only
expert actions become labels; student turns expose the expert to off-policy
power/material/expansion states under the real multiplayer market.
"""
    version='v13plus-bc-mixture'

    def __init__(self,*args,model,teacher_probability=.5,seed=0,**kwargs):
        super().__init__(*args,**kwargs)
        self.model=model;self.teacher_probability=teacher_probability
        self.rng=random.Random(seed);self.max_economy_actions=96
        self.truncated=0;self.economic_actions=Counter();self.teacher_turns=0;self.student_turns=0

    def run_economy(self,ledger):
        if self.rng.random()<self.teacher_probability:
            self.teacher_turns+=1
            super().run_economy(ledger)
        else:
            self.student_turns+=1
            LearnedClient.run_economy(self,ledger)
