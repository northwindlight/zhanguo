# -*- coding: utf-8 -*-
"""诊断：可执行性辅助头（`exec_head`）到底有没有排序能力？值不值得开？

**为什么要问这个**（2026-09-13 晚，实测驱动）：
`probe_validity.py` 的两档对照测出——对训过头的 `ckpt_33`，**开了软加权的撞墙率
反而更高**（33.9% → 37.3%）。软加权本意是"把概率从死选项上挪开"，结果往里推。

读损失代码（`ppo.py:429-435`）看到可疑之处：
    标签**只有"当步选中的那个候选"有**（`env.step` 的 ok），其余候选无标签。
⇒ 头的训练集有**选择偏差**（全是策略愿意选的动作，其中 ~67% 成功），
  而策略**不选**的那些候选（恰恰是死选项）**从未被标注**，`p_exec` 是外推；
  软加权却对**全体候选**生效 ⇒ 可能在没训过的候选上加噪声。

**本脚本直接验它**：在真实状态上**暴力测出每个候选的真实可执行性**
（逐个 `deepcopy` 后 `step`，取 `info["ok"]`），然后算两个 AUC：

    AUC(p_exec, 真值)          ← 辅助头自己的排序能力（0.5 = 纯噪声）
    AUC(策略 logits, 真值)     ← ★**对照组**：策略本来就已经编码了多少

★对照组的用处：若两者相当，**这个头就是白加的** —— 策略自己已经知道了，
  再乘一个噪声权重只会破坏它。

用法：python experiments/probe_exec_head.py <ckpt> [states] [turns]
"""
import sys
import copy
import random

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.ppo import policy_logits, forward_batch, _one_step

CKPT = sys.argv[1]
N_STATES = int(sys.argv[2]) if len(sys.argv) > 2 else 16
TURNS = int(sys.argv[3]) if len(sys.argv) > 3 else 30


def auc(scores, labels):
    """Mann-Whitney AUC：随机取一正一负，正样本得分更高的概率。0.5 = 无区分度。"""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return float("nan")
    wins = sum((1.0 if a > b else 0.5 if a == b else 0.0)
               for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
ck = torch.load(CKPT, map_location="cpu", weights_only=False)
_miss, _ = m.load_state_dict(ck["model"], strict=False)
m.eval()
if any(k.startswith("exec_head.") for k in _miss):
    print("[中止] 这个 ckpt 的 exec_head 是随机初始化的（没训过），测了没意义。")
    sys.exit(2)

print(f"权重 {CKPT}（第 {ck.get('iter', '?')} 块）")
print(f"在 {N_STATES} 个真实状态上**暴力枚举**全部候选的可执行性\n")

states = []                      # (p_exec, 策略logit, 真值) 三元组，逐候选
ep = 0
while len(states) < N_STATES:
    torch.manual_seed(3000 + ep)
    obs = env.reset(300 + ep)
    ep += 1
    turn = 0
    while True:
        win = tokenize(env, obs)
        lg, _v, cm = policy_logits(m, obs, win=win, use_exec=False)
        _lg2, _v2, _m2, pexec = forward_batch(m, [_one_step(obs)], [win],
                                              return_exec=True)
        cands = obs.cand["actions"]
        # 只扫**有效位**（非 padding）的候选
        valid = cm[0].tolist()
        idxs = [i for i, v in enumerate(valid) if v]

        # ★暴力枚举：逐个 deepcopy 后真跑一次，拿 ground truth
        truths = []
        for i in idxs:
            e2 = copy.deepcopy(env)
            try:
                _o, _r, _d, info = e2.step(cands[i])
                truths.append(bool(info["ok"]))
            except Exception:
                truths.append(False)
        pe = [float(pexec[0, i]) for i in idxs]
        pl = [float(lg[0, i]) for i in idxs]
        states.append((pe, pl, truths, sum(truths) / len(truths)))
        print(f"  局{ep - 1} 第{turn}步：候选 {len(idxs)} 个，"
              f"**真可执行 {sum(truths)}/{len(idxs)} = {sum(truths) / len(idxs):.1%}**")

        # 真实推进一局（用策略自己的动作）
        logp = torch.log_softmax(lg, dim=-1)
        i = int(torch.multinomial(logp.exp(), 1).item())
        obs, _r, done, info = env.step(cands[i])
        turn += 1
        if done or len(states) >= N_STATES:
            break

pe_all = [s for st in states for s in st[0]]
pl_all = [s for st in states for s in st[1]]
y_all = [y for st in states for y in st[2]]
base = sum(y_all) / len(y_all)

print(f"\n=== 汇总（{len(y_all)} 个候选，跨 {len(states)} 个状态）===")
print(f"  基准率：真可执行 {base:.1%}（随机猜的 AUC 就是 0.5）")
print(f"  ★AUC(exec_head 的 p_exec, 真值)   = {auc(pe_all, y_all):.3f}")
print(f"  ★AUC(策略自己的 logits, 真值)     = {auc(pl_all, y_all):.3f}   ← 对照组")

# 逐状态看：头有没有在"状态内"排序（全局 AUC 会被状态间差异灌水）
per_head, per_pol, per_rand = [], [], []
for pe, pl, y, _b in states:
    if 0 < sum(y) < len(y):
        per_head.append(auc(pe, y))
        per_pol.append(auc(pl, y))
print(f"\n=== 只在**状态内**排序（{len(per_head)} 个状态正负都有的）===")
print(f"  头   AUC 均值 {sum(per_head) / len(per_head):.3f}")
print(f"  策略 AUC 均值 {sum(per_pol) / len(per_pol):.3f}")
print("\n判据：头 ≈0.5 ⇒ 纯噪声，软加权只会加噪声（关掉）；")
print("      头 明显 >0.5 且 不输对照组 ⇒ 有真信号，值得调系数继续。")
