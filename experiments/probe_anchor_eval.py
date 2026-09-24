# -*- coding: utf-8 -*-
"""**「赶上老师没有」的判据尺**：老师 / BC 起点 / 任意 ckpt，在 5 张留出图上取中位数。

用户 2026-09-18：「**先赶上老师**」—— 那就是 1 号模型的验收线（证明这套机器能超过老师）。

## 为什么必须是这一把尺

`train.py` 的 in-run `eval` 是 **`--eval-episodes 2`（2 局）**，而文档纪律第 1 条：
「**2 张图不能用来看趋势**（得出过相反结论）」。所以进度的**唯一**判据是这里：
**固定 5 张留出图（seed 900000~900004）× `compare.py` 口径（torch 种子内部固定）⇒ 报中位数**。

⚠ 别拿 in-run eval 的数跟这里的数混（协议不同：图不同、局数不同）。

用法：
    python experiments/probe_anchor_eval.py [ckpt ...]        # 不给 ckpt 就只有老师那一行
    ZHANGUO_THREADS=1 ...   # ECS 是 1 物理核
"""
from __future__ import annotations

import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch  # noqa: E402

from rl.compare import run_model, run_rule  # noqa: E402
from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv  # noqa: E402
from rl.hw import set_threads  # noqa: E402
from rl.tokenize import GROUPS, tokenize  # noqa: E402
from rl.transformer import WindowTransformer  # noqa: E402

# ★位置参数只认路径：`--beta 1.0` 的那个 "1.0" 不是 ckpt（踩过：跑完扫描拿它去 load）
CKPTS = [a for a in sys.argv[1:]
         if not a.startswith("--") and not a.lstrip("-").replace(".", "").isdigit()]
SEEDS = [900000 + i for i in range(5)]
TEACHER = "v11plus"
BETA = float(sys.argv[sys.argv.index("--beta") + 1]) if "--beta" in sys.argv else 0.0
set_threads(int(__import__("os").environ.get("ZHANGUO_THREADS", "4")))

env = ZhanguoEnv(map_size=16, max_turns=200, max_actions_per_turn=ACT_SAFETY)
env.reset(0)
_w = tokenize(env, env._obs())


def load(p: str):
    m = WindowTransformer({g: _w.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(p, map_location="cpu", weights_only=False)
    miss, _ = m.load_state_dict(ck["model"], strict=False)
    m.eval()
    print(f"  载入 {p}（缺失 {len(miss)} 项，第 {ck.get('iter', '?')} 块）", flush=True)
    return m


def med(xs):
    return st.median(xs)


def spread(xs, base):
    """逐图离散度：`最大/最小` 倍率 + 逐图占老师的百分比。

    ★为什么中位数之外还要看这个（用户 2026-09-18：「**方差高有点危险了**」）：
    中位数把"5 张图整体上移"和"1 张图暴涨、4 张塌"读成同一个数，而后者才是塌方前兆。
    **判据要跟老师自己的离散度比** —— 老师那 5 张图本来就散（最差/最好 10 图差近一倍，
    见 PLAN §V.26），所以先看老师的倍率当噪声地板，再看学生有没有明显超过它。
    """
    lo, hi = min(xs), max(xs)
    pct = " ".join(f"{v / base:>4.0%}" for v in xs)
    return f"逐图 {pct}   极差 {hi / max(lo, 1):.2f}×"


t0 = time.time()
print(f"留出图 {SEEDS}，各 200 回合；β={BETA}；★ = 该档**超过老师**\n", flush=True)

# ---- 老师（基准线）----
g = [run_rule(env, sd, 200, max_actions=10 ** 9, which=TEACHER) for sd in SEEDS]
base_s, base_t = med([x[0] for x in g]), med([x[1] for x in g])
print(f"  {TEACHER + ' 老师':<26} 消费中位 {base_s:>9,.0f}   地中位 {base_t:>6.1f}", flush=True)
print(f"  {'':<26} {'':>9}   ← 这就是「赶上」的分母", flush=True)
print(f"  {'':<26} {'':>9}   {spread([x[0] for x in g], base_s)}（老师自己的离散＝噪声地板）\n",
      flush=True)

for p in CKPTS:
    m = load(p)
    gg = [run_model(env, m, sd, deterministic=True, use_win=True, exec_beta=BETA)
          for sd in SEEDS]
    ss = [run_model(env, m, sd, deterministic=False, use_win=True, exec_beta=BETA)
          for sd in SEEDS]
    gs, gt = med([x[0] for x in gg]), med([x[1] for x in gg])
    ss_, st_ = med([x[0] for x in ss]), med([x[1] for x in ss])
    print(f"  {Path(p).stem:<26} 贪心 {gs:>9,.0f}（{gs / base_s:>5.0%}）地 {gt:>5.1f}"
          f" | 采样 {ss_:>9,.0f}（{ss_ / base_s:>5.0%}）地 {st_:>5.1f}"
          f"{'  ★超过老师' if max(gs, ss_) > base_s else ''}", flush=True)
    print(f"  {'':<26} 采样 {spread([x[0] for x in ss], base_s)}", flush=True)

print(f"\n总耗时 {time.time() - t0:.0f}s", flush=True)
