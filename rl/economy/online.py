"""On-policy teacher queries and PPO using the paper's one metric S.

Reward = increment of cumulative actual consumption at historical current
prices (recruit + military + administration); construction is fixed capital
formation and is not part of S.  gamma=1 and lambda=1: every step's return is
the full future S increment to the episode end, so a per-turn reward is not
myopic.  No GDP proxy, no virtual invalid-action cost.
"""
from collections import Counter,defaultdict
import copy
import math
import random
import time

import numpy as np
import torch
from torch.nn import functional as F
from torch.utils.data import DataLoader

from host import GameHost
from ruleai.v13.entry import RuleClient as BaseClient
from .adapter import PROJECT_FINGERPRINT
from .model import Policy,collate,device_of,prepare
from .projects import InvestmentPlanner,sample
from .scenarios import config,source_hash,snapshot
from .train import metrics


class Deadline(Exception):pass


def check_deadline(deadline):
    if time.time()>=deadline:raise Deadline('Scheduled training deadline reached')


def checked_batches(loader,deadline):
    for batch in loader:
        check_deadline(deadline)
        yield batch


def expert_index(planner,encoded):
    """Ask v13plus for its next project without purchases or world mutation."""
    class Choice(Exception):
        def __init__(self,index):self.index=index
    oracle=InvestmentPlanner(planner.ledger,planner.state)
    def choose(action,site,kind,recipe,reserve):
        args=dict(site=site['id'],kind=kind)
        if action=='recruit':args['count']=1
        index=encoded['actions'].index((action,args))
        if encoded['legal'][index]:raise Choice(index)
        return False
    oracle.execute_project=choose
    try:
        oracle.recruit();oracle.invest()
    except Choice as choice:return choice.index
    finally:
        # finish() sells surplus and updates memory; a label query must do neither.
        planner.ledger.on_result=oracle.original_do
    return 0


S_CATEGORIES=('recruit','military','administration')
S_SCALE=1000.


def consumption_total(host,actor):
    """Paper S: cumulative actual consumption at historical current prices.

    The three categories are ``settlement.settle``'s consumption proxies.
    Construction is fixed capital formation; it is reported separately and is
    not part of S.  No observation ever receives S (see the 2026-10-19 incident).
    """
    spending=host.world.economy.spending(actor)
    for key in S_CATEGORIES:
        if key not in spending:raise ValueError(f'Missing consumption category: {key}')
        if not spending[key].complete:raise ValueError(f'Unpriced consumption: {key}')
    return sum(spending[key].known for key in S_CATEGORIES)


def consumption_increment(host,actor,previous=0.):
    total=consumption_total(host,actor)
    return total-previous,total


class OnlineClient(BaseClient):
    version='economy-online'
    def __init__(self,*args,model,mode,deadline=math.inf,budget=None,reuse_noop=True,**kwargs):
        super().__init__(*args,**kwargs)
        self.model=model;self.mode=mode;self.deadline=deadline
        self.reuse_noop=reuse_noop
        self.budget=budget if budget is not None else Counter()
        self.rows=[];self.steps=[];self.truncated=0;self.project_failures=0
        self.economic_actions=Counter();self.family_correct=0;self.decisions=0

    def run_economy(self,ledger):
        ledger.economy_market=ledger.query('market')
        planner=InvestmentPlanner(ledger,self.state)
        try:
            planner.operations()
            cached=None
            for _ in range(96):
                check_deadline(self.deadline)
                if self.budget['rows']>=self.budget.get('limit',6000):raise RuntimeError('World trajectory exceeded bounded row budget')
                if ledger.full():break
                with torch.inference_mode():
                    device=device_of(self.model)
                    if cached is None:
                        encoded=planner.encode();target=expert_index(planner,encoded)
                        logits,value,_=self.model(prepare(collate([encoded]),device),return_value=True)
                        distribution=torch.distributions.Categorical(logits=logits[0])
                    else:encoded,target,distribution,value=cached
                    selected=(int(distribution.sample()) if self.mode=='ppo' else int(logits[0].argmax()))
                    action=target if self.mode=='bc' else selected
                    old_logp=float(distribution.log_prob(torch.tensor(action,device=device)))
                teacher=encoded['actions'][target];student=encoded['actions'][selected]
                family=lambda a:(a[0],a[1].get('kind'))
                self.family_correct+=int(family(teacher)==family(student));self.decisions+=1
                row=sample(encoded,target)
                self.rows.append(row)
                self.budget['rows']+=1
                if self.mode=='ppo':
                    self.steps.append(dict(row=row,action=action,logp=old_logp,value=float(value[0]),
                        reward=0.,period=ledger.player.observe()['period']))
                kind=encoded['actions'][action][0]
                if kind=='finish':break
                actions_before=len(ledger.acts)
                cached=None
                if planner.execute(encoded,action):self.economic_actions[kind]+=1
                else:
                    # Failure is a reliability diagnostic only; it must not
                    # shape the paper's S reward.
                    self.project_failures+=1
                    # Exact reuse of deterministic logits on an unchanged
                    # observation. Still draw every stochastic action, retain
                    # every log probability/reward and count every failure.
                    if self.mode=='ppo' and self.reuse_noop and type(self.model) is Policy and len(ledger.acts)==actions_before:
                        cached=(encoded,target,distribution,value)
                    # The greedy DAgger policy would repeat this same no-op
                    # rejection on an unchanged observation 96 times. Keep its
                    # teacher label once and advance to the next real period.
                    # Evaluation retains the original action budget and reports
                    # every failure; PPO sampling is never intervened on.
                    if self.mode=='dagger' and len(ledger.acts)==actions_before:break
            else:self.truncated+=1
        finally:planner.finish()


