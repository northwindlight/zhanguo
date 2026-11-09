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
import inspect

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
from .provenance import provenance,contracts
from .train import metrics
from .reward_shaping import shape_trajectories


class Deadline(Exception):pass


def validate_device(name):
    device=torch.device(name)
    if device.type=='cuda':
        if not torch.cuda.is_available():raise ValueError('CUDA device requested but CUDA is unavailable')
        if device.index is not None and device.index>=torch.cuda.device_count():raise ValueError('CUDA device index is unavailable')
    try:torch.empty(0,device=device)
    except (RuntimeError,AssertionError,NotImplementedError) as error:raise ValueError(f'Device unavailable: {device}: {error}') from error
    return device


def check_deadline(deadline):
    if time.time()>=deadline:raise Deadline('Scheduled training deadline reached')


def trajectory_row_limit(countries,turns):return countries*turns*96+1


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
        self.success_by_period=defaultdict(Counter)

    def run_economy(self,ledger):
        ledger.economy_market=ledger.query('market')
        planner=InvestmentPlanner(ledger,self.state)
        period=ledger.player.observe()['period']
        try:
            planner.operations()
            cached=None
            for _ in range(96):
                check_deadline(self.deadline)
                if self.budget['rows']>=self.budget.get('limit',6000):raise Deadline('World trajectory exceeded bounded row budget')
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
                        reward=0.,period=period,action_kind=encoded['actions'][action][0],executed=None))
                kind=encoded['actions'][action][0]
                if kind=='finish':break
                actions_before=len(ledger.acts)
                cached=None
                success=planner.execute(encoded,action)
                if self.mode=='ppo':self.steps[-1]['executed']=bool(success)
                if success:
                    self.economic_actions[kind]+=1;self.success_by_period[period][kind]+=1
                else:
                    # Persist raw S rewards. Optional failure penalties are
                    # applied to copies at PPO update time, never to the world.
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
    origin=provenance();h=GameHost.new(config(seed,size=size,swap=swap));failures=defaultdict(Counter)
    focal=[];budget=Counter()
    for actor,c in list(h.clients.items()):
        if c.version=='v13plus':
            h.clients[actor]=OnlineClient(state=c.state,model=model,mode=mode,deadline=deadline,budget=budget);focal.append(actor)
    budget['limit']=trajectory_row_limit(len(focal),turns)
    initial=snapshot(h,failures);milestones=defaultdict(dict);previous=Counter();consumption=Counter();tail=defaultdict(list)
    consumption_on_success=Counter();consumption_without_success=Counter()
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
            if sum(c.success_by_period[turn].values()):consumption_on_success[actor]+=reward
            else:consumption_without_success[actor]+=reward
            # All decisions of this country share its own future outcome. Never
            # run return recursion over interleaved countries or world seeds.
            # gamma=lambda=1, so this turn's S increment is credited to the
            # country's whole future return, not discounted away.
            if mode=='ppo' and c.steps:c.steps[-1]['reward']+=reward/S_SCALE
            if len(h.world.territory.owned(actor))>initial[actor]['land']:
                milestones[actor].setdefault('first_expansion',turn+1)
        if (turn+1)%20==0:print(dict(seed=seed,mode=mode,turn=turn+1,seconds=round(time.monotonic()-begun,1)),flush=True)
    if contracts()['feature_contract']!=origin['feature_contract']:raise RuntimeError('Feature/reward contract changed during online rollout')
    final=snapshot(h,failures);rows=[];trajectories=[]
    for actor in focal:
        c=h.clients[actor];rows.extend(c.rows);trajectories.append(c.steps)
    success_metrics={}
    for actor in focal:
        count=sum(h.clients[actor].economic_actions.values())
        success_metrics[actor]=dict(successful_projects=count,
            total_S_per_successful_project=consumption[actor]/count if count else None,
            success_period_S_per_project=consumption_on_success[actor]/count if count else None,
            S_in_periods_without_success=consumption_without_success[actor],
            attribution='Descriptive ratio; settlement consumption is not the causal marginal return of a project')
    report=dict(**origin,seed=seed,turns=turns,size=size,swap=swap,mode=mode,implementation_end=source_hash(),
        milestones=dict(milestones),countries={a:final[a] for a in focal},
        consumption=dict(consumption),
        tail_consumption={a:sum(v[-50:])/min(50,len(v)) for a,v in tail.items()},
        family_agreement=sum(h.clients[a].family_correct for a in focal)/max(1,sum(h.clients[a].decisions for a in focal)),
        decisions=sum(h.clients[a].decisions for a in focal),seconds=time.monotonic()-begun,
        success_metrics=success_metrics,row_budget=budget['limit'])
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


