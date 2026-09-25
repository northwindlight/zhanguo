# -*- coding: utf-8 -*-
"""联赛权重池的守卫（用户 2026-09-25：「现在做联赛池」）。

用户原话逐条 → 每条一个守卫：

    「联赛池从**最新快照**开始分化」  → `--league-from` 灌进**每一份**（不是各随机）
    「**只增不删**」                  → 淘汰只置 `active=False`，成员**仍在账上**
    「**随机抽 pt**」                 → 抽 k 份**互不重复**、且只抽 active 的
    「标记每个 pt 的胜率，**永久化到 json**」→ 存读一轮必须原样回来（★ 带反向对照）
    「可以有两个**固定主 pt**」        → `main` **不受淘汰规则约束**
    「打 10 局以上胜率低于 20% 的**不再启用**」→ 三条边界：局数不够/恰好在线上/真低于

★ 每个守卫都对着一个**会静默出错**的形状：
  - 账本没落盘 ⇒ 定时重启每 5 iter 换进程 ⇒ **战绩永远攒不到 10 局** ⇒
    淘汰规则变成**死代码**，而日志上看不出任何异常（这正是"永久化"要防的）。
  - 淘汰做成"删除成员" ⇒ 池子越跑越小，最后**凑不齐一局**才炸（那时已白跑一夜）。
  - 抽签允许重复 ⇒ 同一份在一局里扮两个国家（自己打自己），梯度混在一起。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.league import League                     # noqa: E402
from rl.model import build_model                 # noqa: E402

QUIET = staticmethod(lambda *a, **k: None)
FP = {"glob_size": 21, "grid_channels": 27}      # 假的形状指纹（不建真网也够用）


def _net():
    return build_model()


def _mem(**kw):
    """不落盘的池子（`db=None`）—— 战绩不过夜，只用来测规则。"""
    kw.setdefault("mains", 0)
    kw.setdefault("max_k", 1)
    kw.setdefault("min_learners", 1)
    return League(None, fingerprint=FP, log=QUIET, **kw)


def _live(lg, n, net=None):
    net = net or _net()
    mids = []
    for i in range(n):
        mid = f"L{i}"
        lg.bind_live(mid, net)
        mids.append(mid)
    return mids


def _stat(lg, mid, games, wins):
    lg.members[mid].games = games
    lg.members[mid].wins = wins


# ================================================================ 淘汰规则
class TestRetire(unittest.TestCase):
    """「打 10 局以上胜率低于 20% 的不再启用」—— 三条边界都要钉。"""

    def test_retires_below_threshold_after_min_games(self):
        lg = _mem(retire_min_games=10, retire_rate=0.20)
        _live(lg, 3)
        _stat(lg, "L0", 10, 1)          # 10% < 20%  ⇒ 停用
        _stat(lg, "L1", 10, 2)          # 20% (恰在线上，**不是**低于) ⇒ 留
        _stat(lg, "L2", 9, 0)           # 局数不够 ⇒ 留（哪怕一支没赢）
        killed = lg.retire()
        self.assertEqual(killed, ["L0"])
        self.assertFalse(lg.members["L0"].active)
        self.assertTrue(lg.members["L1"].active, "20% 恰好在线上，不该被淘汰（阈值是「低于」）")
        self.assertTrue(lg.members["L2"].active, "只打了 9 局，还没资格谈胜率")

    def test_main_pt_is_exempt(self):
        """★ 「可以有两个固定主 pt」—— 它们是**基座本身**，淘汰掉就没得炼了。"""
        lg = _mem(mains=2, retire_min_games=10, retire_rate=0.20)
        _live(lg, 4)
        self.assertEqual(lg.members["L0"].kind, "main")
        self.assertEqual(lg.members["L1"].kind, "main")
        self.assertEqual(lg.members["L2"].kind, "live")
        for m in ("L0", "L1", "L2", "L3"):
            _stat(lg, m, 20, 0)          # 全 0% —— 除主 pt 外都该走
        killed = lg.retire()
        self.assertNotIn("L0", killed)
        self.assertNotIn("L1", killed)
        self.assertTrue(lg.members["L0"].active, "主 pt 不受淘汰约束")
        self.assertIn("L3", killed)

    def test_never_goes_below_max_k(self):
        """★ 兜底①：active 不得少于**当前最大国家数** —— 否则下一局凑不齐 k 份直接崩。"""
        lg = _mem(retire_min_games=1, retire_rate=0.99, max_k=3)
        _live(lg, 5)
        for m in lg.members:
            _stat(lg, m, 5, 0)
        lg.retire()
        self.assertEqual(len(lg.active()), 3, "该停在 max_k 上，不能继续淘汰")
        lg.draw(3, __import__("numpy").random.default_rng(0))     # 凑得齐，不抛

    def test_never_kills_the_last_learner(self):
        """★ 兜底②：在训成员至少留 `min_learners` 份 —— 全停用 = 炉子没得炼。"""
        lg = _mem(retire_min_games=1, retire_rate=0.99, max_k=1, min_learners=2)
        _live(lg, 3)
        for m in lg.members:
            _stat(lg, m, 5, 0)
        lg.retire()
        self.assertEqual(len([m for m in lg._live() if m.active]), 2,
                         "必须在 min_learners 上停住，不能把在训的全淘汰")

    def test_only_add_never_delete(self):
        """★★ 「只增不删」—— 淘汰是 `active=False`，成员**必须还在**。"""
        lg = _mem(retire_min_games=1, retire_rate=0.99)
        _live(lg, 3)
        _stat(lg, "L2", 5, 0)
        lg.retire()
        self.assertIn("L2", lg.members, "被停用的成员**不许从池子里消失**")
        self.assertFalse(lg.members["L2"].active)
        self.assertNotIn("L2", lg.active(), "但它不该再被抽上场")


# ================================================================ 抽签
class TestDraw(unittest.TestCase):
    def test_draw_is_distinct_and_active_only(self):
        """★ 「随机抽 pt」：k 份**互不重复**，且抽出来的**一定 active**。"""
        import numpy as np
        lg = _mem(retire_min_games=1, retire_rate=0.99, max_k=1)
        _live(lg, 6)
        lg.members["L5"].active = False                # 手工停用一份
        rng = np.random.default_rng(7)
        seen_retired = 0
        for _ in range(200):
            d = lg.draw(4, rng)
            self.assertEqual(len(d), len(set(d)), "抽重了 ⇒ 同一份在一局里扮两个国家")
            seen_retired += sum(1 for m in d if m == "L5")
        self.assertEqual(seen_retired, 0, "停用的成员**不许**再上场")

    def test_draw_raises_when_pool_too_small(self):
        """凑不齐就得**当场炸**，别静默出一局人数不够的（那种局的数据是脏的）。"""
        import numpy as np
        lg = _mem()
        _live(lg, 2)
        with self.assertRaises(RuntimeError):
            lg.draw(3, np.random.default_rng(0))


# ================================================================ 快照
class TestSnapshot(unittest.TestCase):
    def test_snapshot_is_a_frozen_copy_not_the_live_net(self):
        """★ 快照**只当对手**：不是那个还在训的对象，且 `requires_grad=False`。"""
        net = _net()
        lg = _mem()
        lg.bind_live("L0", net)
        m = lg.add_snapshot(net, 3, mid="S3")
        snap = lg.net_of("S3")
        self.assertIsNot(snap, net, "快照必须是一份**拷贝**，不是那个在训的对象")
        self.assertIs(lg.net_of("L0"), net, "在训成员取的必须是**那份活的**（不能被复制）")
        for p in snap.parameters():
            self.assertFalse(p.requires_grad, "快照不许进梯度")
        # ★ 之后改活网，快照**不许跟着变**（否则快照就不是"那一代的自己"了）
        before = [p.detach().clone() for p in snap.parameters()]
        with torch.no_grad():
            for p in net.parameters():
                p.add_(1.0)
        for a, b in zip(before, snap.parameters()):
            self.assertTrue(torch.equal(a, b), "活网改了，快照跟着变了 ⇒ 它没被冻住")

    def test_snapshot_is_idempotent(self):
        """同一 mid 重复冻 ⇒ 直接返回旧的（只增不删里"增"的那一半不该重复增）。"""
        net = _net()
        lg = _mem()
        a = lg.add_snapshot(net, 3, mid="S3")
        b = lg.add_snapshot(net, 4, mid="S3")
        self.assertIs(a, b)
        self.assertEqual(len(lg.members), 1)


# ================================================================ 永久化
class TestPersistence(unittest.TestCase):
    """「标记每个 pt 的胜率，**永久化到一个 json 文件**」——跨进程重启必须活着。"""

    def test_roundtrip_restores_the_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.json")
            lg = League(db, mains=2, retire_min_games=10, retire_rate=0.20,
                        max_k=1, fingerprint=FP, log=QUIET)
            _live(lg, 3)
            _stat(lg, "L0", 12, 5)          # 主 pt
            _stat(lg, "L2", 11, 3)          # 27% ⇒ 留
            lg.members["L2"].active = False  # 手工停用一份，看它回不回来
            lg.updated_iter = 42
            lg.save()
            self.assertTrue(os.path.exists(db), "账本没落盘 ⇒ 定时重启会把它清零")

            lg2 = League(db, mains=2, retire_min_games=10, retire_rate=0.20,
                         max_k=1, fingerprint=FP, log=QUIET)
            self.assertTrue(lg2.load(), "账本在，必须读得回来")
            self.assertEqual(lg2.updated_iter, 42)
            m = lg2.members["L2"]
            self.assertEqual((m.games, m.wins), (11, 3), "战绩没读回来")
            self.assertFalse(m.active, "停用状态没读回来")
            self.assertEqual(lg2.members["L0"].kind, "main", "主 pt 的身份漂了")
            # ★ 反向对照：扰动过的那份必须与它不同（否则上面的"相等"是空的）
            self.assertNotEqual(lg2.members["L0"].wins, m.wins)

    def test_bind_live_keeps_the_loaded_kind(self):
        """★★ 重启后重新 `bind_live` **不许**把主 pt 的身份重算漂掉。"""
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.json")
            lg = League(db, mains=2, max_k=1, fingerprint=FP, log=QUIET)
            _live(lg, 3)
            lg.save()
            lg2 = League(db, mains=0, max_k=1, fingerprint=FP, log=QUIET)   # ★ 故意换个 mains
            lg2.load()
            net = _net()
            for i in range(3):
                lg2.bind_live(f"L{i}", net)
            self.assertEqual(lg2.members["L0"].kind, "main",
                             "重启后 mains 变了也不许改已入册的主 pt 身份")

    def test_stale_fingerprint_is_refused(self):
        """★ 账本里的快照是**旧代码**训的 ⇒ 整池作废（不是警告）。"""
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.json")
            blob = {"version": 1, "fingerprint": {"glob_size": 999}, "members": []}
            Path(db).write_text(json.dumps(blob), encoding="utf-8")
            lg = League(db, max_k=1, fingerprint=FP, log=QUIET)
            with self.assertRaises(SystemExit):
                lg.load()

    def test_write_is_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.json")
            lg = League(db, max_k=1, fingerprint=FP, log=QUIET)
            _live(lg, 1)
            lg.save()
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [],
                             "留下了 .tmp ⇒ 原子写没做干净")


if __name__ == "__main__":
    unittest.main()