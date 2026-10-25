"""Paid, account-wide deterministic compute quotas and leased script slots."""
from copy import deepcopy
import math

from engine.runtime import GameMod, RuleSection, Stage


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} 必须是至少 {minimum} 的整数')
    return value


class ComputePools:
    """每回合重买的算力与交易槽；研究槽按次收费，不占共享算力池。"""

    def __init__(self, parameters):
        self.parameters = dict(parameters)
        self.accounts = {}

    def bind_state(self, world, key):
        self.world = world
        return self

    def open_account(self, actor):
        self.accounts.setdefault(actor, dict(pool=1, slots=1, steps=0, calls=0,
                                             fees_paid=0.0, research_runs=0, notice=''))

    def _row(self, actor):
        if actor not in self.accounts:
            raise ValueError('没有该账户的算力池')
        return self.accounts[actor]

    def _pay(self, actor, amount):
        if amount:
            self.world.runtime.object_call(self.world, 'stock:portfolio', 'spend',
                                           actor, amount, 'compute')

    def _tier_price(self, kind, tier):
        prefix = 'Pool' if kind == 'pool' else 'Slot'
        return int(self.parameters[f'{prefix}FeeTier{tier}'])

    def buy(self, actor, kind):
        row = self._row(actor)
        if kind == 'pool':
            if row['pool'] >= self.parameters['MaxPools']:
                raise ValueError(f'算力池已达上限 {self.parameters["MaxPools"]} 档')
            tier = row['pool'] + 1
        elif kind == 'slot':
            if row['slots'] >= self.parameters['MaxSlots']:
                raise ValueError(f'交易槽已达上限 {self.parameters["MaxSlots"]} 个')
            tier = row['slots'] + 1
        else:
            raise ValueError('kind 只能是 pool 或 slot；研究槽按次计费，不用购买')
        fee = self._tier_price(kind, tier)
        self._pay(actor, fee)  # 先付钱再改资源
        row['pool' if kind == 'pool' else 'slots'] += 1
        row['fees_paid'] += fee
        row['notice'] = f'已购买第 {tier} 档；只在本回合有效，下回合归零需重买。'
        return dict(paid=fee, **self.status(actor))

    def release(self, actor, kind):
        row = self._row(actor)
        if kind == 'pool' and row['pool'] > 1:
            row['pool'] -= 1
        elif kind == 'slot' and row['slots'] > 1:
            row['slots'] -= 1
        else:
            raise ValueError('没有可退的该类资源（免费池和免费槽保留）')
        row['notice'] = '已退租；本回合费用不退，资源用到收盘。'
        return self.status(actor)

    def begin_research(self, actor):
        """研究槽按次收费：每次固定 1000 现金，每回合有次数上限，不占共享算力池。"""
        row = self._row(actor)
        cap = self.parameters['ResearchRunsPerRound']
        if row['research_runs'] >= cap:
            raise ValueError(f'本回合研究次数已用完（每回合 {cap} 次），下回合再来')
        fee = self.parameters['ResearchRunFee']
        self._pay(actor, fee)
        row['research_runs'] += 1
        row['fees_paid'] += fee
        return dict(paid=fee, runs_left=cap - row['research_runs'])

    def allowed(self, actor, slot):
        row = self._row(actor)
        if slot == 'research':
            return True
        return str(slot) in tuple(str(i) for i in range(1, row['slots'] + 1))

    def status(self, actor):
        row = self._row(actor)
        p = self.parameters
        steps, calls = row['pool'] * p['StepsPerPool'], row['pool'] * p['CallsPerPool']
        return dict(pool=row['pool'], slots=row['slots'],
                    steps_limit=steps, calls_limit=calls,
                    steps_used=row['steps'], calls_used=row['calls'],
                    steps_remaining=max(0, steps - row['steps']),
                    calls_remaining=max(0, calls - row['calls']),
                    research_runs=row['research_runs'],
                    research_runs_left=max(0, p['ResearchRunsPerRound'] - row['research_runs']),
                    fees_paid=row['fees_paid'], notice=row['notice'],
                    max_pools=p['MaxPools'], max_slots=p['MaxSlots'],
                    prices=dict(pool_tiers={str(t): self._tier_price('pool', t)
                                            for t in range(2, p['MaxPools'] + 1)},
                                slot_tiers={str(t): self._tier_price('slot', t)
                                            for t in range(2, p['MaxSlots'] + 1)},
                                research_run=p['ResearchRunFee'],
                                research_runs_per_round=p['ResearchRunsPerRound']))

    def consume(self, actor, steps, calls):
        integer(steps, 'steps'); integer(calls, 'calls')
        row = self._row(actor)
        current = self.status(actor)
        if steps > current['steps_remaining'] or calls > current['calls_remaining']:
            raise ValueError('共享算力池额度不足')
        row['steps'] += steps
        row['calls'] += calls

    def reset(self):
        """新回合：付费档位全部归零（要重买），用量与研究次数重置。"""
        for actor in self.world.nation_actors:
            row = self._row(actor)
            row.update(pool=1, slots=1, steps=0, calls=0, research_runs=0,
                       notice='新回合：算力池和交易槽已归零为免费档，需要请重买；研究次数已重置。')

    def to_save(self):
        return deepcopy(self.accounts)

    def restore(self, state):
        if not isinstance(state, dict):
            raise ValueError('无效的算力池存档')
        for row in state.values():
            if (type(row) is not dict
                    or type(row.get('pool')) is not int
                    or not 1 <= row['pool'] <= self.parameters['MaxPools']
                    or type(row.get('slots')) is not int
                    or not 1 <= row['slots'] <= self.parameters['MaxSlots']):
                raise ValueError('无效的算力池档位')
            for field in ('steps', 'calls', 'research_runs'):
                integer(row.get(field), field)
            value = row.get('fees_paid')
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
            f'【免费档】每回合免费 {p["StepsPerPool"]} 计算单位、{p["CallsPerPool"]} 次 API、1 个自动交易槽。'
            f'【每回合重买】算力池和交易槽只在本回合有效，新回合自动归零，需要重新购买：\n'
            f'  算力池第 2/3/4 档：{p["PoolFeeTier2"]} / {p["PoolFeeTier3"]} / {p["PoolFeeTier4"]} 现金每回合，'
            f'每档 +{p["StepsPerPool"]} 计算单位、+{p["CallsPerPool"]} 次 API；\n'
            f'  交易槽第 2/3/4 个：{p["SlotFeeTier2"]} / {p["SlotFeeTier3"]} / {p["SlotFeeTier4"]} 现金每回合，'
            '每多一个槽就多一份可并行的策略。\n'
            'compute(action=buy,kind=pool|slot) 是当回合购买，compute(action=release,...) 退租不退钱。'
            '算力池档数同时决定单次回调上限：第 N 档 = 50000 步 / 200 次 API 的 N 倍。'
            f'【研究槽】不需要购买，按次收费：每次 script(action=run,slot="research") 固定 '
            f'{p["ResearchRunFee"]} 现金，每回合最多 {p["ResearchRunsPerRound"]} 次；'
            '研究不占共享算力池，单次上限见规则「Python 策略与研究槽」。'
            '研究槽只能读行情、账户和已买报告，不能下单、借贷或推进行情。',
            ('算力', '计算槽', '研究', 'compute'), '购买、每回合重买、共享额度与研究槽', system=True),)
