# -*- coding: utf-8 -*-
"""沙盒策略网络：**窗口 Transformer 主干**（`feat/rl` 那套的沙盒重建版）。

    形状
    ────
        logits, value = net(batch)

    `batch` 是 `encode.obs_of` → `train.collate` 的产物：
        · `grid  [B,C,H,W]`           局面网格（候选按目标格 gather 空间特征）
        · `win   {g: [B,n,W]}`        ★ **窗口 token 组**（`g` 全局 / `a` 军队）
        · `type_idx/tile_hx/tile_hy/army_idx/tile_xy/cand_content`  ★ 候选的**下标形态**
        · `mask [B,K]`

    为什么换主干（用户 2026-09-24：§11「主干换成 WindowTransformer」）
    ────────────────────────────────────────────────────────────────
    旧的 `logits = q(s)·c(a)` 是**双线性**的：每个候选**独立打分**。而实战里最常见的
    三类运算（旧线 `TOKEN_DESIGN.md` §3 点的名）——

        targets.sort(key=lambda p: need(p) / max(garrison(p), 1))   # 排序
        def dist_to(p): return min(cheb(a, p) for a in armies)      # 取最近
        near = [a for a in armies if 距离 ≤ 1][:need_n]             # 成组/配额

    ——"**哪支军离这个落点最近**"这件事，独立打分**表达不出来**：候选 "军7→X" 的编码里
    只有军7，它不知道军3 更近。换成 **候选作 Q、窗口作 K/V 的 cross-attention** 之后，
    "在上下文里找最近的那个"变成一个**可表达的运算**。这是换主干真正买到的东西。

    ★★ 四条从 `feat/rl/transformer.py` 搬过来的教训（都是踩出来的，逐条照用）
    ──────────────────────────────────────────────────────────────────────
    1. ★ **不加位置编码**。绝对 PE 会把"我在图哪个位置"漏回去，而那是智能体
       **不可能知道**的（迷雾挡着、从未探索过边界）。位置一律以**特征**形式进
       （相对家的偏移 ÷ 边长）；相对位置 cross-attention 自己算得出来。
    2. ★ **`key_padding_mask` 的 True = 不看**（是 `~mask`，不是 `mask`）。
       补出来的零向量若以"内容"身份参与 softmax ⇒ 不报错、只是学歪。
    3. ★ **整条全是 padding 的行**会让 softmax 全 `-inf` → NaN。显式放行第 0 条兜底
       （`g` 组恒亮 ⇒ 沙盒其实碰不到，但**别删**：组结构是可扩展的）。
    4. ★ **`cand_mask.to(device)`** 是防御不是多余 —— 它由 collate 在 CPU 上产生，
       搬模型到 GPU 后忘了搬就报 "expected self and mask to be on the same device"。
       这类漏搬**在 CPU 上完全看不出来**，所以值得防。
    5. ★ **`cross2` 用独立参数层**（不绑 `cross`）：候选→窗口 与 候选→候选 是两种关系，
       绑成一套权重会压住这一改想修的表达力。
    6. ★ **打分不能只看注意力输出**：注意力输出是窗口的加权和，**不带候选身份** ⇒
       `score` 吃 `[h, q0]`（互看后的表示 ⊕ 候选自身编码）。

    ★ 规则表数值走**第二路**（`features`）：候选的内容段 = 执行军兵种数值 ⊕ 目标格地形数值；
      军队 token 尾部挂该军兵种的数值；全局 token 挂骰子/撤退/续战常数。**现算**，
      不查表不缓存 —— 引擎改了数值，同一盘面的输入就变（旧线栽过"石油厂 240→180
      模型动作一字不变"）。
"""
from __future__ import annotations

import torch
import torch.nn as nn

from . import features as F
from . import vocab as V


