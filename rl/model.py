# -*- coding: utf-8 -*-
"""沙盒策略网络：网格 CNN + 全局 MLP + 军队 token + **候选动作打分** + 价值头。

    形状（与旧线同源，用户 2026-09-24：「模型就用上次的模型，只是去掉大量原有的维度
    和 token」）
    ─────────────────────────────────────────────────────────────────────
        logits, value = net(grid, glob, cand, mask, army, army_mask)

    · `grid [B, 14, 8, 8]`   局面（`encode.encode_grid`）
    · `glob [B, 14]`         标量（`encode.encode_glob`）
    · `cand [B, K, 12]`      **每个候选一行特征**（`encode.candidate_features`）
    · `mask [B, K]`          候选掩码（沙盒里 `legal()` 已屏蔽过，这里只是补 padding 位）
    · `army [B, n, 10]`      军队 token + `army_mask [B, n]`

    ★ 砍掉了什么（相对 `feat/rl` 的 `rl/model.py`）
    ─────────────────────────────────────────────
      · `SubEmbedder`（子项查表 + 规则表内容投影）—— 沙盒没有"子项"
      · `amt_emb`（数量嵌入）—— 沙盒的动作不带数量
      · `army_mlp` + `tile_idx`/`army_idx`/`sub_idx` 的 **gather 拼装** ——
        沙盒的候选特征已经在 `encode.candidate_features()` 里算成 12 列平铺，
        不需要"从网格取地块、从军队表取军队"再拼

    ★ 保留了什么（都是旧线**栽过跟头换来的**，不是随便留的）
    ────────────────────────────────────────────────
      · **点积打分** `q·c/√d`（不是 MLP 出标量）—— O(d) 而非 O(d²)/候选
      · ★ **打分前 LayerNorm**（`cand_ln`/`q_ln`）：点积对**模长**敏感，而模长与局面无关
        ⇒ 不归一化的话模型会走捷径：把某几类候选的模长顶上去，不看局面也能让它们排第一
        （旧线实测：模型能把 `build` 排进前 10 的 82%，却从不排第一）
      · ★ **价值头不共用 query 那份表征**：旧线实测 `0.5·loss_v ≈ 500` 而 `loss_pi ≈ 3`，
        主干梯度几乎全归价值 ⇒ argmax 长期锁死在同一类上、策略不再看局面。
        价值头从**原始 glob** 自己走一条路，两边各练各的。
"""
from __future__ import annotations

import torch
import torch.nn as nn

from . import vocab as V
from .encode import CAND_WIDTH


