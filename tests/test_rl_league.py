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

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import train as train_mod                # noqa: E402
from rl.league import League                     # noqa: E402
from rl.model import build_model                 # noqa: E402
from rl.sandbox import Sandbox                   # noqa: E402
from rl.train import collect_episode             # noqa: E402

QUIET = staticmethod(lambda *a, **k: None)
FP = {"glob_size": 21, "grid_channels": 27}      # 假的形状指纹（不建真网也够用）


def _net():
    return build_model()


_OPEN: list = []          # 本模块开过的池子，`tearDownModule` 里统一关掉


def tearDownModule():
    """★ 把连接关干净 —— 24 条 `ResourceWarning` 会把**真问题**淹在噪声里。"""
    for lg in _OPEN:
        lg.close()
    _OPEN.clear()


def _mem(**kw):
    """不落盘的池子（`db=None` ⇒ `:memory:`）—— 战绩不过夜，只用来测规则。"""
    kw.setdefault("mains", 0)
    kw.setdefault("max_k", 1)
    kw.setdefault("min_learners", 1)
    lg = League(None, fingerprint=FP, log=QUIET, **kw)
    _OPEN.append(lg)
    return lg


def _live(lg, n, net=None):
    net = net or _net()
    mids = []
    for i in range(n):
        mid = f"L{i}"
        lg.bind_live(mid, net)
        mids.append(mid)
    return mids


def _mk(case, db, **kw):
    """建一个**落盘**的池子，并登记 `addCleanup` 关连接（见 `tearDownModule`）。

    ★ 兜底默认值和 `_mem` **必须一致** —— 不一致的话，同一个用例换个存储
      就悄悄换了口径（`max_k` 缺省 5 会让"兜底①"永远先触发，淘汰一条都不发生，
      而**测试看起来还在测淘汰**）。
    """
    kw.setdefault("fingerprint", FP)
    kw.setdefault("mains", 0)
    kw.setdefault("max_k", 1)
    kw.setdefault("min_learners", 1)
    lg = League(db, log=QUIET, **kw)
    case.addCleanup(lg.close)
    return lg


def _stat(lg, mid, games, wins):
    """★ 必须写进**库**（不是只改内存缓存）：判据（淘汰）前会 `refresh()`，
    只改内存的话会被库里那行**静默盖掉** ⇒ 用例变成"什么都没测"。"""
    lg.conn.execute("UPDATE members SET games=?, wins=? WHERE mid=?",
                    (games, wins, mid))
    lg.conn.commit()
    m = lg.members[mid]
    m.games, m.wins = games, wins


def _deactivate(lg, mid):
    """同上：停用状态也必须落库（`draw` 才真的抽不到它）。"""
    lg.conn.execute("UPDATE members SET active=0 WHERE mid=?", (mid,))
    lg.conn.commit()
    lg.members[mid].active = False


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
        """★★ 「只增不删」—— 淘汰是 `active=0`，成员**必须还在**。

        ★★ **必须在库里查**，不能只看内存缓存：把淘汰改成真 `DELETE` 时，
          只在内存里断言的版本**照样是绿的**（实测踩到 —— 那是一条假绿）。
          ⇒ 这里从**库**里查，并且**另开一个池子**读同一个库复核。
        """
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.db")
            lg = _mk(self, db, retire_min_games=1, retire_rate=0.99)
            _live(lg, 3)
            _stat(lg, "L2", 5, 0)
            lg.retire()
            row = lg.conn.execute("SELECT kind,active FROM members WHERE mid=?",
                                  ("L2",)).fetchone()
            self.assertIsNotNone(row, "被停用的成员**不许从库里消失**（那是「删」不是「停用」）")
            self.assertEqual(row[1], 0, "库里没标成停用")
            self.assertNotIn("L2", lg.active(), "停用后不该再被抽上场")
            # ★ 另一个池子读同一个库，结论必须一样
            other = _mk(self, db, retire_min_games=1, retire_rate=0.99)
            other.load()
            self.assertIn("L2", other.members, "别的进程读不到这条被停用的成员")
            self.assertFalse(other.members["L2"].active)

    def test_cache_does_not_lie_when_a_row_disappears(self):
        """★ 库里的行没了 ⇒ 缓存里也不许留着（否则缓存**撒谎**且不报错）。"""
        lg = _mem()
        _live(lg, 2)
        lg.conn.execute("DELETE FROM members WHERE mid=?", ("L1",))
        lg.refresh()
        self.assertNotIn("L1", lg.members, "库里那行没了，缓存还留着它")
        self.assertIn("L0", lg.members, "★ 反向对照：没被删的那份不该跟着没")


