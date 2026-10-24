"""Paid, account-wide deterministic compute quotas and leased script slots."""
from copy import deepcopy
import math

from engine.runtime import GameMod, RuleSection, Stage
from mods.stock.calls import CALLS


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} 必须是至少 {minimum} 的整数')
    return value


class ComputePools:
    def __init__(self, parameters):
        self.parameters = dict(parameters)
        self.accounts = {}

    def bind_state(self, world, key):
        self.world = world
        return self

    def open_account(self, actor):
        self.accounts.setdefault(actor, dict(pool=1, slots=1, research_unlocked=False,
                                             research=False, steps=0, calls=0,
                                             fees_paid=0.0, paid_today=0.0, notice=''))

    def _row(self, actor):
        if actor not in self.accounts:
            raise ValueError('没有该账户的算力池')
        return self.accounts[actor]

    def _price(self, row):
        p = self.parameters
        return (p['PoolDailyFee'] * (2 ** (row['pool'] - 1) - 1)
                + p['SlotDailyFee'] * (row['slots'] - 1)
                + p['ResearchDailyFee'] * int(row['research']))

    def _pay(self, actor, amount):
        if amount:
            self.world.runtime.object_call(self.world, 'stock:portfolio', 'spend',
                                           actor, amount, 'compute')

    def buy(self, actor, kind):
        row = self._row(actor)
        candidate = deepcopy(row)
        extra = 0
        if kind == 'pool':
            if row['pool'] >= self.parameters['MaxPools']:
                raise ValueError('算力池已达上限')
            candidate['pool'] += 1
        elif kind == 'slot':
            if row['slots'] >= self.parameters['MaxSlots']:
                raise ValueError('交易计算槽已达上限')
            candidate['slots'] += 1
        elif kind == 'research':
            if row['research']:
                raise ValueError('行动内研究槽已经启用')
            if not row['research_unlocked']:
                extra = self.parameters['ResearchUnlockFee']
            candidate.update(research=True, research_unlocked=True)
        else:
            raise ValueError('kind 只能是 pool、slot 或 research')
        fee = max(0, self._price(candidate) - row['paid_today']) + extra
        self._pay(actor, fee)  # Validate/pay before committing the resource change.
        candidate['fees_paid'] += fee
        candidate['paid_today'] = max(row['paid_today'], self._price(candidate))
        candidate['notice'] = '已立即生效；所有槽共享本日已用额度，不重置用量。'
        self.accounts[actor] = candidate
        return dict(paid=fee, **self.status(actor))

    def release(self, actor, kind):
        row = self._row(actor)
        if kind == 'pool' and row['pool'] > 1:
            row['pool'] -= 1
        elif kind == 'slot' and row['slots'] > 1:
            row['slots'] -= 1
        elif kind == 'research' and row['research']:
            row['research'] = False
        else:
            raise ValueError('没有可退租的该类资源（免费池和免费槽保留）')
        row['notice'] = '已退租；当日费用不退，已用预算不重置，源码保留。'
        return self.status(actor)

    def allowed(self, actor, slot):
        row = self._row(actor)
        if slot == 'research':
            return row['research']
        return str(slot) in tuple(str(i) for i in range(1, row['slots'] + 1))

    def status(self, actor):
        row = self._row(actor)
        p = self.parameters
        steps, calls = row['pool'] * p['StepsPerPool'], row['pool'] * p['CallsPerPool']
        return dict(pool=row['pool'], slots=row['slots'], research=row['research'],
                    research_unlocked=row['research_unlocked'], steps_limit=steps,
                    calls_limit=calls, steps_used=row['steps'], calls_used=row['calls'],
                    steps_remaining=max(0, steps - row['steps']),
                    calls_remaining=max(0, calls - row['calls']),
                    daily_fee=self._price(row), fees_paid=row['fees_paid'],
                    notice=row['notice'], max_pools=p['MaxPools'], max_slots=p['MaxSlots'],
                    prices=dict(next_pool=p['PoolDailyFee'] * 2 ** (row['pool'] - 1),
                                slot_daily=p['SlotDailyFee'], research_daily=p['ResearchDailyFee'],
                                research_unlock=0 if row['research_unlocked'] else p['ResearchUnlockFee']))

    def consume(self, actor, steps, calls):
        integer(steps, 'steps'); integer(calls, 'calls')
        row = self._row(actor)
        current = self.status(actor)
        if steps > current['steps_remaining'] or calls > current['calls_remaining']:
            raise ValueError('共享算力池额度不足')
        row['steps'] += steps
        row['calls'] += calls

    def reset(self):
        for actor in self.world.nation_actors:
            row = self._row(actor)
            row.update(steps=0, calls=0, paid_today=0.0, notice='')
            fee = self._price(row)
            try:
                self._pay(actor, fee)
            except ValueError:
                row.update(pool=1, slots=1, research=False,
                           notice='资金不足或风控冻结，已降为免费池和免费槽；研究槽解锁资格及源码保留。')
            else:
                row['fees_paid'] += fee
                row['paid_today'] = fee

    def to_save(self):
        return deepcopy(self.accounts)

    def restore(self, state):
        if not isinstance(state, dict):
            raise ValueError('无效的算力池存档')
        for row in state.values():
            if (type(row) is not dict or type(row.get('pool')) is not int
                    or not 1 <= row['pool'] <= self.parameters['MaxPools']
                    or type(row.get('slots')) is not int
                    or not 1 <= row['slots'] <= self.parameters['MaxSlots']
                    or type(row.get('research')) is not bool
                    or type(row.get('research_unlocked')) is not bool
                    or row['research'] and not row['research_unlocked']):
                raise ValueError('无效的算力池档位')
            for field in ('steps', 'calls'):
                integer(row.get(field), field)
            for field in ('fees_paid', 'paid_today'):
                value = row.get(field)
                if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                    raise ValueError('无效的计算资源费用')
            if (row['steps'] > self.parameters['StepsPerPool'] * self.parameters['MaxPools']
                    or row['calls'] > self.parameters['CallsPerPool'] * self.parameters['MaxPools']):
                raise ValueError('无效的已用计算量')
        self.accounts = deepcopy(state)


