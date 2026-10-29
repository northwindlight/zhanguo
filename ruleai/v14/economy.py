"""Joint mixed-integer planning, followed by validated first-period execution."""
from collections import Counter
from .inputs import collect
from .joint import Joint, HORIZON

GOLD='native:gold'


def execute(ledger, data, plan):
    """Check exact current quotes and the whole cash/material prefix first."""
    if plan.status in ('infeasible','unbounded','solver_error') or not plan.forecast:
        return {'executed':False,'reason':'联合模型未提供经过可行性验证的解'}
    if set(plan.sales)&set(plan.purchases):
        return {'executed':False,'reason':'计划包含同资源双向交易'}
    wallet=dict(data['stocks']);actions=len(plan.sales)+len(plan.purchases)+len(plan.construction)+len(plan.recruitment)
    if ledger.max_actions is not None and len(ledger.acts)+actions>ledger.max_actions:
        return {'executed':False,'reason':'剩余行动预算不足以提交整个本期方案'}
    for side,orders in (('sell',plan.sales),('buy',plan.purchases)):
        for resource,quantity in sorted(orders.items()):
            try:
                quote=ledger.player.call_api('economy:queries','trade_quote',{
                    'resource':resource,'quantity':quantity,'side':side})
            except ValueError as error:
                return {'executed':False,'reason':str(error)}
            if side=='sell' and wallet.get(resource,0)<quantity:
                return {'executed':False,'reason':'出售量超过本国库存'}
            wallet[resource]=wallet.get(resource,0)+quantity*(1 if side=='buy' else -1)
            wallet[GOLD]=wallet.get(GOLD,0)+quote['exact_total']*(1 if side=='sell' else -1)-1
    for order in plan.construction:
        site=next(s for s in data['sites']['sites'] if s['id']==order['site'])
        options=ledger.player.call_api('native:buildings','planning',{'x':site['position'][0],'y':site['position'][1]})
        row=next(r for r in options['buildings'] if r['kind']==order['kind'])
        if row['reason'] is not None or row['price'] is None:
            return {'executed':False,'reason':row['reason'] or '项目已不可建'}
        for resource,amount in row['price']['costs'].items():wallet[resource]=wallet.get(resource,0)-amount
    for order in plan.recruitment:
        site=next(s for s in data['sites']['sites'] if s['id']==order['site'])
        options=ledger.player.call_api('native:army','planning',{'x':site['position'][0],'y':site['position'][1],'count':order['count']})
        row=next(r for r in options if r['kind']==order['kind'])
        if row['reason'] is not None:
            return {'executed':False,'reason':row['reason']}
        for resource,amount in row['costs'].items():wallet[resource]=wallet.get(resource,0)-amount
    if any(v<-1e-6 for v in wallet.values()):
        return {'executed':False,'reason':'正式整单成本与建设材料不能共同满足预算'}
    for side,orders in (('sell',plan.sales),('buy',plan.purchases)):
        for resource,quantity in sorted(orders.items()):
            if not ledger.do(side,resource=resource,quantity=quantity):
                return {'executed':False,'reason':'交易回执与规划不符，剩余方案未提交'}
    for order in plan.construction:
        if not ledger.do('build',**order):
            return {'executed':False,'reason':'建设回执与规划不符，剩余方案未提交'}
    for order in plan.recruitment:
        if not ledger.do('recruit',**order):
            return {'executed':False,'reason':'征募回执与规划不符，剩余方案未提交'}
    return {'executed':True,'reason':''}


def run(ledger, state, *, manage_military=True, horizon=HORIZON):
    first_action=len(ledger.acts)
    data=collect(ledger.player,manage_military=manage_military)
    # Native loans mature within five periods. Include a known obligation's
    # due date, rather than letting it disappear just beyond the base horizon.
    horizon=max(horizon,max((p['period']-data['period']+1 for p in data['bank']['repayment_schedule']),default=0))
    if manage_military:
        if getattr(state,'recruit_due',None) is None:state.recruit_due=data['period']+HORIZON-1
        data['recruit_due']=state.recruit_due
    data['action_budget']=None if ledger.max_actions is None else max(0,ledger.max_actions-len(ledger.acts))
    data['future_action_budget']=None if ledger.max_actions is None else max(0,ledger.max_actions-1)
    # Establish an attainable current service level before a bounded search
    # over future investments. A limited long-horizon incumbent cannot be
    # allowed to worsen this known feasible level of current maintenance.
    operations=Joint({**data,'manage_military':False,'desired_recruits':0},ledger.player,state,horizon=1)
    for decision in operations.x.values():operations.model.equal(decision,0)
    safe=operations.build().solve()
    joint=Joint(data,ledger.player,state,horizon=horizon).build()
    if safe.forecast:
        joint.model.limit(joint.period_unserved[0],upper=safe.certificate['current_shortfall']+1e-6)
    plan=joint.solve()
    if not plan.forecast:
        # Feasibility restoration uses the SAME physical/budget model. Freeze
        # discretionary investment and solve current operations only; a hard
        # multi-period problem must not suppress an affordable current fuel or
        # maintenance package. There is no greedy building/resource fallback.
        failed=plan.certificate
        plan=safe
        plan.certificate.update(mode='operations_only',requested_horizon=horizon,
            planning_attempt=failed,operations_optimality_proven=plan.certificate['optimality_proven'],
            optimality_proven=False)
    else:plan.certificate['mode']='joint'
    plan.certificate['current_service_reference']={k:safe.certificate[k] for k in ('service_status','current_shortfall')}
    result=execute(ledger,data,plan)
    if manage_military and any(r['ok'] and r['action']=='recruit' for r in ledger.acts[first_action:]):
        state.recruit_due=None
    own=Counter()
    for receipt in ledger.acts[first_action:]:
        if receipt['ok'] and receipt['action'] in ('buy','sell'):
            row=receipt['data'];own[row['resource']]+=row['quantity']*(1 if receipt['action']=='sell' else -1)
    state.market=joint.market.remember(own)
    return {'certificate':plan.certificate,'result':result,'plan':plan}