# ================================================================ 抽签
class TestDraw(unittest.TestCase):
    def test_draw_is_distinct_and_active_only(self):
        """★ 「随机抽 pt」：k 份**互不重复**，且抽出来的**一定 active**。"""
        import numpy as np
        lg = _mem(retire_min_games=1, retire_rate=0.99, max_k=1)
        _live(lg, 6)
        _deactivate(lg, "L5")                          # 手工停用一份
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
    """「标记每个 pt 的胜率，**永久化到数据库**」——跨进程重启必须活着。

    ★ 存储是 **SQLite**（不是 json），用户给的理由是**为了以后并行**：
      「池子够大并行抽，起多个独立进程筛」。
    """

    def test_roundtrip_restores_the_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.db")
            lg = _mk(self, db, mains=2, retire_min_games=10, retire_rate=0.20,
                            max_k=1)
            _live(lg, 3)
            _stat(lg, "L0", 12, 5)          # 主 pt
            _stat(lg, "L2", 11, 3)          # 27% ⇒ 留
            _deactivate(lg, "L2")           # 手工停用一份，看它回不回来
            lg.updated_iter = 42
            lg.save()
            self.assertTrue(os.path.exists(db), "库没落盘 ⇒ 定时重启会把它清零")

            lg2 = _mk(self, db, mains=2, retire_min_games=10, retire_rate=0.20,
                             max_k=1)
            self.assertTrue(lg2.load(), "库在，必须读得回来")
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
            db = os.path.join(d, "league.db")
            lg = _mk(self, db, mains=2, max_k=1)
            _live(lg, 3)
            lg.save()
            lg2 = _mk(self, db, mains=0, max_k=1)      # ★ 故意换个 mains
            lg2.load()
            net = _net()
            for i in range(3):
                lg2.bind_live(f"L{i}", net)
            self.assertEqual(lg2.members["L0"].kind, "main",
                             "重启后 mains 变了也不许改已入册的主 pt 身份")

    def test_stale_fingerprint_is_refused(self):
        """★ 库里的快照是**旧代码**训的 ⇒ 整池作废（不是警告）。"""
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.db")
            _mk(self, db, max_k=1).save()
            other = dict(FP, glob_size=999)          # 假装是旧代码那版
            with self.assertRaises(SystemExit):
                _mk(self, db, max_k=1, fingerprint=other).load()