def rollout(model,seed,turns,*,mode='dagger',size=24,swap=False,deadline=math.inf):
    """BC follows expert; DAgger labels student states; PPO samples current policy."""
    if mode not in ('bc','dagger','ppo'):raise ValueError(mode)
    source=source_hash();h=GameHost.new(config(seed,size=size,swap=swap));failures=defaultdict(Counter)
    focal=[];budget=Counter()
    for actor,c in list(h.clients.items()):
        if c.version=='v13plus':
            h.clients[actor]=OnlineClient(state=c.state,model=model,mode=mode,deadline=deadline,budget=budget);focal.append(actor)
    if mode=='ppo':budget['limit']=len(focal)*turns*96+1
    initial=snapshot(h,failures);milestones=defaultdict(dict);previous=Counter();consumption=Counter();tail=defaultdict(list)
    def receipt(actor,action,args,result):
        if not result['ok']:failures[actor][action+':'+result['message']]+=1
        elif action=='recruit':milestones[actor].setdefault('first_recruit',turn+1)
        elif action=='build' and args['kind']=='native:barracks':milestones[actor].setdefault('first_barracks',turn+1)
    begun=time.monotonic()
    for turn in range(turns):
        check_deadline(deadline);h.cycle(on_result=receipt)
        for actor in focal:
            reward,consumption[actor]=consumption_increment(h,actor,previous[actor])
            previous[actor]=consumption[actor];tail[actor].append(reward)
            c=h.clients[actor]
            # All decisions of this country share its own future outcome. Never
            # run return recursion over interleaved countries or world seeds.
            # gamma=lambda=1, so this turn's S increment is credited to the
            # country's whole future return, not discounted away.
            if mode=='ppo' and c.steps:c.steps[-1]['reward']+=reward/S_SCALE
            if len(h.world.territory.owned(actor))>initial[actor]['land']:
                milestones[actor].setdefault('first_expansion',turn+1)
        if (turn+1)%20==0:print(dict(seed=seed,mode=mode,turn=turn+1,seconds=round(time.monotonic()-begun,1)),flush=True)
    if source_hash()!=source:raise RuntimeError('Source changed during online rollout')
    final=snapshot(h,failures);rows=[];trajectories=[]
    for actor in focal:
        c=h.clients[actor];rows.extend(c.rows);trajectories.append(c.steps)
    report=dict(seed=seed,turns=turns,size=size,swap=swap,mode=mode,source=source,
        milestones=dict(milestones),countries={a:final[a] for a in focal},
        consumption=dict(consumption),
        tail_consumption={a:sum(v[-50:])/min(50,len(v)) for a,v in tail.items()},
        family_agreement=sum(h.clients[a].family_correct for a in focal)/max(1,sum(h.clients[a].decisions for a in focal)),
        decisions=sum(h.clients[a].decisions for a in focal),seconds=time.monotonic()-begun)
    return rows,trajectories,report


