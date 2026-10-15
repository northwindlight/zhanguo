"""价格通道证伪探针：同量物理实现能否取得不同分数。

设计（对应论文 §10.1）：
  成对世界初始状态完全相同，逐期建设与用量完全相同；
  处理组只增加**合法市场交易**（买或卖），买入的木材原样留在仓库。

比较：
  S  = 现行字段 cumulative_spending（市场结算后的当期行情估值）
  Cᵇ = 按市场基准价复算的同量实现

判据：
  若各臂的物理实现向量相同而 S 不同 ⇒ 论文摘要中的中心命题 H
  （“单独改变交易路径不应改变现行累计消费字段”）被证伪。
  固定定价臂若各臂相等，则说明差异来自估值通道而非用途识别。

全部合成世界，不读存档、不调模型。
运行：.venv/bin/python experiments/consumption_metric/price_channel_probe.py
"""
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from engine.runtime import Runtime

from probe import audit

CYCLES = 6
SITES = 8
BUILD = 'native:watchtower'          # 60 金 + 30 木
TRADE_EACH_CYCLE = 300               # 每期买卖量；6×300=1800 < 木材深度 1920


def make_world(*, fixed=False, gold, wood):
    runtime = Runtime.load(ROOT / 'mods/economy_acceptance')
    if fixed:
        units = [u for u in runtime.units.values() if u.name != 'native:market-pricing']
        units += list(Runtime.load(ROOT / 'mods/examples/fixed_market_pricing').units.values())
        runtime = Runtime(units, runtime.distribution)
    world = runtime.world_class()()
    world.resources.open('Qin', {'native:gold': gold, 'native:wood': wood,
                                'native:food': 100, 'native:ore': 100, 'native:oil': 100})
    for index in range(SITES):
        world.buildings.register_site(f'example:qin{index}', 'Qin', (index, 0), resources={
            'native:gold': 20, 'native:farmland': 20, 'native:wood': 20, 'native:ore': 20})
    return world


def run(*, fixed=False, gold, wood, buy=0, sell=0, cycles=CYCLES):
    world = make_world(fixed=fixed, gold=gold, wood=wood)
    series = []
    for cycle in range(cycles):
        world.buildings.build('Qin', f'example:qin{cycle}', BUILD)
        if buy:
            world.market.buy('Qin', 'native:wood', buy)
        if sell:
            world.market.sell('Qin', 'native:wood', sell)
        world.resolve_turn()
        market = world.market.snapshot()
        report = world.economy.turns('Qin')[-1]
        series.append({'cycle': cycle,
                       'wood_price': dict(market.prices)['native:wood'],
                       'wood_inventory': dict(market.inventory)['native:wood'],
                       'reference': dict(market.reference)['native:wood'],
                       'period_spending': report.spending.total,
                       'cumulative_spending': report.cumulative_spending.total,
                       'wood_stock': world.resources.balance('Qin', 'native:wood')})
    row = audit(world)
    return {'fixed': fixed, 'buy_per_cycle': buy, 'sell_per_cycle': sell,
            'endowment': {'gold': gold, 'wood': wood},
            'series': series,
            'cumulative_spending': series[-1]['cumulative_spending'],
            'period_spending_total': sum(s['period_spending'] for s in series),
            'final_use_quantities': row['final_use_quantities'],
            'final_wood_stock': world.resources.balance('Qin', 'native:wood'),
            'final_gold': world.resources.balance('Qin', 'native:gold'),
            'max_wood_price': max(s['wood_price'] for s in series),
            'min_wood_price': min(s['wood_price'] for s in series),
            'facts': row['facts'], 'audit_residual': row['residuals']}


def base_value(quantities, base_prices):
    """按基准价复算实现总额；数量向量来自资源侧、与估值通道无关。"""
    return sum(base_prices.get(resource, 1.0) * amount
               for resource, amount in quantities.items())


