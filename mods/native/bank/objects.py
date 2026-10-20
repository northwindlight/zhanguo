"""Owned central-bank contracts. Loading the module supplies the bank."""
from dataclasses import asdict, dataclass, replace
from math import floor

from engine.identifiers import content_id
from engine.resource import Resource
from engine.runtime import ModError
from engine.state import active_unit
from mods.native.contracts.economy import integer, owner_name, PeriodReport
from mods.native.economy_reports.objects import record


@dataclass(frozen=True)
class BankPolicy:
    currency: str
    initial_rate: float
    minimum_rate: float
    maximum_rate: float
    loan_spread: float
    credit_multiple: float
    loan_term: int
    report_fee: int
    purchase_limit: int

    def __post_init__(self):
        content_id(self.currency)
        for field in ('initial_rate','minimum_rate','maximum_rate','loan_spread','credit_multiple'):
            Resource.finite_number(getattr(self,field))
        if not -1 < self.minimum_rate <= self.initial_rate <= self.maximum_rate:
            raise ValueError('Invalid savings-rate range')
        if self.loan_spread < 0 or self.credit_multiple < 0:
            raise ValueError('Invalid bank policy')
        integer(self.loan_term,1);integer(self.report_fee);integer(self.purchase_limit,1)


@dataclass(frozen=True)
class Loan:
    owner: str
    principal: int
    due: int
    remaining: int
    taken: int
    initial_rate: float


@dataclass(frozen=True)
class FinanceEntry:
    owner: str
    period: int
    kind: str
    cash: int
    liability_delta: int = 0


@dataclass(frozen=True)
class ReportPurchase:
    buyer: str
    period: int
    fee: int
    report: PeriodReport


@dataclass(frozen=True)
class BankSnapshot:
    next_period: int
    rate: float
    loans: tuple
    entries: tuple
    purchases: tuple


@dataclass(frozen=True)
class FinancialPosition:
    owner: str
    period: int
    cash: int | float
    credit_limit: int | None
    loan: Loan | None
    liabilities: int
    purchases: tuple
    # 授信口径：旧版把「GDP×5、利率、期限、最多一笔」写在央行行里，
    # 只给一个额度数字，模型不知道这笔钱是按什么算的、什么时候到期。
    gdp: float | None = None
    credit_multiple: float = 5.0
    savings_rate: float = 0.0
    loan_rate: float = 0.0
    loan_term: int = 5


