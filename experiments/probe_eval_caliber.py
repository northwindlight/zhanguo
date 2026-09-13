# -*- coding: utf-8 -*-
"""同一 ckpt、**同一次 run 内**，比"推理时加权 / 不加权"两档的评估消费。

## 为什么要问（ECS 侧 AI 2026-09-14 的反向线索）

修 ratio 错位 bug 时选了「(b) 采样侧去掉软加权」，但那条修复**顺手把评估也去掉了加权**。
随后重测新口径基线，出现一个反转：

| ckpt | 旧口径（加权）采样 | 新口径（不加权）采样 | 差 |
|---|---|---|---|
| BC ep100（**随机头**） | 12,029 | 13,025 | **+8.3%** |
| v10 ckpt_5（**训过头**） | **14,497** | 12,983 | **−10.4%** |
| v10 ckpt_35（训过头） | 9,312 | 8,521 | −8.5% |

⇒ **加权对"消费"不是空操作**（§V.3d 只测过撞墙率，没测消费）。
**方向是：头训过的 ckpt 加权后都更好。**

结合 S1 的发现 —— **全网唯一有一致学习方向的就是 exec 头**（cos 0.75）——
可能的真实故事是：**PPO 的 pg 没学到东西，但 exec 辅助头学到了，
而且只在推理时加权才兑现**。若如此，**(b) 恰好删掉了唯一有效的那部分**，
应该改用 **(a) 更新侧也加权（`pexec` detach）**。

## 本探针做什么

**同一个进程、同一次 run 内**跑两档（避免跨 run 的采样 RNG 不可比）：
- 不加权（`use_exec=False`）= 新代码的训练/评估口径
- 加权（`use_exec=True`）= 旧口径

**每档都跑贪心与采样**。★**贪心档逐位可比**（argmax + 固定种子，不消耗 RNG）
—— 它是本判别最干净的信号；采样档只作参考（同一进程内同种子，但两档共享 RNG 流）。

## 判据（事先写死）

- **两档无差** ⇒ 反向线索作废，(b) 维持
- **加权档稳定 > 不加权档** ⇒ 线索成立 ⇒ **改用 (a)**：更新侧也加权，`pexec` 走 `detach`
  （别让策略梯度去改 exec 头）
- 参照点：BC 新口径基线 **13,025**（同 run 内也一并测，作锚）

用法：python experiments/probe_eval_caliber.py <ckpt> [ckpt...] [--episodes N] [--turns T]
"""
import sys

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.train import play_episode

EPS = 8
TURNS = 200
CKPTS = []
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--episodes":
        i += 1; EPS = int(sys.argv[i])
    elif a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    else:
        CKPTS.append(a)
    i += 1
if not CKPTS:
    print(__doc__); sys.exit(2)

env = ZhanguoEnv(map_size=16, max_turns=TURNS)      # 评估一律真值（jitter 不传）
env.reset(0)
w0 = tokenize(env, env._obs())

print(f"{EPS} 局 × {TURNS} 回合；**同一进程内**跑两档（贪心逐位可比，采样仅参考）\n")

for ck_path in CKPTS:
    m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(ck_path, map_location="cpu", weights_only=False)
    _miss, _ = m.load_state_dict(ck["model"], strict=False)
    m.eval()
    untrained = any(k.startswith("exec_head.") for k in _miss)

    name = ck_path.split("/")[-1]
    print(f"===== {name}（第 {ck.get('iter','?')} 块）"
          f"{'  ★exec 头随机（未训过）' if untrained else ''} =====")

    out = {}
    for use_exec, tag in ((False, "不加权"), (True, "加权  ")):
        if use_exec and untrained:
            print(f"  {tag}: 跳过（头随机，加权无意义）")
            continue
        # ★两档**各自逐局重设 torch RNG**（ECS 2026-09-14 指出）：
        #   不重设的话，"先跑不加权、再跑加权"会让后跑那档用被前一档消耗过的流
        #   ⇒ 采样档不再是同种子配对（贪心档不受影响，argmax 不吃 RNG）。
        #   种子取法与 `probe_validity.py` 一致（2000+局号）。
        g, s = [], []
        for i in range(EPS):
            torch.manual_seed(2000 + i)
            g.append(play_episode(env, m, seed=900_000 + i, deterministic=True,
                                  use_win=True, use_exec=use_exec))
        for i in range(EPS):
            torch.manual_seed(2000 + i)
            s.append(play_episode(env, m, seed=800_000 + i, deterministic=False,
                                  use_win=True, use_exec=use_exec))
        gm = sum(x["spend_total"] for x in g) / EPS
        sm = sum(x["spend_total"] for x in s) / EPS
        out[use_exec] = (gm, sm)
        print(f"  {tag}: 贪心 {gm:>9,.0f}（地 {sum(x['tiles'] for x in g)/EPS:5.1f}）"
              f"   采样 {sm:>9,.0f}（地 {sum(x['tiles'] for x in s)/EPS:5.1f}）")

    if len(out) == 2:
        dg = out[True][0] - out[False][0]
        ds = out[True][1] - out[False][1]
        print(f"  ── 加权 − 不加权：**贪心 {dg:+,.0f}（{dg/max(1,out[False][0]):+.1%}）**"
              f"   采样 {ds:+,.0f}（{ds/max(1,out[False][1]):+.1%}）")
        print("     判据：两档无差 ⇒ 线索作废；加权稳定更好 ⇒ 改用 (a)")
    print()