def main():
    started = time.perf_counter()
    arms = {}
    # 对 1：同一初始禀赋，处理组只买入并囤积木材
    arms['dynamic/control'] = run(gold=12000, wood=400)
    arms['dynamic/buy'] = run(gold=12000, wood=400, buy=TRADE_EACH_CYCLE)
    arms['fixed/control'] = run(fixed=True, gold=12000, wood=400)
    arms['fixed/buy'] = run(fixed=True, gold=12000, wood=400, buy=TRADE_EACH_CYCLE)
    # 对 2：同一初始禀赋，处理组只卖出木材（通道的反方向）
    arms['dynamic/control-sell'] = run(gold=12000, wood=2600)
    arms['dynamic/sell'] = run(gold=12000, wood=2600, sell=TRADE_EACH_CYCLE)

    base_prices = {'native:gold': 1.0, 'native:wood': 2.0, 'native:food': 1.5,
                   'native:ore': 2.5, 'native:oil': 6.0, 'native:equipment': 8.0,
                   'native:supply': 6.0}
    for arm in arms.values():
        uses = arm['final_use_quantities'].get('Qin', {})
        arm['final_use_quantities'] = uses
        arm['base_value'] = base_value(uses, base_prices)
        arm['score_over_base'] = (arm['cumulative_spending'] / arm['base_value']
                                  if arm['base_value'] else None)

    use_vectors = {name: sorted(arm['final_use_quantities'].items()) for name, arm in arms.items()}
    checks = {
        'use_vectors_identical': len({json.dumps(v, sort_keys=True) for v in use_vectors.values()}) == 1,
        'use_vectors': use_vectors,
        'dynamic_buy_minus_control': arms['dynamic/buy']['cumulative_spending'] - arms['dynamic/control']['cumulative_spending'],
        'dynamic_sell_minus_control': arms['dynamic/sell']['cumulative_spending'] - arms['dynamic/control-sell']['cumulative_spending'],
        'fixed_buy_minus_control': arms['fixed/buy']['cumulative_spending'] - arms['fixed/control']['cumulative_spending'],
        'base_values_equal': len({round(arm['base_value'], 6) for arm in arms.values()}) == 1,
        'base_value': arms['dynamic/control']['base_value'],
        'max_price_ratio_to_base': max(arm['max_wood_price'] for arm in arms.values()) / 2.0,
        'e': 2.718281828459045,
    }
    checks['H_falsified_for_current_field'] = (
        checks['use_vectors_identical']
        and (abs(checks['dynamic_buy_minus_control']) > 1e-7
             or abs(checks['dynamic_sell_minus_control']) > 1e-7))
    checks['invariance_holds_under_fixed_valuation'] = (
        checks['use_vectors_identical'] and abs(checks['fixed_buy_minus_control']) < 1e-7)
    checks['price_bound_holds'] = checks['max_price_ratio_to_base'] <= checks['e']

    result = {'method': '成对合成世界：相同禀赋、相同逐期建设与用量，处理组只增加合法市场交易',
              'cycles': CYCLES, 'build': BUILD, 'trade_each_cycle': TRADE_EACH_CYCLE,
              'base_prices': base_prices, 'arms': arms, 'checks': checks,
              'elapsed_seconds': time.perf_counter() - started,
              'probe_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
    out = HERE / 'price_channel_results.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    summary = {name: {'S': round(arm['cumulative_spending'], 4),
                      'Cb': round(arm['base_value'], 4),
                      'S/Cb': round(arm['score_over_base'], 4) if arm['score_over_base'] else None,
                      'wood_price_end': arm['series'][-1]['wood_price'],
                      'wood_stock_end': arm['final_wood_stock']}
               for name, arm in arms.items()}
    print(json.dumps({'arms': summary,
                      'checks': {k: v for k, v in checks.items() if k != 'use_vectors'},
                      'use_vector': checks['use_vectors']['dynamic/control'],
                      'elapsed_seconds': round(result['elapsed_seconds'], 2)}, ensure_ascii=False, indent=2))
    print(f'Wrote {out}')


if __name__ == '__main__':
    main()