def _legacy_ppo_update(model,trajectories,anchor,*,epochs=2,deadline=math.inf,lr=3e-5,checkpoint_callback=None,
        policy_coef=1.,value_coef=.5,execution_coef=.1,anchor_coef=.05,entropy_coef=.001,
        clip_ratio=.2,target_kl=.03,anchor_periods=12,batch_size=8,max_grad_norm=.5,weight_decay=.01,
        detach_value_encoder=False,max_updates=None,diagnostic_callback=None):
    steps=monte_carlo(trajectories)
    if not steps:raise ValueError('Empty PPO rollout')
    device=device_of(model)
    anchor.to(device)
    returns=torch.tensor([s['return'] for s in steps],device=device)
    old_values=torch.tensor([s['value'] for s in steps],device=device)
    advantage=returns-old_values;advantage=(advantage-advantage.mean())/advantage.std(unbiased=False).clamp_min(1e-6)
    value_scale=returns.std(unbiased=False).clamp_min(1.)
    optimizer=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=weight_decay)
    order=list(range(len(steps)));stats=Counter();stop=False;kl_stopped=False;examples_used=0
    model.train();anchor.eval()
    for _ in range(epochs):
        random.shuffle(order)
        for start in range(0,len(order),batch_size):
            check_deadline(deadline);ids=order[start:start+batch_size];chunk=[steps[i] for i in ids]
            b=prepare(collate([s['row'] for s in chunk]),device)
            logits,values,executable=model(b,return_value=True,detach_value_encoder=detach_value_encoder)
            d=torch.distributions.Categorical(logits=logits)
            actions=torch.tensor([s['action']for s in chunk],device=device)
            old=torch.tensor([s['logp']for s in chunk],device=device);logratio=d.log_prob(actions)-old;ratio=logratio.exp()
            adv=advantage[ids];pg=torch.maximum(-adv*ratio,-adv*ratio.clamp(1-clip_ratio,1+clip_ratio)).mean()
            vf=((values-returns[ids])/value_scale).square().mean()
            execution=F.binary_cross_entropy_with_logits(executable[b['candidate_mask']],b['legal'][b['candidate_mask']].float())
            opening=torch.tensor([s['period']<anchor_periods for s in chunk],device=device)
            bc_kl=logits.sum()*0
            if opening.any():
                with torch.no_grad():reference=anchor(b)[0].softmax(-1)
                bc_kl=F.kl_div(logits.log_softmax(-1),reference,reduction='none').sum(-1)[opening].mean()
            entropy=d.entropy().mean()
            loss=policy_coef*pg+value_coef*vf+execution_coef*execution+anchor_coef*bc_kl-entropy_coef*entropy
            if not torch.isfinite(loss):raise RuntimeError('Non-finite PPO loss')
            kl=float(((ratio-1)-logratio).mean().detach())
            if diagnostic_callback is not None:
                detail=dict(updates=stats['n'],kl=kl,clip_fraction=float(((ratio-1).abs()>clip_ratio).float().mean().detach()),
                    policy=float(pg.detach()),value=float(vf.detach()),execution=float(execution.detach()),
                    anchor=float(bc_kl.detach()),entropy=float(entropy.detach()),loss=float(loss.detach()),kl_stop=kl>target_kl)
                if stats['n'] in (0,1,9):
                    from .ppo_diagnostics import component_gradients
                    detail['gradients']=component_gradients(model,dict(policy=policy_coef*pg,value=value_coef*vf,
                        execution=execution_coef*execution,anchor=anchor_coef*bc_kl,entropy=-entropy_coef*entropy,total=loss))
                diagnostic_callback(detail)
            if kl>target_kl:stop=True;kl_stopped=True;break
            optimizer.zero_grad(set_to_none=True);loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),max_grad_norm);optimizer.step()
            examples_used+=len(ids)
            stats.update(n=1,policy=float(pg.detach()),value=float(vf.detach()),entropy=float(entropy.detach()),kl=kl,anchor=float(bc_kl.detach()))
            if checkpoint_callback is not None and stats['n']%100==0:
                checkpoint_callback(dict(updates=stats['n'],steps=len(steps)))
            if max_updates is not None and stats['n']>=max_updates:stop=True;break
        if stop:break
    model.eval()
    return dict(**{k:v/max(1,stats['n']) for k,v in stats.items() if k!='n'},updates=stats['n'],kl_stopped=kl_stopped,
        examples_used=examples_used,
        update_limit_reached=max_updates is not None and stats['n']>=max_updates,
        return_mean=float(returns.mean()),return_std=float(returns.std(unbiased=False)),steps=len(steps))


