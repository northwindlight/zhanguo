"""Rolling-horizon joint MILP for physical production, investment and trading."""
from collections import Counter
from dataclasses import dataclass
import math

from .linear import Model, expr
from .inputs import current_counts
from ruleai.v13.market import MarketModel
from ruleai.v13.profiles import PROFILES
from ruleai.v11plus.parameters import MIL_SHARE

GOLD='native:gold'
SUPPLY='native:supply'
EQUIPMENT='native:equipment'
HORIZON=4
NODE_LIMIT=8
ECONOMIC_GAP=.02
TIME_LIMIT=5.0


@dataclass
class Plan:
    status: str
    sales: dict
    purchases: dict
    construction: list
    recruitment: list
    certificate: dict
    forecast: list


class Joint:
    def __init__(self, data, player, state, *, horizon=HORIZON):
        self.data,self.player,self.state=data,player,state
        self.horizon=horizon
        self.model=Model()
        self.catalog={r['id']:r for r in data['catalog']}
        self.kinds=list(self.catalog)
        self.sites=data['sites']['sites']
        self.resources=list(dict(data['market']['inventory']))
        self.counts=current_counts(data)
        self.profile=PROFILES[state.profile]
        self.market=MarketModel(data['market'],state.market,1)
        self.x,self.recruits,self.buys,self.sells={},{},{},{}
        self.productions,self.flows,self.grids,self.cash_end,self.stock_end=[],[],[],[],[]
        self.unserved=expr(0)
        self.army_goal=expr(0)
        self._curves={}
        self.trade_actions={}
        self.period_unserved=[]
        self._build_variables()

    def present(self, site, kind, t):
        base=sum(n for (s,k,remaining),n in self.counts.items() if s==site and k==kind and remaining<=t)
        return base+sum((self.x.get((site,kind,tau),expr(0)) for tau in range(t)),expr(0))

    def _build_variables(self):
        m=self.model
        for site in self.sites:
            sid=site['id'];plan=self.data['plans'][sid]
            pending=[r for r in self.data['orders'] if r['site']==sid and r['status']!='construction']
            for kind in self.kinds:
                definition=self.catalog[kind]
                economic=(definition['phase'] in ('gather','power','industry','income') or 'academy' in definition['roles'])
                military=self.data['manage_military'] and 'recruiter' in definition['roles']
                if not (economic or military):continue
                if definition['site_resource'] and not any(r['resource']==definition['site_resource'] and r['free']>0 for r in plan['space']['resources']):continue
                if definition['min_slots']>plan['occupied']+self.horizon-1:continue
                row=next(r for r in plan['buildings'] if r['kind']==kind)
                if row['price'] is None:continue
                for t in range(self.horizon):
                    allowed=not pending and (t>0 or row['reason'] is None)
                    self.x[sid,kind,t]=m.var(f'build/{sid}/{kind}/{t}',upper=int(allowed),integer=True)
            for t in range(self.horizon):
                starts=sum((v for (s,k,tau),v in self.x.items() if s==sid and tau==t),expr(0))
                m.limit(starts,upper=1 if t>0 else plan['space']['order_remaining'])
                all_starts=sum((v for (s,k,tau),v in self.x.items() if s==sid and tau<=t),expr(0))
                m.limit(all_starts,upper=plan['space']['free'])
                for kind in self.kinds:
                    definition=self.catalog[kind]
                    own_starts=sum((v for (s,k,tau),v in self.x.items() if s==sid and k==kind and tau<=t),expr(0))
                    if definition['max_per_site'] is not None:
                        existing=sum(n for (s,k,_),n in self.counts.items() if s==sid and k==kind)+sum(r['kind']==kind for r in pending)
                        m.limit(own_starts,upper=max(0,definition['max_per_site']-existing))
                    decision=self.x.get((sid,kind,t))
                    if decision is not None and definition['min_slots']:
                        previous=sum((v for (s,k,tau),v in self.x.items() if s==sid and tau<t),expr(0))
                        m.limit(plan['occupied']+previous-definition['min_slots']*decision,lower=0)
                for resource in plan['space']['resources']:
                    use=sum((v for (s,k,tau),v in self.x.items() if s==sid and tau<=t and self.catalog[k]['site_resource']==resource['resource']),expr(0))
                    m.limit(use,upper=resource['free'])
        for kind,row in self.catalog.items():
            if row['self_build_limit'] is not None:
                queued=sum(o['kind']==kind and o['status']!='construction' for o in self.data['orders'])
                m.limit(sum((v for (s,k,t),v in self.x.items() if k==kind),expr(0)),upper=max(0,row['self_build_limit']-self.data['sites']['self_built'].get(kind,0)-queued))

    def conjunction(self, left, right, name):
        z=self.model.var(name,upper=1,integer=True)
        self.model.limit(z-left,upper=0);self.model.limit(z-right,upper=0)
        self.model.limit(z-left-right,lower=-1)
        return z

    def construction(self, t):
        costs=Counter()
        for (sid,kind,tau),x in self.x.items():
            if tau!=t:continue
            row=next(r for r in self.data['plans'][sid]['buildings'] if r['kind']==kind)
            plain=row['price']['costs'];after=row['price_after_academy']['costs']
            for resource,amount in plain.items():costs[resource]+=x*amount
            reduction=plain.get(GOLD,0)-after.get(GOLD,0)
            if reduction:
                academy=sum((self.present(sid,k,t) for k,b in self.catalog.items() if 'academy' in b['roles']),expr(0))
                costs[GOLD]-=reduction*self.conjunction(x,academy,f'discount/{sid}/{kind}/{t}')
        return costs

    def _trade_curve(self, resource, side, cap):
        key=resource,side,cap
        if key in self._curves:return self._curves[key]
        points=sorted({0,cap,*[n for n in (1,5,10,25,50,100,250,500,1000,2000) if n<cap]})
        values=[0]
        for quantity in points[1:]:
            q=self.player.call_api('economy:queries','trade_quote',{'resource':resource,'quantity':quantity,'side':side})
            values.append(q['exact_total'])
        result=[(b-a,(vb-va)/(b-a)) for a,b,va,vb in zip(points,points[1:],values,values[1:])]
        self._curves[key]=result
        return result

    def trades(self, t, forecast, cumulative):
        m=self.model;cost=expr(0);quantity={};action_count=expr(0)
        buy0=dict(self.data['market']['buy_prices']);sell0=dict(self.data['market']['sell_prices'])
        for resource in self.resources:
            initial=dict(self.data['market']['inventory'])[resource]
            cap=max(1,min(2000,math.ceil(self.data['stocks'].get(resource,0)+len(self.sites)*20*max(2,self.profile.reserve))))
            buy_cap=min(initial,cap)
            mode=m.var(f'buy_mode/{resource}/{t}',upper=1,integer=True)
            b=m.var(f'buy/{resource}/{t}',upper=buy_cap,integer=True)
            s=m.var(f'sell/{resource}/{t}',upper=cap,integer=True)
            self.buys[resource,t]=b;self.sells[resource,t]=s
            m.limit(b-buy_cap*mode,upper=0);m.limit(s+cap*mode,upper=cap)
            for side,amount,limit,base,sign in (('buy',b,buy_cap,buy0,1),('sell',s,cap,sell0,-1)):
                segments=expr(0)
                for i,(width,slope) in enumerate(self._trade_curve(resource,side,limit)):
                    z=m.var(f'{side}_segment/{resource}/{t}/{i}',upper=width)
                    segments+=z
                    factor=forecast[0 if side=='buy' else 1][resource]/base[resource]
                    cost+=sign*slope*factor*z
                m.equal(amount,segments)
            # One conservative rounding coin per active direction (at most one).
            active=m.var(f'trade_active/{resource}/{t}',upper=1,integer=True)
            m.limit(b+s-cap*active,upper=0);cost+=active
            action_count+=active
            quantity[resource]=b-s
            cumulative[resource]+=b-s
            external=min(0,self.market.external.get(resource,0))
            m.limit(cumulative[resource],upper=max(0,initial+external*t))
        self.trade_actions[t]=action_count
        return quantity,cost

    def liquidation(self, resource, stock, sell_price):
        """Conservative terminal wealth uses executable depth, not Q*spot.

        Uncertain price appreciation is not booked as earned profit. Future
        inputs can still be hedged by holding inventory in the physical model.
        """
        capacity=sum(s['capacity'] for s in self.sites)
        output=max((r['outputs'].get(resource,0) for r in self.catalog.values()),default=0)
        cap=math.ceil(self.data['stocks'].get(resource,0)+dict(self.data['market']['inventory'])[resource]+self.horizon*capacity*output)
        quantity=self.model.var('liquidation/'+resource,upper=cap,integer=True)
        self.model.limit(quantity-stock,upper=0)
        total,revenue=expr(0),expr(0)
        for index,(width,slope) in enumerate(self._trade_curve(resource,'sell',cap)):
            segment=self.model.var(f'liquidation_segment/{resource}/{index}',upper=width)
            factor=sell_price/dict(self.data['market']['sell_prices'])[resource]
            total+=segment;revenue+=slope*factor*segment
        self.model.equal(quantity,total)
        return revenue

    def automatic(self, count, stock, inputs, powered, name, *, max_count=None):
        """Exact maximal runnable group: no imaginary per-factory off switch."""
        m=self.model
        if max_count is None:max_count=max(20,max((r['capacity'] for r in self.sites),default=20))
        q=m.var('run/'+name,upper=max_count,integer=True)
        m.limit(q-count,upper=0)
        flags=[]
        for resource,amount in inputs.items():
            shortage=m.var(f'lack/{name}/{resource}',upper=1,integer=True)
            output=max((r['outputs'].get(resource,0) for r in self.catalog.values()),default=0)
            big=max(1,self.data['stocks'].get(resource,0)+dict(self.data['market']['inventory']).get(resource,0)+self.horizon*len(self.sites)*20*output)
            m.limit(stock[resource]-amount*q,lower=0)
            m.limit(stock[resource]-amount*q+big*shortage,upper=big+amount-1e-4)
            flags.append(shortage)
        if powered is not None:m.limit(q-max_count*powered,upper=0)
        m.limit(q-count+max_count*sum(flags,expr(0))+(max_count*(1-powered) if powered is not None else 0),lower=0)
        return q

    def production(self, t, stock):
        m=self.model
        grid=m.var(f'grid/{t}',upper=1,integer=True);self.grids.append(grid)
        generated,demand=expr(0),expr(0)
        rows=[];flows={r:expr(0) for r in [GOLD,*self.resources]}
        for phase in ('gather','power','industry','income','passive'):
            for site in self.sites:
                sid=site['id']
                for kind,row in self.catalog.items():
                    if row['phase']!=phase:continue
                    count=self.present(sid,kind,t)
                    demand+=row['energy_use']*count
                    if not count.terms and count.constant==0:continue
                    if not(row['inputs'] or row['outputs'] or row['energy_output'] or 'gold_base' in row['effects']):continue
                    q=self.automatic(count,stock,row['inputs'],grid if row['energy_use'] else None,f'{sid}/{kind}/{t}')
                    for resource,amount in row['inputs'].items():
                        stock[resource]-=amount*q;flows[resource]-=amount*q
                    output={r:amount*q for r,amount in row['outputs'].items()}
                    if 'gold_base' in row['effects']:
                        total=sum((self.present(sid,k,t) for k in self.kinds if k!=kind),expr(0))
                        density=m.var(f'hall_density/{sid}/{t}',upper=site['capacity'])
                        m.limit(density-total,upper=0);m.limit(density-site['capacity']*q,upper=0)
                        m.limit(density-total-site['capacity']*q,lower=-site['capacity'])
                        output[GOLD]=row['effects']['gold_base']*q+row['effects']['gold_per_slot']*density
                    for resource,value in output.items():
                        retained=self.tax(resource,value,t,f'{sid}/{kind}')
                        stock[resource]+=retained;flows[resource]+=retained
                    generated+=row['energy_output']*q
                    rows.append((sid,kind,q))
        power_bound=1+len(self.sites)*20*max((r['energy_output']+r['energy_use'] for r in self.catalog.values()),default=1)
        m.limit(generated-demand-power_bound*grid,lower=-power_bound)
        m.limit(generated-demand-power_bound*grid,upper=-1)
        shortage=m.var(f'energy_unserved/{t}')
        m.limit(shortage+generated-demand,lower=0)
        self.unserved+=shortage
        self.productions.append(rows)
        self.flows.append(flows)

    def tax(self, resource, gross, t, name):
        tribute=self.data['econ']['preview'].get('tribute')
        if not tribute:return gross
        percent=tribute['percent']/100
        carried=self.remainders.get(resource,tribute['carried_remainders'].get(resource,0))
        due=self.model.var(f'tribute/{resource}/{name}/{t}',integer=True)
        remainder=self.model.var(f'tribute_remainder/{resource}/{name}/{t}',upper=1-1e-4)
        self.model.equal(carried+gross*percent,due+remainder)
        self.remainders[resource]=remainder
        return gross-due

    def enlist(self, t, stock):
        if not self.data['manage_military'] or 'native:infantry' not in self.data['units']:return expr(0)
        unit=self.data['units']['native:infantry'];new=expr(0)
        for site in self.sites:
            sid=site['id']
            quota=sum((self.present(sid,k,t)*row['effects'].get('recruit_cap',0) for k,row in self.catalog.items()),expr(0))
            if t==0:
                official=next(r for r in self.data['recruitment'][sid] if r['kind']=='native:infantry')
                upper=official['quota'] if official['reason'] is None else 0
            else:upper=site['capacity']
            y=self.model.var(f'recruit/{sid}/{t}',upper=upper,integer=True)
            self.model.limit(y-quota,upper=0)
            if t>0:self.model.limit(y-site['capacity']*self.grids[t-1],upper=0)
            self.recruits[sid,t]=y;new+=y
            for resource,amount in unit['recruit'].items():stock[resource]-=amount*y
        # Explicit existing military policy: at most one imported-supply growth
        # unit per period; the economic model chooses whether/where it is feasible.
        self.model.limit(new,upper=1)
        due=max(0,self.data.get('recruit_due',self.data['period']+self.horizon-1)-self.data['period'])
        # A saved absolute deadline permits economic scheduling without
        # postponing the same request forever as the rolling window moves.
        # Missed requests remain eligible, with lateness minimized.
        self.army_goal+=new*(self.horizon-max(0,t-due))
        return new

    def military_budget(self, t, need, growth, prices, maximum_need):
        """New troops must fit the original military share of REAL net output.

        Existing obligations remain serviced even when oversized. Any new
        growth must fit after production/input bottlenecks and after adding
        the new unit's upkeep; a theoretical full-capacity snapshot cannot
        justify recruits that crowd out the economy supporting them.
        """
        if not self.data['desired_recruits']:return
        m=self.model;net=self.flows[-1];income=net[GOLD]
        capacity=sum(s['capacity'] for s in self.sites)
        worst_inputs=0
        for resource in self.resources:
            bound=capacity*max((r['inputs'].get(resource,0) for r in self.catalog.values()),default=0)*prices[0][resource]
            value=m.var(f'military_income/{resource}/{t}',lower=-bound)
            m.limit(value-net[resource]*prices[0][resource],upper=0)
            m.limit(value-net[resource]*prices[1][resource],upper=0)
            income+=value;worst_inputs+=bound
        cap=maximum_need*prices[0][SUPPLY]+MIL_SHARE*worst_inputs
        m.limit(need*prices[0][SUPPLY]-MIL_SHARE*income-cap*(1-growth),upper=0)

    def build(self):
        m=self.model
        stock={k:expr(self.data['stocks'].get(k,0)) for k in [GOLD,*self.resources]}
        cumulative={r:expr(0) for r in self.resources}
        self.remainders={}
        baseline=Counter()
        for row in self.data['sites']['instances']:
            for k,v in self.catalog[row['kind']]['outputs'].items():baseline[k]+=v
            for k,v in self.catalog[row['kind']]['inputs'].items():baseline[k]-=v
        # This period is quoted at this period's market, not a forecast that
        # already includes a future inventory drift.
        prices=[(dict(self.data['market']['buy_prices']),dict(self.data['market']['sell_prices']))]
        prices+=list(self.market.path(baseline,{},max(self.horizon,self.profile.horizon)-1))
        # A price interval joins current quotes and the public-flow forecast.
        # Optimize against its adverse ends; predicted appreciation alone
        # cannot finance investments or turn warehousing into certain profit.
        spot=prices[0]
        prices=[({r:max(spot[0][r],buy[r]) for r in self.resources},
                 {r:min(spot[1][r],sell[r]) for r in self.resources}) for buy,sell in prices]
        added_army=expr(0);last_need=expr(0)
        for t in range(self.horizon):
            previous_shortfall=self.unserved
            quantities,cash=self.trades(t,prices[t],cumulative)
            stock[GOLD]-=cash
            for resource,value in quantities.items():stock[resource]+=value
            costs=self.construction(t)
            for resource,value in costs.items():stock[resource]-=value
            added_army+=self.enlist(t,stock)
            if self.data.get('action_budget') is not None:
                budget=self.data['action_budget'] if t==0 else self.data['future_action_budget']
                starts=sum((v for (s,k,tau),v in self.x.items() if tau==t),expr(0))
                recruited=sum((v for (s,tau),v in self.recruits.items() if tau==t),expr(0))
                m.limit(starts+recruited+self.trade_actions[t],upper=budget)
            for value in stock.values():m.limit(value,lower=0)
            self.production(t,stock)
            need=self.data['econ']['preview']['military']['need']
            if t>0:
                # Prepaid recruits are not ours to cancel. Reserve their full
                # eventual upkeep; their uncertain start date is conservative.
                need+=sum(o['remaining']*self.data['units'][o['kind']]['supply'] for o in self.data['recruit_orders'])
            need+=self.data['units'].get('native:infantry',{}).get('supply',0)*added_army
            maximum_need=self.data['econ']['preview']['military']['need']
            maximum_need+=sum(o['remaining']*self.data['units'][o['kind']]['supply'] for o in self.data['recruit_orders'])
            maximum_need+=self.data['desired_recruits']*self.data['units'].get('native:infantry',{}).get('supply',0)
            maximum_need=max(1,math.ceil(maximum_need))
            self.military_budget(t,need,added_army,prices[t],maximum_need)
            served=self.automatic(need,stock,{SUPPLY:1},None,f'military_supply/{t}',max_count=maximum_need)
            stock[SUPPLY]-=served;self.unserved+=need-served
            last_need=served
            if t==0:
                repair=self.data['econ']['preview']['military']['equipment_need']
                fed=m.var('fully_fed',upper=1,integer=True)
                m.limit(need-served-maximum_need*(1-fed),upper=0)
                m.limit(need-served+fed,lower=1)
                restored=self.automatic(repair,stock,{EQUIPMENT:1},fed,'repair',max_count=max(1,repair))
                stock[EQUIPMENT]-=restored;self.unserved+=repair-restored
            self.period_unserved.append(self.unserved-previous_shortfall)
            for value in stock.values():m.limit(value,lower=0)
            rate=self.data['bank']['savings_rate']
            if rate:
                interest=m.var(f'interest/{t}',lower=-math.inf if rate<0 else 0,integer=True)
                if rate>0:
                    m.limit(stock[GOLD]*rate-interest,lower=0,upper=1-1e-4)
                else:
                    m.limit(interest-stock[GOLD]*rate,lower=0,upper=1-1e-4)
                stock[GOLD]+=interest
            for payment in self.data['bank']['repayment_schedule']:
                if payment['period']==self.data['period']+t:stock[GOLD]-=payment['gold']
            m.limit(stock[GOLD],lower=0)
            # Materialize period-end state so the next period cannot rewrite a
            # previously added conservation constraint through a mutable dict.
            end={}
            for resource,value in stock.items():
                end[resource]=m.var(f'stock/{resource}/{t}')
                m.equal(end[resource],value)
            stock=end;self.stock_end.append(dict(end));self.cash_end.append(end[GOLD])
        discount=.99
        terminal=(stock[GOLD]+sum((self.liquidation(r,stock[r],prices[self.horizon-1][1][r]) for r in self.resources),expr(0)))*discount**(self.horizon-1)
        # Continuation value prices the last period's real feasible net output,
        # not fictitious completed buildings or a fixed construction bonus.
        last=self.flows[-1]
        last[SUPPLY]-=last_need
        terminal_index=max(self.horizon,self.profile.horizon)-1
        # Perpetual productive assets retain value after the explicit horizon.
        # Forecast the published market path, then a stationary terminal market;
        # 1% per-period discount is a declared objective parameter, not a hurdle
        # hidden inside individual building scores.
        tail=sum(discount**t for t in range(self.horizon,len(prices)))+discount**len(prices)/(1-discount)
        continuation=last[GOLD]*tail
        for resource in self.resources:
            buy_tail=sum(discount**t*prices[t][0][resource] for t in range(self.horizon,len(prices)))+discount**len(prices)*prices[terminal_index][0][resource]/(1-discount)
            sell_tail=sum(discount**t*prices[t][1][resource] for t in range(self.horizon,len(prices)))+discount**len(prices)*prices[terminal_index][1][resource]/(1-discount)
            lower=-max(1e6,buy_tail*len(self.sites)*1000)
            value=m.var('continuation/'+resource,lower=lower)
            m.limit(value-last[resource]*buy_tail,upper=0)
            m.limit(value-last[resource]*sell_tail,upper=0)
            continuation+=value
        m.limit(added_army,upper=self.data['desired_recruits'])
        # Present service cannot be traded for a smaller hypothetical future
        # shortage. The scale is derived from a bound on ALL future shortages,
        # so this is a lexicographic objective, not a building-specific bonus.
        future_need=self.data['econ']['preview']['military']['need']
        future_need+=sum(o['remaining']*self.data['units'][o['kind']]['supply'] for o in self.data['recruit_orders'])
        future_need+=self.data['desired_recruits']*self.data['units'].get('native:infantry',{}).get('supply',0)
        max_demand=sum(s['capacity'] for s in self.sites)*max((r['energy_use'] for r in self.catalog.values()),default=0)
        scale=1+(self.horizon-1)*(future_need+max_demand)
        self.service_objective=self.unserved+(scale-1)*self.period_unserved[0]
        self.objective=terminal+continuation
        return self

    def solve(self):
        service=self.model.solve(-self.service_objective,node_limit=NODE_LIMIT,gap=0,time_limit=TIME_LIMIT)
        if service.values is not None:
            self.model.limit(self.service_objective,upper=self.service_objective.value(service.values)+1e-6)
        strategic=self.model.solve(self.army_goal,node_limit=NODE_LIMIT,gap=0,time_limit=TIME_LIMIT) if service.values is not None and self.data['desired_recruits'] else service
        if strategic.values is None and service.values is not None:strategic=service
        if strategic.values is not None and self.data['desired_recruits']:
            self.model.limit(self.army_goal,lower=self.army_goal.value(strategic.values)-1e-6)
        economic=self.model.solve(self.objective,node_limit=NODE_LIMIT,gap=ECONOMIC_GAP,time_limit=TIME_LIMIT) if strategic.values is not None else None
        result=economic or strategic
        fallback=result.values is None and strategic.values is not None
        if fallback:result=strategic
        status='feasible_fallback' if fallback else result.status
        certificate={'status':status,'objective':float(self.objective.value(result.values)) if result.values is not None else None,
            'bound':economic.bound if economic is not None else None,'gap':economic.gap if economic is not None else None,
            'nodes':result.nodes,'variables':len(self.model.names),'constraints':len(self.model.rows),'horizon':self.horizon,
            'service_status':service.status,'service_shortfall':float(self.unserved.value(result.values)) if result.values is not None else None,
            'current_shortfall':float(self.period_unserved[0].value(result.values)) if result.values is not None else None,
            'military_status':strategic.status,'military_target':self.data['desired_recruits'],
            'optimality_proven':economic is not None and economic.values is not None and all(s.status=='optimal' for s in (service,strategic,economic)),
            'service_priority_value':float(self.service_objective.value(result.values)) if result.values is not None else None,
            'service_priority_lower_bound':-service.bound if service.bound is not None else None,
            'economic_status':economic.status if economic is not None else 'not_solved',
            'node_limit':NODE_LIMIT,'economic_gap_target':ECONOMIC_GAP,'stage_time_limit':TIME_LIMIT,
            'solution_stage':'previous_feasible' if fallback else 'economic' if economic is not None else 'service',
            'message':result.message}
        if result.values is None:
            return Plan(result.status,{}, {},[],[],certificate,[])
        values=result.values
        purchases={r:int(round(self.buys[r,0].value(values))) for r in self.resources if self.buys[r,0].value(values)>.5}
        sales={r:int(round(self.sells[r,0].value(values))) for r in self.resources if self.sells[r,0].value(values)>.5}
        builds=[{'site':s,'kind':k} for (s,k,t),v in self.x.items() if t==0 and v.value(values)>.5]
        recruits=[{'site':s,'kind':'native:infantry','count':int(round(v.value(values)))} for (s,t),v in self.recruits.items() if t==0 and v.value(values)>.5]
        forecast=[{'period':t,'grid':round(self.grids[t].value(values)),
            'stocks':{r:float(v.value(values)) for r,v in self.stock_end[t].items()},
            'production':[{'site':s,'kind':k,'count':round(q.value(values))} for s,k,q in self.productions[t]]} for t in range(self.horizon)]
        return Plan(status,sales,purchases,builds,recruits,certificate,forecast)
