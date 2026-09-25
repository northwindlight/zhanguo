# -*- coding: utf-8 -*-
"""存档 / 续跑 / **定时重启** 的守卫（用户 2026-09-25：「算了，不如定时重启」）。

★ 为什么需要"定时重启"：训练进程的 RSS 会跨 iter 涨（实测 5 小时涨到 2.46G / 3.3G 可用）。
  与其去追那个增长，不如**跑够 N 个 iter 就换一个新进程**（RSS 归零）并从存档续跑。
  ⇒ 前提是两件事必须靠得住：**存档能读回来**、**读回来的形状对得上**。

★ 这里钉两条，各对着一个**会静默出错**的口径：

  1. **续跑真的把权重灌回去了** —— 若只读了 `meta` 没灌权重，训练会**从随机权重重新开始**
     而**不报错**（日志还显示"从第 N iter 续跑"）⇒ 一夜的活儿白干。
     ★ 带**反向对照**：扰动过的那份必须**明显不同**，否则"相等"是因为"全都一样"。
  2. ★★ **形状指纹对不上必须拒**（不是警告）—— 用户定的铁律：
     「**ckpt 会被新代码加载就必须重炼**」。权重能 `load_state_dict` 成功、却喂错口径的
     通道，是**看不出来**的（旧线 `feat/rl` 的教训）⇒ 只能用指纹挡。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import train                                    # noqa: E402
from rl.model import build_model                        # noqa: E402

QUIET = staticmethod(lambda *a, **k: None)


def _params(net):
    return list(net.parameters())


class TestSaveLoad(unittest.TestCase):
    def test_resume_actually_restores_the_weights(self):
        nets = {i: build_model() for i in range(3)}
        with torch.no_grad():                            # 扰动：让"灌回去"有可观测差别
            for p in _params(nets[1]):
                p.add_(0.01)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.pt")
            train._save_ckpt(path, nets, 7, meta={"fingerprint": train._shape_fingerprint()})
            fresh = {i: build_model() for i in range(3)}
            it0 = train._load_ckpt(path, fresh, log=QUIET)
            self.assertEqual(it0, 7, "续跑必须知道已经跑过几个 iter（否则 iter 号会重来）")
            for i in nets:
                for a, b in zip(_params(nets[i]), _params(fresh[i])):
                    self.assertTrue(torch.equal(a, b), f"第 {i} 份权重没灌回去")
            # ★ 反向对照：扰动过的那份必须与"没扰动的"明显不同
            self.assertFalse(
                torch.equal(_params(nets[0])[0], _params(nets[1])[0]),
                "两份网本应不同 ⇒ 上面那条「相等」是空的")

    def test_stale_fingerprint_is_refused(self):
        """★★ 旧代码训的档**必须被拒**（不是警告）—— 铁律：「会被新代码加载就必须重炼」。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stale.pt")
            fp = dict(train._shape_fingerprint())
            fp["glob_size"] = 999                        # 假装是旧代码（当年 28 列那版）
            train._save_ckpt(path, {0: build_model()}, 1, meta={"fingerprint": fp})
            with self.assertRaises(SystemExit):
                train._load_ckpt(path, {0: build_model()}, log=QUIET)

    def test_current_fingerprint_is_accepted(self):
        """★ 反向对照：**对得上**的档必须能读（别把闸门做成"谁都进不来"）。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ok.pt")
            train._save_ckpt(path, {0: build_model()}, 3,
                             meta={"fingerprint": train._shape_fingerprint()})
            self.assertEqual(train._load_ckpt(path, {0: build_model()}, log=QUIET), 3)

    def test_fingerprint_covers_the_shape_deciders(self):
        """指纹得覆盖**决定观测形状**的那几个常量（漏一个就是个后门）。"""
        fp = train._shape_fingerprint()
        for k in ("grid_channels", "glob_size", "army_width", "cand_marks",
                  "cand_content", "glob_content", "grid_cb_bins"):
            self.assertIn(k, fp, f"指纹里少了 {k} —— 它变了旧 ckpt 就该作废")


class TestCkptIsAtomic(unittest.TestCase):
    def test_no_partial_file_left_behind(self):
        """★ 原子写：只留最终文件，不留 `.tmp`（别让"写了一半"被拉走）。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pt")
            train._save_ckpt(path, {0: build_model()}, 1, meta={})
            self.assertTrue(os.path.exists(path))
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [],
                             "留下了 .tmp ⇒ 原子写没做干净")


if __name__ == "__main__":
    unittest.main()