class Attn(nn.Module):
    """多头注意力（Q 与 K/V 长度可以不同）—— ★ **不用 `nn.MultiheadAttention`**。

    原因是实测出来的：`nn.MultiheadAttention` 在 **batch=1、序列很短**（K≈45、T≈6）
    的时候开销极高 —— 它那一堆 reshape/合并/`need_weights` 分支的固定成本压过了
    真正的矩阵乘。实测 `cross + cross2` 合计 **23.9 ms**，占整个 forward 的 **77%**，
    而把同样的运算交给 `F.scaled_dot_product_attention`（融合核）只要 **~2 ms**。
    训练是 **B=1 逐步采样**（`collect_episode` 每步一次前向）⇒ 这笔固定成本**每次都付**，
    是整个训练吞吐的瓶颈，不是"以后再优化"。

    ★ `key_pad` 的口径与旧代码一致：**True = 不看**（padding 位置不许当 K/V）。
      SDPA 的 `attn_mask` 语义相反（True = 参与），所以这里取反后传。
    """

    def __init__(self, d_model: int, n_head: int, p_drop: float = 0.0):
        super().__init__()
        assert d_model % n_head == 0, (d_model, n_head)
        self.h = int(n_head)
        self.dh = d_model // n_head
        self.q = nn.Linear(d_model, d_model)
        self.kv = nn.Linear(d_model, 2 * d_model)
        self.out = nn.Linear(d_model, d_model)
        self.p_drop = float(p_drop)

    def forward(self, q_in: torch.Tensor, kv_in: torch.Tensor,
                key_pad: torch.Tensor | None = None) -> torch.Tensor:
        b, lq, _ = q_in.shape
        lk = kv_in.shape[1]
        q = self.q(q_in).view(b, lq, self.h, self.dh).transpose(1, 2)
        k, v = self.kv(kv_in).chunk(2, dim=-1)
        k = k.view(b, lk, self.h, self.dh).transpose(1, 2)
        v = v.view(b, lk, self.h, self.dh).transpose(1, 2)
        am = None if key_pad is None else (~key_pad)[:, None, None, :]
        a = torch.nn.functional.scaled_dot_product_attention(
            q, k, v, attn_mask=am, dropout_p=self.p_drop if self.training else 0.0)
        a = a.transpose(1, 2).reshape(b, lq, self.h * self.dh)
        return self.out(a)


class Block(nn.Module):
    """标准 pre-norm Transformer block。**不加位置编码**（理由见模块 docstring 第 1 条）。"""

    def __init__(self, d_model: int, n_head: int, d_ff: int | None = None,
                 p_drop: float = 0.0):
        super().__init__()
        self.ln1 = nn.LayerNorm(d_model)
        self.attn = Attn(d_model, n_head, p_drop)
        self.ln2 = nn.LayerNorm(d_model)
        d_ff = d_ff or 4 * d_model
        self.ff = nn.Sequential(nn.Linear(d_model, d_ff), nn.GELU(),
                                nn.Linear(d_ff, d_model))

    def forward(self, x: torch.Tensor, key_padding_mask: torch.Tensor | None = None):
        h = self.ln1(x)
        x = x + self.attn(h, h, key_padding_mask)
        return x + self.ff(self.ln2(x))


