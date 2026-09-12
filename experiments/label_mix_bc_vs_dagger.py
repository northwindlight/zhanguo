# -*- coding: utf-8 -*-
"""纯 BC 局 vs DAgger 局的**标签类别分布** —— 决定性的那几类各有多少条。"""
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
KEY = ("recruit", "build:兵营", "build:军屯", "move", "attack", "sell", "buy", "end_turn")

def run(tag, student):
    demos, spend, miss = bc.collect_episode(env, turns=TURNS, seed=SEED, teacher_fn=teacher,
                                            student=student, endturn_cap=1, with_window=True)
    c = collections.Counter()
    for o, i, _g, _w in demos:
        a = o.cand["actions"][i]
        c["build:兵营" if a.sub == "兵营" else ("build:军屯" if a.sub == "军屯" else a.kind)] += 1
    n = sum(c.values())
    print(f"\n【{tag}】样本 {n}  学生/老师消费 {spend:,.0f}  未匹配 {miss}")
    for k in KEY:
        print(f"    {k:<12} {c.get(k,0):>5}  {c.get(k,0)/max(1,n):>6.2%}")
    print(f"    （其他 build 合计 {sum(v for k2,v in c.items() if k2.startswith('build:') and k2 not in ('build:兵营','build:军屯'))}）")

run("纯 BC（老师自己走）", None)
run("DAgger（学生=ep20 走，老师贴标签）", m)
