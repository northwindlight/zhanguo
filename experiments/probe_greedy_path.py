# -*- coding: utf-8 -*-
"""① 号实验：end_turn 的 logit 是不是一个「常量基线赢家」。

A. 初始状态前 6 名（end_turn 差多少）
B. 沿贪心路径（每回合都 end_turn）逐回合记录：end_turn logit、最强非 end_turn logit、差值
   ⇒ 若 end_turn logit 跨 70 个状态的 σ 远小于别人、且 margin 恒正，就是常量塌陷。
C. 屏蔽 end_turn 后贪心，看**头 2 个回合**它到底干什么（每次前向只算一次，快）
"""
import sys, collections, statistics as st
import torch, torch.nn.functional as F
from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import forward_batch, _one_step

CKPT, SEED, TURNS = sys.argv[1], int(sys.argv[2]), 70
env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(CKPT, map_location="cpu", weights_only=False)["model"]); m.eval()
torch.manual_seed(0)

def lg_of(obs, mask_et=False):
    lg, _v, cm = forward_batch(m, [_one_step(obs)], [tokenize(env, obs)])
    lg = lg[0].masked_fill(~cm[0], -1e9)
    acts = obs.cand["actions"]
    et = [i for i, a in enumerate(acts) if a.kind == "end_turn"]
    non = [i for i, a in enumerate(acts) if a.kind != "end_turn"]
    best_non = max((float(lg[i]) for i in non), default=float("-inf"))
    etv = float(lg[et[0]]) if et else float("-inf")
    if mask_et:
        for i in et:
            lg[i] = -1e9
    return lg, acts, etv, best_non

# ---- A ----
obs = env.reset(SEED)
lg, acts, etv, best_non = lg_of(obs)
print(f"【A】初始状态：候选 {len(acts)} 个")
o = torch.argsort(lg, descending=True)[:6]
for i in o.tolist():
    print(f"      {float(lg[i]):+.3f}  {acts[i].label()}")
print(f"      end_turn {etv:+.3f}   最强非end_turn {best_non:+.3f}   margin {etv-best_non:+.3f}")

# ---- B：沿贪心(end_turn)路径 ----
obs = env.reset(SEED)
ets, bns, kinds = [], [], collections.Counter()
for step in range(TURNS):
    lg, acts, etv, best_non = lg_of(obs)
    ets.append(etv); bns.append(best_non)
    i = int(lg.argmax())
    kinds[f"build:{acts[i].sub}" if acts[i].kind == "build" else acts[i].kind] += 1
    obs, _r, done, _info = env.step(acts[i])
    if done:
        break
print(f"\n【B】贪心路径 {len(ets)} 个状态（每步选：{dict(kinds)}）")
print(f"      end_turn logit   均值 {st.mean(ets):+.3f}  σ {st.pstdev(ets):.3f}  范围 [{min(ets):+.2f},{max(ets):+.2f}]")
print(f"     最强非et logit    均值 {st.mean(bns):+.3f}  σ {st.pstdev(bns):.3f}  范围 [{min(bns):+.2f},{max(bns):+.2f}]")
mg = [e - b for e, b in zip(ets, bns)]
print(f"      margin           均值 {st.mean(mg):+.3f}  恒正? {all(x > 0 for x in mg)}  最小 {min(mg):+.3f}")

# ---- C：屏蔽 end_turn，看它干什么（只跑 2 回合）----
obs = env.reset(SEED)
seq, n = [], 0
base_turn = env.world.turn
while env.world.turn < base_turn + 2 and n < 400:
    lg, acts, _e, _b = lg_of(obs, mask_et=True)
    i = int(lg.argmax()); a = acts[i]
    seq.append(f"T{env.world.turn}:{a.label()}")
    n += 1
    obs, _r, done, _info = env.step(a)
    if done:
        break
s = env.summary()
print(f"\n【C】屏蔽 end_turn 后头 2 回合（{n} 步，消费 {s['spend_total']:,.0f}，地 {s['tiles']}）：")
print("      " + "  ".join(seq[:26]))
