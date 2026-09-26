# -*- coding: utf-8 -*-
"""**「谁上场谁学」+「学一个增一个」**的守卫 —— 用户 2026-09-26 改的口径（原话）：

    「**谁上场谁学，同时冻结**」
    「**学一个增一个**」
    「**都进硬盘，每次上场都冻一份，这就是只增不删**」
    「训过的」→「**钉住 + 下次冻结落盘**」
    「**打十次以上，胜率低于20%，就丢到退役池子**」（= 原有的 `retire` 规则，未变）

改动本身是**否定我自己的一个实现决定**：原来 `train()` 里写着
`if mi in buf`（只有那 5 个在训槽进梯度、快照只当对手）—— 那不是用户口径。

★ 每条守卫对着一个**会静默错**的形状：

  ① **父本不许被改写** —— 新生儿是"另一份"，父本自己的 `.pt` 永远是它出生那一版。
     写错（把学完的权重盖回父本自己的文件）⇒ 池子按**身份**漂移，
     而账本上"这一份"的胜率会跨版本、**没有任何东西报错**。
  ② **新生儿带 0 战绩** —— 一局的胜负属于"**上场的那一版**"（父本那行）。
     写错（新生儿继承父本战绩）⇒ 每一代都在"继承"前代的胜率，判据失去意义。
  ③ **学过的份不许被逐出**（LRU 的判据是"盘上有副本"，而学过的**比盘上新**）
     ⇒ 不钉住就**静默**回到旧版本（学习丢了，日志上什么都没有）。
  ④ **每 iter 不许出现"零梯度"** —— 一局三个座位全是快照时，
     老代码那一整轮一点梯度都没有（生产日志里出现过 `网络 {}`）。
  ⑤ **只增不删** —— 成员数单调不减；旧库升级（补 `parent` 列）不许重建表。

★ 都故意破坏过（见每条 docstring 里写的破坏方式）。
"""
from __future__ import annotations

import ast
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import train as T                                  # noqa: E402
from rl import vocab as V                                  # noqa: E402
from rl.league import League                               # noqa: E402
from rl.model import build_model                           # noqa: E402

FP = T._shape_fingerprint(0)


def _league(d, *, net_cap=24):
    return League(str(Path(d) / "l.db"), mains=0, fingerprint=FP, device="cpu",
                  net_cap=net_cap, log=lambda *_: None)


def _fhash(p) -> str:
    return hashlib.sha1(Path(p).read_bytes()).hexdigest()