class ComputeAPI:
    def __init__(self, world): self.world = world
    def status(self, actor): return self.world.compute.status(actor)
    def buy(self, actor, kind): return self.world.compute.buy(actor, kind)
    def release(self, actor, kind): return self.world.compute.release(actor, kind)


class ComputeMod(GameMod):
    def mixins(self): return ()
    def configure(self, declaration, path):
        super().configure(declaration, path)
        p = declaration['Parameters']
        for key, value in p.items():
            integer(value, key, 1 if key in ('StepsPerPool', 'CallsPerPool', 'MaxPools', 'MaxSlots') else 0)
        if p['MaxPools'] > 4 or p['MaxSlots'] > 4:
            raise ValueError('当前实现最多4档算力和4个交易槽')
    def initialize_world(self, world):
        world.compute = ComputePools(self.declaration['Parameters'])
    def objects(self, world): return {'stock:compute': world.compute}
    def player_api(self, world): return ComputeAPI(world)
    def before_tool(self, world, actor, name, arguments):
        CALLS.note(actor, world.clock.period())
        if name == 'compute':
            self.runtime.object_call(world, 'stock:clock', 'charge', actor)
    def save_state(self, world): return world.compute.to_save()
    def load_state(self, world, state): world.compute.restore(state)
    def stages(self):
        return (Stage('stock.compute.reset', lambda w, c: w.compute.reset(),
                      hook='cycle.end', after=('stock.clock.advance',)),)
    def rule_sections(self, world=None):
        p = self.declaration['Parameters']
        return (RuleSection('算力池与计算槽',
            f'每账户每天免费 {p["StepsPerPool"]} 计算单位、{p["CallsPerPool"]} 次 API、1 个自动交易槽。'
            f'compute(action=buy,kind=pool) 增购一档，每档同量，最多 {p["MaxPools"]} 档；'
            f'额外池每日费用依次 {p["PoolDailyFee"]}、{p["PoolDailyFee"]*2}、{p["PoolDailyFee"]*4}。'
            f'kind=slot 增加交易槽，最多 {p["MaxSlots"]} 槽，每额外槽每日 {p["SlotDailyFee"]}。'
            f'kind=research 解锁行动内只读研究槽，首次 {p["ResearchUnlockFee"]}，每日 {p["ResearchDailyFee"]}。'
            '购买立即生效，当日按整日计费；以后每日自动续租，现金不足降回免费档。'
            'compute(action=release,kind=...) 退租，当日不退款；源码和研究解锁资格保留。'
            '所有交易槽与研究槽共享每日总预算，换槽、改代码、重跑均不重置用量。'
            '研究槽 script(action=run,slot=research,inputs={...}) 仅在自己行动时手动运行，'
            '只能读已知数据和输出，不能下单、借贷、修改脚本或推进行情。费用从游戏现金扣除。',
            ('算力', '计算槽', '研究', 'compute'), '购买、续租、共享额度与研究槽', system=True),)
