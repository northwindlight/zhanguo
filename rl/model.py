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
from rl.features import CONTENT_DIM_OF_KIND
from rl.tokenize import GROUPS


class SubEmbedder(nn.Module):
    """`(type_idx, sub_idx)` → `d_out` 维。**两路相加**（`TOKEN_DESIGN.md` §10.2 载体 B）：

        学到的查表（`nn.Embedding(sub_idx)`，语义是训练时拟合的）
      + 规则表**内容**的投影（`content[kind][sub_idx]`，数值每帧现算）

    为什么非要第二路：只有查表时，`sub_idx` 是个纯下标 —— 引擎把石油能源厂从 240
    降到 180，模型在**同一个盘面**上给出的决策一模一样（它看不见那个数），而最优
    决策已经变了。这是**表达能力**问题，不是拟合精度问题（`rl/PLAN.md` 〇.5）。

    `content` 缺省（或某个 kind 没有内容）时退化成纯查表 —— 那条路留给不便构造
    内容表的调用点，但**训练/评估都该带上它**。
    """

    def __init__(self, sub_sizes: list[int], *, d_out: int = 16, kinds=KINDS):
        super().__init__()
        self.kinds = tuple(kinds)
        self.d_out = int(d_out)
        self.embs = nn.ModuleList([nn.Embedding(max(1, s), d_out) for s in sub_sizes])
        self.proj = nn.ModuleList([
            nn.Linear(d, d_out) if d else None
            for d in (CONTENT_DIM_OF_KIND.get(k, 0) for k in self.kinds)])

    def forward(self, type_idx: torch.Tensor, sub_idx: torch.Tensor,
                content: dict | None = None) -> torch.Tensor:
        b, k = type_idx.shape
        si = sub_idx.clamp(min=0)
        parts = []
        for i, emb in enumerate(self.embs):
            v = emb(si.clamp(max=emb.num_embeddings - 1))
            proj = self.proj[i]
            ctab = None if content is None else content.get(self.kinds[i])
            if proj is not None and ctab is not None:
                # ctab [B, n_sub, F] → 按 sub_idx 取到每个候选那一行。
                # ★`sub_idx` 是**跨 kind 共享**的一个张量（build 的能到 18，recruit 的只到 4），
                #   所以往小表 gather 前必须**按这张表的大小再 clamp 一次** —— 越界的那些
                #   候选不是这个 kind 的，最终会被下面的 `type_idx` gather 丢掉。
                f = ctab.size(-1)
                si_k = si.clamp(max=ctab.size(1) - 1)
                v = v + proj(ctab.gather(1, si_k.unsqueeze(-1).expand(b, k, f)))
            parts.append(v)
        stacked = torch.stack(parts, dim=2)                 # [B,K,n_kinds,d_out]
        picked = stacked.gather(2, type_idx.view(b, k, 1, 1)
                                .expand(-1, -1, 1, self.d_out))
        return picked.squeeze(2)


class WindowEncoder(nn.Module):
    """token 窗口 → 定长向量（P3：**只喂策略的 query**）。

    P3 刻意用最笨的形态：**每组各一层 Linear 投到同一宽度 → 掩码均值池化 → 拼 → MLP**。

    为什么不像 P4 那样做 self-attention：P3 的任务是**把 tokenize 和主干分开验证**
    （`TOKEN_DESIGN` §7）。这里一旦上了注意力，下次分数掉了就分不清是 tokenize 编错了
    还是注意力没训上来。所以 P3 的编码器**故意不含任何集合级运算**——
    它只证明「这条管子通了、窗口里的信息够驱动 query」。

    各组宽度不同（g 56 / m 39 / a 12 / n 16 / e 12 / r 16 / k 8），所以**每组一层投影**，
    不补零到同一宽度当同质 token 用——那样等于凭空给窄组塞一堆常数维度。
    """

    def __init__(self, widths: dict[str, int], d_enc: int = 64, d_out: int = 128,
                 groups: tuple[str, ...] = GROUPS):
        super().__init__()
        self.groups = tuple(groups)
        self.proj = nn.ModuleDict({g: nn.Linear(int(widths[g]), d_enc)
                                   for g in self.groups})
        self.mlp = nn.Sequential(nn.Linear(len(self.groups) * d_enc, d_out), nn.ReLU(),
                                 nn.Linear(d_out, d_out), nn.ReLU())

    def forward(self, win: dict) -> torch.Tensor:
        """`win = {"feats": {组: [B,n,F]}, "mask": {组: [B,n] bool}}` → `[B,d_out]`。"""
        outs = []
        for g in self.groups:
            h = self.proj[g](win["feats"][g])                        # [B,n,d]
            m = win["mask"][g].unsqueeze(-1).to(h.dtype)             # [B,n,1]
            # 全灭的组（外交/事件/记忆各预留组）分母夹到 1 → 输出恒 0。
            # 这就是"预留位现在不亮"在数学上的样子：**不参与、也不污染**。
            outs.append((h * m).sum(1) / m.sum(1).clamp(min=1.0))
        return self.mlp(torch.cat(outs, dim=-1))


