"""Account-owned standing orders, executed once at the end of its action turn.

Orders use the same curve, currency carry and atomic trade as manual orders.
There is no new world-settlement stage or privileged source of goods/cash.
"""
from dataclasses import asdict, dataclass
from copy import deepcopy
import math

from .contracts import integer, number


@dataclass(frozen=True)
class AutoTradeRule:
    resource: str
    enabled: bool = True
    buy_below: int | None = None
    sell_above: int | None = None
    buy_limit: int = 50
    sell_limit: int = 50
    reserve_gold: int = 0
    max_buy_price: float | None = None
    min_sell_price: float | None = None


def validate_rule(resource, **values):
    """Pure validation shared by submission, save restore and human preview."""
    rule=AutoTradeRule(resource,**values)
    if type(rule.enabled) is not bool:raise ValueError('自动交易开关须为布尔值')
    for key in ('buy_below','sell_above'):
        if getattr(rule,key) is not None:integer(getattr(rule,key))
    for key in ('buy_limit','sell_limit'):integer(getattr(rule,key),1)
    integer(rule.reserve_gold)
    for key in ('max_buy_price','min_sell_price'):
        if getattr(rule,key) is not None:number(getattr(rule,key))
    if rule.buy_below is not None and rule.sell_above is not None and rule.buy_below>rule.sell_above:
        raise ValueError('自动购买目标不能高于自动出售保留量')
    return rule


class AutomaticTrading:
    def _init_automation(self, inspect):
        self._inspect=inspect
        self._auto_rules={}
        self._auto_runs={}

    def _validate_rule(self, resource, **values):
        self._listing(resource)
        return validate_rule(resource,**values)

    def set_auto_trade(self, owner, resource, *, enabled=True, buy_below=None, sell_above=None,
                       buy_limit=50, sell_limit=50, reserve_gold=0, max_buy_price=None, min_sell_price=None):
        self._write();self._owner(owner)
        if self._inspect is None:raise ValueError('此市场未配置资源账户查询')
        self._inspect(owner)
        rule=self._validate_rule(resource,enabled=enabled,buy_below=buy_below,sell_above=sell_above,
            buy_limit=buy_limit,sell_limit=sell_limit,reserve_gold=reserve_gold,
            max_buy_price=max_buy_price,min_sell_price=min_sell_price)
        self._auto_rules.setdefault(owner,{})[resource]=rule
        return asdict(rule)

    def remove_auto_trade(self, owner, resource):
        self._write();self._owner(owner);self._listing(resource)
        removed=self._auto_rules.get(owner,{}).pop(resource,None)
        if not self._auto_rules.get(owner):self._auto_rules.pop(owner,None)
        return {'resource':resource,'removed':removed is not None}

    def auto_trades(self, owner):
        self._owner(owner)
        return {'rules':[asdict(rule) for rule in self._auto_rules.get(owner,{}).values()],
                'last':deepcopy(self._auto_runs.get(owner))}

    def _auto_quantity(self, owner, rule, side, requested, stocks):
        cap=min(requested,self._inventory[rule.resource]) if side=='buy' else min(requested,math.floor(stocks[rule.resource]))
        budget=max(0,stocks[self.currency]-rule.reserve_gold)
        def allowed(quantity):
            quote=self.quote(rule.resource,quantity,side,owner)
            average=quote.exact_total/quantity
            if side=='buy':return quote.gold<=budget and (rule.max_buy_price is None or average<=rule.max_buy_price)
            return rule.min_sell_price is None or average>=rule.min_sell_price
        # Quote costs/average worsen monotonically with order size. Choose the
        # largest legal whole quantity, rather than failing a whole standing order.
        low,high=0,cap
        while low<high:
            mid=(low+high+1)//2
            if allowed(mid):low=mid
            else:high=mid-1
        return low

    def run_auto_trades(self, owner):
        self._write();self._owner(owner)
        previous=self._auto_runs.get(owner)
        if previous is not None and previous['period']==self.period:
            return {'period':self.period,'orders':[],'already_executed':True}
        orders=[]
        rules=self._auto_rules.get(owner,{})
        if not rules:return {'period':self.period,'orders':[]}
        # Sell excess before purchasing shortages; resource order follows the
        # declared market listings, not the order in which settings were edited.
        for side in ('sell','buy'):
            for resource in self.ids:
                rule=rules.get(resource)
                if rule is None or not rule.enabled:continue
                target=rule.buy_below if side=='buy' else rule.sell_above
                if target is None:continue
                stocks={r.id:r.amount for r in self._inspect(owner)}
                gap=target-stocks[resource] if side=='buy' else stocks[resource]-target
                requested=min(math.ceil(gap) if side=='buy' else math.floor(gap),rule.buy_limit if side=='buy' else rule.sell_limit)
                if requested<1:continue
                order={'resource':resource,'side':side,'requested':requested,'quantity':0,'status':'blocked'}
                try:
                    quantity=self._auto_quantity(owner,rule,side,requested,stocks)
                    if not quantity:
                        order['reason']='市场缺货、可用资金或整单均价不满足限制' if side=='buy' else '整单均价低于出售底价'
                    else:
                        trade=getattr(self,side)(owner,resource,quantity)
                        trade_data=asdict(trade)
                        trade_data['remaining']=stocks[resource]+(quantity if side=='buy' else -quantity)
                        order.update(status='filled' if quantity==requested else 'partial',quantity=quantity,
                                     gold=trade.gold,exact_total=trade.exact_total,trade=trade_data)
                except ValueError as exc:
                    # Expected domain rejection is an observable skipped order.
                    # Unrelated programming/runtime failures must still propagate.
                    order['reason']=str(exc)
                orders.append(order)
        result={'period':self.period,'orders':orders}
        self._auto_runs[owner]=deepcopy(result)
        return result

    def _automation_save(self):
        return {'rules':{owner:[asdict(r) for r in rules.values()] for owner,rules in self._auto_rules.items()},
                'runs':deepcopy(self._auto_runs)}

    def _automation_restore(self, data, period):
        if not isinstance(data,dict) or set(data)!={'rules','runs'} or not isinstance(data['rules'],dict) or not isinstance(data['runs'],dict):
            raise ValueError('Invalid automatic trading save')
        rules={}
        for owner,rows in data['rules'].items():
            self._owner(owner)
            if not isinstance(rows,list) or not rows:raise ValueError('Invalid standing orders')
            rules[owner]={}
            for row in rows:
                if not isinstance(row,dict) or set(row)!=set(AutoTradeRule.__dataclass_fields__):raise ValueError('Invalid standing order')
                values=dict(row);resource=values.pop('resource');rule=self._validate_rule(resource,**values)
                if resource in rules[owner]:raise ValueError('Duplicate standing order')
                rules[owner][resource]=rule
        runs=deepcopy(data['runs'])
        for owner,run in runs.items():
            self._owner(owner)
            if not isinstance(run,dict) or set(run)!={'period','orders'}:raise ValueError('Invalid automatic trade run')
            if integer(run['period'])>period or not isinstance(run['orders'],list):raise ValueError('Invalid automatic trade period')
            for row in run['orders']:
                if not isinstance(row,dict) or not {'resource','side','requested','quantity','status'}<=set(row):raise ValueError('Invalid automatic trade result')
                self._listing(row['resource']);integer(row['requested'],1);integer(row['quantity'])
                if row['side'] not in ('buy','sell') or row['status'] not in ('filled','partial','blocked') or row['quantity']>row['requested']:
                    raise ValueError('Invalid automatic trade outcome')
        return rules,runs