class TestParallelReady(unittest.TestCase):
    """★★ 库是**为并行准备的**（用户：「池子够大并行抽，起多个独立进程筛」）。

    钉两件事，都是并行下**会静默出错**的形状：
      1. 别人进程记的战绩，我这边 `refresh()` 之后**必须看得见**
         （看不见 ⇒ 拿**过时的胜率**去淘汰/抽签，而不报错）。
      2. 记账必须**原子自增**。若写成"读出来加一再写回"，两个进程同时记同一份
         ⇒ 后写的把先写的**抹掉**，而两边都以为记上了。
    """

    def test_two_instances_see_each_others_records(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.db")
            a = _mk(self, db, mains=0, max_k=1)
            _live(a, 2)
            b = _mk(self, db, mains=0, max_k=1)
            b.load()
            for _ in range(3):
                a.record("L0", True)
            b.refresh()
            self.assertEqual(b.members["L0"].games, 3, "别人记的战绩看不见")
            for _ in range(2):
                b.record("L0", False)
            a.refresh()
            self.assertEqual((a.members["L0"].games, a.members["L0"].wins), (5, 3),
                             "两边交替记，总数必须对得上（自增丢了 ⇒ 有进程白干）")

    def test_concurrent_writers_do_not_lose_games(self):
        """★ 真·并发：两个**独立连接**同时往同一份上各记 N 局，最终必须是 2N。

        ★ 每个线程**自己建连接**（sqlite3 缺省不允许跨线程复用连接 —— 而跨线程
          复用会抛 `ProgrammingError`，那个异常**被线程吞掉**、主线程毫不知情
          ⇒ 测试假绿。所以下面连**线程里的异常**一起断言）。
          用两个独立连接 + 两个线程 ≈ "两个独立进程并行抽、并行记账"的形状。
        """
        import threading
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.db")
            setup = League(db, mains=0, max_k=1, fingerprint=FP, log=QUIET)
            self.addCleanup(setup.close)
            _live(setup, 2)
            N = 60
            errs: list[str] = []

            def hammer(won):
                try:
                    lg = League(db, mains=0, max_k=1, fingerprint=FP, log=QUIET)
                    lg.load()
                    for _ in range(N):
                        lg.record("L0", won)
                    lg.close()
                except Exception as e:            # ★ 别让线程把异常吞了
                    errs.append(repr(e))

            t1 = threading.Thread(target=hammer, args=(True,))
            t2 = threading.Thread(target=hammer, args=(False,))
            t1.start(); t2.start(); t1.join(); t2.join()
            self.assertEqual(errs, [], "并发里抛异常了 —— 那正是并行会丢数据的地方")
            setup.refresh()
            self.assertEqual(setup.members["L0"].games, 2 * N,
                             "并发记账丢了局数 ⇒ 用的是『读-加-写回』而不是原子自增")
            self.assertEqual(setup.members["L0"].wins, N)

    def test_results_table_keeps_the_per_game_trail(self):
        """★ 逐局流水（不是只留聚合值）—— 将来"筛"的时候要按窗口重算。"""
        lg = _mem(worker="w1")
        _live(lg, 1)
        lg.record("L0", True, it=7)
        lg.record("L0", False, it=7)
        rows = lg.conn.execute(
            "SELECT mid,won,iter,worker FROM results ORDER BY id").fetchall()
        self.assertEqual(rows, [("L0", 1, 7, "w1"), ("L0", 0, 7, "w1")])


class TestDrawIsNotALoss(unittest.TestCase):
    """★★ **平局不进战绩** —— 胜率的分母是「有胜负的局」。

    不排除平局的话，"没赢"被记成"输了" ⇒ 一池子平局把**所有人**的胜率压到 0
    ⇒ 淘汰规则把池子清空，而日志上看只是"大家都在输"。**静默**那一类。

    ★ 用 `--t-max 1` 造平局：第 1 回合就到上限、场上还有 3 个实体 ⇒ 无胜方。
      并且**先证明那局真的打完了**（否则这条用例会因为"被截断"而假绿 —— 截断同样不记战绩）。
    """

    def test_draw_is_finished_but_not_recorded(self):
        # ① 前提：t_max=1 那一局是**打完的平局**，不是截断
        sb = Sandbox(seed=1, size=8, n_nations=3, t_max=1, halls_known=True).reset()
        torch.manual_seed(0)
        nets = {p: build_model() for p in sb.players}
        steps, info = collect_episode(nets, sb, rng=np.random.default_rng(0))
        self.assertFalse(info["truncated"],
                         "用例前提：t_max=1 该是**打完的平局**；截断的话本用例会假绿")
        self.assertIsNone(info["winner"], "用例前提：该判平局（无胜方）")
        self.assertTrue(steps)
        # ② 正题：整条 train() 跑一轮，池子里**谁的战绩都不该动**
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "league.db")
            train_mod.train(iters=1, episodes_per_iter=1, size=8, t_max=1,
                            pool=3, league_db=db, league_mains=0,
                            league_snapshot_every=0, log=QUIET)
            lg = League(db, fingerprint=train_mod._shape_fingerprint(), log=QUIET)
            self.addCleanup(lg.close)
            lg.load()
            self.assertTrue(lg.members, "池子没建起来 ⇒ 本用例什么都没测到")
            for m in lg.members.values():
                self.assertEqual(m.games, 0, f"{m.mid} 把平局记成了战绩")
            self.assertEqual(len(lg.active()), len(lg.members), "平局不该触发淘汰")


if __name__ == "__main__":
    unittest.main()