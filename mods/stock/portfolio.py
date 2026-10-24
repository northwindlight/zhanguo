"""Single brokerage ledger: reservations, immediate fills, loans and margin."""
from copy import deepcopy
import math

from engine.runtime import GameMod, ModError, RuleSection, Stage
from engine.state import active_unit


def money(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('金额必须是有限的正数')
    rounded = round(value, 2)
    if rounded <= 0: raise ValueError('金额至少为0.01')
    return rounded


class Brokerage:
    def __init__(self, initial_cash=100000, interest_rate=.02, margin_ratio=.5,
                 base_credit_ratio=.5, short_margin=.5, short_maintenance=.3,
                 borrow_rate=.0002):
        self.initial_cash = initial_cash
        self.interest_rate = interest_rate
        self.margin_ratio = margin_ratio
        self.base_credit = round(initial_cash * base_credit_ratio, 2)
        self.short_margin = short_margin
        self.short_maintenance = short_maintenance
        self.borrow_rate = borrow_rate
        self.accounts = {}
        self.applied = set()
        self.fees_collected = 0.0
        self.resource_fees = 0.0
        self.intelligence_fees = 0.0
        self._world = None
        self._domain = None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:
            raise ModError('portfolio 已绑定到别的世界')
        self._world = world
        self._domain = world.runtime.state_owners[key]
        return self

    def _write(self):
        if active_unit.get() not in (None, self._domain):
            raise ModError('只能通过声明的接口改动账户')

    def _market(self, method, *args):
        return self._world.runtime.object_call(self._world, 'stock:market', method, *args)

    def _account(self, actor):
        if actor not in self.accounts:
            raise ValueError(f'没有这个账户：{actor}')
        return self.accounts[actor]

    def open_account(self, actor, name='', background=False, cash=None, shares=None):
        self._write()
        if actor not in self.accounts:
            self.accounts[actor] = dict(name=name or actor,
                cash=float(self.initial_cash if cash is None else cash), debt=0.0,
                positions=deepcopy(shares or {}), reservations={}, equity=[],
                realized_pnl=0.0, interest_paid=0.0, borrow_fees=0.0,
                compute_expenses=0.0, intelligence_expenses=0.0, liquidations=[], alive=True,
                retired_round=None, background=background, risk_frozen=False, risk_notice='',
                final_cash=None, final_round=None, final_rank=None,
                final_liquidations=[], final_repaid=0.0)
        return self.accounts[actor]

    def active_actors(self):
        return tuple(a for a, row in self.accounts.items() if row['alive'] and not row['background'])

    def begin_day(self, period):
        self._write()
        prefixes = (f'{period}:', f'{period-1}:')
        self.applied = {ident for ident in self.applied if ident.startswith(prefixes)}

    def _values(self, account):
        long_value = short_value = 0.0
        for symbol, position in account['positions'].items():
            value = position['quantity'] * self._market('price_of', symbol)
            long_value += max(0, value)
            short_value += max(0, -value)
        return round(long_value, 2), round(short_value, 2)

    def holdings_value(self, account):
        long_value, short_value = self._values(account)
        return round(long_value - short_value, 2)

    def credit_limit(self, account):
        long_value, short_value = self._values(account)
        return round(max(0, self.base_credit + self.margin_ratio * long_value - short_value), 2)

    def free_cash(self, account):
        _, short_value = self._values(account)
        reserved = sum(r['cash'] for r in account['reservations'].values())
        return round(account['cash'] - (1 + self.short_margin) * short_value - reserved, 2)

    def position_of(self, actor):
        account = self._account(actor)
        return dict(cash=max(0, self.free_cash(account)),
                    shares={s: p['quantity'] for s, p in account['positions'].items()})

    def holdings(self, actor):
        account = self._account(actor)
        positions = []
        for symbol, position in sorted(account['positions'].items()):
            quantity, cost = position['quantity'], position['cost']
            price = self._market('price_of', symbol)
            value = round(quantity * price, 2)
            average = round(cost / abs(quantity), 4) if quantity else 0
            pnl = value - cost if quantity > 0 else cost + value
            reserved = sum(r['quantity'] for r in account['reservations'].values()
                           if r['symbol'] == symbol and r['side'] in ('sell', 'cover'))
            positions.append(dict(symbol=symbol, name=self._market('name_of', symbol),
                quantity=quantity, side='long' if quantity > 0 else 'short',
                available_quantity=max(0, abs(quantity) - reserved), avg_cost=average,
                price=price, market_value=value, pnl=round(pnl, 2),
                pnl_pct=round(pnl / cost * 100, 3) if cost else 0))
        long_value, short_value = self._values(account)
        wealth = round(account['cash'] + long_value - short_value - account['debt'], 2)
        return dict(name=account['name'], cash=account['cash'],
            available_cash=max(0, self.free_cash(account)),
            reserved_cash=round(sum(r['cash'] for r in account['reservations'].values()), 2),
            short_collateral=round((1 + self.short_margin) * short_value, 2),
            debt=account['debt'], net_cash=round(account['cash'] - account['debt'], 2),
            positions=positions, holdings_value=round(long_value - short_value, 2),
            long_value=long_value, short_value=short_value, wealth=wealth,
            initial_cash=self.initial_cash, return_pct=round((wealth/self.initial_cash-1)*100, 3),
            credit_limit=self.credit_limit(account),
            margin_limit=round(self.base_credit + self.margin_ratio * long_value, 2),
            short_maintenance=round(self.short_maintenance * short_value, 2),
            interest_paid=account['interest_paid'], borrow_fees=account['borrow_fees'],
            realized_pnl=account['realized_pnl'], compute_expenses=account['compute_expenses'],
            intelligence_expenses=account['intelligence_expenses'],
            risk_frozen=account['risk_frozen'], risk_notice=account['risk_notice'],
            final_cash=account.get('final_cash'), final_round=account.get('final_round'),
            final_rank=account.get('final_rank'), final_repaid=account.get('final_repaid', 0.0),
            liquidations=deepcopy(account['liquidations'][-5:]), alive=account['alive'],
            retired_round=account['retired_round'], equity_curve=deepcopy(account['equity'][-12:]))

    def equity_curve(self, actor):
        return deepcopy(self._account(actor)['equity'])

    def spend(self, actor, amount, category='compute'):
        self._write()
        amount = money(amount)
        if category not in ('compute', 'intelligence'):
            raise ValueError('未知的服务费用类别')
        account = self._account(actor)
        if account['risk_frozen']: raise ValueError('账户处于风控冻结，不能购买资源或情报')
        if amount > self.free_cash(account) + 1e-9:
            raise ValueError('可用现金不足，冻结资金不能用来支付计算资源')
        account['cash'] = round(account['cash'] - amount, 2)
        field = category + '_expenses'
        account[field] = round(account[field] + amount, 2)
        if category == 'compute': self.resource_fees = round(self.resource_fees + amount, 2)
        else: self.intelligence_fees = round(self.intelligence_fees + amount, 2)
        return dict(paid=amount, category=category)

    def participants(self):
        return [dict(id=a, name=row['name'], alive=row['alive'])
                for a, row in self.accounts.items() if not row['background']]

    def intelligence_snapshot(self, actor):
        account = self._account(actor)
        if account['background']: raise ValueError('商业谍报只能针对玩家')
        book = self.holdings(actor)
        result = {k: deepcopy(book[k]) for k in ('name', 'cash', 'available_cash', 'debt',
                    'long_value', 'short_value', 'holdings_value')}
        result['positions'] = [{k: row[k] for k in ('symbol', 'name', 'side', 'quantity', 'market_value')}
                               for row in book['positions']]
        return result

    def borrow(self, actor, amount):
        self._write()
        amount = money(amount)
        account = self._account(actor)
        limit = self.credit_limit(account)
        if account['risk_frozen']: raise ValueError('账户处于风控冻结，不能增加贷款')
        if account['debt'] + amount > limit + 1e-9:
            raise ValueError(f'超出授信额度：上限 {limit}')
        account['cash'] = round(account['cash'] + amount, 2)
        account['debt'] = round(account['debt'] + amount, 2)
        return dict(borrowed=amount, cash=account['cash'], debt=account['debt'], credit_limit=limit)

    def repay(self, actor, amount):
        self._write()
        account = self._account(actor)
        amount = min(money(amount), account['debt'], max(0, self.free_cash(account)))
        if amount <= 0:
            raise ValueError('没有可还的钱')
        account['cash'] = round(account['cash'] - amount, 2)
        account['debt'] = round(account['debt'] - amount, 2)
        return dict(repaid=amount, cash=account['cash'], debt=account['debt'])

    def short_used(self, symbol):
        return sum(max(0, -a['positions'].get(symbol, {}).get('quantity', 0))
                   + sum(r['quantity'] for r in a['reservations'].values()
                         if r['symbol'] == symbol and r['side'] == 'short')
                   for a in self.accounts.values())

    def reserve(self, actor, order, commission):
        self._write()
        account = self._account(actor)
        if not account['alive']:
            raise ValueError('账户已破产')
        side, symbol, quantity = order['side'], order['symbol'], order['quantity']
        if account['risk_frozen'] and side in ('buy', 'short'):
            raise ValueError('账户处于风控冻结，只能减仓或还款')
        held = account['positions'].get(symbol, {}).get('quantity', 0)
        existing = [r for r in account['reservations'].values() if r['symbol'] == symbol]
        if any((r['side'] in ('buy', 'sell')) != (side in ('buy', 'sell')) for r in existing):
            raise ValueError('同一股票的多空委托不能混合；请先平仓和撤单')
        reserved_qty = sum(r['quantity'] for r in existing if r['side'] == side)
        if (side in ('buy', 'sell') and held < 0) or (side in ('short', 'cover') and held > 0):
            raise ValueError('请先平掉相反方向的仓位')
        if side == 'sell' and quantity > held - reserved_qty:
            raise ValueError('持仓不足或已被其他委托冻结')
        if side == 'cover' and quantity > -held - reserved_qty:
            raise ValueError('可回补的空头持仓不足')
        if side == 'short' and self.short_used(symbol) + quantity > self._market('borrow_limit', symbol):
            raise ValueError('可借券数量不足')
        notional = order['limit_price'] * quantity
        if side == 'buy':
            reserve = notional * (1 + commission)
        elif side == 'short':
            reserve = notional * (self.short_margin + commission)
        elif side == 'cover':
            if notional * (1 + commission) > account['cash'] + 1e-9:
                raise ValueError('回补现金不足')
            reserve = max(0, notional * (1 + commission)
                          - (1 + self.short_margin) * self._market('price_of', symbol) * quantity)
        else:
            reserve = 0
        reserve = round(reserve + .01, 2) if reserve else 0
        if reserve > max(0, self.free_cash(account)) + 1e-9:
            raise ValueError('现金不足或保证金不足')
        account['reservations'][str(order['id'])] = dict(cash=reserve, symbol=symbol,
                                                       side=side, quantity=quantity)

    def release(self, actor, order_id):
        self._write()
        self._account(actor)['reservations'].pop(str(order_id), None)

    def execute(self, fill, remaining=0):
        self._write()
        if fill['execution_id'] in self.applied:
            return False
        account = self._account(fill['actor'])
        symbol, side, quantity = fill['symbol'], fill['side'], fill['quantity']
        amount, fee = fill['amount'], fill['fee']
        position = account['positions'].get(symbol, dict(quantity=0, cost=0.0))
        if side in ('buy', 'short'):
            sign = 1 if side == 'buy' else -1
            if position['quantity'] * sign < 0:
                raise ModError('成交不能隐式翻转多空仓位')
            position['quantity'] += sign * quantity
            position['cost'] = round(position['cost'] + amount + (fee if side == 'buy' else -fee), 2)
            account['positions'][symbol] = position
        else:
            if quantity > abs(position['quantity']):
                raise ModError('成交数量超过持仓')
            cost_out = round(position['cost'] * quantity / abs(position['quantity']), 2)
            position['quantity'] += quantity if side == 'cover' else -quantity
            position['cost'] = round(position['cost'] - cost_out, 2)
            pnl = amount - cost_out if side == 'sell' else cost_out - amount
            account['realized_pnl'] = round(account['realized_pnl'] + pnl - fee, 2)
            if position['quantity'] == 0:
                account['positions'].pop(symbol, None)
        account['cash'] = round(account['cash'] + (amount if side in ('sell', 'short') else -amount) - fee, 2)
        self.fees_collected = round(self.fees_collected + fee, 2)
        key = str(fill.get('order_id'))
        reservation = account['reservations'].get(key)
        if reservation:
            if remaining:
                ratio = remaining / reservation['quantity']
                reservation.update(cash=round(reservation['cash'] * ratio, 2), quantity=remaining)
            else:
                self.release(fill['actor'], key)
        self.applied.add(fill['execution_id'])
        return True

    def risk_check(self, round_number):
        records = []
        for actor in tuple(self.active_actors()):
            account = self.accounts[actor]
            long_value, short_value = self._values(account)
            wealth = account['cash'] + long_value - short_value - account['debt']
            if (account['debt'] <= self.base_credit + self.margin_ratio * long_value + 1e-9
                    and wealth >= self.short_maintenance * short_value - 1e-9
                    and account['cash'] >= 0):
                account.update(risk_frozen=False, risk_notice='')
                continue
            account.update(risk_frozen=True, risk_notice='保证金或贷款风险超限，正在减仓')
            self._market('cancel_actor', actor)
            if account['debt'] > 0 and self.free_cash(account) > 0:
                self.repay(actor, min(account['debt'], self.free_cash(account)))
            records.extend(self.force_liquidate(self._world, round_number, actor, account))
            long_value, short_value = self._values(account)
            wealth = account['cash'] + long_value - short_value - account['debt']
            if (account['debt'] <= self.base_credit + self.margin_ratio * long_value + 1e-9
                    and wealth >= self.short_maintenance * short_value - 1e-9
                    and account['cash'] >= 0):
                account.update(risk_frozen=False, risk_notice='')
            if self.holdings(actor)['wealth'] <= 0:
                account.update(alive=False, retired_round=round_number)
        return records

    def force_liquidate(self, world, round_number, actor, account):
        self._write()
        records = []
        for symbol, position in list(account['positions'].items()):
            long_value, short_value = self._values(account)
            wealth = account['cash'] + long_value - short_value - account['debt']
            if (account['debt'] <= self.base_credit + self.margin_ratio * long_value + 1e-9
                    and wealth >= self.short_maintenance * short_value - 1e-9 and account['cash'] >= 0):
                break
            side = 'sell' if position['quantity'] > 0 else 'cover'
            quantity = abs(position['quantity'])
            try:
                fill = self._market('liquidate', actor, side, symbol, quantity)
            except ValueError as exc:
                account['risk_notice'] = str(exc) + '；保持风险冻结，等待后续流动性或主动减仓'
                continue
            if account['debt'] > 0 and self.free_cash(account) > 0:
                repaid = self.repay(actor, min(account['debt'], self.free_cash(account)))['repaid']
            else:
                repaid = 0
            records.append(dict(round=round_number, symbol=symbol, quantity=quantity,
                                side=side, price=fill['price'], amount=fill['amount'],
                                fee=fill['fee'], repaid=repaid))
        account['liquidations'].extend(records)
        return records

    def finalize(self, actor, round_number):
        """最终回合清算：撤单 → 按最后价平掉全部仓位（照收佣金）→ 用现金尽量还贷。

        结算现金 = 现金 − 仍未还清的贷款；这就是排名口径。
        """
        self._write()
        account = self._account(actor)
        self._market('cancel_actor', actor)
        records = []
        for symbol, position in list(account['positions'].items()):
            quantity = abs(position['quantity'])
            if not quantity:
                continue
            side = 'sell' if position['quantity'] > 0 else 'cover'
            fill = self._market('final_close', actor, side, symbol, quantity)
            records.append(dict(symbol=symbol, side=side, quantity=quantity,
                                price=fill['price'], amount=fill['amount'], fee=fill['fee']))
            account['liquidations'].append(dict(round=round_number, symbol=symbol,
                quantity=quantity, side=side, price=fill['price'], amount=fill['amount'],
                fee=fill['fee'], repaid=0.0))
        repaid = 0.0
        if account['debt'] > 0 and self.free_cash(account) > 0:
            repaid = self.repay(actor, min(account['debt'], self.free_cash(account)))['repaid']
        final_cash = round(account['cash'] - account['debt'], 2)
        account.update(final_cash=final_cash, final_round=round_number,
                       final_liquidations=records, final_repaid=repaid)
        return dict(cash=round(account['cash'], 2), debt=round(account['debt'], 2),
                    final_cash=final_cash, repaid=repaid, liquidations=records)

    def settle(self, world, round_number, fills):
        self._write()
        # Matching applies fills immediately; execution IDs prevent double booking.
        for fill in fills:
            self.execute(fill, fill.get('remaining', 0))
        for actor in self.active_actors():
            account = self.accounts[actor]
            interest = round(account['debt'] * self.interest_rate, 2)
            _, short_value = self._values(account)
            borrow_fee = round(short_value * self.borrow_rate, 2)
            account['cash'] = round(account['cash'] - interest - borrow_fee, 2)
            account['interest_paid'] += interest
            account['borrow_fees'] += borrow_fee
            self.fees_collected = round(self.fees_collected + interest + borrow_fee, 2)
        records = self.risk_check(round_number)
        for actor, account in self.accounts.items():
            if not account['background']:
                wealth = self.holdings(actor)['wealth']
                account['equity'].append(dict(round=round_number, wealth=wealth))
                if wealth <= 0:
                    account.update(alive=False, retired_round=round_number)
        return records

    def to_save(self):
        return dict(accounts=deepcopy(self.accounts), applied=sorted(self.applied),
                    fees_collected=self.fees_collected, resource_fees=self.resource_fees,
                    intelligence_fees=self.intelligence_fees)

    def restore(self, state):
        self._write()
        if not isinstance(state, dict) or set(state) != {'accounts', 'applied', 'fees_collected', 'resource_fees', 'intelligence_fees'}:
            raise ValueError('无效的券商账本存档')
        self.accounts = deepcopy(state['accounts'])
        self.applied = set(state['applied'])
        self.fees_collected = state['fees_collected']
        self.resource_fees = state['resource_fees']
        self.intelligence_fees = state['intelligence_fees']
        for account in self.accounts.values():
            if not all(math.isfinite(account[k]) for k in ('cash', 'debt')) or account['debt'] < 0:
                raise ValueError('无效的券商资金')


class ActorScope:
    @property
    def nation_actors(self): return self.portfolio.active_actors()


class PortfolioAPI:
    def __init__(self, world): self.world = world
    def holdings(self, actor): return self.world.portfolio.holdings(actor)
    def borrow(self, actor, amount): return self.world.portfolio.borrow(actor, amount)
    def repay(self, actor, amount): return self.world.portfolio.repay(actor, amount)
    def equity_curve(self, actor): return self.world.portfolio.equity_curve(actor)


class PortfolioMod(GameMod):
    def mixins(self): return (ActorScope,)
    def initialize_world(self, world):
        p = self.declaration['Parameters']
        world.portfolio = Brokerage(p['InitialCash'], p['InterestRate'], p['MarginRatio'],
                                    p['BaseCreditRatio'], p['ShortInitialMargin'],
                                    p['ShortMaintenance'], p['BorrowRate'])
    def objects(self, world): return {'stock:portfolio': world.portfolio}
    def save_state(self, world): return world.portfolio.to_save()
    def load_state(self, world, state): world.portfolio.restore(state)
    def player_api(self, world): return PortfolioAPI(world)
    def before_tool(self, world, actor, name, arguments):
        if name == 'bank':
            self.runtime.object_call(world, 'stock:clock', 'charge', actor)
    def stages(self):
        return (Stage('stock.portfolio.settle', self._settle, hook='market.settlement',
                      after=('stock.market.settle',)),)
    def _settle(self, world, changes):
        changes['liquidations'] = world.portfolio.settle(world, world.clock.period(), world.market.fills())
    def rule_sections(self, world=None):
        p = self.declaration['Parameters']
        return (RuleSection('账户、借贷与多空保证金',
            f'初始现金 {p["InitialCash"]}。净值=现金+多头市值−空头市值−现金借款。'
            f'现金借款每回合利息 {p["InterestRate"]*100:g}%，只从现金扣一次。'
            '买卖成交立即入账；挂单先冻结现金、股票或保证金，撤单、到期释放。'
            'buy/sell 是开多/平多，short/cover 是借券卖空/买回还券，同股票先平反向仓位。'
            f'借券有限，卖空收入及额外 {p["ShortInitialMargin"]*100:g}% 保证金受限；'
            f'维持保证金 {p["ShortMaintenance"]*100:g}%，每日借券费 {p["BorrowRate"]*100:g}%。'
            '风控会撤风险挂单、使用可用现金还债，再由清算银行按标记价及清算折价接盘减仓；'
            '清算会实际扣付现金和转移股票，净值非正则破产。算力费用计入净值。',
            ('账户', '借贷', '做空', '保证金', '强平'), '资金恒等式、做空和风控', system=True),)