class PolicyNet(nn.Module):
    def __init__(self, *, win_widths: dict[str, int], n_grid_ch: int = V.GRID_CHANNELS,
                 n_types: int = len(V.KIND), f_cand: int = F.F_CAND,
                 f_marks: int = V.CAND_MARKS,
                 arm_width: int = V.A_WIDTH_RAW + F.F_U,
                 d_conv: int = 96, d_model: int = 160, n_layer: int = 3, n_head: int = 4,
                 d_cand: int = 160, groups: tuple[str, ...] = V.TOKEN_GROUPS):
        super().__init__()
        self.groups = tuple(groups)
        self.d_model = int(d_model)
        self.n_types = int(n_types)
        self.arm_width = int(arm_width)

        # ---- 网格侧：只做 1×1→3×3 提特征，供候选按目标格 **gather** ----
        #   ★ 不做全局平均池化 —— 池化会把空间信息压成一个数，候选就"看不见自己那格
        #     周围有什么、离核心多远"。棋盘游戏的关键正是空间。
        self.conv = nn.Sequential(
            nn.Conv2d(n_grid_ch, d_conv, 1), nn.ReLU(),
            nn.Conv2d(d_conv, d_conv, 3, padding=1), nn.ReLU())

        # ---- 窗口侧：每组一层投影（各组宽度不同，**不补零当同质 token**）----
        self.proj = nn.ModuleDict({g: nn.Linear(int(win_widths[g]), d_model)
                                   for g in self.groups})
        # 组身份嵌入：投影后各组的统计性质仍不同（g 是摘要、a 是单支军队），
        # 给模型一个"这条 token 是哪种"的显式提示。
        self.group_emb = nn.Embedding(len(self.groups), d_model)
        self.blocks = nn.ModuleList([Block(d_model, n_head) for _ in range(n_layer)])
        self.ln_out = nn.LayerNorm(d_model)

        # ---- 候选侧：★ **下标形态**（不是平铺标量）----
        self.type_emb = nn.Embedding(self.n_types, 16)
        # ★ 输出宽度必须是 `d_model`（不是 32）：它要和**上下文里的军队 token**
        #   （已经过 transformer，宽 `d_model`）相加 —— 写 32 会在 forward 报
        #   "The size of tensor a (160) must match the size of tensor b (32)"。
        self.army_mlp = nn.Sequential(nn.Linear(arm_width, d_model), nn.ReLU())
        # 落点 (dx,dy)/边长 + 有无落点 + 军队编码 d_model(+1 has) + 规则表内容 + 标记段
        d_q = 16 + 3 + (d_model + 1) + int(f_cand) + int(f_marks)
        self.cand_mlp = nn.Sequential(nn.Linear(d_q + d_conv, d_cand), nn.ReLU())
        self.to_q = nn.Linear(d_cand, d_model)
        # ★ 候选 → 窗口（K/V = 窗口）
        self.cross = Attn(d_model, n_head)
        self.ln_c = nn.LayerNorm(d_model)
        # ★ candx2：候选**互相可见**，用**独立**注意力层（教训第 5 条）
        self.cross2 = Attn(d_model, n_head)
        self.ln_cand = nn.LayerNorm(d_model)
        # ★ 打分 = 互看输出 ⊕ 候选自身编码（教训第 6 条）
        self.score = nn.Sequential(nn.Linear(d_model + d_cand, d_model), nn.GELU(),
                                   nn.Linear(d_model, 1))
        # ★ 价值头走上限池化后的**窗口**（不是 glob）—— 窗口才是全局状态的唯一来源。
        self.value = nn.Sequential(nn.Linear(d_model, 128), nn.ReLU(),
                                   nn.Linear(128, 1))

    # ------------------------------------------------------------------
    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())

    # ------------------------------------------------------------------
    def encode_window(self, win: dict, wmask: dict) -> tuple[torch.Tensor, torch.Tensor]:
        """窗口 → `(tokens [B,T,d], mask [B,T])`。T = 各组 token 数之和。"""
        toks, msks = [], []
        for gi, g in enumerate(self.groups):
            h = self.proj[g](win[g])
            h = h + self.group_emb.weight[gi].view(1, 1, -1)
            toks.append(h)
            msks.append(wmask[g])
        return torch.cat(toks, dim=1), torch.cat(msks, dim=1)

    def forward(self, batch: dict):
        grid = batch["grid"]
        win, wmask = batch["win"], batch["win_mask"]

        # ---- 窗口（K/V 的来源）----
        x, wm = self.encode_window(win, wmask)
        # ★ 教训第 2 条：`key_padding_mask` 的 True = **不看** ⇒ 取反
        kpm = ~wm
        # ★ 教训第 3 条：整条全 padding 的行会让 softmax 全 -inf → NaN，放行第 0 条兜底
        dead = kpm.all(dim=1)
        if bool(dead.any()):
            kpm = kpm.clone()
            kpm[dead, 0] = False
        for blk in self.blocks:
            x = blk(x, key_padding_mask=kpm)
        x = self.ln_out(x)
        n_a = int(win["a"].shape[1]) if "a" in win else 0

        # ---- 网格：候选按目标格 gather 空间特征 ----
        fmap = self.conv(grid)                                  # [B,dc,H,W]
        hh, ww = fmap.shape[2], fmap.shape[3]
        flat = fmap.flatten(2).transpose(1, 2)                   # [B,H*W,dc]
        xy = batch["tile_xy"].clamp(min=0)                       # (-1,-1) → (0,0)
        idx = (xy[..., 0] * ww + xy[..., 1]).clamp(0, hh * ww - 1)
        tf = flat.gather(1, idx.unsqueeze(-1).expand(-1, -1, flat.size(-1)))

        # ---- 候选：**下标形态** ----
        b, k = batch["type_idx"].shape
        te = self.type_emb(batch["type_idx"])                    # [B,K,16]
        # 落点：**相对家的偏移，已在 `encode` 里除以地图边长** —— 不是绝对坐标、
        # 也不是扁平下标（扁平下标绑死网格形状，而地图尺寸是逐局随机的）。
        # ★ 归一**放在 encode 侧**、不在这里：`pos_scale` 若是模型上的一个 buffer，
        #   就只有一个值，而**一批里会混不同尺寸的地图**（随机地图 + 跨局攒 buffer）
        #   ⇒ 那是个"不报错、只是尺度对不上"的静默坑。
        pos = torch.cat([batch["pos_dx"].unsqueeze(-1), batch["pos_dy"].unsqueeze(-1),
                         batch["has_pos"].unsqueeze(-1)], dim=-1)
        # ★ **被引用的军队**：直接从**上下文里**的军队 token 取（不是另起一个 MLP）——
        #   这样它带着"跟其他军/敌军的关系"，正是 cross-attention 买到的那个能力。
        if n_a:
            xa = x[:, 1:1 + n_a]                                 # `g` 恒在第 0 位
            af = self.army_mlp(win["a"])                         # 兜底：原始特征
            ai = batch["army_idx"].clamp(0, n_a - 1)
            ag = xa.gather(1, ai.unsqueeze(-1).expand(-1, -1, xa.size(-1))) \
                 + af.gather(1, ai.unsqueeze(-1).expand(-1, -1, af.size(-1)))
            ahas = (batch["army_idx"] < n_a).float().unsqueeze(-1)
        else:
            ag = torch.zeros(b, k, self.d_model, dtype=x.dtype, device=x.device)
            ahas = torch.zeros(b, k, 1, dtype=x.dtype, device=x.device)

        q0 = self.cand_mlp(torch.cat([te, pos, ag, ahas,
                                      batch["cand_content"], batch["cand_marks"], tf],
                                     dim=-1))                                # [B,K,dc]
        qq = self.to_q(q0)                                                   # [B,K,d]
        # Q = 候选（K 个），K/V = 窗口（T 个）—— `batch_first=True` 下序列维就是 K
        att = self.cross(qq, x, kpm)
        h = self.ln_c(att + qq)                       # 残差：注意力输出 + 候选自己
        # ---- candx2：候选互相可见（独立层）----
        cm = None if batch.get("mask") is None else ~batch["mask"].to(h.device)
        att2 = self.cross2(h, h, cm)
        h = self.ln_cand(att2 + h)
        logits = self.score(torch.cat([h, q0], dim=-1)).squeeze(-1)
        if batch.get("mask") is not None:
            # ★ 教训第 4 条：`.to(device)` 是防御（GPU 上漏搬才炸，CPU 上看不出来）
            logits = logits.masked_fill(~batch["mask"].to(logits.device), -1e9)

        # ---- 价值：窗口的**掩码均值池化** ----
        m = wm.unsqueeze(-1).to(x.dtype)
        pooled = (x * m).sum(1) / m.sum(1).clamp(min=1.0)
        return logits, self.value(pooled).squeeze(-1)


def build_model(n_grid_ch: int = V.GRID_CHANNELS, **kw) -> PolicyNet:
    """按沙盒词表建网（`train.py` 用）。

    窗口各组的宽度**从词表与 `features` 现算**（不是写死的表）—— 将来加组只改
    `vocab.TOKEN_GROUPS` 与这里的一行，主干不动（§11「结构留位」）。
    """
    widths = {
        "g": V.GLOB_SIZE + F.F_GLOB,
        "a": V.A_WIDTH_RAW + F.F_U,
    }
    return PolicyNet(win_widths={g: widths[g] for g in V.TOKEN_GROUPS},
                     n_grid_ch=n_grid_ch, **kw)