class TestFreezeTrainedCreatesANewVersion(unittest.TestCase):
    """① ② 父本不动、新生儿 0 战绩。"""

    def test_parent_file_is_untouched_and_newborn_starts_at_zero(self):
        import torch
        with tempfile.TemporaryDirectory() as d:
            lg = _league(d)
            net = build_model()
            live = lg.bind_live("L0", net)
            lg.add_snapshot(net, 3, mid="S00003_1")
            snap = lg.members["S00003_1"]
            # ★ 给父本**先攒一点战绩** —— 否则"新生儿继承父本战绩"这种破坏
            #   继承到的是 (0,0) ⇒ 断言照样绿（**假绿**，实测踩过）。
            for _ in range(3):
                lg.record("S00003_1", True, it=4)
            lg.refresh()
            self.assertEqual(lg.members["S00003_1"].games, 3, "父本战绩没记上（测试前提不成立）")
            before = _fhash(snap.path)
            # ★ 让它"学"一下（改权重），再冻 —— 模拟 train() 的那一步
            with torch.no_grad():
                for p in net.parameters():
                    p.add_(0.01)
            lg.mark_hot("S00003_1")
            born = lg.freeze_trained(net, "S00003_1", 5)
            self.assertNotEqual(born.mid, "S00003_1", "新生儿不该复用父本的 mid")
            self.assertEqual(born.parent, "S00003_1", "父本记错了 ⇒ 血脉追不回来")
            self.assertEqual((born.games, born.wins), (0, 0),
                             f"新生儿带着战绩出生 {born} ⇒ 一局的胜负被记到了"
                             f"**还没上过场的那一版**头上，账本口径跨版本混了")
            # ★★ **必须查"落盘的那一行"**，不能只查返回的对象：
            #   返回的 `Member` 是**刚 new 出来的 Python 对象**（`games` 缺省 0），
            #   哪怕 DB 那行被写成了继承父本战绩，这个对象也照样是 0
            #   ⇒ 只断言对象 = **假绿**（实测：故意让新生儿继承战绩，这条守卫**没响**）。
            lg.refresh()
            row = lg.members[born.mid]
            self.assertEqual((row.games, row.wins), (0, 0),
                             f"**库里那一行**带着战绩：{row} ⇒ 账本口径跨版本混了"
                             f"（内存对象看不出来，必须 refresh 后查库）")
            self.assertEqual(_fhash(snap.path), before,
                             "**父本的文件被改写了** ⇒ 池子按身份漂移（父本应当永远是"
                             "它出生那一版）")
            self.assertTrue(Path(born.path).exists(), "新生儿的 .pt 没落盘")
            # ★ 新生儿是**另一份权重**：文件内容与父本不同（父本只被写过一次）
            self.assertNotEqual(_fhash(born.path), before,
                                "新生儿的权重与父本**一模一样** ⇒ 那这次学习没被归档")
            # ★ 父本（快照）学完的那一版已归档 ⇒ 从缓存撤掉，回到它出生那一版
            self.assertNotIn("S00003_1", lg._nets,
                             "父本还赖在缓存里 ⇒ 下次抽到它用的是**学过的**权重，"
                             "而账本记的是它出生那一版（别名污染）")
            self.assertIn(live.mid, lg._nets, "在训成员**不许**从缓存里撤掉（训练回路要用）")

    def test_id_hashes_the_parent_so_lineage_is_traceable(self):
        with tempfile.TemporaryDirectory() as d:
            lg = _league(d)
            net = build_model()
            lg.bind_live("L0", net)
            lg.add_snapshot(net, 1, mid="S00001_x")
            b = lg.freeze_trained(net, "S00001_x", 7)
            short = hashlib.sha1(b"S00001_x").hexdigest()[:8]
            self.assertIn(short, b.mid, "新生儿 mid 里没有父本的缩写 ⇒ 光看 id 追不到血脉")
            self.assertIn("G00007", b.mid, f"mid 里没有代次：{b.mid}")
            self.assertLess(len(b.mid), 60, f"mid 太长（每代会长一点，几十代就撑破文件名）：{b.mid}")

    def test_freezing_twice_in_the_same_iter_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            lg = _league(d)
            net = build_model()
            lg.bind_live("L0", net)
            lg.add_snapshot(net, 1, mid="S00001_x")
            a = lg.freeze_trained(net, "S00001_x", 9)
            n0 = len(lg.members)
            b = lg.freeze_trained(net, "S00001_x", 9)
            self.assertEqual(a.mid, b.mid)
            self.assertEqual(len(lg.members), n0, "同一 iter 同父本冻了两次 ⇒ 多出一个成员")


class TestTrainedMembersArePinned(unittest.TestCase):
    """③ 学过的份不许被 LRU 逐出（防"静默回到旧版本"）。"""

    def test_hot_member_survives_eviction(self):
        with tempfile.TemporaryDirectory() as d:
            lg = _league(d, net_cap=1)
            net = build_model()
            lg.bind_live("L0", net)
            lg.add_snapshot(net, 1, mid="S00001_a")
            lg.add_snapshot(net, 2, mid="S00002_b")
            lg.mark_hot("S00001_a")           # ★ 它刚学过（内存比盘上新）
            lg.net_of("S00001_a")
            lg.net_of("S00002_b")             # 这一下会把缓存顶过 net_cap ⇒ 触发逐出
            self.assertIn("S00001_a", lg._nets,
                          "**学过的那份被逐出了** ⇒ 下次抽到它读回的是盘上旧版"
                          "（学习静默丢失，日志上什么都没有）")

    def test_hot_is_cleared_after_freezing(self):
        with tempfile.TemporaryDirectory() as d:
            lg = _league(d)
            net = build_model()
            lg.bind_live("L0", net)
            lg.add_snapshot(net, 1, mid="S00001_a")
            lg.mark_hot("S00001_a")
            lg.freeze_trained(net, "S00001_a", 3)
            self.assertNotIn("S00001_a", lg._hot,
                             "冻完还钉着 ⇒ 永远逐不出去（缓存会一直涨）")


