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
        # 网格 → 整图池化（沙盒只 8×8，池化够；旧线是"按候选的地块下标去 gather"，
        # 那需要 tile_idx，沙盒的候选特征里没有也不需要）
        self.grid_pool = nn.Sequential(nn.Linear(d_conv, d_cand), nn.ReLU())

        self.glob_mlp = nn.Sequential(nn.Linear(n_glob, d_global), nn.ReLU(),
                                      nn.Linear(d_global, d_global), nn.ReLU())
        self.army_enc = ArmyEncoder(army_width, d_enc=32, d_out=d_army)

        self.cand_mlp = nn.Sequential(nn.Linear(n_cand, d_cand), nn.ReLU())
        # ★ 打分前的 LayerNorm —— 旧线栽过的那个跟头，见模块 docstring
        self.cand_ln = nn.LayerNorm(d_cand)
        self.q_ln = nn.LayerNorm(d_cand)
        self.query = nn.Linear(d_global + d_army + d_cand, d_cand)
        # ★ 价值头从**原始 glob** 自己走一条路（不共用 query 的表征），理由见 docstring
        self.value = nn.Sequential(nn.Linear(n_glob, 128), nn.ReLU(), nn.Linear(128, 1))

    def forward(self, grid: torch.Tensor, glob: torch.Tensor, cand: torch.Tensor,
                mask: torch.Tensor | None = None,
                army: torch.Tensor | None = None,
                army_mask: torch.Tensor | None = None):
        """→ `(logits [B,K], value [B])`。"""
        b, k, _ = cand.shape
        fmap = self.conv(grid)                                  # [B,d,H,W]
        pooled = fmap.mean(dim=(2, 3))                          # [B,d]
        gp = self.grid_pool(pooled)                             # [B,dc]

        if army is not None and army.size(1) > 0:
            am = self.army_enc(army, army_mask)                 # [B,d_army]
        else:
            am = torch.zeros(b, self.army_enc.mlp[-2].out_features,
                             dtype=gp.dtype, device=gp.device)
        g = self.glob_mlp(glob)                                 # [B,dg]

        c = self.cand_mlp(cand)                                 # [B,K,dc]
        q = self.query(torch.cat([g, am, gp], dim=-1))          # [B,dc]
        logits = (self.q_ln(q).unsqueeze(1) * self.cand_ln(c)).sum(-1) / (c.size(-1) ** 0.5)
        if mask is not None:
            logits = logits.masked_fill(~mask, -1e9)
        return logits, self.value(glob).squeeze(-1)


def build_model(**kw) -> PolicyNet:
    """按沙盒词表建网（`train.py` 用）。"""
    return PolicyNet(**kw)