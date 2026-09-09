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
                 d_conv: int = 48, d_global: int = 128, d_cand: int = 128, d_army: int = 32):
        super().__init__()
        self.n_kinds = len(KINDS)
        self.n_tiles = int(n_tiles)          # 地块数；null 下标 = n_tiles
        self.n_amounts = len(AMOUNTS)

        self.conv = nn.Sequential(
            nn.Conv2d(n_grid_ch, 32, 3, padding=1), nn.ReLU(),
            nn.Conv2d(32, d_conv, 3, padding=1), nn.ReLU(),
        )
        self.glob_mlp = nn.Sequential(nn.Linear(n_glob, d_global), nn.ReLU(),
                                      nn.Linear(d_global, d_global), nn.ReLU())
        self.army_mlp = nn.Sequential(nn.Linear(ARMY_FEAT, d_army), nn.ReLU(),
                                      nn.Linear(d_army, d_army), nn.ReLU())
        self.type_emb = nn.Embedding(self.n_kinds, 16)
        self.sub_embs = nn.ModuleList([nn.Embedding(max(1, s), 16) for s in sub_sizes])
        self.amt_emb = nn.Embedding(self.n_amounts, 8)
        d_in = d_conv + d_army + 16 + 16 + 8
        self.cand_mlp = nn.Sequential(nn.Linear(d_in, d_cand), nn.ReLU(),
                                      nn.Linear(d_cand, d_cand), nn.ReLU())
        self.score = nn.Sequential(nn.Linear(d_global + d_cand, 128), nn.ReLU(),
                                   nn.Linear(128, 1))
        self.value = nn.Sequential(nn.Linear(d_global, 128), nn.ReLU(), nn.Linear(128, 1))
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
        logits = self.score(torch.cat([g.unsqueeze(1).expand(-1, k, -1), c], dim=-1)).squeeze(-1)
        if mask is not None:
            logits = logits.masked_fill(~mask, -1e9)
        return logits, self.value(g).squeeze(-1)
