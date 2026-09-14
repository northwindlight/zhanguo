# -*- coding: utf-8 -*-
"""candx 验收测试 v2（Pi 19:24 五判据 + 20:04 审查要求：**绑定版与独立层版并排**）。

跑法：OMP_NUM_THREADS=1 PYTHONPATH=$HOME/zhanguo python experiments/_ecs_scripts/test_cand_visibility.py
不动仓库任何主文件；三个模型只在内存里构建：
  old   = rl.transformer.WindowTransformer（基线）
  tied  = transformer_candx.py  （att2 复用 self.cross）
  indep = transformer_candx2.py （新参数 self.cross2）
①先证 old 不敏感（测试非假），再对两版报**效应量**（Pi 的可证伪预测：最大/中位/计数）。
"""
import copy
import importlib.util
import inspect
import statistics as stx
import time

import numpy as np
import torch

from rl.env import AMOUNTS, KINDS, ZhanguoEnv
from rl.ppo import _one_step, act, forward_batch, policy_logits
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer

CK = torch.load("rl/runs/bc1/ckpt_30.pt", map_location="cpu", weights_only=False)
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


def mk(cls, strict):
    m = cls({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=dmodel,
            n_layer=A.get("n_layer", 4), n_head=A.get("n_head", 4))
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    m.load_state_dict(CK["model"], strict=strict)
    m.eval()
    return m


# ★indep 的 cross2 是全新参数、load_state_dict 不覆盖 ⇒ 初值来自 torch RNG。
# 为让"效应量对比"可复算，三个模型统一在 manual_seed(12345) 下构建（tied 无随机新参，不受影响）。
torch.manual_seed(12345)
MODELS = [("old", mk(WindowTransformer, True)),
          ("tied", mk(TIED, False)),
          ("indep", mk(INDEP, False))]
old = MODELS[0][1]


def reach2(step_target, seed=900000, tseed=3000):
    """(obs, win) 成对现采现 tokenize；提前终局则返回最后一个活时点。"""
    torch.manual_seed(tseed)
    obs = env.reset(seed)
    n = 0
    last = None
    while n < step_target:
        w = tokenize(env, obs)
        last = (obs, w)
        i, _lp, _v = act(old, obs, win=w)
        obs, _r, done, _inf = env.step(obs.cand["actions"][i])
        n += 1
        if done:
            return last
    return obs, tokenize(env, obs)


def logits_of(model, obs, w):
    with torch.no_grad():
        return policy_logits(model, obs, win=w)


print("=== ① 决定性判据（扰动候选 j 的 amount_idx，看 i≠j 的 logit）===")
for depth, name in ((48, "浅局(步48)"), (1400, "深局(步1400)")):
    obs, w = reach2(depth)
    K = int(obs.cand["type_idx"].shape[-1])
    with torch.no_grad():
        lg0o, _v, cm = logits_of(old, obs, w)
    valid = cm[0].nonzero().flatten().tolist()
    j = valid[-1]
    obs_p = copy.deepcopy(obs)
    old_val = int(obs_p.cand["amount_idx"][j])
    obs_p.cand["amount_idx"][j] = (old_val + 3) % len(AMOUNTS)
    ii = [x for x in valid if x != j]
    print(f"  [{name}] K={K} 有效={len(valid)} 扰动 j={j}")
    for mname, mdl in MODELS:
        with torch.no_grad():
            lgb, _vb, _cb = logits_of(mdl, obs, w)
            lgp, _vp, _cp = logits_of(mdl, obs_p, w)
        d = (lgp.flatten() - lgb.flatten()).abs()
        vals = [float(d[x]) for x in ii]
        print(f"    {mname:<6} i≠j：变(>1e-6) {sum(x > 1e-6 for x in vals)}/{len(ii)}   "
              f"最大 {max(vals):.3e}   中位 {stx.median(vals):.3e}   自身Δlogit_j {float(d[j]):.3e}")

print("\n=== ② 参数量（精确）===")
p0 = MODELS[0][1].n_params()
for mname, mdl in MODELS:
    p = mdl.n_params()
    print(f"  {mname:<6} {p:,}" + ("" if mname == "old" else f"   Δ {p - p0:+,} ({100 * (p - p0) / p0:+.3f}%)"))

print("\n=== ③ 接口（签名）===")
for fn in (policy_logits, act, forward_batch):
    print(f"    {fn.__name__}{inspect.signature(fn)}   （rl.ppo 未动）")
print(f"  forward 签名：old==tied {str(inspect.signature(WindowTransformer.forward)) == str(inspect.signature(TIED.forward))}，"
      f"old==indep {str(inspect.signature(WindowTransformer.forward)) == str(inspect.signature(INDEP.forward))}")

print("\n=== ④ 冒烟（同轨迹两时点成批造 padding；两版各检）===")
torch.manual_seed(3000)
obs_c = env.reset(900000)
obs_a = w_a = None
n = 0
while n < 1400:
    if n == 48:
        obs_a, w_a = copy.deepcopy(obs_c), tokenize(env, obs_c)
    wtmp = tokenize(env, obs_c)
    i, _l, _v = act(old, obs_c, win=wtmp)
    obs_c, _r, done, _inf = env.step(obs_c.cand["actions"][i])
    n += 1
    if done:
        break
w_b = tokenize(env, obs_c)
for mname, mdl in MODELS[1:]:
    with torch.no_grad():
        lg2, v2, cm2 = forward_batch(mdl, [_one_step(obs_a), _one_step(obs_c)], [w_a, w_b])
    padmask = ~cm2[0].bool()
    print(f"  {mname}: 批 K_max={lg2.shape[1]} 行0 padding={int(padmask.sum())} ⇒ 全 == -1e9: "
          f"{bool((lg2[0][padmask] == -1e9).all())}   valid/value 全有限: "
          f"{bool(torch.isfinite(lg2[0][cm2[0].bool()]).all() and torch.isfinite(lg2[1][cm2[1].bool()]).all() and torch.isfinite(v2).all())}")

print("\n=== ⑤ 前向墙钟（中位 ms，30 次）===")
for depth, name in ((48, "K≈浅"), (1900, "K≈深")):
    obs, w = reach2(depth)
    K = int(obs.cand["type_idx"].shape[-1])
    for mname, mdl in MODELS:
        ts = []
        for _t in range(30):
            t0 = time.perf_counter()
            with torch.no_grad():
                policy_logits(mdl, obs, win=w)
            ts.append((time.perf_counter() - t0) * 1000)
        print(f"  {name}(K={K}) {mname:<6}: 中位 {stx.median(ts):.1f}  p90 {sorted(ts)[27]:.1f}")
