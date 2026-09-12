# -*- coding: utf-8 -*-
"""验证 `--failure-trigger`：同 seed 跑两遍 `collect_episode`（off / on），比样本与标签。

要证两件事：
1. **off 那一遍与开关存在前逐位相同。** 靠代码推理成立（off 时只多建了一个
   **不消耗**的 `rng_fb`，不碰主 rng、不改任何分支）—— 本脚本只能验证它**确定性**
   （同 seed 两遍相同），证不了跨版本等价。
2. **on 那一遍只增不改**：样本数应增加，而 `spend` / `miss` 应**与 off 完全一致**
   （因为失败触发不改变学生实际走的路 —— 它只多问老师、多入库，不干预
   `_act` 的采样，也不动 `rng`）。**这一条是真正的护栏**：只要 spend/miss 变了，
   就说明新代码污染了轨迹，必须修。

用法：python experiments/verify_failure_trigger.py <ckpt> [turns]
"""
import sys
import collections
import torch
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
import rl.bc as bc

CKPT = sys.argv[1]
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 20

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)                      # `_terrain` 要等第一次 reset 才建出来
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"])
m.eval()
t = bc.get_teacher("v10", turns=TURNS)
bc.set_horizon(t, TURNS)


def label_of(a):
    sub = getattr(a, "sub", None)
    return f"{a.kind}:{sub}" if a.kind in ("build", "buy", "sell", "recruit") else a.kind


res = {}
for ft in (False, True):
    # ★两遍必须从**同一个 torch RNG 状态**出发：`act()` 走 `torch.multinomial`
    #   采样，消耗的是 torch 全局 RNG。不重设的话第二遍接着第一遍的状态走，
    #   轨迹自然不同 —— 那是**脚本的 bug**，会被误读成"失败触发污染了轨迹"
    #   （2026-09-12 踩过：spend 2056→2038 差 0.9%，就是采样岔开，不是污染）。
    torch.manual_seed(0)
    # 每遍用全新的 env：把"env 复用残留"这个变量也排掉。
    e = ZhanguoEnv(map_size=16, max_turns=TURNS)
    demos, spend, miss = bc.collect_episode(
        e, TURNS, seed=0, teacher_fn=t, student=m, failure_trigger=ft, fb_cap=2,
        # ★必须开：`act()` 对 transformer 会走 `collate_window([None])` 而崩
        #   （PLAN.md 记过的坑）。窗口训练的 ckpt 只能用窗口推理。
        with_window=True)
    c = collections.Counter(label_of(o.cand["actions"][i]) for o, i, _sp, _w in demos)
    res[ft] = (len(demos), spend, miss)
    print(f"failure_trigger={ft}:  样本 {len(demos):>5}   消费 {spend:>6}   miss {miss}")
    print(f"    标签分布 {dict(c.most_common(10))}")

n0, sp0, ms0 = res[False]
n1, sp1, ms1 = res[True]
print(f"\n增量：样本 {n0} → {n1}（+{n1 - n0}）")
ok = (sp0 == sp1 and ms0 == ms1)
print(f"★护栏：spend {sp0} → {sp1}，miss {ms0} → {ms1}   "
      f"{'通过（轨迹没被污染）' if ok else '★失败 —— 失败触发污染了学生轨迹，必须修'}")
sys.exit(0 if ok else 1)