class PolicyNet(nn.Module):
    def __init__(self, n_grid_ch: int, n_glob: int, sub_sizes: list[int], n_tiles: int, *,
                 d_conv: int = 64, d_bottle: int = 32, n_conv3: int = 2,
                 d_global: int = 128, d_cand: int = 128, d_army: int = 32,
                 win_widths: dict[str, int] | None = None, d_enc: int = 64):
        super().__init__()
        # P3：传 `win_widths` 就把策略 query 的来源从 `glob_mlp(glob)` 换成
        # `WindowEncoder(窗口)`。**别的一律不动** —— CNN（候选的地块特征）、
        # 候选嵌入、点积打分、价值头全部保持原样。见 rl/PLAN.md 的 P3。
        self.win_enc = (WindowEncoder(win_widths, d_enc=d_enc, d_out=d_global)
                        if win_widths else None)
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
        # 候选的子项嵌入：学到的查表 + **规则表内容**（§10.2 载体 B，见 SubEmbedder）
        self.sub_emb = SubEmbedder(sub_sizes)
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
                mask: torch.Tensor | None = None, win: dict | None = None):
        """grid [B,C,H,W]、glob [B,G]、cand 各张量 [B,K]/[B,A,F] → logits [B,K]、value [B]。

        `win`：token 窗口的批（见 `rl/ppo.collate_window`）。给了就用它算 query，
        没给就走原来的 `glob_mlp`。两条路**输出同一个 d_global**，所以下游都不用改。
        """
        b, k = cand["type_idx"].shape
        fmap = self.conv(grid)                                  # [B,d,H,W]
        tile_feat = fmap.flatten(2).transpose(1, 2)             # [B,HW,d]
        null_t = self.null_tile.view(1, 1, -1).expand(b, -1, -1)
        tile_feat = torch.cat([tile_feat, null_t], dim=1)       # [B,HW+1,d]
        # ★空位下标**按张量实时算**，不用固定的 `self.n_tiles`：观测网格是「可见区
        #   外接框」，尺寸逐帧可变（地图尺寸也逐局可变），写死会在换尺寸时静默取错格。
        #   `tile_feat` 的最后一格就是空位（上面 concat 进去的 `null_tile` 参数）。
        ti = cand["tile_idx"].clamp(0, tile_feat.size(1) - 1)
        tf = tile_feat.gather(1, ti.unsqueeze(-1).expand(-1, -1, tile_feat.size(-1)))

        af = self.army_mlp(cand["army_feats"])                  # [B,A,da]
        null_a = self.null_army.view(1, 1, -1).expand(b, -1, -1)
        af = torch.cat([af, null_a], dim=1)                     # [B,A+1,da]
        ai = cand["army_idx"].clamp(0, af.size(1) - 1)
        ag = af.gather(1, ai.unsqueeze(-1).expand(-1, -1, af.size(-1)))

        te = self.type_emb(cand["type_idx"])                    # [B,K,16]
        se = self.sub_emb(cand["type_idx"], cand["sub_idx"], cand.get("content"))
        ae = self.amt_emb(cand["amount_idx"])                   # [B,K,8]

        c = self.cand_mlp(torch.cat([tf, ag, te, se, ae], dim=-1))   # [B,K,dc]
        # ★query 的来源：给了窗口就用窗口，否则仍是 glob。
        #   价值头**照旧吃原始 glob**（下面那行）—— 它有自己的理由不共用 g，
        #   见 __init__ 里那段注释；P3 不动它，好让"换了编码"是唯一的变量。
        g = self.win_enc(win) if (self.win_enc is not None and win is not None) \
            else self.glob_mlp(glob)                                  # [B,dg]
        q = self.query(g)                                             # [B,dc]
        logits = (self.q_ln(q).unsqueeze(1) * self.cand_ln(c)).sum(-1) / (c.size(-1) ** 0.5)
        if mask is not None:
            logits = logits.masked_fill(~mask, -1e9)
        return logits, self.value(glob).squeeze(-1)
