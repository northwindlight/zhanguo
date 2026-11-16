"""一键复跑全部引擎内实验（零模型调用）。

    python run_all.py               # 全部（含 60 回合全尺寸协议，约 20-30 分钟）
    python run_all.py --skip-full   # 跳过 exp2b 全尺寸协议（约 8-12 分钟）

每个实验脚本会把自己的结果写到 results/*.json；本脚本只负责按顺序调用并
在任一实验非零退出时中止。所有脚本都是确定性的，重跑应得到逐字节相同的结果
（exp1 会显式校验这一点）。
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable

STEPS = [
    ('exp1', 'exp1_determinism.py', '确定性 / 重放 + planning_workers 一致性'),
    ('exp2', 'exp2_baselines.py', '缩放协议基线梯子（8 种子 × 4 策略）'),
    ('exp2b', 'exp2b_full_protocol.py', '全尺寸协议（60 回合 × 20k tick）买入持有 vs 论文反事实'),
    ('exp3', 'exp3_mechanism.py', '佣金 0 vs 0.001、盘口深度 100 vs 20'),
    ('exp4', 'exp4_mark_close.py', '结算价（last executed price）可操纵性剂量反应'),
    ('exp5b', 'exp5b_league.py', '联赛/Elo：5 风格选手池、48 局、稳定性与牛熊分组'),
    ('exp5', 'exp5_opportunity.py', '环境健康检查：机会总量、策略跨种子 CV、借券容量'),
]


def main():
    skip_full = '--skip-full' in sys.argv[1:]
    steps = [step for step in STEPS if not (skip_full and step[0] == 'exp2b')]
    for name, script, description in steps:
        print('=' * 72)
        print('[run_all] %s: %s' % (name, description))
        print('=' * 72)
        result = subprocess.run([PYTHON, str(HERE / script)], cwd=str(HERE))
        if result.returncode != 0:
            print('[run_all] %s failed with exit code %s' % (name, result.returncode))
            return result.returncode
    print('[run_all] all done. results in', HERE / 'results')
    return 0


if __name__ == '__main__':
    sys.exit(main())
