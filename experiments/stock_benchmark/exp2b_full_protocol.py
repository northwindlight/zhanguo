"""实验 2b：全尺寸协议（20,000 tick/日、60 回合）下的引擎内基线。

与论文 §6.5 的 +31.46% / +32.46% 直接对口径：
论文那两个数是「用 history 收盘价算的、不含费用的等权买入持有」，
本脚本用同一 seed（2026111303）在引擎里真的把钱按盘口买进去、持有到终局清算，
差值就是费用 + 冲击 + 可执行性（整数股、盘口深度、2% 保护价、终局平仓）。

协议：session_ticks=20000、max_turns=60（第 60 回合只清算，实际交易 43 个交易日）。
种子：论文种子 2026111303 + 两个对照种子。

运行：
    python exp2b_full_protocol.py            # 3 个种子 × {空仓, 买入持有}
输出：
    results/exp2b_full_protocol.json
"""
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import bench_lib as B

OUT = Path(__file__).resolve().parent / 'results' / 'exp2b_full_protocol.json'
TICKS = 20000
TURNS = 60
SEEDS = [2026111303, 20261116, 7]
STRATEGIES = dict(cash=B.CASH, hold10=B.HOLD10)
PAPER_BASELINE = dict(hydrogen_pct=31.46, flash_pct=32.46,
                      note='论文 §6.5：第 1 回合收盘等权买入 10 只初始股、持有到第 59 回合收盘，'
                           '用 history 收盘价计算，不含费用（两局同 seed、不同玩家路径）')


def task(args):
    strategy, seed, source = args
    payload = B.run_once(seed=seed, ticks=TICKS, turns=TURNS, sources=[source])
    return strategy, payload


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tasks = [(name, seed, source) for seed in SEEDS
             for name, source in STRATEGIES.items()]
    print('[exp2b] full protocol: %d games x (ticks=%d, turns=%d)' % (len(tasks), TICKS, TURNS))
    results = []
    with ProcessPoolExecutor(max_workers=3) as pool:
        for strategy, payload in pool.map(task, tasks):
            print('  done', strategy, payload['seed'], 'elapsed', payload['elapsed_sec'],
                  'final_cash', payload['players'][B.ACTOR0]['final_cash'])
            results.append((strategy, payload))
    summary = B.summarize_baselines(results)
    report = dict(protocol=dict(ticks=TICKS, turns=TURNS, seeds=SEEDS,
                                strategies=list(STRATEGIES),
                                engine='mods.stock.toml (PAMS 0.3.0)',
                                note='全尺寸协议（与论文同口径）；玩家全为 replay 确定性脚本，零模型调用'),
                  paper_baseline=PAPER_BASELINE, summary=summary,
                  games=[dict(strategy=s, seed=g['seed'],
                              final_cash=g['players'][B.ACTOR0]['final_cash'],
                              return_pct=round(g['players'][B.ACTOR0]['final_cash'] / 1000 - 100, 4),
                              counterfactual_pct=None if g['counterfactual_equal_weight'] is None
                              else round(g['counterfactual_equal_weight'] * 100, 4),
                              fills=g['players'][B.ACTOR0]['fills_count'],
                              fees=g['players'][B.ACTOR0]['fill_fees'],
                              liq_fees=g['players'][B.ACTOR0]['final_liq_fees'],
                              elapsed_sec=g['elapsed_sec']) for s, g in results])
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, block in summary['strategies'].items():
        print('[exp2b]', name, 'return', block['return_pct'],
              'pure_cf', block['counterfactual_pure_pct'])
    print('[exp2b] written', OUT)


if __name__ == '__main__':
    main()
