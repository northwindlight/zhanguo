"""三类消费口径的价格成对检查；仅创建内存中的合成世界。

预置十支满血步兵，六期内不征召、不建设、不战斗。处理组每期只
买入或卖出补给。逐期核对真实库存扣减、军队状态和经济事实，最后
比较 settlement.settle 实际选取的三类消费。固定报价作为机制对照。
"""
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine.runtime import Runtime
from settlement import settle

ACTOR = 'research:qin'
RESOURCE = 'native:supply'
PERIODS = 6
UNITS = 10
TRADE = 100


def run(fixed, direction):
    runtime = Runtime.load(ROOT / 'mods.toml')
    if fixed:
        members = [u for u in runtime.units.values() if u.name != 'native:market-pricing']
        members += list(Runtime.load(ROOT / 'mods/examples/fixed_market_pricing').units.values())
        runtime = Runtime(members, runtime.distribution)
    world = runtime.world_class()()
    world.territory.configure(8, 73)
    world.nations.create(ACTOR, '合成秦', {'native:gold': 20000, RESOURCE: 2000})
    world.territory.create('research:capital', ACTOR, 1, 1,
                           terrain='native:plain', core=ACTOR)
    world.military.seed(ACTOR, 'native:infantry', 1, 1, count=UNITS)
    series = []
    for period in range(PERIODS):
        before = world.resources.balance(ACTOR, RESOURCE)
        if direction == 'buy':
            world.market.buy(ACTOR, RESOURCE, TRADE)
        elif direction == 'sell':
            world.market.sell(ACTOR, RESOURCE, TRADE)
        after_trade = world.resources.balance(ACTOR, RESOURCE)
        world.resolve_turn()
        world.phase = '行动'
        after = world.resources.balance(ACTOR, RESOURCE)
        units = world.military.snapshot(ACTOR)
        assert len(units) == UNITS and all(u.hp == 100 for u in units)
        assert after_trade - after == UNITS
        expected_trade = {'control': 0, 'buy': TRADE, 'sell': -TRADE}[direction]
        assert after_trade - before == expected_trade
        facts = [row.fact for row in world.economy.snapshot().events
                 if row.fact.owner == ACTOR and row.fact.period == period]
        consumption = [f for f in facts if f.category in ('recruit', 'military', 'administration')]
        assert len(consumption) == 1
        assert consumption[0].category == 'military'
        assert dict(consumption[0].inputs) == {RESOURCE: UNITS}
        assert not any(f.category == 'construction' for f in facts)
        series.append({'period': period, 'stock_before_trade': before,
                       'stock_after_trade': after_trade, 'stock_after_supply': after,
                       'consumption_quantities': {RESOURCE: UNITS},
                       'military_value': world.economy.turns(ACTOR)[-1].military.total,
                       'market_price': dict(world.market.snapshot().prices)[RESOURCE]})
    data = {'host': {'roster': {ACTOR: {'name': '合成秦'}}}}
    row = settle(data, world)[0]
    assert row['investment'] == 0
    assert isclose(row['consumption'], sum(s['military_value'] for s in series), rel_tol=1e-12)
    return {'fixed_prices': fixed, 'direction': direction, 'series': series,
            'consumption': row['consumption'], 'investment': row['investment'],
            'quantity': PERIODS * UNITS, 'fixed_reference_value': PERIODS * UNITS * 6}


def main():
    arms = {f'{pricing}/{direction}': run(pricing == 'fixed', direction)
            for pricing in ('dynamic', 'fixed') for direction in ('control', 'buy', 'sell')}
    baseline = arms['dynamic/control']['consumption']
    for name, arm in arms.items():
        arm['relative_to_dynamic_control'] = arm['consumption'] / baseline - 1
    assert len({a['quantity'] for a in arms.values()}) == 1
    assert arms['dynamic/buy']['consumption'] > baseline
    assert arms['dynamic/sell']['consumption'] < baseline
    assert len({arms[f'fixed/{d}']['consumption'] for d in ('control', 'buy', 'sell')}) == 1
    paths = [Path(__file__).relative_to(ROOT), Path('settlement.py')]
    paths += [p.relative_to(ROOT) for top in ('engine', 'mods/native', 'mods/examples/fixed_market_pricing')
              for p in sorted((ROOT / top).rglob('*')) if p.suffix in ('.py', '.toml')]
    paths += [Path('mods.toml')]
    result = {'kind': 'synthetic_strict_consumption_price_pairs',
              'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'initial_conditions': {'gold': 20000, 'supply': 2000, 'full_health_infantry': UNITS},
              'periods': PERIODS, 'trade_each_period': TRADE,
              'arms': arms, 'source_sha256': {str(p): sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}}
    target = Path(__file__).with_name('strict_consumption_results.json')
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({name: {k: arm[k] for k in ('consumption', 'investment', 'quantity', 'relative_to_dynamic_control')}
                      for name, arm in arms.items()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