def bc_update(model,rows,*,epochs=3,deadline=math.inf,lr=1e-4):
    optimizer=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=.01);history=[]
    for _ in range(epochs):
        check_deadline(deadline)
        loader=DataLoader(rows,batch_size=8,shuffle=True,collate_fn=collate)
        history.append(metrics(model,checked_batches(loader,deadline),
            optimizer,opening_weight=4.,mobilization_weight=3.))
    model.eval();return history


def with_critic(model):
    if model.critic is not None:return model
    config=dict(model.config,critic=True);new=Policy(**config)
    result=new.load_state_dict(model.state_dict(),strict=False)
    if result.unexpected_keys or any(not k.startswith('critic.') for k in result.missing_keys):raise ValueError(result)
    return new.eval()


def monte_carlo(trajectories):
    result=[]
    for trajectory in trajectories:
        future=0.
        for step in reversed(trajectory):
            future+=step['reward'];step['return']=future
        result.extend(trajectory)
    return result


def ppo_update(model,trajectories,anchor,*,epochs=2,deadline=math.inf,lr=3e-5,checkpoint_callback=None):
    steps=monte_carlo(trajectories)
    if not steps:raise ValueError('Empty PPO rollout')
    device=device_of(model)
    anchor.to(device)
    returns=torch.tensor([s['return'] for s in steps],device=device)
    old_values=torch.tensor([s['value'] for s in steps],device=device)
    advantage=returns-old_values;advantage=(advantage-advantage.mean())/advantage.std(unbiased=False).clamp_min(1e-6)
    value_scale=returns.std(unbiased=False).clamp_min(1.)
    optimizer=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=.01)
    order=list(range(len(steps)));stats=Counter();stop=False
    model.train();anchor.eval()
    for _ in range(epochs):
        random.shuffle(order)
        for start in range(0,len(order),8):
            check_deadline(deadline);ids=order[start:start+8];chunk=[steps[i] for i in ids]
            b=prepare(collate([s['row'] for s in chunk]),device);logits,values,executable=model(b,return_value=True)
            d=torch.distributions.Categorical(logits=logits)
            actions=torch.tensor([s['action']for s in chunk],device=device)
            old=torch.tensor([s['logp']for s in chunk],device=device);logratio=d.log_prob(actions)-old;ratio=logratio.exp()
            adv=advantage[ids];pg=torch.maximum(-adv*ratio,-adv*ratio.clamp(.8,1.2)).mean()
            vf=((values-returns[ids])/value_scale).square().mean()
            execution=F.binary_cross_entropy_with_logits(executable[b['candidate_mask']],b['legal'][b['candidate_mask']].float())
            opening=torch.tensor([s['period']<12 for s in chunk],device=device)
            bc_kl=logits.sum()*0
            if opening.any():
                with torch.no_grad():reference=anchor(b)[0].softmax(-1)
                bc_kl=F.kl_div(logits.log_softmax(-1),reference,reduction='none').sum(-1)[opening].mean()
            entropy=d.entropy().mean()
            loss=pg+.5*vf+.1*execution+.05*bc_kl-.001*entropy
            if not torch.isfinite(loss):raise RuntimeError('Non-finite PPO loss')
            kl=float(((ratio-1)-logratio).mean().detach())
            if kl>.03:stop=True;break
            optimizer.zero_grad(set_to_none=True);loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),.5);optimizer.step()
            stats.update(n=1,policy=float(pg.detach()),value=float(vf.detach()),entropy=float(entropy.detach()),kl=kl,anchor=float(bc_kl.detach()))
            if checkpoint_callback is not None and stats['n']%100==0:
                checkpoint_callback(dict(updates=stats['n'],steps=len(steps)))
        if stop:break
    model.eval()
    return dict(**{k:v/max(1,stats['n']) for k,v in stats.items() if k!='n'},updates=stats['n'],kl_stopped=stop,
        return_mean=float(returns.mean()),return_std=float(returns.std(unbiased=False)),steps=len(steps))
