# -*- coding: utf-8 -*-
"""命中率的**三种口径**：精确下标 / 忽略数量档 / 只看类别。

怀疑：`hit_rate` 要求 argmax 命中**完全相同的候选下标**，而 sell 候选 = 4 货 × 10 档
⇒ 老师卖×16、模型卖×12 算 miss。若"忽略数量档"的口径高得多，则 ε 被度量夸大了。
"""
import sys, collections, torch
import rl.bc as bc
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer

CKPT, SEED, TURNS = sys.argv[1], int(sys.argv[2]), 70
env = ZhanguoEnv(map_size=16, max_turns=TURNS); env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"]); m.eval()
teacher = bc.get_teacher("v10", turns=TURNS)
demos, _sp, _miss = bc.collect_episode(env, TURNS, seed=SEED, teacher_fn=teacher, with_window=True)
print(f"样本 {len(demos)}（全新老师局）")

exact = collections.Counter(); loose = collections.Counter(); kind = collections.Counter()
tot = collections.Counter()
for s in range(0, len(demos), 32):
    chunk = demos[s:s+32]
    cand, cmask, wb, acts, _g = bc.pack_tf(chunk)
    pred = m(wb, cand, cmask)[0].argmax(-1).numpy()
    for j, (o, i, _r, _w) in enumerate(chunk):
        acts_l = o.cand["actions"]; p = int(pred[j]); t = acts_l[i]
        key = f"build:{t.sub}" if t.kind == "build" else t.kind
        tot[key] += 1
        a = acts_l[p]
        if p == i: exact[key] += 1
        # 宽松：kind+sub+tile 同（数量档不同也算对）
        if a.kind == t.kind and a.sub == t.sub and a.tile == t.tile: loose[key] += 1
        if a.kind == t.kind: kind[key] += 1

print(f"\n{'类别':<14}{'样本':>5}{'精确':>8}{'忽略数量':>10}{'只看类别':>10}")
for k, n in tot.most_common(10):
    print(f"{k:<14}{n:>5}{exact[k]/n:>8.0%}{loose[k]/n:>10.0%}{kind[k]/n:>10.0%}")
N = sum(tot.values())
print(f"{'合计':<14}{N:>5}{sum(exact.values())/N:>8.0%}{sum(loose.values())/N:>10.0%}{sum(kind.values())/N:>10.0%}")
