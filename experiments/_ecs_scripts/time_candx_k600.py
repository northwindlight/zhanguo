# -*- coding: utf-8 -*-
"""candx 边界条件 §3.1 的硬要求：K≈600 的前向墙钟（真·深 500T 局 + 合成垫高两条腿）。
不训练、不动主文件。跑法：
  OMP_NUM_THREADS=1 PYTHONPATH=$HOME/zhanguo python experiments/_ecs_scripts/time_candx_k600.py
"""
import importlib.util
import statistics as stx
import time

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.ppo import act, policy_logits
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer

CK = torch.load("rl/runs/bc1/last.pt", map_location="cpu", weights_only=False)
A = CK["args"]
dmodel = A.get("d_model", 192)


def load_cls(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.WindowTransformer


TIED = load_cls("experiments/_ecs_scripts/transformer_candx.py", "transformer_candx")
INDEP = load_cls("experiments/_ecs_scripts/transformer_candx2.py", "transformer_candx2")

env = ZhanguoEnv(map_size=16, max_turns=200)
env.reset(0)
w0 = tokenize(env, env._obs())


def mk(cls):
    m = cls({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=dmodel,
            n_layer=A.get("n_layer", 4), n_head=A.get("n_head", 4))
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    m.load_state_dict(CK["model"], strict=False)
    m.eval()
    return m


torch.manual_seed(12345)
MODELS = [("old", mk(WindowTransformer)), ("tied", mk(TIED)), ("indep", mk(INDEP))]
old = MODELS[0][1]

# —— 真·500T 深局：bc1@100 自走，记每一步 K，取最大 K 的时点（(obs,win) 现采现 tokenize）
env500 = ZhanguoEnv(map_size=16, max_turns=500)
torch.manual_seed(3000)
obs = env500.reset(900000)
best = (0, None, None)
n = 0
while True:
    w = tokenize(env500, obs)
    K = int(obs.cand["type_idx"].shape[-1])
    if K > best[0]:
        import copy as _c
        best = (K, _c.deepcopy(obs), w)
    i, _l, _v = act(old, obs, win=w)
    obs, _r, done, _inf = env500.step(obs.cand["actions"][i])
    n += 1
    if done:
        break
Kmax_real, obs_big, w_big = best
print(f"真·500T 局（seed 900000，bc1@100 自走）：{n} 步到终局；全程最大 K={Kmax_real}（计时用该时点）")


def timeit(model, obs, w, reps=20):
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        with torch.no_grad():
            policy_logits(model, obs, win=w)
        ts.append((time.perf_counter() - t0) * 1000)
    return stx.median(ts), sorted(ts)[-3]


for tag, (K, o, w) in (("真 500T 最大时点", best),):
    print(f"\n=== 墙钟（中位/p90 ms，20 次）：{tag} K={K} ===")
    for mname, mdl in MODELS:
        med, p90 = timeit(mdl, o, w)
        print(f"  {mname:<6}: 中位 {med:.1f}  p90 {p90:.1f}")

# —— 合成到 K=600：把真时点的 cand 各数组平铺裁到 600（sub/type 配对不变、mask 全真）
if Kmax_real < 600:
    import numpy as np
    synth = {"actions": None}
    obs_s = _c = None
    from rl.env import Observation  # noqa: F401  （只用它的 cand dict 结构，不重建对象）
    c = {}
    for k, v in obs_big.cand.items():
        if k == "actions":
            continue
        import numpy as _np
        v = _np.asarray(v)
        reps = 600 // v.shape[0] + 1
        c[k] = _np.concatenate([v] * reps, axis=0)[:600]
    c["mask"] = _np.ones(600, dtype=bool)

    class FakeObs:
        pass
    fo = FakeObs()
    fo.grid, fo.glob = obs_big.grid, obs_big.glob
    fo.cand = c
    print(f"\n=== 墙钟（合成 K=600；真 win + cand 平铺，mask 全真；只看成本）===")
    for mname, mdl in MODELS:
        ts = []
        for _ in range(20):
            t0 = time.perf_counter()
            with torch.no_grad():
                policy_logits(mdl, fo, win=w_big)
            ts.append((time.perf_counter() - t0) * 1000)
        print(f"  {mname:<6}: 中位 {stx.median(ts):.1f}  p90 {sorted(ts)[-3]:.1f}")
