"""账户、持仓、借贷与银行强制平仓。

账户口径：

* 净值 = 现金 + 持仓市值 − 负债；负债是银行借款，计入净值但要付利息；
* 每回合末按未偿本金的 `InterestRate` 扣息；
* 授信额度 = 基础授信 + 追保比例 × 持仓市值；
* **银行强制平仓**：负债超过追保比例 × 持仓市值时，银行按市价（同样吃冲击成本）
  从最大的持仓开始砍仓，直到回到追保线以内；砍完仍资不抵债就打到净值非正，
  账户破产退市，不再参与回合。
"""
from __future__ import annotations

from engine.runtime import GameMod, ModError, RuleSection, Stage
from engine.state import active_unit


class Brokerage:
    """账户簿：世界的 `world.portfolio`。"""

    def __init__(self, initial_cash=100000, interest_rate=0.02, margin_ratio=0.5,
                 base_credit_ratio=0.5):
        self.initial_cash = initial_cash
        self.interest_rate = interest_rate
        self.margin_ratio = margin_ratio
        self.base_credit_ratio = base_credit_ratio
        self.base_credit = round(initial_cash * base_credit_ratio, 2)
        self.accounts = {}
        self._world = None
        self._key = None
        self._domain = None

    # ── 状态绑定 ────────────────────────────────────────────────
    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:
            raise ModError('portfolio 已绑定到别的世界')
        self._world = world
        self._key = key
        self._domain = world.runtime.state_owners.get(key)
        return self

    def _write(self):
        if active_unit.get() not in (None, self._domain):
            raise ModError('只能通过声明的接口改动账户')

    def _account(self, actor):
        account = self.accounts.get(actor)
        if account is None:
            raise ValueError(f'没有这个账户：{actor}')
        return account

    # ── 账户开立与名册 ──────────────────────────────────────────
    def open_account(self, actor, name=''):
        self._write()
        if actor in self.accounts:
            return self.accounts[actor]
        self.accounts[actor] = {
            'name': name or actor,
            'cash': float(self.initial_cash),
            'debt': 0.0,
            'positions': {},
            'equity': [],
            'realized_pnl': 0.0,
            'interest_paid': 0.0,
            'liquidations': [],
            'alive': True,
            'retired_round': None,
        }
        return self.accounts[actor]

    def active_actors(self):
        return tuple(actor for actor, account in self.accounts.items() if account['alive'])

    # ── 只读视图 ────────────────────────────────────────────────
    def position_of(self, actor):
        account = self._account(actor)
        return {'cash': account['cash'],
                'shares': {symbol: position['quantity']
                           for symbol, position in account['positions'].items()}}

    def holdings_value(self, account):
        total = 0.0
        runtime = self._world.runtime
        for symbol, position in account['positions'].items():
            total += position['quantity'] * runtime.object_call(self._world, 'stock:market', 'price_of', symbol)
        return round(total, 2)

    def credit_limit(self, account):
        return round(self.base_credit + self.margin_ratio * self.holdings_value(account), 2)

    def holdings(self, actor):
        account = self._account(actor)
        world = self._world
        runtime = world.runtime
        positions = []
        holdings_value = 0.0
        for symbol, position in sorted(account['positions'].items()):
            price = runtime.object_call(world, 'stock:market', 'price_of', symbol)
            value = round(position['quantity'] * price, 2)
            holdings_value += value
            average = round(position['cost'] / position['quantity'], 4) if position['quantity'] else 0.0
            positions.append({
                'symbol': symbol,
                'name': runtime.object_call(world, 'stock:market', 'name_of', symbol),
                'quantity': position['quantity'],
                'avg_cost': average,
                'price': price,
                'market_value': value,
                'pnl': round(value - position['cost'], 2),
                'pnl_pct': round((price / average - 1) * 100, 3) if average else 0.0,
            })
        holdings_value = round(holdings_value, 2)
        wealth = round(account['cash'] + holdings_value - account['debt'], 2)
        return {
            'name': account['name'],
            'cash': account['cash'],
            'debt': account['debt'],
            'net_cash': round(account['cash'] - account['debt'], 2),
            'positions': positions,
            'holdings_value': holdings_value,
            'wealth': wealth,
            'initial_cash': self.initial_cash,
            'return_pct': round((wealth / self.initial_cash - 1) * 100, 3),
            'credit_limit': self.credit_limit(account),
            'margin_limit': round(self.margin_ratio * holdings_value, 2),
            'interest_paid': account['interest_paid'],
            'realized_pnl': account.get('realized_pnl', 0.0),
            'liquidations': account['liquidations'][-5:],
            'alive': account['alive'],
            'retired_round': account['retired_round'],
            'equity_curve': account['equity'][-12:],
        }

    def equity_curve(self, actor):
        account = self._account(actor)
        return list(account['equity'])

    # ── 借贷 ────────────────────────────────────────────────────
    def borrow(self, actor, amount):
        """向银行借钱：现金进账，负债上升，受授信额度限制。"""
        self._write()
        account = self._account(actor)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ValueError('借款金额必须是数字。')
        amount = float(amount)
        if amount <= 0:
            raise ValueError('借款金额必须大于 0。')
        limit = self.credit_limit(account)
        if account['debt'] + amount > limit + 1e-9:
            raise ValueError(
                f'超出授信额度：上限 {limit}，当前负债 {round(account["debt"], 2)}。'
                f'授信 = 基础授信 {self.base_credit} + 追保比例 {self.margin_ratio} × 持仓市值。')
        account['cash'] = round(account['cash'] + amount, 2)
        account['debt'] = round(account['debt'] + amount, 2)
        return {'borrowed': round(amount, 2), 'cash': account['cash'],
                'debt': account['debt'], 'credit_limit': limit}

    def repay(self, actor, amount):
        """还钱：先扣现金再减负债。"""
        self._write()
        account = self._account(actor)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ValueError('还款金额必须是数字。')
        amount = float(amount)
        if amount <= 0:
            raise ValueError('还款金额必须大于 0。')
        amount = min(amount, account['debt'], account['cash'])
        if amount <= 1e-9:
            raise ValueError('没有可还的钱：现金或负债都不足。')
        account['cash'] = round(account['cash'] - amount, 2)
        account['debt'] = round(account['debt'] - amount, 2)
        return {'repaid': round(amount, 2), 'cash': account['cash'], 'debt': account['debt']}

    # ── 回合结算 ────────────────────────────────────────────────
    def settle(self, world, round_number, fills):
        """应用成交、计息、银行强制平仓，再算净值并判定破产。"""
        self._write()
        for fill in fills:
            account = self.accounts.get(fill['actor'])
            if account is None or not account['alive']:
                continue
            symbol = fill['symbol']
            if fill['side'] == 'buy':
                position = account['positions'].setdefault(symbol, {'quantity': 0, 'cost': 0.0})
                position['quantity'] += fill['quantity']
                position['cost'] = round(position['cost'] + fill['amount'], 2)
                account['cash'] = round(account['cash'] - fill['amount'] - fill['fee'], 2)
            else:
                position = account['positions'].get(symbol)
                if position is None:
                    continue
                sold = min(fill['quantity'], position['quantity'])
                ratio = sold / position['quantity'] if position['quantity'] else 0.0
                cost_out = round(position['cost'] * ratio, 2)
                position['quantity'] -= sold
                position['cost'] = round(position['cost'] - cost_out, 2)
                account['cash'] = round(account['cash'] + fill['amount'] - fill['fee'], 2)
                account['realized_pnl'] = round(
                    account['realized_pnl'] + fill['amount'] - fill['fee'] - cost_out, 2)
                if position['quantity'] <= 0:
                    account['positions'].pop(symbol, None)
        liquidations = []
        for actor, account in self.accounts.items():
            if not account['alive']:
                continue
            if account['debt'] > 0:
                # 利息只记一次：从现金里付，负债本金不膨胀。
                # （原来既扣现金又加负债，利息被算了两遍，净值凭空少一块。）
                interest = round(account['debt'] * self.interest_rate, 2)
                account['cash'] = round(account['cash'] - interest, 2)
                account['interest_paid'] = round(account['interest_paid'] + interest, 2)
            liquidations.extend(self.force_liquidate(world, round_number, actor, account))
            wealth = round(account['cash'] + self.holdings_value(account) - account['debt'], 2)
            account['equity'].append({'round': round_number, 'wealth': wealth})
            if wealth <= 0:
                account['alive'] = False
                account['retired_round'] = round_number
        return liquidations

    def force_liquidate(self, world, round_number, actor, account):
        """银行强制平仓：负债超过追保线就按市价砍仓，直到回到追保线以内。"""
        self._write()
        if account['debt'] <= 0:
            return []
        runtime = world.runtime
        limit = round(self.margin_ratio * self.holdings_value(account), 2)
        if account['debt'] <= limit + 1e-9:
            return []
        records = []
        positions = sorted(account['positions'].items(),
                           key=lambda item: item[1]['quantity'], reverse=True)
        for symbol, position in positions:
            if account['debt'] <= limit + 1e-9:
                break
            quantity = position['quantity']
            if quantity <= 0:
                continue
            quote = runtime.object_call(world, 'stock:market', 'sell_quote', symbol, quantity)
            price = quote['price']
            amount = quote['amount']
            fee = quote['fee']
            # 一、卖出：持仓换成现金，净值不变（只掉手续费）。
            account['cash'] = round(account['cash'] + amount - fee, 2)
            account['positions'].pop(symbol, None)
            # 二、拿这笔现金去还债：现金真的减少，负债才减少。
            #    （原来只加现金又减负债，净值凭空多出一笔。）
            repaid = min(amount, account['debt'])
            account['cash'] = round(account['cash'] - repaid, 2)
            account['debt'] = round(account['debt'] - repaid, 2)
            records.append({'round': round_number, 'symbol': symbol, 'quantity': quantity,
                            'price': price, 'amount': amount, 'fee': fee, 'repaid': repaid})
        account['liquidations'].extend(records)
        return records

    # ── 存档 ────────────────────────────────────────────────────
    def to_save(self):
        return {actor: dict(account) for actor, account in self.accounts.items()}

    def restore(self, state):
        self._write()
        self.accounts = {}
        for actor, account in state.items():
            self.accounts[actor] = {
                'name': account['name'],
                'cash': account['cash'],
                'debt': account['debt'],
                'positions': {symbol: dict(position) for symbol, position in account['positions'].items()},
                'equity': [dict(point) for point in account['equity']],
                'realized_pnl': account.get('realized_pnl', 0.0),
                'interest_paid': account.get('interest_paid', 0.0),
                'liquidations': [dict(record) for record in account.get('liquidations', ())],
                'alive': account.get('alive', True),
                'retired_round': account.get('retired_round'),
            }


