# -*- coding: utf-8 -*-
"""**潜槽到底有没有活动** —— 直接读数（用户 2026-09-26：「里面到底有没有活动」）。

    python -m rl.mem_activity --ckpt rl/runs/par_mem/mem01.pt

★ 为什么要有它（`rl/mem_gate.py` 的两道闸不够）：
  闸 A 是**行为**判据（置零/打乱槽 ⇒ 分数掉不掉）、闸 B 是**表征**判据（线性解码）。
  两个都可能因为**别的理由**失败：槽没动但主干把"槽的平均值"当常数偏置用
  （闸 A 会显示"打乱比置零更伤"，那是假记忆）；或者局里压根没出现"看见又失去"
  ⇒ 闸 B 的标签恒 0、R² 平凡为 1。⇒ 先把**最前面那一层**读出来：
  **写门开了没有、槽动了没有、主干读没读它。**

四个量（全部可观测，不改模型 —— 都来自 `forward_state` 返回的槽）：

  ① **写入幅度** `|mem_out − mem_in|`，相对槽自身量级 —— ≈0 ⇒ 那一帧**什么都没写**。
     （`mem_out = mem_in + gate·upd`；初始化时写头 output 层零初始化 ⇒ 恒等是**等式**。）
  ② **局内漂移** `|mem_末 − mem_初|` —— 一路没动 ⇒ 槽只是个**常数**，不是状态。
  ③ **跨状态方差** —— 不同局面若给同一个槽值 ⇒ 没编码任何状态。
  ④ ★★ **功能检验**：同一局面把槽**置零**，策略/价值变不变 ——
     KL(策略) ≈ 0 且 |ΔV| ≈ 0 ⇒ 主干**压根没读它**（这条最硬：它直接问"有没有用"）。

★ 一律**固定种子**（闸门纪律）：要比的是"同一局面下槽不同会怎样"。
★ 没训过的档也要跑一遍当**对照**：期望 ①≈0（零初始化 ⇒ 恒等），
  而"训过的档"①如果还是 ≈0 ⇒ 记忆**从头到尾没启用**（那就得去查梯度/门/口径）。
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import torch

from . import encode as E
from . import train as T
from . import vocab as V
from .model import build_model
from .sandbox import Sandbox


def probe(net, seeds, *, size: int = 12, t_max: int = 200, n_nations: int = 3,
          halls_known: bool = True, max_steps: int = 3000, sample: bool = True):
    """跑若干固定种子的局，逐步记录四个量。返回汇总。"""
    write, kl, dv, slot_abs = [], [], [], []
    kl_swap, dv_swap = [], []
    kl_raw, act_chg, n_cmp = [], [], []
    init_scale = float(net.mem_init(1).detach().abs().mean()) if net.mem_slots else 0.0
    drift, spread, xplayer = [], [], []
    slot_pool: list = []              # 全局槽池：换槽检验要从这里抽"远的"槽
    for i, sd in enumerate(seeds):
        sb = Sandbox(seed=sd, size=size, n_nations=n_nations, t_max=t_max,
                     halls_known=halls_known).reset()
        mem: dict = {}
        per_player: dict = {}
        rng = np.random.default_rng(1000 + i)
        n = 0
        while not sb.is_terminal() and n < max_steps:
            n += 1
            me = sb.current_player()
            if me is None:
                break
            acts = sb.legal()
            if not acts:
                break
            batch = T.collate([E.obs_of(sb, me, acts)])
            m = mem.get(me)
            m_in = net.mem_init(1) if m is None else m
            hist = per_player.setdefault(me, [])
            with torch.no_grad():
                logits_on, v_on, m_out = net.forward_state(batch, m_in)
                # ★ 功能检验 a：同一局面、同一权重，把槽**置零**
                logits_off, v_off, _ = net.forward_state(
                    batch, torch.zeros_like(m_in))
                # ★★ 功能检验 b（**更干净**）：把槽换成**别的局面**的槽 ——
                #   置零混进了"mem0 常数"的影响；换一个**真实但无关**的槽才问得准：
                #   "主干对槽的**内容**敏不敏感"。不敏感 ⇒ 它压根没读记忆。
                logits_sw, v_sw = None, None
                # ★★ 必须从**历史池**里抽一个**远**的槽（跨局/跨玩家）——
                #   我第一版用"上一帧的槽"，而每步只变 ~0.2（槽量级 ~7.8）
                #   ⇒ 两个槽几乎一样 ⇒ KL≈0 是**必然**的，什么都没说明。
                if slot_pool:
                    pick = slot_pool[int(rng.integers(len(slot_pool)))]
                    logits_sw, v_sw, _ = net.forward_state(batch, pick)
            write.append(float((m_out - m_in).abs().mean()))
            slot_abs.append(float(m_in.abs().mean()))
            p_on = torch.softmax(logits_on[0], -1)

            def _kl(p_other):
                p_other = torch.softmax(p_other[0], -1)
                return float((p_on * (torch.log(p_on + 1e-12)
                                      - torch.log(p_other + 1e-12))).sum())

            kl.append(_kl(logits_off))
            dv.append(float((v_on - v_off).abs().mean()))
            if logits_sw is not None:
                kl_swap.append(_kl(logits_sw))
                dv_swap.append(float((v_on - v_sw).abs().mean()))
                # ★★ 两个**更硬**的口径（KL 印成 .6f 会把 1e-8 变成 0 ⇒ 会误判）：
                #   · 原始 KL 的**最大值**（不取平均）
                #   · ★ **动作变了的比例** —— 换槽之后 argmax 变了没有？
                #     "策略分布差 1e-6"和"选的动作变了 30%"是完全不同的两件事。
                with torch.no_grad():
                    p_sw = torch.softmax(logits_sw[0], -1)
                    p_on = torch.softmax(logits_on[0], -1)
                    kl_raw.append(float((p_on * (torch.log(p_on + 1e-12)
                                                 - torch.log(p_sw + 1e-12))).sum()))
                    act_chg.append(float(int(torch.argmax(p_on) != torch.argmax(p_sw))))
                    n_cmp.append(1)
            hist.append(m_in.clone())
            slot_pool.append(m_in.clone())          # ★ 全局历史池（跨局、跨玩家）
            mem[me] = m_out
        # ★ 逐玩家算：② 局内漂移（第一次的槽 vs 最后一次的槽）、
        #   ③ 局内**跨状态**标准差（同一个玩家的槽随时间变多少 ⇒ 有没有编码状态）
        for me, hs in per_player.items():
            if len(hs) < 2:
                continue
            drift.append(float((hs[-1] - hs[0]).abs().mean()))
            S = np.stack([h[0].detach().numpy() for h in hs])
            spread.append(float(S.std(axis=0).mean()))
        # ④ 同一时刻**跨玩家**的差（槽里有没有"我是谁"这种信息）
        if mem:
            ms = list(mem.values())
            if len(ms) > 1:
                xplayer.append(float(max((m - ms[0]).abs().mean() for m in ms)))
    return {
        "初始槽量级": init_scale,
        "写入幅度_均值": float(np.mean(write)) if write else 0.0,
        "槽自身量级_均值": float(np.mean(slot_abs)) if slot_abs else 0.0,
        "写入/槽": (float(np.mean(write) / max(np.mean(slot_abs), 1e-12))
                    if write else 0.0),
        "局内漂移": float(np.mean(drift)) if drift else 0.0,
        "跨状态标准差": float(np.mean(spread)) if spread else 0.0,
        "跨玩家差": float(np.mean(xplayer)) if xplayer else 0.0,
        "策略KL_置零": float(np.mean(kl)) if kl else 0.0,
        "价值差_置零": float(np.mean(dv)) if dv else 0.0,
        "策略KL_换槽": float(np.mean(kl_swap)) if kl_swap else 0.0,
        "换槽KL_最大": float(np.max(kl_raw)) if kl_raw else 0.0,
        "换槽动作变了": (float(np.mean(act_chg)) if act_chg else 0.0),
        "换槽比较次数": len(act_chg),
        "价值差_换槽": float(np.mean(dv_swap)) if dv_swap else 0.0,
        "步数": len(write),
    }


def report(name: str, r: dict) -> None:
    print(f"── {name} ──")
    print(f"  初始槽量级（mem0 std）      {r['初始槽量级']:.4f}")
    print(f"  ① 写入幅度 |mem_out−mem_in|  {r['写入幅度_均值']:.6f}"
          f"   （占槽自身量级 {r['写入/槽']:.1%}）")
    print(f"  ② 局内漂移 |末−初|           {r['局内漂移']:.6f}")
    print(f"  ②b 跨状态标准差（同玩家随时间）{r['跨状态标准差']:.4f}"
          f"   跨玩家差 {r['跨玩家差']:.4f}")
    print(f"  ④a 置零后 KL(策略) {r['策略KL_置零']:.6f}  价值差 {r['价值差_置零']:.6f}")
    print(f"  ④b **换槽**后 KL(策略) {r['策略KL_换槽']:.3e}（最大 {r['换槽KL_最大']:.3e}）"
          f"  价值差 {r['价值差_换槽']:.6f}")
    print(f"      ★ **换槽后动作变了的比例 {r['换槽动作变了']:.1%}**"
          f"（{r['换槽比较次数']} 次比较）—— 这是「记忆有没有影响决策」的直接读数")
    # ★★★ 2026-09-27 加的警示（我差点被它骗过）：
    #   实测**同一份代码、同一个 ④b、五个不同随机种子的未训练网** ⇒
    #     37.5% / 25.8% / 6.5% / 5.3% / **0.0%**（跨度 37.5 个百分点！）
    #   而且方向是**反的**：KL 最大的那个给 0.0%、KL 最小的给 6.5%。
    #   ⇒ 在**策略近均匀**（`e/K ≈ 1`）的区间里，这个比例测的是
    #     「并列怎么被微小差值打破」，**不是**「记忆有没有接上」。
    #   ⇒ **它的噪声底就有 0~37.5%** ⇒ 拿「>5%」当判据 = 拿运气当证据。
    #   ★ 正确用法：**拿未训练的档当零假设基线**（不加 `--ckpt` 跑一遍），
    #     要求"训过的档 ≫ 基线"，而不是跟一个拍出来的 5% 比。
    if abs(r["策略KL_换槽"]) < 1e-4:
        print(f"      ⚠ 但 `换槽 KL = {r['策略KL_换槽']:.1e}` 极小 ⇒ 分布几乎没变；"
              f"此时这个比例**不可信**")
        print(f"         （未训练网的噪声底实测可达 **37.5%** —— 务必跑一次 "
              f"`--ckpt` 不给的对照，比它高才算数）")
    print(f"  （{r['步数']} 步采样）")
    w, k, d = r["写入/槽"], r["策略KL_换槽"], r["价值差_换槽"]
    ac = r["换槽动作变了"]
    print(f"  ⇒ 读数：写入/槽 {w:.2%}，换槽后动作变化 {ac:.1%} —— "
          + ("**有活动且影响决策**" if ac > 0.05 else
             "**在写、但几乎不影响决策**（记忆没被用起来）"))


def main() -> None:
    ap = argparse.ArgumentParser(description="潜槽活跃度直接读数")
    ap.add_argument("--ckpt", default=None, help="不给 ⇒ 未训练的网（对照）")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--seed0", type=int, default=1000)
    ap.add_argument("--size", type=int, default=12)
    ap.add_argument("--t-max", dest="t_max", type=int, default=200)
    ap.add_argument("--nations", type=int, default=None)
    ap.add_argument("--slot", type=int, default=3, help="读第几份网的槽（0 起）")
    ap.add_argument("--max-steps", dest="max_steps", type=int, default=3000)
    a = ap.parse_args()
    torch.set_num_threads(1)

    if a.ckpt:
        blob = torch.load(a.ckpt, map_location="cpu", weights_only=False)
        fp = (blob.get("meta") or {}).get("fingerprint") or {}
        mem_slots = int(fp.get("mem_slots", 0))
        if mem_slots <= 0:
            raise SystemExit(f"★ {a.ckpt} 不是记忆档（mem_slots={mem_slots}）")
        net = build_model(mem_slots=mem_slots)
        net.load_state_dict(blob["nets"][a.slot])
        name = f"{a.ckpt}（第 {a.slot} 份，iter={blob['meta'].get('iters')}）"
    else:
        net = build_model(mem_slots=V.M_SLOTS)
        name = "**未训练**（对照）"
    net.eval()
    seeds = list(range(a.seed0, a.seed0 + a.seeds))
    r = probe(net, seeds, size=a.size, t_max=a.t_max,
              n_nations=a.nations or 3, max_steps=a.max_steps)
    report(name, r)


if __name__ == "__main__":
    main()