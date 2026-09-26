# -*- coding: utf-8 -*-
"""**多 worker 共用一个联赛库**的守卫 —— 在训成员的 mid 必须带 worker 身份。

★★ 病因（2026-09-26 查出来 + 用两个真 worker 实测复现）：
   在训成员的 mid 原来是 `L0..L4` —— **不带 worker 标识**，而 `League.bind_live`
   是 `INSERT … ON CONFLICT(mid) DO UPDATE` ⇒ 两个 worker 共用一个库时，
   各自的 `L0` 落到**同一行账本**。
   实测（两个真 worker、池 3）：`members` 表**只有 3 行**，而两进程各有 3 份网 = **6 份**；
   而且其中一个 worker 那局打满平局（按规矩不记战绩），它的成员却显示着
   **另一个 worker** 赢的那一局 —— 张冠李戴，日志上完全看不出来。
   （冻结快照那边早就带了（`G<iter>@<sha>_<pid>`），**在训成员这边一直漏着** ——
    因为单 worker 下两种写法**完全等价**。）

★ 每条守卫对着一个**会静默错 / 会当场崩**的形状：
  ① 两 worker 的在训成员必须是**不同的行**（否则战绩与 Elo 混在一起）；
  ② **别人的在训成员绝不许被抽上场** —— 它的权重只存在于那个进程的内存里
     （`path=None`），而 `net_of` 对 `path=None` 是**直接抛 `KeyError`**
     ⇒ 抽中就是崩。★ 所以这条守卫要先证明「抽中它真的会崩」，否则挡不挡都看不出差别；
  ③ **主 pt 是「每个 worker 各自 N 份」**（用户 2026-09-26 拍的口径）；
  ④ **同一个 worker 重启前后身份必须一样** —— 否则库里积一堆 `path=None`、
     谁也读不回来的僵尸在训行（`--restart-after` 每 5 iter 换一次进程）；
  ⑤ **别人的在训成员由他自己停用**，我不碰；
  ⑥ **兜底①② 必须按「本进程能抽到的份数」算** —— 按全库算会在本地池子空掉时让
     `draw(k)` 抛「凑不齐一局」。

★ 每条都**故意破坏过一次**确认会响（破坏方式写在各用例的 docstring 里）。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import train as T                                  # noqa: E402
from rl.league import League                               # noqa: E402
from rl.model import build_model                           # noqa: E402

FP = T._shape_fingerprint(0)
QUIET = lambda *_: None                                    # noqa: E731
_OPEN: list = []


def tearDownModule():
    """★ 连接关干净 —— 一堆 `ResourceWarning` 会把真问题淹在噪声里。"""
    for lg in _OPEN:
        lg.close()
    _OPEN.clear()


def _league(db, tag, **kw):
    kw.setdefault("mains", 0)
    kw.setdefault("max_k", 1)          # 兜底① 的下限；用得到它的用例自己覆盖
    kw.setdefault("fingerprint", FP)
    kw.setdefault("device", "cpu")
    kw.setdefault("log", QUIET)
    lg = League(db, worker=tag, **kw)
    _OPEN.append(lg)
    lg.load()
    return lg


def _pair(d, *, n=3, tag_a="wkA", tag_b="wkB", **kw):
    """两个 worker（两个 `League`、**同一个库**）各绑 n 份在训成员，返回 (A, B, midsA, midsB)。"""
    db = str(Path(d) / "l.db")
    A = _league(db, tag_a, **kw)
    B = _league(db, tag_b, **kw)
    ma = [f"L{i}@{tag_a}" for i in range(n)]
    mb = [f"L{i}@{tag_b}" for i in range(n)]
    for i, mid in enumerate(ma):
        A.bind_live(mid, build_model())         # ★ 各绑**各自的**网对象
    for i, mid in enumerate(mb):
        B.bind_live(mid, build_model())
    return A, B, ma, mb


def _streak(lg, target, n=12, *, win=False, others=("X0", "X1")):
    """给 `target` 记 n 局：`win=False` 一直输（评级压到阈值下、RD 压小），
    `win=True` 一直赢（评级抬上去 ⇒ **进不了候选**）。

    ★★ 必须**三人局**：两人池里评级只确定「谁比谁强」，**绝对水平是飘的**
      ⇒ 20 局全输 RD 还有 181（>110，RD 门永远退不掉）。三人局多了「两个输家互平」
      这一路比较 ⇒ 12 局就能把 RD 压到 ≈100。
    ★★ 实测（别凭直觉）：**灌得越深，评级未必越低** ——
      A 灌 12 局得 1353 / B 灌 20 局得 1390（"更弱"反而更高）。
      所以用例里要的是**赢/输两个方向**（1963 vs 1087，拉开 900 分），
      而不是"谁灌得多"。
    """
    for i in range(n):
        w = target if win else others[i % len(others)]
        for m in (target, *others):
            lg.record(m, m == w, it=i, game=f"g-{target}-{i}")


def _rows(lg):
    return dict(lg.conn.execute("SELECT mid,kind FROM members"))


# ============================================================ ① 不并账
class TestTwoWorkersOwnDifferentRows(unittest.TestCase):
    def test_in_train_rows_do_not_merge(self):
        """① 两 worker 各 3 份 ⇒ **6 行**；各自的 `active()` 只含自己的。

        ★ 破坏方式：把 `_pair` 的 `tag_b` 也传成 `"wkA"` ⇒ 两边的 mid 变成同一个字符串
          ⇒ `members` 只剩 3 行，断言当场红。
        """
        with tempfile.TemporaryDirectory() as d:
            A, B, ma, mb = _pair(d)
            rows = _rows(A)
            self.assertEqual(len(rows), 6,
                             f"两 worker × 3 份应当是 6 行，实际 {sorted(rows)}")
            self.assertEqual(set(ma) & set(mb), set(), "两边的 mid 不该相交")
            self.assertEqual(sorted(A.drawable()), sorted(ma),
                             "A 的 drawable() 里混进了别人的成员")
            self.assertEqual(sorted(B.drawable()), sorted(mb),
                             "B 的 drawable() 里混进了别人的成员")
            # ★★ **账本口径必须仍然是 6**（`active()` 不跟着收窄）——
            #   这两个问题是两个方法，见 `League.active` 的 docstring。
            #   合成一个的后果是静默废掉两条旧守卫（那里记着全过程）。
            #   ★ 先 `refresh()`：`active()` 读的是**缓存**，而 A 的缓存是它自己
            #     `load()` 那一刻的快照（那时 B 还没绑）⇒ 不刷新只看得见自己那 3 份。
            #     「判据前一律先 `refresh()`」是这张表的规矩，不是补丁。
            A.refresh()
            self.assertEqual(sorted(A.active()), sorted(ma + mb),
                             "active() 是账本口径，该看得见全库 6 份")
            self.assertEqual(sorted(A.drawable()), sorted(ma),
                             "★ 刷新之后 drawable() 仍然只有自己那 3 份")

    def test_single_worker_is_unchanged(self):
        """★ 单 worker（只有一个 tag）时行为与旧版**逐字等价**：3 份就该是 3 行。"""
        with tempfile.TemporaryDirectory() as d:
            A, _, ma, _ = _pair(d, n=3, tag_b="wkA")   # 两个 League、**同一个身份**
            self.assertEqual(len(_rows(A)), 3)
            self.assertEqual(sorted(A.active()), sorted(ma))
            self.assertEqual(sorted(A.drawable()), sorted(ma),
                             "单 worker 下两个口径必须**逐字等价**")


# ============================================================ ② 别人抽不得
class TestForeignLiveMemberIsNeverDrawable(unittest.TestCase):
    """② 抽中别人的在训成员 = 当场崩（权重只在那个进程的内存里）。"""

    def test_net_of_on_foreign_live_raises(self):
        """先把「抽中它真的会崩」钉住 —— 否则挡不挡都看不出差别（假绿）。"""
        with tempfile.TemporaryDirectory() as d:
            A, _B, _ma, mb = _pair(d)
            with self.assertRaises(KeyError):
                A.net_of(mb[0])

    def test_draw_never_returns_a_foreign_live_mid(self):
        """★ 破坏方式：把 `active()` 的判据改回 `if m.active` ⇒ 200 次抽签必然抽到
          别人的成员，断言当场红。"""
        with tempfile.TemporaryDirectory() as d:
            A, _B, ma, mb = _pair(d)
            rng = np.random.default_rng(0)
            got: set = set()
            for _ in range(200):
                got |= set(A.draw(3, rng))
            self.assertFalse(got & set(mb), f"抽到了别人的在训成员：{got & set(mb)}")
            self.assertEqual(got, set(ma))


# ============================================================ ③ 主 pt 各算各的
class TestEachWorkerGetsItsOwnMains(unittest.TestCase):
    def test_two_mains_per_worker(self):
        """③ `--league-mains 2` ⇒ **每个 worker 各自** 2 份 main（全库共 4 份）。

        ★ 破坏方式：把 `bind_live` 里的 `n_local` 换回 `len(self._live())`（全库口径）
          ⇒ B 的 L0@B/L1@B 会变成 `live`，断言当场红。
        """
        with tempfile.TemporaryDirectory() as d:
            A, _B, ma, mb = _pair(d, mains=2)
            kinds = _rows(A)
            for mid in (ma[0], ma[1], mb[0], mb[1]):
                self.assertEqual(kinds[mid], "main", f"{mid} 该是主 pt")
            for mid in (ma[2], mb[2]):
                self.assertEqual(kinds[mid], "live", f"{mid} 不该是主 pt")
            self.assertEqual(sum(1 for v in kinds.values() if v == "main"), 4)


# ============================================================ ④ 重启后身份不变
class TestIdentityIsStableAcrossRestart(unittest.TestCase):
    def test_rebinding_after_restart_reuses_the_same_rows(self):
        """④ 同一个身份重新绑定 ⇒ **不新增行**，`kind` 保住，本进程身份补回来。

        ★ 破坏方式：第二次打开时换成 `worker="wkB"` 并绑 `L0@wkB`（= 模拟"身份用 pid"
          那种不稳定身份）⇒ 变成 6 行，断言当场红。
        """
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "l.db")
            kw = dict(mains=2, max_k=1, fingerprint=FP, device="cpu", log=QUIET)
            ma = [f"L{i}@wkA" for i in range(3)]

            a1 = League(db, worker="wkA", **kw)
            _OPEN.append(a1)
            a1.load()
            for mid in ma:
                a1.bind_live(mid, build_model())
            a1.save()
            before = _rows(a1)
            a1.close()

            a2 = League(db, worker="wkA", **kw)      # ← **同一个身份**（重启）
            _OPEN.append(a2)
            a2.load()
            for mid in ma:
                a2.bind_live(mid, build_model())

            self.assertEqual(_rows(a2), before, "重启后重新绑定不该新增/改行")
            self.assertEqual(len(_rows(a2)), 3)
            self.assertEqual(before[ma[0]], "main", "kind 该保住（主 pt 不许漂）")
            self.assertEqual(before[ma[2]], "live")
            self.assertEqual(sorted(a2.drawable()), sorted(ma),
                             "重启后本进程的在训身份没补回来 ⇒ 抽签抽不到自己")

    def test_tag_from_out_is_stable_and_distinct(self):
        """④ 身份缺省从 `--out` 派生：**同一个 out 恒等、不同 out 必不同**。

        ★ 破坏方式：让 `_worker_tag` 在缺省分支返回 `f"pid{os.getpid()}"`
          ⇒ 同一个 `--out` 两次调用给出**不同**身份（重启对不上），断言当场红。
        """
        t1, src = T._worker_tag(None, "/x/y/runs/mem01.pt")
        t2, _ = T._worker_tag(None, "/x/y/runs/mem01.pt")
        t3, _ = T._worker_tag(None, "/x/y/runs/mem02.pt")
        self.assertEqual(t1, t2, "同一个 --out 必须给同一个身份（重启要对得上）")
        self.assertNotEqual(t1, t3, "不同 --out 必须是不同身份（两臂不许并账）")
        self.assertEqual(src, "从 --out 派生")
        self.assertEqual(T._worker_tag("mem1", "/x/y/mem01.pt")[0], "mem1",
                         "显式 --worker-id 优先于派生")
        self.assertNotEqual(T._worker_tag(None, None)[0], "",
                            "没有 --out 也必须给个非空身份（否则 mid 会退回裸 L0）")


# ============================================================ ⑤ 别人的在训成员不碰
class TestRetireLeavesForeignLiveAlone(unittest.TestCase):
    """⑤ 只停用**自己**的在训成员；别人那份弱的由他自己管。"""

    def test_the_fixture_can_actually_retire_someone(self):
        """★ 先证明**这套 fixture 真的能退人** —— 否则下面那条 `killed == []` 是空转。

        （只有一个身份 ⇒ 两份都是"自己的"，一直输 ⇒ 必然被退。）
        """
        with tempfile.TemporaryDirectory() as d:
            A, _B, ma, _mb = _pair(d, n=2, tag_b="wkA", min_learners=0)
            for mid in ma:
                _streak(A, mid, 12, win=False)
            killed = A.retire()
            self.assertTrue(set(killed) & set(ma), f"该退没退：{killed}")

    def test_foreign_live_is_never_retired(self):
        """⑤ 别人那份**很弱**也不许我停用。

        ★★ 布局是**刻意**的：自己那份**一直赢**（评级 1963/1842 ⇒ 根本进不了候选），
          别人那份**一直输**（1087/1103、RD 81 ⇒ 一旦守卫被删，它们必然被挑中）。
          ⇒ "没退"这件事**只能是守卫的功劳**，不可能是"碰巧它们不弱"。
        ★ 破坏方式：把 `retire` 里 `if r[1]=="live" and r[0] not in mine: continue`
          那两行删掉 ⇒ B 的成员进候选并被停用，断言当场红。
        ★ 试过但**不行**的写法：两边都灌输、靠"灌得更深"让别人的评级更低 ——
          实测方向是反的（灌 20 局的 1390 > 灌 12 局的 1353），破坏**不会响**。
        """
        with tempfile.TemporaryDirectory() as d:
            A, B, ma, mb = _pair(d, n=2, min_learners=0)
            for mid in ma:
                _streak(A, mid, 12, win=True)          # 自己：强 ⇒ 不是候选
            for mid in mb:
                _streak(A, mid, 12, win=False)         # 别人：弱 ⇒ 是候选（但归他管）
            killed = A.retire()
            self.assertEqual(killed, [], f"停用了别人的在训成员：{killed}")
            B.refresh()
            for mid in mb:
                self.assertTrue(B.members[mid].active,
                                f"别人把我那份停用了：{mid}")


# ============================================================ ⑦ 旧库要吭声
class TestStaleDbIsLoud(unittest.TestCase):
    def test_old_format_live_rows_get_a_loud_warning(self):
        """⑦ 旧库（裸 `L0..L4`）用新代码打开时**必须大声说一句**。

        ★ 为什么这是"必须"而不是"最好"：那些旧行会变成 `path=None`、谁也读不回来的
          僵尸，而新的 `active()` 会把它们挡在抽签之外 ⇒ **不会崩**
          ⇒ **不吭声的话，症状只是"池子份数看着不对"**（正是静默那一类）。
        ★ 破坏方式：把 `load()` 里那段 `stale` 告警删掉 ⇒ 断言当场红。
        """
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "l.db")
            kw = dict(mains=0, max_k=1, fingerprint=FP, device="cpu")
            old = League(db, worker="x", log=QUIET, **kw)
            _OPEN.append(old)
            old.load()
            for mid in ("L0", "L1"):
                old.bind_live(mid, build_model())
            old.save()

            msgs: list = []
            new = League(db, worker="wkA", log=msgs.append, **kw)
            _OPEN.append(new)
            new.load()
            self.assertTrue(any("旧格式" in m for m in msgs), f"没告警：{msgs}")
            # ★ 判据是 **`drawable()`**（我抽不到它们），不是 `active()`（账本上它们
            #   当然还是 active 的）—— 两个口径别混，见 `League.active` 的 docstring。
            self.assertEqual(new.drawable(), [], "旧格式的僵尸行不该被抽上场")
            self.assertEqual(sorted(new.active()), ["L0", "L1"],
                             "账本口径照旧看得见它们（只是抽不到）")


# ============================================================ ⑥ 兜底按本地算
class TestDrawableFloorIsLocal(unittest.TestCase):
    def test_floor_counts_local_drawable_not_global(self):
        """⑥ 兜底① 数**本进程能抽到的**份数，不是全库的。

        ★ 场景：全库 4 份（≥ `max_k=3`），但**我这一侧只有 2 份** ⇒ 一份都不该退
          （退了 `draw(3)` 就抛「凑不齐一局」）。
        ★ 破坏方式：把兜底① 的 `n_draw` 换回全库口径 `n_active` ⇒ 会退掉 1 份，
          断言当场红。
        """
        with tempfile.TemporaryDirectory() as d:
            A, _B, ma, mb = _pair(d, n=2, min_learners=0, max_k=3)
            for mid in ma + mb:
                _streak(A, mid, 12, win=False)
            killed = A.retire()
            self.assertEqual(killed, [], f"按全库数淘汰了：{killed}")
            self.assertEqual(len(A.drawable()), 2, "本地池子不该被削到 max_k 以下")
