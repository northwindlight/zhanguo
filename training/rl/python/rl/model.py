"""策略/价值网络：实体 token + 候选 token 的 Transformer，对**候选**打分；带回合级 GRU 记忆。

记忆（用户 2026-11-19：「真的 rnn 比较好」；设计与教训来自战争 RL 分支 `feat/rl-tactics` 的感知/潜槽）：
- 每个回合开头把"回合摘要 x_t"（全局向量、上回合动作计数、视野内敌军统计、无效步数；全是原始特征）喂给 GRU，
  隐状态 m_t 经线性投影成**一个记忆 token** 加进序列；本回合所有决策共用 m_t。
- 回合级（~100 步）而非决策级（~1500 步），且 GRU 输入是原始特征 ⇒ 训练时可对**整局序列精确 BPTT**，
  不用"存隐状态当常量"的近似。
- **辅助监督（战争分支的教训：只靠策略梯度，记忆学不出"该记什么"）**：从 m_t 预测"下一回合将离开视野的那批敌军"
  （后见之明，只作监督目标，绝不进观测）。
- **critic 只读原始特征**（全局向量 + 地块/军队/市场的掩码均值 + 记忆 detach），不与主干共享任何参数
  （战争分支实测：共享主干的价值头会把主干磨平、V 退化成常数）。

- 输入是 `rl/obs.py` 的 collate 结果：globals / market / nations / tiles / armies / cand（+ 指针）。
- **不做硬掩码**（用户要求，沿用上次 economy 训练）：只屏蔽补位（padding）；候选的 `executable` 是输入特征，
  同时有 exec 头做辅助监督；可选软先验 `exec_prior_weight * log σ(exec_logit)`（默认 0）。
- 价值头单独一支，且对主干的梯度缩放（`value_grad_scale`，默认 0.1）：旧线记录过
  "价值梯度是策略的 41 倍、把主干压平"，这里从结构上限制它。
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .candidates import FEAT_DIM
from .obs import A_DIM, F_DIM, G_DIM, M_AUX, M_DIM, N_DIM, T_DIM


class _GradScale(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, s):
        ctx.s = s
        return x.view_as(x)

    @staticmethod
    def backward(ctx, g):
        return g * ctx.s, None


def aux_loss(pred, tgt, mask, nz_w: float = 10.0):
    """记忆辅助损失。目标在绝大多数回合全 0（没有敌军离开视野），朴素 MSE 会被"全预测 0"压到 ≈0 而没有信号；
    所以对"目标非零"的回合加权 nz_w，并返回 (loss, 非零回合上的 MSE / 全零预测的 MSE)。后者 <1 才说明记忆真的在预测。"""
    if not bool(mask.any()):
        z = torch.zeros((), device=pred.device)
        return z, z
    se = ((pred - tgt) ** 2).mean(-1)[mask]
    nz = (tgt.abs().sum(-1) > 0)[mask]
    w = 1.0 + (nz_w - 1.0) * nz.float()
    loss = (se * w).sum() / w.sum()
    if bool(nz.any()):
        base = (tgt[mask][nz] ** 2).mean(-1).mean().clamp(min=1e-8)
        rel = se[nz].mean().detach() / base
    else:
        rel = torch.zeros((), device=pred.device)
    return loss, rel


class CandBlock(nn.Module):
    """候选只读局面：交叉注意力 + FFN（pre-norm），候选之间不互相注意。"""

    def __init__(self, d: int, heads: int, self_attn: bool = False):
        super().__init__()
        self.ln1, self.ln2 = nn.LayerNorm(d), nn.LayerNorm(d)
        self.attn = nn.MultiheadAttention(d, heads, batch_first=True)
        self.ff = nn.Sequential(nn.Linear(d, 4 * d), nn.GELU(), nn.Linear(4 * d, d))
        if self_attn:                                       # 候选之间互相比较（"这几处里建哪一处"）
            self.ln0 = nn.LayerNorm(d)
            self.sattn = nn.MultiheadAttention(d, heads, batch_first=True)
        self.self_attn = self_attn

    def forward(self, c, board, board_pad, cand_pad):
        if self.self_attn:
            q = self.ln0(c)
            c = c + self.sattn(q, q, q, key_padding_mask=cand_pad, need_weights=False)[0]
        q = self.ln1(c)
        c = c + self.attn(q, board, board, key_padding_mask=board_pad, need_weights=False)[0]
        return c + self.ff(self.ln2(c))


class Policy(nn.Module):
    def __init__(self, d: int = 256, layers: int = 4, heads: int = 8, value_grad_scale: float = 0.1,
                 exec_prior_weight: float = 0.0, mem_h: int = 128, arch: str = "full", cand_layers: int = 1,
                 cand_self: bool = False):
        super().__init__()
        self.cfg = dict(d=d, layers=layers, heads=heads, value_grad_scale=value_grad_scale,
                        exec_prior_weight=exec_prior_weight, mem_h=mem_h)
        if arch != "full":                                  # 旧检查点的 cfg 不含这两项，保持可加载
            self.cfg.update(arch=arch, cand_layers=cand_layers, cand_self=cand_self)
        assert arch in ("full", "xattn")
        self.arch = arch
        self.mem_h = mem_h
        self.gru = nn.GRU(F_DIM, mem_h, batch_first=True)
        self.mem_proj = nn.Linear(mem_h, d)
        self.mem_aux = nn.Sequential(nn.Linear(mem_h, mem_h), nn.GELU(), nn.Linear(mem_h, M_AUX))
        self.proj = nn.ModuleDict(dict(
            g=nn.Linear(G_DIM, d), m=nn.Linear(M_DIM, d), n=nn.Linear(N_DIM, d),
            t=nn.Linear(T_DIM, d), a=nn.Linear(A_DIM, d), c=nn.Linear(FEAT_DIM, d)))
        self.kind = nn.Embedding(7, d)                      # token 类别：g m n t a c mem
        layer = nn.TransformerEncoderLayer(d, heads, 4 * d, dropout=0.0, batch_first=True, norm_first=True)
        self.enc = nn.TransformerEncoder(layer, layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(d)
        if arch == "xattn":
            # 编码器只跑局面 token（全局/记忆/市场/国家/地块/军队，约 183 个）；候选用交叉注意力读编码后的局面
            self.cross = nn.ModuleList([CandBlock(d, heads, cand_self) for _ in range(cand_layers)])
            self.cnorm = nn.LayerNorm(d)
        self.logit = nn.Linear(d, 1)
        self.exec_head = nn.Linear(d, 1)
        crit_in = G_DIM + T_DIM + A_DIM + M_DIM * 6 + mem_h
        self.value = nn.Sequential(nn.Linear(crit_in, 256), nn.GELU(), nn.Linear(256, 256), nn.GELU(), nn.Linear(256, 1))
        self.vgs = value_grad_scale
        self.xw = exec_prior_weight

    @staticmethod
    def _gather(tok, ptr):
        """按指针从实体 token 里取向量；-1 → 0。tok:[B,N,D] ptr:[B,K]"""
        if tok.shape[1] == 0:
            return torch.zeros(ptr.shape[0], ptr.shape[1], tok.shape[2], device=tok.device, dtype=tok.dtype)
        idx = ptr.clamp(min=0).unsqueeze(-1).expand(-1, -1, tok.shape[2])
        out = torch.gather(tok, 1, idx)
        return out * (ptr >= 0).unsqueeze(-1).to(out.dtype)

    # ---- 记忆 ----
    def mem_seq(self, X):
        """整局序列：X [E,T,F] → 各回合的隐状态 [E,T,H]（训练：对序列精确 BPTT；fp32）。"""
        return self.gru(X)[0]

    def mem_step(self, x, h):
        """推理：x [n,F]，h [n,H] → 新 h [n,H]（与 mem_seq 逐步等价）。"""
        return self.gru(x[:, None, :], h[None])[1][0]

    @staticmethod
    def _mmean(x, mask):
        m = mask.unsqueeze(-1).to(x.dtype)
        return (x * m).sum(1) / m.sum(1).clamp(min=1.0)

    def forward(self, b: dict):
        p = self.proj
        g = p["g"](b["globals"]) + self.kind.weight[0]
        m = p["m"](b["market"]) + self.kind.weight[1]
        n = p["n"](b["nations"]) + self.kind.weight[2]
        t = p["t"](b["tiles"]) + self.kind.weight[3]
        a = p["a"](b["armies"]) + self.kind.weight[4]
        c = p["c"](b["cand"]) + self.kind.weight[5]
        ptr = b["cand_ptr"]
        # 候选 token 先"看一眼"它指向的地块/军队/国家（指针式拼接，比全连接注意力省）
        c = c + self._gather(t, ptr[..., 0]) + self._gather(a, ptr[..., 1]) + self._gather(n, ptr[..., 2])
        mem = b["mem"]                                                   # [B,H] 本回合的记忆（GRU 隐状态）
        mt = (self.mem_proj(mem) + self.kind.weight[6]).unsqueeze(1)
        ones = torch.ones(mem.shape[0], 1, dtype=torch.bool, device=mem.device)
        if self.arch == "xattn":
            hc = self._xattn(g, mt, m, n, t, a, b, ones)
        else:
            x = torch.cat([g, mt, m, n, t, a, c], 1)
            pad = ~torch.cat([b["globals_mask"], ones, b["market_mask"], b["nations_mask"], b["tiles_mask"],
                              b["armies_mask"], b["cand_mask"]], 1)
            h = self.norm(self.enc(x, src_key_padding_mask=pad))
            K = c.shape[1]
            hc = h[:, -K:]
        logits = self.logit(hc).squeeze(-1)
        exec_logit = self.exec_head(hc).squeeze(-1)
        if self.xw:
            logits = logits + self.xw * F.logsigmoid(exec_logit)
        logits = logits.masked_fill(~b["cand_mask"], -1e9)          # 只屏蔽补位
        # critic：只读原始特征（不共享主干参数；记忆 detach）
        raw = torch.cat([b["globals"][:, 0], self._mmean(b["tiles"], b["tiles_mask"]),
                         self._mmean(b["armies"], b["armies_mask"]), b["market"].flatten(1), mem.detach()], 1)
        value = self.value(raw).squeeze(-1)
        return logits, exec_logit, value

    def _xattn(self, g, mt, m, n, t, a, b, ones):
        x = torch.cat([g, mt, m, n, t, a], 1)
        pad = ~torch.cat([b["globals_mask"], ones, b["market_mask"], b["nations_mask"], b["tiles_mask"],
                          b["armies_mask"]], 1)
        h = self.norm(self.enc(x, src_key_padding_mask=pad))
        o = g.shape[1] + 1 + m.shape[1]                                  # 编码后各实体段的起点
        hn, ht, ha = (h[:, o:o + n.shape[1]], h[:, o + n.shape[1]:o + n.shape[1] + t.shape[1]],
                      h[:, o + n.shape[1] + t.shape[1]:])
        ptr = b["cand_ptr"]
        c = self.proj["c"](b["cand"]) + self.kind.weight[5]
        c = c + self._gather(ht, ptr[..., 0]) + self._gather(ha, ptr[..., 1]) + self._gather(hn, ptr[..., 2])
        for blk in self.cross:
            c = blk(c, h, pad, ~b["cand_mask"])
        return self.cnorm(c)
