# -*- coding: utf-8 -*-
"""**冻结的「111 张量」那代 WindowTransformer** —— 只为公平评估 2026-09-13 之前训的 ckpt。

`rl/transformer.py` 换过三代，`state_dict` 键数就是代际指纹：

| 键数 | 代 | 分界提交 | 代表 ckpt |
|---|---|---|---|
| **111** | 无 exec_head、无 candx | `9a79f3f`(加 exec_head) 之前 | `gpu1000_norm/bc/ep500.pt`、`bc_cont/ep100.pt`、`ppo_v4/v5/v6` |
| 113 | +`exec_head.*`（2 键） | `9a79f3f` / `02a307c` | `exec_v1`、`gpu_mirror/*` |
| 119 | +`cross2.*`/`ln_cand.*`（candx，6 键） | `3c89f7a` | **现在这炉** |

**为什么必须用当时的架构**：拿旧 ckpt 走 `strict=False` 塞进新架构能"跑"，但**新键是随机初值**
⇒ 行为已被污染，新旧对比不成立（candx 那层在读出段，直接改 `h`）。

★本文件 = `git show 9a79f3f^:rl/transformer.py`，逐字复制，**别改、别让活代码 import 它**。
★它能**同时**吃 111 和 113 的 ckpt：113 多的 `exec_head` 用 `strict=False` 留随机即可 ——
  推理时 `act/policy_logits` 的 `use_exec` **默认 False**，那个头根本不参与决策。

配套：`experiments/probe_beyond_horizon.py --arch t111`
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from rl.env import AMOUNTS, ARMY_FEAT, KINDS
from rl.model import SubEmbedder          # 候选子项嵌入（查表 + 规则表内容，§10.2 载体 B）
from rl.tokenize import GROUPS
from rl.vocab import POS_SCALE


class Block(nn.Module):
    """标准 pre-norm Transformer block。**不加位置编码**。

    理由与 `rl/env.py` 里那条同源（用户 2026-09-11 口径）：绝对 PE 会把
    "我在图哪个位置"漏回去，而那是智能体**不可能知道**的（迷雾挡着、从未探索过边界）。
    位置一律以**特征**形式进（相对家的偏移 ÷ POS_SCALE），见 `rl/tokenize.py`。
    相对位置由 cross-attention 自己算得出来，不需要 PE 帮忙。
    """

    def __init__(self, d_model: int, n_head: int, d_ff: int | None = None, p_drop: float = 0.0):
        super().__init__()
        self.ln1 = nn.LayerNorm(d_model)
        self.attn = nn.MultiheadAttention(d_model, n_head, dropout=p_drop, batch_first=True)
        self.ln2 = nn.LayerNorm(d_model)
        d_ff = d_ff or 4 * d_model
        self.ff = nn.Sequential(nn.Linear(d_model, d_ff), nn.GELU(), nn.Linear(d_ff, d_model))

    def forward(self, x: torch.Tensor, key_padding_mask: torch.Tensor | None = None):
        h = self.ln1(x)
        a, _ = self.attn(h, h, h, key_padding_mask=key_padding_mask, need_weights=False)
        x = x + a
        return x + self.ff(self.ln2(x))


class WindowTransformer(nn.Module):
    """窗口 → (候选 logits, value)。

        logits, value = net(win, cand, cand_mask)

    `win` 是 `rl.ppo.collate_window` 的产物；`cand` 沿用 `rl.ppo.collate` 里那一套，
    但**只用 type/sub/amount/tile_dx/tile_dy/army_* 这些字段**，不用 `tile_idx`
    （那是给 CNN 用的，P4 里 CNN 退场）。
    """

    def __init__(self, win_widths: dict[str, int], *, d_model: int = 192, n_layer: int = 4,
                 n_head: int = 4, d_ff: int | None = None, p_drop: float = 0.0,
                 d_cand: int = 128, groups: tuple[str, ...] = GROUPS):
        super().__init__()
        self.groups = tuple(groups)
        self.d_model = int(d_model)

        # 各组原始宽度不同（g 56 / m 39 / a 12 / …），**每组一层投影**，
        # 不补零到同一宽度当同质 token —— 那样等于凭空给窄组塞一堆常数维度。
        self.proj = nn.ModuleDict({g: nn.Linear(int(win_widths[g]), d_model)
                                   for g in self.groups})
        # 组身份嵌入：投影后各组的统计性质仍不同（M 是块均值、A 是单支军队），
        # 给模型一个"这条 token 是哪种"的显式提示。
        self.group_emb = nn.Embedding(len(self.groups), d_model)
        self.blocks = nn.ModuleList([Block(d_model, n_head, d_ff, p_drop)
                                     for _ in range(n_layer)])
        self.ln_out = nn.LayerNorm(d_model)

        # ---- 候选侧：特征与现有头**保持一致**（类型/子项/数量/落点/被引用军队），
        #      这样"换主干"是唯一的变量，候选编码本身没动。
        self.n_kinds = len(KINDS)
        self.n_amounts = len(AMOUNTS)
        self.type_emb = nn.Embedding(self.n_kinds, 16)
        self.sub_emb = SubEmbedder([1] * len(KINDS))  # 尺寸由 `set_sub_sizes` 覆盖
        self.amt_emb = nn.Embedding(self.n_amounts, 8)
        self.army_mlp = nn.Sequential(nn.Linear(ARMY_FEAT, 32), nn.ReLU())
        # 落点 (dx,dy) + 有无落点 + 被引用军队的 (dx,dy,hp,kind)
        d_q = 16 + 16 + 8 + 3 + (32 + 1)
        self.cand_mlp = nn.Sequential(nn.Linear(d_q, d_cand), nn.ReLU())
        self.to_q = nn.Linear(d_cand, d_model)
        self.cross = nn.MultiheadAttention(d_model, n_head, dropout=p_drop, batch_first=True)
        self.ln_c = nn.LayerNorm(d_model)
        # 打分 = 注意力输出(d_model) ⊕ 候选自身编码(d_cand) —— 光有注意力输出
        # 会丢掉"这个候选自己是什么"（注意力的输出是窗口的一个加权和，不带候选身份）。
        self.score = nn.Sequential(nn.Linear(d_model + d_cand, d_model), nn.GELU(),
                                   nn.Linear(d_model, 1))
        self.value = nn.Sequential(nn.Linear(d_model, 128), nn.ReLU(), nn.Linear(128, 1))

    # ------------------------------------------------------------------
    def set_sub_sizes(self, sub_sizes: list[int]) -> None:
        """子项嵌入表的大小只有 env 知道（按 kind 分组），建表时先占位、这里补齐。

        与 `rl/env.sub_tables` 一一对应；**顺序即 kind 顺序**，别重排。
        """
        self.sub_emb = SubEmbedder(sub_sizes)

    def cand_pos_block(self, cand: dict) -> torch.Tensor:
        """候选落点的归一化特征 `[B,K,3]` = `(dx, dy, 有无落点)`。

        ★**必须与 `rl/tokenize.py` 的 M/A 组同尺度**。这里原来写死 `64.0`，而
        `vocab.POS_SCALE = 32.0` —— 同一段物理距离在同一个模型里被表达成两个尺度
        （A 组按 32、候选按 64），能学，但是个**静默**的坑：以后谁改了 POS_SCALE，
        只有这一处不跟着动。抽成函数 + 用同一个常量，测试才钉得住。

        ★**同原点**（2026-09-12 对齐）：用 `tile_hx/tile_hy`（**相对家**），与 M/A 组的
        token 位置同原点。以前这里取 `tile_dx/tile_dy`（相对**可见区外接框**）——
        尺度一样但**原点差一个每帧变化的偏移量**（家 − 框原点），模型得自己把它学出来
        才能把"候选在哪"和"patch 在哪"对上。

        `-1` = 这个候选没有落点（buy/sell/end_turn）。因为 `-1` 是个合法相对坐标的反面，
        所以"有没有落点"必须**显式**进特征，不能靠 `-1` 隐式表达。
        """
        dx = cand["tile_hx"].float()
        dy = cand["tile_hy"].float()
        has = (dx >= 0).float().unsqueeze(-1)
        return torch.cat([dx.unsqueeze(-1) / POS_SCALE,
                          dy.unsqueeze(-1) / POS_SCALE, has], dim=-1)

    def encode_window(self, win: dict) -> tuple[torch.Tensor, torch.Tensor]:
        """窗口 → `(tokens [B,T,d], mask [B,T])`。T = 各组 token 数之和。"""
        toks, msks = [], []
        for gi, g in enumerate(self.groups):
            h = self.proj[g](win["feats"][g])                    # [B,n,d]
            h = h + self.group_emb.weight[gi].view(1, 1, -1)
            toks.append(h)
            msks.append(win["mask"][g])
        return torch.cat(toks, dim=1), torch.cat(msks, dim=1)

    def forward(self, win: dict, cand: dict, cand_mask: torch.Tensor | None = None):
        x, wmask = self.encode_window(win)
        # ★`key_padding_mask` 的 True = **不看**。padding 位置必须挡住，
        #   否则补出来的零向量会以"内容"的身份参与 softmax（不报错、只是学歪）。
        kpm = ~wmask
        # 整条全是 padding 的行（理论上不该有：G 组恒亮）会让 softmax 全 -inf → NaN。
        # 显式放行第 0 条兜底，比事后 nan_to_num 干净。
        dead = kpm.all(dim=1)
        if dead.any():
            kpm = kpm.clone()
            kpm[dead, 0] = False
        for blk in self.blocks:
            x = blk(x, key_padding_mask=kpm)
        x = self.ln_out(x)

        # ---- 候选 → query ----
        b, k = cand["type_idx"].shape
        te = self.type_emb(cand["type_idx"])
        # 子项嵌入 = 学到的查表 + **规则表内容**（§10.2 载体 B，与 P3 共用 SubEmbedder）
        se = self.sub_emb(cand["type_idx"], cand["sub_idx"], cand.get("content"))
        ae = self.amt_emb(cand["amount_idx"].clamp(0, self.n_amounts - 1))
        # 落点：**相对家的偏移**（外接框内坐标），不是扁平下标 —— 扁平下标绑死网格形状，
        # 而 P4 已经没有网格了。`-1` = 这个候选没有落点（buy/sell/end_turn）。
        pos = self.cand_pos_block(cand)
        # 被引用的军队（`army_idx == null` 时取 army_feats 的末尾空位）
        af = torch.cat([self.army_mlp(cand["army_feats"]),
                        torch.zeros(b, 1, 32, device=x.device)], dim=1)
        ai = cand["army_idx"].clamp(0, af.size(1) - 1)
        ag = af.gather(1, ai.unsqueeze(-1).expand(-1, -1, af.size(-1)))
        ahas = (cand["army_idx"] < cand["army_feats"].size(1)).float().unsqueeze(-1)

        q0 = self.cand_mlp(torch.cat([te, se, ae, pos, ag, ahas], dim=-1))   # [B,K,dc]
        qq = self.to_q(q0)                                                   # [B,K,d]
        # Q = 候选（K 个），K/V = 窗口（T 个）—— `batch_first=True` 下序列维就是 K。
        # 代价 O(K·T·d)，**不是** O(K²)：候选之间互不可见，这是明知的不做。
        att, _ = self.cross(qq, x, x, key_padding_mask=kpm, need_weights=False)
        h = self.ln_c(att + qq)          # 残差：注意力输出 + 候选自己
        logits = self.score(torch.cat([h, q0], dim=-1)).squeeze(-1)
        if cand_mask is not None:
            # ★`.to(logits.device)` 是**防御**，不是多余：`cand_mask` 由 collate 产生，
            #   在 CPU 上；模型搬到 GPU 之后忘了搬它就会报
            #   "expected self and mask to be on the same device"。
            #   已经同设备时 `.to()` 是 no-op，所以这行的代价是零。
            #   （两个搬运点已经搬了它，这里是第二道保险 —— 这类漏搬只在 GPU 上炸，
            #   在 CPU 上完全看不出来，所以值得防。）
            logits = logits.masked_fill(~cand_mask.to(logits.device), -1e9)

        # ---- 价值：窗口的**掩码均值池化**（P4 起窗口是全局状态的唯一来源）
        # 已知代价（2026-09-12 审阅）：均值池化把每条 token 等权，丢掉了"哪些重要"
        # （全局 G、关键军队可能比某个 patch 重要得多）。先跑通再说，但如果价值学不好，
        # **最便宜的替代是取 G 组那条 token**（`GROUPS` 的顺序里 g 是第 0 条，
        # 而它本来就是全局状态的摘要），一行就能换；再不够再上 max 池化或注意力池化。
        m = wmask.unsqueeze(-1).to(x.dtype)
        pooled = (x * m).sum(1) / m.sum(1).clamp(min=1.0)
        return logits, self.value(pooled).squeeze(-1)

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())