class ActorScope:
    """混入 World 的类：行动者名册由账户簿给出。"""

    @property
    def nation_actors(self):
        return self.portfolio.active_actors()


class PortfolioAPI:
    """玩家侧 API：看持仓与净值、借贷、查净值曲线。"""

    def __init__(self, world):
        self.world = world

    def holdings(self, actor):
        return self.world.portfolio.holdings(actor)

    def borrow(self, actor, amount):
        return self.world.portfolio.borrow(actor, amount)

    def repay(self, actor, amount):
        return self.world.portfolio.repay(actor, amount)

    def equity_curve(self, actor):
        return self.world.portfolio.equity_curve(actor)


class PortfolioMod(GameMod):
    def mixins(self):
        return (ActorScope,)

    def numbers(self):
        cfg = self.declaration['Parameters']
        return {f'portfolio.{key}': value for key, value in cfg.items()}

    def initialize_world(self, world):
        cfg = self.declaration['Parameters']
        world.portfolio = Brokerage(cfg['InitialCash'], cfg['InterestRate'],
                                    cfg['MarginRatio'], cfg['BaseCreditRatio'])

    def objects(self, world):
        return {'stock:portfolio': world.portfolio}

    def save_state(self, world):
        return world.portfolio.to_save()

    def load_state(self, world, state):
        world.portfolio.restore(state)

    def player_api(self, world):
        return PortfolioAPI(world)

    def before_tool(self, world, actor, name, arguments):
        """借贷算一次窗口额度；只读事实不记。"""
        tool = self.runtime.tools.get(name)
        if tool is not None and tool.read_only:
            return
        self.runtime.object_call(world, 'stock:clock', 'charge', actor)

    def stages(self):
        return (Stage('stock.portfolio.settle', self._settle, hook='market.settlement',
                       after=('stock.market.settle',)),)

    def _settle(self, world, changes):
        runtime = self.runtime
        round_number = runtime.object_call(world, 'stock:clock', 'period')
        fills = runtime.object_call(world, 'stock:market', 'fills')
        liquidations = world.portfolio.settle(world, round_number, fills)
        changes.update(liquidations=liquidations)

    def rule_sections(self, world=None):
        cfg = self.declaration['Parameters']
        return (RuleSection(
            '账户、借贷与强制平仓',
            f'初始资金 {cfg["InitialCash"]}。净值 = 现金 + 持仓市值 − 负债。'
            f'回合中随时可以向银行借钱：授信额度 = 基础授信 + {cfg["MarginRatio"]} × 持仓市值；'
            f'借款进现金也进负债，每回合末按 {cfg["InterestRate"] * 100:.1f}% 计息。'
            f'负债超过 {cfg["MarginRatio"]} × 持仓市值时银行强制平仓：按市价（同样吃冲击成本）'
            f'从最大的持仓开始砍，直到回到追保线以内。净值跌破零就破产退市。',
            ('借贷', '借款', '还款', '强制平仓', '追保', '破产', '净值', '财富'),
            '账户口径、授信、计息与银行强制平仓',
            system=True),)