class ArmyEncoder(nn.Module):
    """军队 token 窗口 → 定长向量（旧线 `WindowEncoder` 的**单组**简化版）。

    旧线是"每组各一层投影 → 掩码均值池化 → 拼 → MLP"（g/m/a/n/e/r/k 七组）。
    沙盒只有**一组**（军队）—— 别的组（地块/外交/事件/记忆）沙盒里根本不存在。
    """

    def __init__(self, width: int = V.A_WIDTH, d_enc: int = 32, d_out: int = 64):
        super().__init__()
        self.proj = nn.Linear(int(width), d_enc)
        self.mlp = nn.Sequential(nn.Linear(d_enc, d_out), nn.ReLU(),
                                 nn.Linear(d_out, d_out), nn.ReLU())

    def forward(self, feats: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """`feats [B,n,W]`、`mask [B,n] bool` → `[B,d_out]`。空组 ⇒ 输出恒 0。"""
        h = self.proj(feats)
        m = mask.unsqueeze(-1).to(h.dtype)
        pooled = (h * m).sum(1) / m.sum(1).clamp(min=1.0)
        return self.mlp(pooled)


class PolicyNet(nn.Module):
    def __init__(self, n_grid_ch: int = V.GRID_CHANNELS, n_glob: int = V.GLOB_SIZE,
                 n_cand: int = CAND_WIDTH, d_conv: int = 64, d_bottle: int = 32,
                 n_conv3: int = 2, d_global: int = 128, d_cand: int = 128,
                 d_army: int = 64, army_width: int = V.A_WIDTH):
        super().__init__()
        self.n_kinds = len(V.KIND)

        # 1×1 瓶颈把通道压下来，再堆 n_conv3 层 3×3 提特征（旧线的同一套）
        layers: list[nn.Module] = [nn.Conv2d(n_grid_ch, d_bottle, 1), nn.ReLU()]
        prev = d_bottle
        for _ in range(n_conv3):
            layers += [nn.Conv2d(prev, d_conv, 3, padding=1), nn.ReLU()]
            prev = d_conv
        self.conv = nn.Sequential(*layers)
        # ★ 候选的**空间特征**：按候选的**目标格**去卷积特征图里 **gather**（旧线 `tile_idx` 的做法）。
        #   **不做全局平均池化** —— 池化会把 8×8 的空间信息压成一个数，候选就"看不见自己
        #   那格周围有什么、离核心多远"，而棋盘游戏的关键正是空间。
        #   （旧版这里图省事写过 `.mean(dim=(2,3))`，是错的。）

        self.glob_mlp = nn.Sequential(nn.Linear(n_glob, d_global), nn.ReLU(),
                                      nn.Linear(d_global, d_global), nn.ReLU())
        self.army_enc = ArmyEncoder(army_width, d_enc=32, d_out=d_army)

        # 候选编码吃「**该格的空间特征** + 候选自己的 12 列特征」
        self.cand_mlp = nn.Sequential(nn.Linear(d_conv + n_cand, d_cand), nn.ReLU())
        # ★ 打分前的 LayerNorm —— 旧线栽过的那个跟头，见模块 docstring
        self.cand_ln = nn.LayerNorm(d_cand)
        self.q_ln = nn.LayerNorm(d_cand)
        # ★ 输入是 `g(d_global) + am(d_army) + gp(d_conv)` —— `gp` 是**卷积特征图的整图均值**
        #   ⇒ 宽度是 `d_conv` 不是 `d_cand`（删掉 `grid_pool` 时差点漏改这里，实测报
        #   "mat1 and mat2 shapes cannot be multiplied (1x256 and 320x128)"）
        self.query = nn.Linear(d_global + d_army + d_conv, d_cand)
        # ★ 价值头从**原始 glob** 自己走一条路（不共用 query 的表征），理由见 docstring
        self.value = nn.Sequential(nn.Linear(n_glob, 128), nn.ReLU(), nn.Linear(128, 1))

    def forward(self, grid: torch.Tensor, glob: torch.Tensor, cand: torch.Tensor,
                mask: torch.Tensor | None = None,
                army: torch.Tensor | None = None,
                army_mask: torch.Tensor | None = None,
                cand_xy: torch.Tensor | None = None):
        """→ `(logits [B,K], value [B])`。

        `cand_xy [B,K,2]`：候选的**目标格索引**（`encode.candidate_xy`）——
        给了就按它 gather 那一格的空间特征；没给则退回全局池化（只做兜底）。
        """
        b, k, _ = cand.shape
        fmap = self.conv(grid)                                  # [B,d,H,W]
        h, w = fmap.shape[2], fmap.shape[3]
        flat = fmap.flatten(2).transpose(1, 2)                  # [B,H*W,d]
        if cand_xy is not None:
            xy = cand_xy.clamp(min=0)                           # `end_turn` 的 (-1,-1) → (0,0)
            idx = (xy[..., 0] * w + xy[..., 1]).clamp(0, h * w - 1)      # [B,K]
            tf = flat.gather(1, idx.unsqueeze(-1).expand(-1, -1, flat.size(-1)))
        else:
            tf = flat.mean(1, keepdim=True).expand(-1, k, -1)   # 兜底
        gp = flat.mean(1)                                       # query 那一份仍看整图

        if army is not None and army.size(1) > 0:
            am = self.army_enc(army, army_mask)                 # [B,d_army]
        else:
            am = torch.zeros(b, self.army_enc.mlp[-2].out_features,
                             dtype=fmap.dtype, device=fmap.device)
        g = self.glob_mlp(glob)                                 # [B,dg]

        c = self.cand_mlp(torch.cat([tf, cand], dim=-1))        # [B,K,dc]
        q = self.query(torch.cat([g, am, gp], dim=-1))          # [B,dc]
        logits = (self.q_ln(q).unsqueeze(1) * self.cand_ln(c)).sum(-1) / (c.size(-1) ** 0.5)
        if mask is not None:
            logits = logits.masked_fill(~mask, -1e9)
        return logits, self.value(glob).squeeze(-1)


def build_model(**kw) -> PolicyNet:
    """按沙盒词表建网（`train.py` 用）。"""
    return PolicyNet(**kw)