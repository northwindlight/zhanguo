#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""★ **重炼固定基线档** —— 验收的对照臂就靠它。

    python scripts/rl_make_baseline.py --out rl/runs/baseline_init.pt --nets 3 --seed 20261024

★ 为什么要有这个脚本（2026-10-28）
  ──────────────────────────────
  基线档 = **固定种子的未训练网**（验收时当"未训练对照"用）。
  ★ 但它**必须重炼**：改了网络结构（形状指纹变了）⇒ 旧基线档**作废**
    （仓库铁律：「ckpt 会被新代码加载就必须重炼」—— 能 `load_state_dict` 成功、
    却喂错口径的通道，比形状不符危险得多）。
  ⇒ 所以**每次结构一动就要重跑一遍这个脚本**，别手搓。

★ 口径（**和旧基线档保持一致**，否则历史评测对不上）：
  · `nets` = `{槽号: state_dict}`，**每份网的权重用同一个种子**依次生成
    （⇒ 可复现）；
  · `meta.fingerprint` = `_shape_fingerprint(mem_slots)`（**形状指纹**）；
  · `meta.model` = `"baseline(untrained)"`（★ **不是** `V.MODEL_NAME`：
    基线**不是**任何型号，它是"没训练过的那份"）；
  · `meta.seed` = 生成用的种子；`meta.iters` = 0。
"""
from __future__ import annotations

import argparse
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rl.model import build_model                              # noqa: E402
from rl.train import _shape_fingerprint                       # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="重炼固定基线档")
    ap.add_argument("--out", default="rl/runs/baseline_init.pt")
    ap.add_argument("--nets", type=int, default=3, help="几份网（= 池子规模）")
    ap.add_argument("--seed", type=int, default=20261024)
    ap.add_argument("--mem-slots", type=int, default=0)
    a = ap.parse_args(argv)

    # ★★ **播种一次、连建 n 份**（不是每份换种子）—— 这与旧基线档的生成方式
    #   **逐位一致**（实测：同一份代码重跑 ⇒ 三份网逐位相同）⇒ 结构没动时，
    #   重炼出来的基线**就是原来那份**（历史评测对得上）。
    torch.manual_seed(a.seed)
    nets = {}
    for i in range(a.nets):
        net = build_model(mem_slots=a.mem_slots)
        net.eval()
        nets[i] = {k: v.detach().clone() for k, v in net.state_dict().items()}

    blob = {"nets": nets,
            "meta": {"fingerprint": _shape_fingerprint(a.mem_slots),
                     "iters": 0,
                     "model": "baseline(untrained)",
                     "seed": int(a.seed)}}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    torch.save(blob, a.out)
    print(f"★ 基线档 → {a.out}（{a.nets} 份网 · 种子 {a.seed} · mem_slots {a.mem_slots}）")
    print(f"★ 指纹：{blob['meta']['fingerprint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
