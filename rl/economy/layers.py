"""Adapted from feat/rl-tactics c650a82; inference caches are isolated on copy."""
import torch
from torch import nn

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
        # deepcopy copies the cache but resets the new Parameter's version.
        # The same version number can recur, so also bind to Parameter identity.
        self._wt = None          # (tensor, version, device, dtype, Parameter reference)

    def __getstate__(self):
        state=super().__getstate__().copy()
        state['_wt']=None
        return state

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if torch.is_grad_enabled():                 # ★★ 训练路径：原样（见上）
            return nn.functional.linear(x, self.weight, self.bias)
        w = self.weight
        c = self._wt
        if (c is None or c[4] is not w or c[1] != w._version or c[2] != w.device
                or c[3] != w.dtype):
            c = (w.detach().t().contiguous(), w._version, w.device, w.dtype,w)
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
