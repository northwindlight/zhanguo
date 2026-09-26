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


class FastLinear(nn.Linear):
    """★ **只在推理路径**把权重预转置成连续 —— 救的是 **Raspberry Pi** 那份 torch。

    ★★★ 为什么要有这个（2026-09-26，用户：「如果在 rpi 上跑快点也挺不错，
       毕竟内存大，可以修，如果好修的话」）：
      在 **Pi（aarch64）** 上 `F.linear(x, w)` 比**等价的** `x @ w.t().contiguous()`
      慢 **6.6×**（实测 182.7 vs 27.8 µs，[1,45,160]×[160,160] 单线程）；
      而在 **x86/ECS 上只差 1.05×**（33.4 vs 31.8）⇒ 那是**这份 aarch64 torch
      构建的 BLAS 回退**，**不是模型的问题**，x86 上也**没有收益**。
      ★ 代价有多大（同一副工具 `rl/real_phase.py`，同配置 size12/t_max40）：

        | | 前向 | 同一局的战斗 DP（纯 Python） |
        |---|---|---|
        | Pi | **66.07 ms/步（占整条回路 70%）** | 17.49 ms/步 |
        | x86 免费机 | 5.32 ms/步（18%） | 17.42 ms/步 |

      ⇒ 前向差 **12.4×**，而纯 Python 的 DP **一模一样** ⇒ 差的全在 torch。
      ⇒ 换成本类之后：Pi 上前向 **2.00×**（实测同进程交替，输出最大差 1.6e-07）。

    ★★ **不需要动 state_dict**（我原先以为必须动、所以说"别改"，那是错的）：
      参数仍然是 `nn.Linear.weight`（`[out,in]`）**原样不动**，只是**另存**一份
      `[in,out]` 连续的副本 ⇒ **老 ckpt 照样读**，两台训练机的档不作废。
      ★ 失效靠 `weight._version`（**自动**：实测 `opt.step()` 与 no_grad 下的
        `add_()` 都会让它自增）—— **不靠"记得刷新"**，那种是静默错的形状。

    ★★★ **唯一的硬约束：这条路只许用在推理路径。** 缓存那份是 `detach()` 出来的
      拷贝 ⇒ 梯度会流到**拷贝**上、`weight.grad` 永远是 `None` ⇒ 训练**静默不学**。
      ⇒ 用 `torch.is_grad_enabled()` **自动分流**：采集回路在 `inference_mode`
        下（不记梯度）走缓存；`ppo_update` 记梯度 ⇒ 走原来的 `F.linear`。
      ★ **别把这道门改成"记得传个 flag"** —— 那正是会静默搞坏训练的形状。
      门由 `tests/test_rl_fast_linear.py` 钉着（含"梯度路径必须真的拿到梯度"）。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        # ★ 普通属性（**不是** buffer ⇒ 不进 state_dict、不参与 .to()）。
        #   所以下面判有效时要连 device/dtype 一起看 —— 模型搬到别的设备后
        #   这份副本还在原地，直接用会炸（好在是**响的**，不是静默错）。
        self._wt = None          # (tensor, version, device, dtype)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if torch.is_grad_enabled():                 # ★★ 训练路径：原样（见上）
            return nn.functional.linear(x, self.weight, self.bias)
        w = self.weight
        c = self._wt
        if (c is None or c[1] != w._version or c[2] != w.device
                or c[3] != w.dtype):
            c = (w.detach().t().contiguous(), w._version, w.device, w.dtype)
            self._wt = c
        out = x @ c[0]
        return out if self.bias is None else out + self.bias


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
        self.q = FastLinear(d_model, d_model)
        self.kv = FastLinear(d_model, 2 * d_model)
        self.out = FastLinear(d_model, d_model)
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
        self.ff = nn.Sequential(FastLinear(d_model, d_ff), nn.GELU(),
                                FastLinear(d_ff, d_model))

    def forward(self, x: torch.Tensor, key_padding_mask: torch.Tensor | None = None):
        h = self.ln1(x)
        x = x + self.attn(h, h, key_padding_mask)
        return x + self.ff(self.ln2(x))


class PolicyNet(nn.Module):
    def __init__(self, *, win_widths: dict[str, int], n_grid_ch: int = V.GRID_CHANNELS,
                 n_types: int = len(V.KIND), f_cand: int = F.F_CAND,
                 f_marks: int = V.CAND_MARKS,
                 arm_width: int = V.A_WIDTH_RAW + F.F_U + V.A_EXTRA,
                 d_conv: int = 96, d_model: int = 160, n_layer: int = 3, n_head: int = 4,
                 d_cand: int = 160, groups: tuple[str, ...] = V.TOKEN_GROUPS,
                 mem_slots: int = 0, mem_gate_bias: float = V.MEM_GATE_BIAS):
        super().__init__()
        self.groups = tuple(groups)
        self.d_model = int(d_model)
        self.n_types = int(n_types)
        self.arm_width = int(arm_width)
        # ★★ **潜槽个数**（`0` = 关掉记忆 ⇒ **一个参数都不多建**
        #   ⇒ 不开 `--memory` 的老档照样能读、老行为逐字不变）。见 `vocab.MEM_GROUP`。
        self.mem_slots = int(mem_slots)

        # ---- 网格侧：只做 1×1→3×3 提特征，供候选按目标格 **gather** ----
        #   ★ 不做全局平均池化 —— 池化会把空间信息压成一个数，候选就"看不见自己那格
        #     周围有什么、离核心多远"。棋盘游戏的关键正是空间。
        self.conv = nn.Sequential(
            nn.Conv2d(n_grid_ch, d_conv, 1), nn.ReLU(),
            nn.Conv2d(d_conv, d_conv, 3, padding=1), nn.ReLU())

        # ---- 窗口侧：每组一层投影（各组宽度不同，**不补零当同质 token**）----
        self.proj = nn.ModuleDict({g: FastLinear(int(win_widths[g]), d_model)
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
        self.army_mlp = nn.Sequential(FastLinear(arm_width, d_model), nn.ReLU())
        # 落点 (dx,dy)/边长 + 有无落点 + 军队编码 d_model(+1 has) + 规则表内容 + 标记段
        d_q = 16 + 3 + (d_model + 1) + int(f_cand) + int(f_marks)
        self.cand_mlp = nn.Sequential(FastLinear(d_q + d_conv, d_cand), nn.ReLU())
        self.to_q = FastLinear(d_cand, d_model)
        # ★ 候选 → 窗口（K/V = 窗口）
        self.cross = Attn(d_model, n_head)
        self.ln_c = nn.LayerNorm(d_model)
        # ★ candx2：候选**互相可见**，用**独立**注意力层（教训第 5 条）
        self.cross2 = Attn(d_model, n_head)
        self.ln_cand = nn.LayerNorm(d_model)
        # ★ 打分 = 互看输出 ⊕ 候选自身编码（教训第 6 条）
        self.score = nn.Sequential(FastLinear(d_model + d_cand, d_model), nn.GELU(),
                                   FastLinear(d_model, 1))
        # ★ 价值头走上限池化后的**窗口**（不是 glob）—— 窗口才是全局状态的唯一来源。
        self.value = nn.Sequential(FastLinear(d_model, 128), nn.ReLU(),
                                   FastLinear(128, 1))

        # ---- ★★ 潜槽（`--memory`；见 `vocab.MEM_GROUP` 与模块 docstring 末段）----
        if self.mem_slots:
            # ★ 槽的**初值**（学出来的"空"）。★ 用**很小的**标准差初始化：
            #   它同时是"没有记忆时的默认状态"，而进注意力时它越小、对既有
            #   注意力的扰动越小 ⇒ 开记忆的第一版**接近**马尔可夫基线。
            #   ⚠ 但"接近"不是"相等"：槽毕竟占了 M 行 K/V。**信息上**才是空的
            #     （写门初值关着 ⇒ 槽跨步不变 ⇒ 槽里没有本局的信息），这条由
            #     `MEM_GATE_BIAS` 保证、由守卫钉住（见 `tests/test_rl_memory.py`）。
            self.mem0 = nn.Parameter(torch.zeros(1, self.mem_slots, d_model))
            nn.init.normal_(self.mem0, std=0.02)
            # 组身份（槽 vs 观测 token）——让主干知道"这行是内部状态"。
            self.mem_emb = nn.Parameter(torch.zeros(1, 1, d_model))
            # ★ 写：**门控 cross-attention**（Q = 槽，K/V = **观测** token）。
            #   ★ K/V 只取观测段、**不取槽自己**：让槽互相写会引入"槽→槽"的
            #     二阶回路（谁先写谁就先被读），既难训又容易自激。
            self.mem_write = Attn(d_model, n_head)
            # ★★ 写头输出层：**小而非零**初始化（`V.MEM_WRITE_INIT`，标准差 1e-3）。
            #   ⇒ `upd ≠ 0`（小）⇒ 初值槽**带状态信息** ⇒ **读路径从第一步就有梯度**。
            #
            #   ★★★ 这里是 2026-09-26 改的，改的原因**是量出来的**（不是想当然）：
            #   原先零初始化（`upd ≡ 0` 逐位成立 ⇒「开记忆 = 马尔可夫基线」是**等式**）
            #   看着最干净，**但它把读路径掐死了**：
            #     · 初值槽不含**状态** ⇒ 读路径能学到的最多是"用 `mem0` 这个常数"；
            #     · 而写路径有**辅助损失**独立推着（它自己的梯度不受零初始化影响）
            #       ⇒ 写头照样学得动、"写得很欢"（实测 0.0078/步、局内漂移 21）；
            #     · 两条路**解耦** ⇒ 写出来的东西**没人读**。
            #   实测铁证（`rl/mem_activity.py`，mem 臂 iter 38/40 的档）：
            #     换掉整个槽的内容 ⇒ **策略 KL 4.4e-7**、8999 次比较里**动作变了 0 次**；
            #     而"把槽置零"价值变 0.066（那只是 `mem0` 当**常数偏置**被用）。
            #   ⇒ 结论：**"精确恒等"这个漂亮性质，代价是记忆永远接不进决策**。
            #     改小初始化之后，"= 基线"退回"≈ 基线"（扰动有界，见守卫），
            #     而对照并没有丢 —— `--memory none` 那条臂就是马尔可夫基线本身。
            nn.init.normal_(self.mem_write.out.weight, std=V.MEM_WRITE_INIT)
            nn.init.zeros_(self.mem_write.out.bias)
            #   ★ 顺带记下另一条**量出来**的教训：原先只把门偏置压到 -3
            #     （sigmoid≈0.047）以为"几乎不写"——实测槽每步仍变 4.1e-02，
            #     而 `mem0` 的标准差才 0.02 ⇒ 门乘 O(1) 的注意力输出**并不小**。
            #     ⇒ "靠门压死来控制早写"不成立；要控就得控**写头本身的尺度**。
            # ★ 门 = sigmoid(线性([槽的上下文, 写入候选]))。★ 用门而不是"直接覆盖"：
            #   覆盖会把槽变成**最近一帧的复读机**（容量全用在"当下"上，
            #   而"当下"本来就在观测里）。
            self.mem_gate = FastLinear(2 * d_model, 1)
            nn.init.zeros_(self.mem_gate.weight)
            nn.init.constant_(self.mem_gate.bias, float(mem_gate_bias))
            # ★ 辅助头：从**全部槽**预测"即将离开视野的那部分"（`rl/mem_aux.py`）。
            #   全展平（不是均值池化）—— M 个槽是 M 条独立记忆，池化会把它们搅在一起。
            self.mem_aux = nn.Sequential(FastLinear(self.mem_slots * d_model, 64),
                                         nn.ReLU(), FastLinear(64, V.M_AUX))

    # ------------------------------------------------------------------
    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())

    # ------------------------------------------------------------------
    def mem_init(self, batch_size: int, *, init: torch.Tensor | None = None,
                 device=None, dtype=None) -> torch.Tensor | None:
        """本局**开局**的槽状态 `[B,M,d]`（`mem_slots=0` ⇒ `None`）。

        ★ `init` = **外部给定的开局槽**（形状 `[B,M,d]` 或 `[M,d]`）——
          这就是潜槽这一侧的"**合法外部修改**"接口（用户 2026-09-25：「任何记忆
          都允许合法外部修改…这是配合情报的设计」）。
        ★★ 但要说实话：**它不该是情报的主入口**。槽是 `d_model` 维的**学出来的**
          向量，人/LLM **写不出来**（没有词汇表、也没有语义坐标）。
          ⇒ 真情报走**显式记忆**（厅 / 番号账本，见 `sandbox.tell_halls/tell_enemies`），
            潜槽**跟着学**。`init` 留着是给两件事用的：① 把某一局的槽**存档/复现**；
            ② 将来若要拿另一个模型（或一个编码器）把情报**编码**成槽，接口在这儿。
        """
        if not self.mem_slots:
            return None
        if init is not None:
            t = torch.as_tensor(init)
            if t.dim() == 2:
                t = t.unsqueeze(0)
            if t.shape[0] == 1 and int(batch_size) > 1:
                t = t.expand(int(batch_size), -1, -1)
            if int(t.shape[0]) != int(batch_size) or int(t.shape[1]) != self.mem_slots:
                raise ValueError(
                    f"外部槽的形状 {tuple(t.shape)} 不对（期望 [B={batch_size}, "
                    f"M={self.mem_slots}, d]）")
            return t.contiguous().to(dtype=t.dtype)
        p = self.mem0
        t = p.expand(int(batch_size), -1, -1)
        if device is not None or dtype is not None:
            t = t.to(device=device or p.device, dtype=dtype or p.dtype)
        return t.contiguous()

    def mem_aux_loss(self, mem: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        """辅助损失（MSE）—— 见 `rl/mem_aux.py`。★ 目标**只在训练时**有。"""
        pred = self.mem_aux(mem.flatten(1))
        return nn.functional.mse_loss(pred, target)

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
        """★ **不带记忆**的那条路（`mem_slots=0` 时它就是唯一的路）。"""
        logits, value, _ = self.forward_state(batch, None)
        return logits, value

    def forward_state(self, batch: dict, mem: torch.Tensor | None = None):
        """真正的实现：→ `(logits, value, mem_out)`。

        `mem` = **上一步**的槽 `[B,M,d]`（`None` ⇒ 用初值 `mem0`）；`mem_slots=0` ⇒
        `mem_out is None`。

        ★ 为什么保留 `forward` 那个两元组的老签名：`collect_episode` / `ppo_update` /
          `eval_fixed` 都在用它，而记忆是**可选**的 ⇒ 老路径**一个字节都不该动**
          （「不开 `--memory` 的行为逐字不变」是这条线的地基）。

        ★★ 代价（**这是"工具骗人"的形状，所以写在这里**）：`mem_slots>0` 时调
          `forward(batch)` 会用**初值槽** ⇒ *记忆等于没带*，而且**不报错**。
          它在两处是**正当**的：① 评估时的**对照臂**（"不带记忆"）；
          ② 守卫里钉"初值槽 = 马尔可夫基线"。**其余地方一律走 `forward_state`。**
        """
        grid = batch["grid"]
        win, wmask = batch["win"], batch["win_mask"]

        # ---- 窗口（K/V 的来源）----
        x, wm = self.encode_window(win, wmask)
        n_obs = int(x.shape[1])
        # ★★ 潜槽拼在**观测 token 之后**（不是之前）—— 这样 `g`/`a`/`k` 的下标
        #   一个都不动 ⇒ `army_idx` 的 gather、`x[:, 1:1+n_a]` 全部原样成立。
        #   槽**恒亮**（always unmasked）：它是定长的内部状态，没有 padding。
        s_in = None
        if self.mem_slots:
            b = x.shape[0]
            s_in = self.mem0.expand(b, -1, -1) if mem is None else mem
            # ★ 身份路径用 `s_in`（**不加** `mem_emb`）：加了会让槽每步累积组身份
            #   ⇒ 槽值**自己往上漂**（与"记了什么"无关）—— 那是个静默的漂移。
            x = torch.cat([x, s_in + self.mem_emb], dim=1)
            wm = torch.cat([wm, torch.ones(b, self.mem_slots, dtype=wm.dtype,
                                           device=wm.device)], dim=1)
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

        # ---- ★★ 写槽：门控 cross-attention（**先读完再写**）----
        #   Q = 槽（已经在 `blocks` 里**读过**观测了 ⇒ "读走现有注意力"）；
        #   K/V = **观测** token（**不含槽自己**：槽互相写会引入二阶回路）。
        #   门初值偏向"保持"（`MEM_GATE_BIAS`）⇒ 初版几乎不写 ⇒ 对照干净。
        mem_out = None
        if self.mem_slots:
            upd = self.mem_write(x[:, n_obs:], x[:, :n_obs], ~wm[:, :n_obs])
            gate = torch.sigmoid(self.mem_gate(
                torch.cat([x[:, n_obs:], upd], dim=-1)))
            mem_out = s_in + gate * upd

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
        # ★ 池化覆盖**观测 + 槽**：记忆是"局面有多少价值"的一部分（"我还有一支
        #   看不见的敌军在旁边"本来就该影响估值）。`mem_slots=0` 时与原来逐字相同。
        m = wm.unsqueeze(-1).to(x.dtype)
        pooled = (x * m).sum(1) / m.sum(1).clamp(min=1.0)
        return logits, self.value(pooled).squeeze(-1), mem_out


def build_model(n_grid_ch: int = V.GRID_CHANNELS, mem_slots: int = 0,
                **kw) -> PolicyNet:
    """按沙盒词表建网（`train.py` 用）。

    窗口各组的宽度**从词表与 `features` 现算**（不是写死的表）—— 将来加组只改
    `vocab.TOKEN_GROUPS` 与这里的一行，主干不动（§11「结构留位」）。
    """
    widths = {
        "g": V.GLOB_SIZE + F.F_GLOB,
        "a": V.A_WIDTH_RAW + F.F_U + V.A_EXTRA,
        # ★★ `"k"` = **记忆中的敌军**（按番号，见 `vocab.TOKEN_GROUPS` 的注释）。
        #   将来的 `"m"`（潜槽）加在这里一行即可 —— 主干不动（§11「结构留位」）。
        "k": V.K_WIDTH,
    }
    return PolicyNet(win_widths={g: widths[g] for g in V.TOKEN_GROUPS},
                     n_grid_ch=n_grid_ch, mem_slots=mem_slots, **kw)