def ppo_update(model,trajectories,anchor,*,kl_mode='adaptive',update_batch_size=128,
        kl_coef=.1,kl_window_batches=8,invalid_action_penalty=0.,**kwargs):
    """Accumulate an effective batch and adapt KL over windows, without micro-batch stops.

    ``batch_size`` remains the memory-sized micro batch. ``legacy`` exists only
    for reproducing earlier experiments; the normal path consumes every row in
    each epoch unless an explicit update budget or deadline is reached.
    """
    trajectories,reward_shaping=shape_trajectories(trajectories,invalid_action_penalty)
    if kl_mode=='legacy':
        result=_legacy_ppo_update(model,trajectories,anchor,**kwargs)
        result['reward_shaping']=reward_shaping
        return result
    if kl_mode!='adaptive':raise ValueError('Unknown KL mode')
    defaults={name:p.default for name,p in inspect.signature(_legacy_ppo_update).parameters.items()
        if p.default is not inspect.Parameter.empty}
    unknown=set(kwargs)-set(defaults)
    if unknown:raise TypeError(f'Unknown PPO options: {sorted(unknown)}')
    options=dict(defaults,**kwargs)
    if min(update_batch_size,options['batch_size'],kl_window_batches)<1:raise ValueError('Batch/window sizes must be positive')
    if options['epochs']<1 or (options['max_updates'] is not None and options['max_updates']<1):raise ValueError('Epoch/update budgets must be positive')
    if options['target_kl']<=0 or kl_coef<0:raise ValueError('Invalid adaptive KL target/coefficient')
    steps=monte_carlo(trajectories)
    if not steps:raise ValueError('Empty PPO rollout')
    device=device_of(model);anchor.to(device)
    returns=torch.tensor([s['return']for s in steps],device=device)
    old_values=torch.tensor([s['value']for s in steps],device=device)
    advantage=returns-old_values;advantage=(advantage-advantage.mean())/advantage.std(unbiased=False).clamp_min(1e-6)
    value_scale=returns.std(unbiased=False).clamp_min(1.)
    optimizer=torch.optim.AdamW(model.parameters(),lr=options['lr'],weight_decay=options['weight_decay'])
    order=list(range(len(steps)));stats=Counter();seen=set();windows=[];window=[]
    beta=float(kl_coef);completed=0;limited=False
    model.train();anchor.eval()
    for epoch in range(options['epochs']):
        random.shuffle(order)
        for start in range(0,len(order),update_batch_size):
            check_deadline(options['deadline'])
            ids=order[start:start+update_batch_size];size=len(ids)
            candidates=sum(len(steps[i]['row']['candidates'])for i in ids)
            opening_count=sum(steps[i]['period']<options['anchor_periods']for i in ids)
            measured=Counter();gradient_detail=None;optimizer.zero_grad(set_to_none=True)
            for offset in range(0,size,options['batch_size']):
                check_deadline(options['deadline'])
                micro_ids=ids[offset:offset+options['batch_size']];chunk=[steps[i]for i in micro_ids]
                b=prepare(collate([s['row']for s in chunk]),device)
                logits,values,executable=model(b,return_value=True,detach_value_encoder=options['detach_value_encoder'])
                distribution=torch.distributions.Categorical(logits=logits)
                actions=torch.tensor([s['action']for s in chunk],device=device)
                old=torch.tensor([s['logp']for s in chunk],device=device)
                logratio=distribution.log_prob(actions)-old;ratio=logratio.exp()
                adv=advantage[micro_ids];clip=options['clip_ratio']
                pg=torch.maximum(-adv*ratio,-adv*ratio.clamp(1-clip,1+clip)).sum()/size
                vf=((values-returns[micro_ids])/value_scale).square().sum()/size
                execution=F.binary_cross_entropy_with_logits(executable[b['candidate_mask']],
                    b['legal'][b['candidate_mask']].float(),reduction='sum')/candidates
                opening=torch.tensor([s['period']<options['anchor_periods']for s in chunk],device=device)
                bc_kl=logits.sum()*0
                if opening_count and opening.any():
                    with torch.no_grad():reference=anchor(b)[0].softmax(-1)
                    bc_kl=F.kl_div(logits.log_softmax(-1),reference,reduction='none').sum(-1)[opening].sum()/opening_count
                entropy=distribution.entropy().sum()/size
                kl=((ratio-1)-logratio).sum()/size
                terms=dict(policy=options['policy_coef']*pg,value=options['value_coef']*vf,
                    execution=options['execution_coef']*execution,anchor=options['anchor_coef']*bc_kl,
                    entropy=-options['entropy_coef']*entropy,kl_penalty=beta*kl)
                loss=sum(terms.values())
                if not torch.isfinite(loss):raise RuntimeError('Non-finite PPO loss')
                if options['diagnostic_callback'] is not None and offset==0 and stats['n'] in (0,1,9):
                    from .ppo_diagnostics import component_gradients
                    gradient_detail=component_gradients(model,dict(terms,total=loss))
                loss.backward()
                measured.update(policy=float(pg.detach()),value=float(vf.detach()),execution=float(execution.detach()),
                    entropy=float(entropy.detach()),anchor=float(bc_kl.detach()),kl=float(kl.detach()),loss=float(loss.detach()),
                    clip_fraction=float(((ratio-1).abs()>clip).float().sum().detach())/size)
            norm=torch.nn.utils.clip_grad_norm_(model.parameters(),options['max_grad_norm'],error_if_nonfinite=True)
            optimizer.step();stats['n']+=1;stats['examples']+=size;seen.update(ids)
            for key,value in measured.items():stats[key]+=value
            window.append((measured['kl']*size,size))
            if len(window)>=kl_window_batches or start+size==len(order):
                estimate=sum(k for k,n in window)/sum(n for k,n in window)
                before=beta
                if kl_coef>0:
                    if estimate>1.5*options['target_kl']:beta=min(10.,beta*2)
                    elif estimate<options['target_kl']/1.5:beta=max(1e-4,beta/2)
                windows.append(dict(examples=sum(n for k,n in window),kl=estimate,beta_before=before,beta_after=beta))
                window=[]
            if options['diagnostic_callback'] is not None:
                detail=dict(updates=stats['n'],examples_used=stats['examples'],logical_batch_size=size,
                    micro_batch_size=options['batch_size'],kl_mode='adaptive',kl_stop=False,kl_beta=beta,
                    gradient_norm=float(norm),**measured)
                if gradient_detail is not None:detail.update(gradients=gradient_detail,gradient_scope='first_micro_batch_scaled_to_logical_batch')
                options['diagnostic_callback'](detail)
            if options['checkpoint_callback'] is not None and stats['n']%100==0:
                options['checkpoint_callback'](dict(updates=stats['n'],steps=len(steps),examples_used=stats['examples'],kl_beta=beta))
            if options['max_updates'] is not None and stats['n']>=options['max_updates']:
                limited=True;break
        if start+size>=len(order):completed+=1
        if limited:break
    model.eval()
    return dict(**{k:v/max(1,stats['n'])for k,v in stats.items()if k not in ('n','examples')},
        updates=stats['n'],examples_used=stats['examples'],unique_examples_used=len(seen),coverage=len(seen)/len(steps),
        epochs_completed=completed,kl_stopped=False,update_limit_reached=limited,kl_mode='adaptive',
        effective_batch_size=update_batch_size,micro_batch_size=options['batch_size'],kl_beta=beta,kl_windows=windows,
        return_mean=float(returns.mean()),return_std=float(returns.std(unbiased=False)),steps=len(steps),
        reward_shaping=reward_shaping)
