# -*- coding: utf-8 -*-
"""策略网络：网格 CNN + 全局 MLP + **候选动作打分**（candidate-set policy）+ 价值头。

动作空间是「每步变长的合法动作清单」，所以不做固定维度的动作头，而是给清单里
每个候选动作打分再 softmax——清单本身由 env 保证可行，网络不必学"什么合法"。

    logits, value = net(grid, glob, cand)
    cand 是 env 打包好的候选张量（type/sub/tile/army/amount 下标 + 军队特征表）
"""
from __future__ import annotations

import torch
import torch.nn as nn

from rl.env import AMOUNTS, ARMY_FEAT, KINDS


class PolicyNet(nn.Module):
    def __init__(self, n_grid_ch: int, n_glob: int, sub_sizes: list[int], n_tiles: int, *,
                 d_conv: int = 64, d_bottle: int = 32, n_conv3: int = 2,
                 d_global: int = 128, d_cand: int = 128, d_army: int = 32):
        super().__init__()
        self.n_kinds = len(KINDS)
        self.n_tiles = int(n_tiles)          # 地块数；null 下标 = n_tiles
        self.n_amounts = len(AMOUNTS)

        # 1×1 瓶颈先把通道压下来，再堆 n_conv3 层 3×3 提特征（规模由 rl/bench.py 实测后定）
        layers: list[nn.Module] = [nn.Conv2d(n_grid_ch, d_bottle, 1), nn.ReLU()]
        prev = d_bottle
        for _ in range(n_conv3):
            layers += [nn.Conv2d(prev, d_conv, 3, padding=1), nn.ReLU()]
            prev = d_conv
        self.conv = nn.Sequential(*layers)
        self.glob_mlp = nn.Sequential(nn.Linear(n_glob, d_global), nn.ReLU(),
                                      nn.Linear(d_global, d_global), nn.ReLU())
        self.army_mlp = nn.Sequential(nn.Linear(ARMY_FEAT, d_army), nn.ReLU(),
                                      nn.Linear(d_army, d_army), nn.ReLU())
        self.type_emb = nn.Embedding(self.n_kinds, 16)
        self.sub_embs = nn.ModuleList([nn.Embedding(max(1, s), 16) for s in sub_sizes])
        self.amt_emb = nn.Embedding(self.n_amounts, 8)
        d_in = d_conv + d_army + 16 + 16 + 8
        # 候选编码只做一层：打分用「全局 query · 候选 key」点积（O(d) 而非 O(d²)/候选）。
        # 实测（rl/bench.py）：每步 K≈300 个候选时，打分头是唯一瓶颈，卷积规模几乎不影响耗时。
        self.cand_mlp = nn.Sequential(nn.Linear(d_in, d_cand), nn.ReLU())
        # 打分前归一化 query 与候选：点积 q·c 对向量**模长**敏感，而模长与局面无关——
        # 模型于是走了捷径：把某几类候选（实测 recruit 模长 4.26 vs sell 3.08）的模长
        # 顶上去，不看局面也能让它们的地一名。实测模型能把 build 排进前 10（82%）
        # 却从不把它排第一，而 argmax 长期只落在 recruit/sell 两类上。
        # LayerNorm 后每向量零均值单位方差，模长信息被抹掉，只能靠**方向**（内容）打分。
        self.cand_ln = nn.LayerNorm(d_cand)
        self.q_ln = nn.LayerNorm(d_cand)
        self.query = nn.Linear(d_global, d_cand)
        # 价值头**不接共用的 g**：`g` 是策略唯一的「现在该干什么」输入（query 只从它来），
        # 让它兼差预测「还能赚多少」会把表征整个带偏——实测 BC 里 0.5·loss_v ≈ 500
        # 而 loss_pi ≈ 3，主干梯度几乎全归价值，argmax 于是长期锁死在同一类上
        # （实测 move 56% 而老师只有 15%；sell 老师占 66% 却只有 1.9%），策略不再看局面。
        # 价值头从**原始 glob** 自己走一条路，两边各练各的。
        self.value = nn.Sequential(nn.Linear(n_glob, 128), nn.ReLU(), nn.Linear(128, 1))
        self.null_tile = nn.Parameter(torch.zeros(d_conv))
        self.null_army = nn.Parameter(torch.zeros(d_army))

    def forward(self, grid: torch.Tensor, glob: torch.Tensor, cand: dict,
                mask: torch.Tensor | None = None):
        """grid [B,C,H,W]、glob [B,G]、cand 各张量 [B,K]/[B,A,F] → logits [B,K]、value [B]。"""
        b, k = cand["type_idx"].shape
        fmap = self.conv(grid)                                  # [B,d,H,W]
        tile_feat = fmap.flatten(2).transpose(1, 2)             # [B,HW,d]
        null_t = self.null_tile.view(1, 1, -1).expand(b, -1, -1)
        tile_feat = torch.cat([tile_feat, null_t], dim=1)       # [B,HW+1,d]
        ti = cand["tile_idx"].clamp(0, self.n_tiles)
        tf = tile_feat.gather(1, ti.unsqueeze(-1).expand(-1, -1, tile_feat.size(-1)))

        af = self.army_mlp(cand["army_feats"])                  # [B,A,da]
        null_a = self.null_army.view(1, 1, -1).expand(b, -1, -1)
        af = torch.cat([af, null_a], dim=1)                     # [B,A+1,da]
        ai = cand["army_idx"].clamp(0, af.size(1) - 1)
        ag = af.gather(1, ai.unsqueeze(-1).expand(-1, -1, af.size(-1)))

        te = self.type_emb(cand["type_idx"])                    # [B,K,16]
        subs = torch.stack([emb(cand["sub_idx"].clamp(0, emb.num_embeddings - 1))
                            for emb in self.sub_embs], dim=2)   # [B,K,n_kinds,16]
        se = subs.gather(2, cand["type_idx"].view(b, k, 1, 1).expand(-1, -1, 1, 16)).squeeze(2)
        ae = self.amt_emb(cand["amount_idx"])                   # [B,K,8]

        c = self.cand_mlp(torch.cat([tf, ag, te, se, ae], dim=-1))   # [B,K,dc]
        g = self.glob_mlp(glob)                                       # [B,dg]
        q = self.query(g)                                             # [B,dc]
        logits = (self.q_ln(q).unsqueeze(1) * self.cand_ln(c)).sum(-1) / (c.size(-1) ** 0.5)
        if mask is not None:
            logits = logits.masked_fill(~mask, -1e9)
        return logits, self.value(glob).squeeze(-1)
