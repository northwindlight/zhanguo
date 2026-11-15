"""实验 2：引擎内确定性基线梯子（缩放协议）。

协议（缩放，只作机制方向）：
    session_ticks=4000（默认 20000）、max_turns=20（默认 60），
    即每局 19 个回合、其中 15 个交易日；动力学噪声按日归一，缩的是日内 tick 数。

策略（全部为 replay 沙盒脚本，不调模型）：
    cash   空仓不动（不写脚本、不交易、不买算力）
    hold10 第 1 回合收盘附近等权买入 10 只初始股，之后不动
    ma     ma5 上穿 ma20 建仓 10% 现金、下穿清仓，每 500 tick 检查
    noise  LCG 伪随机（沙盒禁 random）：每次唤醒随机买 5% 现金或清仓一只

对照：
    * 同 seed「空仓」局的价格路径上算 10 只初始股等权收盘->终局收盘收益，
      这是论文 +31.46%/+32.46% 的引擎内同口径反事实（不含费用）；
    * 引擎内买入持有的实际终局现金与它之差 = 费用 + 冲击 + 可执行性；
    * 另报「只对投出去的钱」的收益，扣除现金拖累。

运行：
    python exp2_baselines.py
输出：
    results/exp2_baselines.json
"""
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import bench_lib as B

OUT = Path(__file__).resolve().parent / 'results' / 'exp2_baselines.json'
TICKS = 4000
TURNS = 20
SEEDS = [11, 22, 33, 44, 55, 66, 77, 88]
STRATEGIES = dict(cash=B.CASH, hold10=B.HOLD10, ma=B.MA, noise=B.NOISE)

PAPER_NOTE = (
    '论文 §6.5 的 +31.46%/+32.46% 是 60 回合全尺寸协议（43 个交易日）下、'
    '用 history 收盘价、不含费用的等权买入持有；本表是 15 个交易日的缩放协议，'
    '收益幅度不可直接对比，只能对比「同口径反事实 → 引擎内实际」的差（费用+冲击+可执行性）。'
)


def task(args):
    name, seed = args
    payload = B.run_once(seed=seed, ticks=TICKS, turns=TURNS, sources=[STRATEGIES[name]])
    return name, payload


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tasks = [(name, seed) for seed in SEEDS for name in STRATEGIES]
    print('[exp2] scaled ladder: %d games x (ticks=%d, turns=%d)' % (len(tasks), TICKS, TURNS))
    results = []
    with ProcessPoolExecutor(max_workers=6) as pool:
        for name, payload in pool.map(task, tasks):
            results.append((name, payload))
            print('  done', name, payload['seed'], 'final', payload['players'][B.ACTOR0]['final_cash'],
                  'elapsed', payload['elapsed_sec'])
    summary = B.summarize_baselines(results)
    games = [dict(strategy=name, seed=g['seed'],
                  final_cash=g['players'][B.ACTOR0]['final_cash'],
                  return_pct=round(g['players'][B.ACTOR0]['final_cash'] / 1000 - 100, 4),
                  counterfactual_pct=None if g['counterfactual_equal_weight'] is None
                  else round(g['counterfactual_equal_weight'] * 100, 4),
                  fills=g['players'][B.ACTOR0]['fills_count'],
                  fees=round(g['players'][B.ACTOR0]['fill_fees'], 2),
                  liq_fees=round(g['players'][B.ACTOR0]['final_liq_fees'], 2),
                  elapsed_sec=g['elapsed_sec']) for name, g in results]
    report = dict(
        protocol=dict(ticks=TICKS, turns=TURNS, seeds=SEEDS, strategies=list(STRATEGIES),
                      trading_days=15, engine='mods.stock.toml (PAMS 0.3.0)',
                      engine_defaults=dict(session_ticks=20000, turns=60, commission=0.001,
                                           spread=0.002, depth_shares=100, float_shares=20000),
                      scale='session_ticks 缩到 1/5、回合缩到 1/3；日频动力学不随 tick 数改变，'
                            '但日内做市报价/路径点数同比减少，只作机制方向'),
        paper_note=PAPER_NOTE,
        summary=summary, games=games,
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for name in STRATEGIES:
        block = summary['strategies'].get(name)
        if not block:
            continue
        print('[exp2]', name, 'final_cash', block['final_cash'],
              'return', block['return_pct'])
    print('[exp2] written', OUT)


if __name__ == '__main__':
    main()