class Bank:
    def __init__(self, policy, call, active=None):
        self._policy,self._call = policy,call
        self._active=active
        self._rate,self._next_period = policy.initial_rate,0
        self._loans,self._entries,self._purchases = {},[],[]
        self._world,self._domain = None,None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:raise ModError('Bank already bound')
        self._world,self._domain = world,world.runtime.state_owners[key]
        return self

    def _write(self):
        if self._world is not None and active_unit.get() not in (None,self._domain):
            raise ModError('Use declared bank interface')

    @property
    def policy(self):return self._policy

    @property
    def next_period(self):return self._next_period
    @property
    def rate(self):return self._rate
    @property
    def loan_rate(self):return self.rate + self.policy.loan_spread

    def set_rate(self, rate):
        self._write();Resource.finite_number(rate)
        self._rate = min(self.policy.maximum_rate,max(self.policy.minimum_rate,rate))
        return self.rate

    def _stocks(self, owner):
        owner_name(owner)
        return self._call('native:resource-inspection','snapshot',owner)

    def _cash(self, owner):
        return next(row.amount for row in self._stocks(owner) if row.id==self.policy.currency)

    def credit_limit(self, owner):
        self._stocks(owner)
        turns = self._call('native:economy','turns',owner)
        if not turns:return 0
        gdp = turns[-1].gdp.known
        value = max(0,gdp*self.policy.credit_multiple)
        Resource.finite_number(value)
        return floor(value)

    def borrow_quote(self, owner):
        fee = self._call('native:diplomacy', 'fee', owner)
        pos = self.position(owner)
        loan = None if pos.loan is None else {'principal': pos.loan.principal, 'due': pos.loan.due,
                                               'remaining': pos.loan.remaining, 'rate': pos.loan.initial_rate}
        return {'fee': fee, 'cash': pos.cash, 'credit': pos.credit_limit, 'loan': loan,
                'can_borrow': loan is None and (pos.credit_limit or 0) > 0 and pos.cash >= fee,
                'loan_rate': pos.loan_rate, 'loan_term': pos.loan_term,
                'gdp': pos.gdp, 'savings_rate': pos.savings_rate}

    def borrow(self, owner, fee=0):
        """Take the declared credit limit; diplomatic fees are supplied explicitly."""
        self._write();integer(fee)
        if owner in self._loans:raise ValueError('你已有一笔贷款未还清：还清前不能再向央行借款')
        cash = self._cash(owner)
        if fee and cash < fee:raise ValueError(
            f'现金不足以支付借款手续费：需 {fee} 金，现有 {cash} 金；先卖资源使现金≥{fee}，'
            f'或用 query(panel=quote, action=borrow) 先看')
        principal = self.credit_limit(owner)
        if principal is None:raise ValueError('最近一期报表的 GDP 未能全额估值，授信额度算不出来：先补齐缺项（见 report 与 econ）再借')
        integer(principal)
        if principal <= 0:raise ValueError('当前授信额度为 0：额度按最近一期已结 GDP×5 计算，先做出 GDP 才有钱可借')
        loan = Loan(owner,principal,principal,self.policy.loan_term,self.next_period,self.loan_rate)
        self._call('native:bank-cash','adjust',self.policy.currency,{owner:principal-fee})
        self._loans[owner] = loan
        self._entries.append(FinanceEntry(owner,self.next_period,'loan',principal,principal))
        if fee:self._entries.append(FinanceEntry(owner,self.next_period,'loan-fee',-fee))
        return loan

    def buy_report(self, buyer, target, turn=None):
        self._write()
        cash = self._cash(buyer);self._stocks(target)
        if buyer==target:raise ValueError('本国报表本来就免费，不用买：用 report 查询即可')
        reports = self._call('native:economy','reports',target)
        if turn is not None:
            integer(turn);reports=tuple(r for r in reports if r.end==turn)
        if not reports:raise ValueError('指定国家或周期没有已公布报表')
        fee = self.policy.report_fee
        if fee and cash < fee:raise ValueError(f'现金不足以购买报表：需 {fee} 金，现有 {cash} 金')
        receipt = ReportPurchase(buyer,self.next_period,fee,reports[-1])
        self._call('native:bank-cash','adjust',self.policy.currency,{buyer:-fee})
        # Each buyer has its own bounded history; repeated purchases still cost money.
        own = [row for row in self._purchases if row.buyer==buyer] + [receipt]
        self._purchases = [row for row in self._purchases if row.buyer!=buyer] + own[-self.policy.purchase_limit:]
        self._entries.append(FinanceEntry(buyer,self.next_period,'report-fee',-fee))
        return receipt

    def fees(self,period):
        result=[]
        for row in reversed(self._entries):
            if row.period<period:break
            if row.period==period and row.kind in ('loan-fee','report-fee'):result.append(row)
        return tuple(reversed(result))

    def liabilities(self):return {owner:loan.due for owner,loan in self._loans.items()}

    def settle(self, period):
        self._write();integer(period)
        if period!=self.next_period:raise ValueError('Bank periods must be consecutive')
        changes,loans,entries = {},{owner:loan for owner,loan in self._loans.items() if self._active and owner not in self._active()},[]
        owners = self._active() if self._active else self._call('native:resource-inspection','owners')
        if not set(self._loans)<=set(self._call('native:resource-inspection','owners')):raise ValueError('Loan owner has no account')
        for owner in owners:
            cash = self._cash(owner)
            interest = int(cash*self.rate) if cash>0 else 0
            changes[owner] = interest
            if interest:entries.append(FinanceEntry(owner,period,'deposit-interest',interest))
            if owner in self._loans:
                loan = self._loans[owner]
                value = loan.due*(1+self.loan_rate)
                Resource.finite_number(value)
                due = max(0,int(round(value)))
                entries.append(FinanceEntry(owner,period,'loan-interest',0,due-loan.due))
                updated = replace(loan,due=due,remaining=loan.remaining-1)
                if updated.remaining:
                    loans[owner] = updated
                else:
                    changes[owner] -= due
                    entries.append(FinanceEntry(owner,period,'repayment',-due,-due))
        self._call('native:bank-cash','adjust',self.policy.currency,changes)
        self._loans = loans
        self._entries.extend(entries)
        self._next_period += 1
        return tuple(entries)

    def position(self, owner):
        cash = self._cash(owner)
        loan = self._loans.get(owner);due = 0 if loan is None else loan.due
        turns = self._call('native:economy','turns',owner)
        gdp = turns[-1].gdp.known if turns else None
        return FinancialPosition(owner,self.next_period,cash,self.credit_limit(owner),loan,due,
                                 tuple(row for row in self._purchases if row.buyer==owner),
                                 gdp,self.policy.credit_multiple,self.rate,self.loan_rate,self.policy.loan_term)

    def snapshot(self):return BankSnapshot(self.next_period,self.rate,tuple(self._loans.values()),tuple(self._entries),tuple(self._purchases))
    def __deepcopy__(self,memo):
        value=self.snapshot();memo[id(self)]=value;return value

    def to_save(self):
        return {'policy':asdict(self.policy),'next_period':self.next_period,'rate':self.rate,
                'loans':[asdict(row) for row in self._loans.values()],
                'entries':[asdict(row) for row in self._entries],
                'purchases':[record(asdict(row)) for row in self._purchases]}

    def restore(self, data):
        self._write()
        if not isinstance(data,dict) or set(data)!={'policy','next_period','rate','loans','entries','purchases'}:
            raise ValueError('Invalid bank save')
        if not isinstance(data['policy'],dict) or data['policy']!=asdict(self.policy):raise ValueError('Bank policy mismatch')
        BankPolicy(**data['policy'])
        period = integer(data['next_period']);Resource.finite_number(data['rate'])
        if not self.policy.minimum_rate<=data['rate']<=self.policy.maximum_rate:raise ValueError('Invalid bank rate')
        if any(not isinstance(data[key],list) for key in ('loans','entries','purchases')):raise ValueError('Invalid bank record list')
        owners = set(self._call('native:resource-inspection','owners'))
        loans,entries,purchases = {},[],[]
        for raw in data['loans']:
            if not isinstance(raw,dict) or set(raw)!=set(Loan.__dataclass_fields__):raise ValueError('Invalid loan record')
            row = Loan(**raw)
            owner_name(row.owner);integer(row.principal,1);integer(row.due);integer(row.remaining,1);integer(row.taken)
            Resource.finite_number(row.initial_rate)
            if row.owner not in owners or row.owner in loans or row.taken>period or (not self._active or row.owner in self._active()) and row.remaining!=self.policy.loan_term-(period-row.taken):
                raise ValueError('Inconsistent loan clock')
            if not self.policy.minimum_rate+self.policy.loan_spread<=row.initial_rate<=self.policy.maximum_rate+self.policy.loan_spread:
                raise ValueError('Invalid initial loan rate')
            loans[row.owner] = row
        for raw in data['entries']:
            if not isinstance(raw,dict) or set(raw)!=set(FinanceEntry.__dataclass_fields__):raise ValueError('Invalid finance record')
            row = FinanceEntry(**raw)
            integer(row.period);owner_name(row.owner)
            if type(row.cash) is not int or type(row.liability_delta) is not int or row.owner not in owners or row.period>period:raise ValueError('Invalid finance settlement')
            if row.kind not in ('loan','loan-fee','report-fee','deposit-interest','repayment','loan-interest'):raise ValueError('Unknown finance category')
            if row.kind=='loan' and row.cash<=0 or row.kind in ('loan-fee','report-fee','repayment') and row.cash>0:
                raise ValueError('Invalid finance direction')
            if row.period==period and row.kind in ('deposit-interest','repayment','loan-interest'):raise ValueError('Future bank settlement')
            if row.kind in ('loan','repayment') and row.liability_delta!=row.cash:
                raise ValueError('Loan cash and debt differ')
            if row.kind=='loan-interest' and row.cash!=0:
                raise ValueError('Loan interest is accrued, not paid')
            if row.kind not in ('loan','repayment','loan-interest') and row.liability_delta:
                raise ValueError('Non-loan event changes liabilities')
            entries.append(row)
        for raw in data['purchases']:
            if not isinstance(raw,dict) or set(raw)!=set(ReportPurchase.__dataclass_fields__):raise ValueError('Invalid purchase record')
            report = self._call('native:economy','report_from_save',raw['report'])
            row = ReportPurchase(raw['buyer'],integer(raw['period']),integer(raw['fee']),report)
            owner_name(row.buyer)
            if row.buyer not in owners or report.owner not in owners or row.buyer==report.owner or row.period>period or report.end>=row.period or row.fee!=self.policy.report_fee:
                raise ValueError('Invalid purchased report')
            if report not in self._call('native:economy','reports',report.owner):
                raise ValueError('Purchased report differs from publication')
            if not any(e.owner==row.buyer and e.period==row.period and e.kind=='report-fee' and e.cash==-row.fee for e in entries):
                raise ValueError('Purchase has no payment receipt')
            purchases.append(row)
        if any(sum(row.buyer==owner for row in purchases)>self.policy.purchase_limit for owner in owners):raise ValueError('Purchase history exceeds policy')
        for loan in loans.values():
            if not any(row.owner==loan.owner and row.period==loan.taken and row.kind=='loan' and row.cash==loan.principal for row in entries):
                raise ValueError('Loan has no disbursement receipt')
        for owner in owners:
            due = sum(row.liability_delta for row in entries if row.owner==owner)
            if due!=(loans[owner].due if owner in loans else 0):raise ValueError('Debt differs from finance ledger')
        self._rate,self._next_period = data['rate'],period
        self._loans,self._entries,self._purchases = loans,entries,purchases
