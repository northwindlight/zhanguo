"""Accounting over immutable facts, without writing producer domains."""
from dataclasses import asdict

from engine.identifiers import content_id
from engine.resource import Resource
from engine.runtime import ModError
from engine.state import active_unit
from .contracts import (EconomicFact, Money, Valuation, ValuedFact, TurnReport,
                        PeriodReport, EconomySnapshot, integer, owner_name)


def add(values):
    result = Money()
    for value in values:result = result.plus(value)
    return result


def growth(current, previous):
    if current is None or previous is None or previous<=0:return None
    value = (current-previous)/previous
    Resource.finite_number(value)
    return value


def record(value):
    """Normalize known DTO containers to explicit JSON arrays/maps."""
    if isinstance(value,dict):return {key:record(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [record(item) for item in value]
    return value


class Economy:
    def __init__(self, report_every, currency, valuation, owners, inventory, replacement):
        self._every = integer(report_every,1)
        self._currency = content_id(currency)
        self._valuation,self._owners,self._inventory,self._replacement = valuation,owners,inventory,replacement
        self._next_period = 0
        self._events,self._turns,self._reports = {},{},[]
        self._world,self._domain = None,None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:raise ModError('Economy already bound')
        self._world,self._domain = world,world.runtime.state_owners[key]
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller!=self._domain:
            raise ModError('Use declared economy interface')

    @property
    def next_period(self):return self._next_period

    @property
    def currency(self):return self._currency

    def currency_id(self):return self.currency

    def _value(self, amounts):
        if not amounts:return Money()
        rows = self._valuation(amounts)
        if not isinstance(rows,tuple) or len(rows)!=len(amounts):raise ValueError('Invalid valuation result')
        known,unpriced,seen = 0,[],set()
        for row in rows:
            if not isinstance(row,Valuation) or row.resource not in amounts or row.resource in seen or row.amount!=amounts[row.resource]:
                raise ValueError('Valuation does not match resource recipe')
            seen.add(row.resource)
            if row.value is None:
                if row.amount:unpriced.append(row.resource)
            else:
                Resource.finite_number(row.value)
                known += row.value
        return Money(known,tuple(unpriced))

    def ingest(self, facts):
        self._write()
        if not isinstance(facts,(list,tuple)):raise ValueError('Expected economic facts')
        candidates = {}
        owners = set(self._owners())
        for fact in facts:
            if not isinstance(fact,EconomicFact):raise ValueError('Expected EconomicFact')
            previous = self._events.get(fact.id) or candidates.get(fact.id)
            if previous is not None:
                if previous.fact!=fact:raise ValueError('Economic fact ID reused with different contents')
                continue
            if fact.period!=self.next_period:raise ValueError('New fact belongs to a different accounting period')
            if fact.owner not in owners:raise ValueError('Economic fact owner has no resource account')
            if fact.category in ('construction','recruit','military') and fact.outputs:
                raise ValueError('Expense facts cannot contain output credits')
            if fact.category=='import' and dict(fact.inputs)!={self._currency:fact.cash}:
                raise ValueError('Import payment must match actual currency settlement')
            if fact.category=='export' and dict(fact.outputs)!={self._currency:fact.cash}:
                raise ValueError('Export receipt must match actual currency settlement')
            candidates[fact.id] = ValuedFact(fact,self._value(dict(fact.inputs)),self._value(dict(fact.outputs)))
        self._events.update(candidates)
        return len(candidates)

    def _flow(self, period, owner):
        rows = [entry for entry in self._events.values() if entry.fact.period==period and entry.fact.owner==owner]
        production = [row for row in rows if row.fact.category=='production']
        outputs = add(row.outputs for row in production)
        inputs = add(row.inputs for row in production)
        expenses = {category:add(row.inputs for row in rows if row.fact.category==category)
                    for category in ('construction','recruit','military')}
        imports = sum(row.fact.cash for row in rows if row.fact.category=='import')
        exports = sum(row.fact.cash for row in rows if row.fact.category=='export')
        cash = sum(dict(row.fact.outputs).get(self._currency,0) for row in production)
        cumulative = add(row.inputs for row in self._events.values()
                         if row.fact.owner==owner and row.fact.period<=period and row.fact.category in expenses)
        return outputs,inputs,cash,expenses,imports,exports,cumulative

    def _turn(self, period, owner, inventory, buildings, liabilities=0):
        output,inputs,cash,expenses,imports,exports,cumulative = self._flow(period,owner)
        spending = add(expenses.values())
        return TurnReport(owner,period,output,inputs,cash,output.plus(inputs,-1),expenses['construction'],
                          expenses['recruit'],expenses['military'],imports,exports,inventory,buildings,
                          inventory.plus(buildings),spending,cumulative,liabilities,
                          inventory.plus(buildings).plus(Money(liabilities),-1))

    def _publication(self, owner, period, turns, prior):
        window = [row for (p,n),row in turns.items() if n==owner and period+1-self._every<=p<=period]
        last = turns[(period,owner)]
        invest = add(row.investment for row in window)
        recruit = add(row.recruitment for row in window)
        imports,exports = sum(row.imports for row in window),sum(row.exports for row in window)
        produced = add(row.production for row in window)
        denominator = produced.known-sum(row.cash_production for row in window)+imports
        ratio = None if not produced.complete else (exports+imports)/denominator if denominator>0 else 0.0
        if ratio is not None:Resource.finite_number(ratio)
        military_ratio = (last.military.total/last.gdp.total
                          if last.military.complete and last.gdp.complete and last.gdp.total>0 else None)
        if military_ratio is not None:Resource.finite_number(military_ratio)
        previous = next((row for row in reversed(prior) if row.owner==owner),None)
        return PeriodReport(owner,min(row.period for row in window),period,len(window),last.gdp,invest,recruit,
                            last.military,imports,exports,ratio,last.inventory_assets,last.building_assets,last.total_assets,
                            growth(last.gdp.total,previous.gdp.total if previous else None),
                            growth(invest.total,previous.investment.total if previous else None),
                            growth(last.total_assets.total,previous.total_assets.total if previous else None),military_ratio,last.liabilities,last.net_assets)

    def close(self, period, liabilities=None):
        self._write();integer(period)
        if period<self.next_period:return self.turns(period=period)
        if period!=self.next_period:raise ValueError('Accounting periods must be consecutive')
        liabilities = {} if liabilities is None else liabilities
        if not isinstance(liabilities,dict) or not set(liabilities)<=set(self._owners()):
            raise ValueError('Invalid liability positions')
        for amount in liabilities.values():integer(amount)
        candidates = {}
        for owner in self._owners():
            owner_name(owner)
            stocks = self._inventory(owner)
            inventory = self._value({row.id:row.amount for row in stocks})
            buildings = self._value(dict(self._replacement(owner)))
            candidates[(period,owner)] = self._turn(period,owner,inventory,buildings,liabilities.get(owner,0))
        turns = {**self._turns,**candidates}
        publications = []
        if (period+1)%self._every==0:
            publications = [self._publication(owner,period,turns,self._reports) for p,owner in candidates]
        self._turns.update(candidates)
        self._reports.extend(publications)
        self._next_period += 1
        return tuple(candidates.values())

    def settle(self, period, facts, liabilities=None):
        """Ingest and close atomically, including failures in asset valuation."""
        self._write()
        integer(period)
        if not isinstance(facts,(list,tuple)) or any(not isinstance(fact,EconomicFact) or fact.period!=period for fact in facts):
            raise ValueError('Settlement facts must belong to its period')
        before = dict(self._events)
        try:
            self.ingest(facts)
            return self.close(period,liabilities)
        except Exception:
            self._events = before
            raise

    def turns(self, owner=None, *, period=None):
        return tuple(row for (p,n),row in self._turns.items() if (owner is None or n==owner) and (period is None or p==period))

    def reports(self, owner=None):return tuple(row for row in self._reports if owner is None or row.owner==owner)

    def snapshot(self):
        return EconomySnapshot(self.next_period,tuple(self._events.values()),self.turns(),self.reports())

    def __deepcopy__(self,memo):
        result = self.snapshot();memo[id(self)] = result
        return result

    def to_save(self):
        return {'report_every':self._every,'currency':self._currency,'next_period':self.next_period,
                'events':[record(asdict(row)) for row in self._events.values()],
                'turns':[record(asdict(row)) for row in self._turns.values()],
                'reports':[record(asdict(row)) for row in self._reports]}

    @staticmethod
    def _money(data):
        if not isinstance(data,dict) or set(data)!={'known','unpriced'} or not isinstance(data['unpriced'],(list,tuple)):
            raise ValueError('Invalid saved economic value')
        return Money(data['known'],tuple(data['unpriced']))

    @classmethod
    def _report(cls, kind, data):
        if not isinstance(data,dict) or set(data)!=set(kind.__dataclass_fields__):raise ValueError('Invalid saved report')
        values = dict(data)
        fields = ('production','inputs','gdp','investment','recruitment','military','inventory_assets',
                  'building_assets','total_assets','spending','cumulative_spending','net_assets') if kind is TurnReport else (
                  'gdp','investment','recruitment','military','inventory_assets','building_assets','total_assets','net_assets')
        for field in fields:values[field] = cls._money(values[field])
        owner_name(values['owner']);integer(values['liabilities'])
        for field in ('imports','exports'):
            Resource.finite_number(values[field])
            if values[field]<0:raise ValueError('Negative trade settlement')
        if kind is TurnReport:
            integer(values['period']);Resource.finite_number(values['cash_production'])
        else:
            integer(values['start']);integer(values['end']);integer(values['span'],1)
            for field in ('trade_ratio','gdp_growth','investment_growth','assets_growth','military_ratio'):
                if values[field] is not None:Resource.finite_number(values[field])
        return kind(**values)

    def restore(self, data):
        self._write()
        expected = {'report_every','currency','next_period','events','turns','reports'}
        if not isinstance(data,dict) or set(data)!=expected:raise ValueError('Invalid economy save')
        if data['report_every']!=self._every or type(data['report_every']) is not int or data['currency']!=self._currency:
            raise ValueError('Economy reporting policy mismatch')
        period = integer(data['next_period'])
        if any(not isinstance(data[field],list) for field in ('events','turns','reports')):raise ValueError('Invalid economy record list')
        candidate = Economy(self._every,self._currency,self._valuation,self._owners,self._inventory,self._replacement)
        resource_ids = set(row.id for owner in self._owners() for row in self._inventory(owner))
        owners = set(self._owners())
        for row in data['events']:
            if not isinstance(row,dict) or set(row)!={'fact','inputs','outputs'}:raise ValueError('Invalid valued fact')
            raw = row['fact']
            if not isinstance(raw,dict) or set(raw)!=set(EconomicFact.__dataclass_fields__):raise ValueError('Invalid fact record')
            fact = EconomicFact(**raw)
            if fact.id in candidate._events or fact.period>period:raise ValueError('Duplicate/future fact')
            if fact.owner not in owners:raise ValueError('Unknown saved fact owner')
            if fact.category in ('construction','recruit','military') and fact.outputs:raise ValueError('Invalid expense outputs')
            if fact.category=='import' and dict(fact.inputs)!={self._currency:fact.cash}:raise ValueError('Invalid import settlement')
            if fact.category=='export' and dict(fact.outputs)!={self._currency:fact.cash}:raise ValueError('Invalid export settlement')
            if any(key not in resource_ids for key,_ in (*fact.inputs,*fact.outputs)):raise ValueError('Unknown fact resource')
            inputs,outputs = self._money(row['inputs']),self._money(row['outputs'])
            for value,leg in ((inputs,fact.inputs),(outputs,fact.outputs)):
                if not set(value.unpriced)<=set(key for key,amount in leg if amount):raise ValueError('Unpriced fact resources do not match leg')
                if value.known<0:raise ValueError('Negative valued resource flow')
            candidate._events[fact.id] = ValuedFact(fact,inputs,outputs)
        for raw in data['turns']:
            row = self._report(TurnReport,raw)
            key = (row.period,row.owner)
            if key in candidate._turns or row.period>=period:raise ValueError('Duplicate/future turn report')
            if row.owner not in owners:raise ValueError('Unknown saved report owner')
            # Rebuild all flow metrics from preserved valued facts; asset values
            # are historical observations, never recomputed at today's prices.
            expected_row = candidate._turn(row.period,row.owner,row.inventory_assets,row.building_assets,row.liabilities)
            if row!=expected_row:raise ValueError('Inconsistent economic report totals')
            candidate._turns[key] = row
        for raw in data['reports']:
            row = self._report(PeriodReport,raw)
            if (row.end+1)%self._every or (row.end,row.owner) not in candidate._turns:
                raise ValueError('Publication has no completed period')
            if any(previous.end==row.end and previous.owner==row.owner for previous in candidate._reports):raise ValueError('Duplicate publication')
            expected_row = candidate._publication(row.owner,row.end,candidate._turns,candidate._reports)
            if row!=expected_row:raise ValueError('Inconsistent period report')
            candidate._reports.append(row)
        for row in candidate._events.values():
            if row.fact.period<period and (row.fact.period,row.fact.owner) not in candidate._turns:
                raise ValueError('Closed fact has no turn report')
        for owner in owners:
            observed = set(p for p,n in candidate._turns if n==owner)
            if observed and observed!=set(range(min(observed),period)):raise ValueError('Missing owner accounting period')
        expected_publications = {(p,n) for p,n in candidate._turns if (p+1)%self._every==0}
        if {(row.end,row.owner) for row in candidate._reports}!=expected_publications:raise ValueError('Missing publication')
        self._events,self._turns,self._reports = candidate._events,candidate._turns,candidate._reports
        self._next_period = period
