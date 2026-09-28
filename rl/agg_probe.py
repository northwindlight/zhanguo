# -*- coding: utf-8 -*-
"""给 **C 版 DP 里剩下的那块 Python 肉** 挂表：`_agg_damage` 回调到底还值多少。

    用法：python rl/agg_probe.py [size] [t_max]      例：python rl/agg_probe.py 8 40

★ 为什么要有它（M2 的前置问题，2026-09-28）：
  `_combat_fast` 把 DP 搬进 C 之后，**每一状态仍要回调 Python 一次**拿 agg 表
  （约束①：伤害公式只有一份）。于是"DP 还剩多少 Python"这件事**没人量过** ——
  `real_phase.py` 只看得到 `assess` 的总时长，看不见里面那层回调。
  ⇒ 这里把表挂在 `CB._agg_damage` **本尊**上（`_fast_dp` 每次回调都过它），
  按 `b.agg_cache` 是否命中劈成 **miss（真在 Python 里展开 6^n）/ hit（查表返回）**。

★ 与 `real_phase.py` 同一口径：**不抄任何逻辑**，跑真的 `collect_episode`。
  数字只在这台机、这副配置下有效；换配置（尤其换 `t_max`）要重跑。
"""
import sys
import time

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import combat_probs as CB                 # noqa: E402
from rl import train as T                         # noqa: E402
from rl.model import build_model                 # noqa: E402
from rl.sandbox import Sandbox                    # noqa: E402

size = int(sys.argv[1]) if len(sys.argv) > 1 else 12
t_max = int(sys.argv[2]) if len(sys.argv) > 2 else 40
torch.set_num_threads(1)


def _so_stamp() -> str:
    """`.so` 比 `_combat_fast.c` 旧 ⇒ 这一轮量的是**旧二进制**，数字作废重编。"""
    if CB._FAST is None:
        return ".so=（无）"
    so = getattr(CB._FAST, "__file__", "") or ""
    src = os.path.join(os.path.dirname(so), "_combat_fast.c")
    try:
        tm, ts = os.path.getmtime(so), os.path.getmtime(src)
    except OSError:
        return ".so=时间戳读不到"
    return f".so {time.strftime('%m-%d %H:%M', time.localtime(tm))}" + \
        ("  ★**比源码旧 ⇒ 先重编**" if tm < ts else "")

AGG = dict(t=0.0, calls=0, miss=0, miss_t=0.0, combos=0, entries=0)
ASS = dict(t=0.0, n=0)
real_agg, real_assess = CB._agg_damage, CB.assess


def timed_agg(b, sig):
    t0 = time.perf_counter()
    before = sig in b.agg_cache
    out = real_agg(b, sig)
    dt = time.perf_counter() - t0
    AGG["t"] += dt
    AGG["calls"] += 1
    if not before:
        AGG["miss"] += 1
        AGG["miss_t"] += dt
        AGG["combos"] += len(CB.DIE_FACES) ** len(sig)
        AGG["entries"] += len(out)
    return out


def timed_assess(b, **kw):
    t0 = time.perf_counter()
    try:
        return real_assess(b, **kw)
    finally:
        ASS["t"] += time.perf_counter() - t0
        ASS["n"] += 1


CB._agg_damage, CB.assess = timed_agg, timed_assess

sb = Sandbox(seed=11, size=size, t_max=t_max, n_nations=3, halls_known=True,
             territory=True, alliances="random2v2").reset()
torch.manual_seed(0)
nets = {p: build_model(mem_slots=8) for p in sb.players}
for n in nets.values():
    n.eval()
t0 = time.perf_counter()
with torch.inference_mode():
    steps, _info = T.collect_episode(nets, sb, rng=np.random.default_rng(0))
wall = time.perf_counter() - t0
nstep = len(steps)
hit_t = AGG["t"] - AGG["miss_t"]

print(f"size={size} t_max={t_max}：{nstep} 步  墙钟 {wall:.2f} s = "
      f"{wall / nstep * 1000:.1f} ms/步   _FAST={'在' if CB._FAST else '**不在**'}"
      # ★ **构建出处**：`.so` 比 `.c` 旧 ⇒ 量的是旧二进制（信箱第 2 封 F 条）
      f"   {_so_stamp()}"
      f"   _FAST_ERR={CB._FAST_ERR!r}")
print(f"assess {ASS['n']} 次 = {ASS['t'] * 1000:.0f} ms"
      f"（{ASS['t'] / wall * 100:.1f}% 墙钟，{ASS['t'] / nstep * 1000:.2f} ms/步）")
print(f"  └ 回调 _agg_damage {AGG['calls']} 次 = {AGG['t'] * 1000:.0f} ms"
      f"（占 assess {AGG['t'] / max(ASS['t'], 1e-9) * 100:.1f}%）")
print(f"      · **miss** {AGG['miss']} 次 = {AGG['miss_t'] * 1000:.0f} ms"
      f" ⇒ 占 assess {AGG['miss_t'] / max(ASS['t'], 1e-9) * 100:.1f}%"
      f" / 占墙钟 {AGG['miss_t'] / wall * 100:.2f}%"
      f" / {AGG['miss_t'] / nstep * 1000:.3f} ms每步")
if AGG["miss"]:
    print(f"        平均 6^方数 = {AGG['combos'] / AGG['miss']:.0f}，"
          f"平均伤害向量 {AGG['entries'] / AGG['miss']:.1f} 条，"
          f"单次 {AGG['miss_t'] / AGG['miss'] * 1000:.3f} ms")
print(f"      · **hit** {AGG['calls'] - AGG['miss']} 次 = {hit_t * 1000:.0f} ms"
      f"（占 assess {hit_t / max(ASS['t'], 1e-9) * 100:.1f}%，"
      f"{hit_t / max(AGG['calls'] - AGG['miss'], 1) * 1e6:.2f} µs/次）"
      f" —— 这是 `build_sig` + 调用 + `PyDict_Next` 的固定税，与 miss 无关")
if CB._FAST_ERR:
    print("★ _FAST_ERR 非空 ⇒ 有东西回落过：", CB._FAST_ERR)