class TestOnlyAddNeverDelete(unittest.TestCase):
    """⑤ 只增不删 + 旧库迁移。"""

    def test_members_are_monotone(self):
        with tempfile.TemporaryDirectory() as d:
            lg = _league(d)
            net = build_model()
            lg.bind_live("L0", net)
            seq = [len(lg.members)]
            for i in range(1, 6):
                lg.add_snapshot(net, i, mid=f"S{i:05d}")
                lg.freeze_trained(net, f"S{i:05d}", i)
                seq.append(len(lg.members))
            self.assertEqual(seq, sorted(seq), f"成员数不是单调不减：{seq}")

    def test_old_db_gets_the_parent_column(self):
        """★ 旧库（没有 `parent` 列）必须能**就地升级**，不能重建表（只增不删）。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "old.db"
            c = sqlite3.connect(str(p))
            c.executescript("""
                CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
                CREATE TABLE members (mid TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    born INTEGER NOT NULL, games INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
                    path TEXT, added TEXT NOT NULL);
                CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mid TEXT NOT NULL, won INTEGER NOT NULL, iter INTEGER,
                    worker TEXT, ts TEXT NOT NULL);
                INSERT INTO members(mid,kind,born,added) VALUES('S00001','snap',1,'x');
            """)
            c.commit()
            c.close()
            lg = League(str(p), mains=0, fingerprint=FP, device="cpu",
                        log=lambda *_: None)
            cols = {r[1] for r in lg.conn.execute("PRAGMA table_info(members)")}
            self.assertIn("parent", cols, "旧库没补上 `parent` 列 ⇒ 之后 SELECT parent 会报错")
            lg.refresh()
            self.assertIn("S00001", lg.members, "迁移把老成员弄丢了（只增不删）")


class TestNoIterIsWasted(unittest.TestCase):
    """④ **谁上场谁学** —— 判据取自日志那一行里的 `上场` 与 `网络`（两者必须一致）。

    ★★ 为什么用日志而不是库表（两版都试过，记下来）：
      · 第一版查日志里的 `网络 {}`（空 = 零梯度）⇒ **故意破坏时没响**：
        那个形状只在"一局三个座位全是快照"时出现，小池子的小测试里几乎凑不齐 ⇒ **假绿**。
      · 第二版改用 `results` 表对账（上场 vs 诞生者的父本）⇒ **在整套测试里红了**：
        `results` 只记**有胜负**的局，而测试用 `t_max=6` ⇒ 局局打满判平 ⇒ 表是空的
        ⇒ 断言时有时无（**比红更糟：它飘**）。
      ⇒ 正解：把这条口径**搬进日志**（`上场 {…}` 与 `网络 {…}` 并列，2026-09-26 加），
        于是它既能在测试里钉、也能在生产日志里直接核对 —— 而上限是 `国数×局数`，
        行不会无限长。
    """

    def test_everyone_who_played_learned_and_spawned_a_version(self):
        import re
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "l.db")
            buf: list[str] = []
            T.train(iters=2, episodes_per_iter=2, pool=2, t_max=6, size=8,
                    size_min=8, size_max=8, halls_known=True, league_db=db,
                    league_mains=0, league_snapshot_every=1, memory="none",
                    out=None, log=lambda s, *a: buf.append(str(s)))
            iters = [l for l in buf if l.startswith("[")]
            self.assertGreaterEqual(len(iters), 2, f"没跑出 iter 行：{buf[-3:]}")
            for line in iters:
                m = re.search(r"\| 上场 (\[[^\]]*\]) \| 网络", line)
                self.assertIsNotNone(m, f"日志里没有 `上场` 段（口径没法核对）：{line[:160]}")
                played = ast.literal_eval(m.group(1))
                self.assertTrue(played, f"这一轮没有任何成员上场（测试没生效）：{line[:160]}")
                # ★ `网络 {…}` 的键 = 这一轮**学到梯度**的成员，必须与上场集合**一致**
                net_seg = line.split("| 网络 ", 1)[1]
                trained = set(re.findall(r"'([^']+)':", net_seg))
                self.assertEqual(set(played), trained,
                                 f"**上场的和学过的对不上**：\n"
                                 f"  上场没学：{sorted(set(played) - trained)}\n"
                                 f"  {line[:200]}")
            # ★ 每个学过的都冻了一个新生儿（学一个增一个），且都在盘上
            lg = League(db, mains=0, fingerprint=FP, device="cpu", log=lambda *_: None)
            lg.refresh()
            for m in lg.members.values():
                if m.kind == "snap":
                    self.assertTrue(m.parent, f"{m.mid} 没记父本")
                    self.assertTrue(Path(m.path).exists(), f"{m.mid} 的 .pt 不在盘上")


class TestRetireWiringThroughTrain(unittest.TestCase):
    """★★ **按评级退役**在 `train()` 这条路上真的接上了（不只是 `League` 单测）。

    ★ 为什么单测 + CLI 的 AST 守卫**不够**：CLI 守卫只比"关键字名有没有传"，
      **不看值**；而 `League.retire` 的单测直接调它、绕过了 `train()`。
      ⇒ 出过的事故正是这一类：`alliances` 加进了函数体却**漏进签名** ⇒
      一跑 `train()` 就 `NameError`，而 `--help` 与 AST 守卫**两边都绿**
      （它们都不调用 `train()`）。这条守的就是"真的跑一遍"。

    ★ 场景要**刻意造**：`t_max=6` 时局局判平 ⇒ `results` 是空的 ⇒ 没有评级 ⇒
      "不判"（这是设计：没打过就不能判）⇒ 那样子根本碰不到退役那行代码。
      所以先把"决定过的对局"**预写进库**（`seed` 那段），再让 `train()` 去退。
    """

    def test_train_actually_retires_by_rating(self):
        from rl.model import build_model as _bm
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "l.db")
            seed = League(db, mains=0, fingerprint=FP, device="cpu",
                          log=lambda *_: None)
            for i in range(3):
                seed.bind_live(f"L{i}", _bm())
            for i in range(12):                   # L0 一直输 ⇒ 评级低、RD 小
                w = "L1" if i % 2 == 0 else "L2"
                g = f"seed{i}"
                for m in ("L0", "L1", "L2"):
                    seed.record(m, m == w, it=0, game=g)
            seed.close()
            buf: list[str] = []
            T.train(iters=1, episodes_per_iter=1, pool=3, t_max=6, size=8,
                    size_min=8, size_max=8, halls_known=True, league_db=db,
                    league_mains=0, league_snapshot_every=1, memory="none",
                    out=None, league_retire_rating=1400.0, league_retire_rd=110.0,
                    log=lambda s, *a: buf.append(str(s)))
            hit = [l for l in buf if "停用（评级" in l]
            self.assertTrue(hit, f"`train()` 里没打印停用行 ⇒ 参数没接上：{buf[-4:]}")
            self.assertIn("L0", hit[0], f"该退的 L0 没退：{hit[0]}")
            lg = League(db, mains=0, fingerprint=FP, device="cpu", log=lambda *_: None)
            lg.refresh()
            self.assertFalse(lg.members["L0"].active, "库里没标停用")
            self.assertIn("L0", lg.members, "停用 ≠ 删除（只增不删）")


if __name__ == "__main__":
    unittest.main()