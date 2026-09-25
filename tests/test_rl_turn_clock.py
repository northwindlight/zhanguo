# -*- coding: utf-8 -*-
"""`my_turn`（**当前回合数**）的守卫 —— 用户 2026-09-25：

    「可以加一个当前回合数，也就是 **llm 玩家也知道的**，现在多少回合了」
    →「我不想让模型记忆每个回合的固定打法，模型打法应该和绝对回合数无关，
        **只看相对回合数**」
    →「**myturn 不用撤，只是每次开局传入一个随机偏移就行**」

★ 钉六件事，每条都对着一个**会静默学错**的形状：

  ① **逐局随机偏移** —— 偏移不是装饰，它就是"死记无处落脚"那条的实现。
     随机性丢了（比如有人写死成 0）⇒ 模型可以照着"第 50 回合"背打法，
     **而这条完全不报错**，只会让泛化悄悄变差。
  ② ★★ **不许拿 `t_max` 当分母** —— `t_max` 是训练为了防僵局定的**地平线**，
     不是游戏规则；删掉 `turn_frac` 就是为了它。写成 `turn/t_max` 等于
     把删掉的东西从后门放回来，而**观测的形状一模一样、谁也不报错**。
     ⇒ 守卫直接钉：**同一 seed 下改 `t_max`，这一列必须一个比特都不动**。
  ③ **不夹到 1** —— 回合数一直涨；夹了就把后半段的增量抹平，
     而增量正是 ① 里唯一**特意保留**下来的东西。
  ④ **局内恒定、随回合递增** —— 局内逐帧做差要读得出"又过了一回合"。
  ⑤ **同一 seed 可复现** —— 确定性是这条线的地基（`test_determinism`）。
  ⑥ ★ **抽偏移不许扰动开局** —— 否则"加了偏移那一版"与"没加那一版"在
     同一 seed 下**地图都变了** ⇒ 版间 A/B 对比整个失效，而没有任何东西会报错。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import encode as E                            # noqa: E402
from rl import vocab as V                             # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402

# ★ seed=11 的开局核心（**抽偏移顺序的黄金值**，见 ⑥）。
#   ⚠ 它**不是**"随机开局算法"的规格 —— 以后算法有意改了，它就是要跟着改。
#     它存在的唯一目的是：**"多加了一次随机 draw" 这种改动会让它响**。
GOLDEN_STARTS_SEED11 = {"甲": (1, 8), "乙": (10, 1), "丙": (10, 7)}


def _sb(seed=11, t_max=150):
    return Sandbox(seed=seed, size=12, n_nations=3, t_max=t_max,
                   halls_known=True).reset()


def _turn_col(sb, me):
    return float(E.encode_glob(sb, me)[V.GLOB.index("my_turn")])


class TestTurnClock(unittest.TestCase):
    # ★ `my_turn` 在 `GLOB` 里的**下标黄金值**。真正的铁律是**只许追加**：
    #   往中间插一列 ⇒ 后面每一列都平移 ⇒ 所有 ckpt 的输入口径静默错位
    #   （`load_state_dict` 照样成功，因为形状没变！）。下标黄金值钉的就是这个。
    #   ⚠ 我第一版写的是"`my_turn` 必须是**最后一列**"——那是个**假规则**：
    #     它把"当时它恰好是最后一个"当成了不变量，于是**后来追加情报那 12 列时它红了**
    #     （追加是**合法**的！）。钉"下标"才是钉住了真东西。
    TURN_COL_IDX = 21

    def test_the_column_exists(self):
        """① `my_turn` 在 `GLOB` 里，且**下标不许变**（只许追加）。"""
        self.assertIn("my_turn", V.GLOB)
        self.assertEqual(V.GLOB.index("my_turn"), self.TURN_COL_IDX,
                         "`my_turn` 的下标变了 ⇒ 有列被插到了它前面 "
                         "⇒ 所有 ckpt 的输入口径静默错位（形状不变、加载照样成功）")
        self.assertEqual(V.GLOB_SIZE, len(V.GLOB))

    def test_offset_is_random_per_episode(self):
        """① **逐局随机** —— 写死成 0 会让"死记绝对回合"重新可行，且不报错。"""
        offs = {Sandbox(seed=s, size=12, n_nations=3, halls_known=True).reset().turn_offset
                for s in range(40)}
        self.assertGreater(len(offs), 8,
                           f"40 个 seed 只抽出了 {len(offs)} 种偏移 ⇒ 偏移不是随机的：{offs}")
        self.assertTrue(all(0 <= o < V.TURN_OFFSET_SPAN for o in offs),
                        f"偏移越界（应在 [0,{V.TURN_OFFSET_SPAN})）：{sorted(offs)}")

    def test_same_seed_reproduces_the_offset(self):
        """⑤ 确定性：同一 seed 必须抽到同一个偏移（这条线的地基）。"""
        self.assertEqual(_sb(11).turn_offset, _sb(11).turn_offset)
        self.assertEqual(_turn_col(_sb(11), "甲"), _turn_col(_sb(11), "甲"))

    def test_tmax_must_not_move_this_column(self):
        """★★ ② **改 `t_max` 不许动这一列**（防 `turn_frac` 从后门回来）。

        ★ 这条是这个文件里最重要的一条：写成 `turn / t_max` 时，
          **观测形状一模一样**、训练照跑、loss 照降 —— 只有这条守卫会响。
        """
        for me in ("甲", "乙", "丙"):
            a = _turn_col(_sb(11, t_max=50), me)
            b = _turn_col(_sb(11, t_max=150), me)
            c = _turn_col(_sb(11, t_max=5000), me)
            self.assertEqual(a, b, f"改 `t_max` 动到了 `my_turn`（{me}）⇒ 分母是地平线")
            self.assertEqual(b, c, f"改 `t_max` 动到了 `my_turn`（{me}）")

    def test_not_clamped_at_one(self):
        """③ **不夹到 1** —— 夹了后半段的增量就没了，而增量是唯一保留的东西。"""
        sb = _sb(11)
        sb.turn_offset = 90
        sb.turn = 240                          # (240+90)/100 = 3.3
        self.assertGreater(_turn_col(sb, "甲"), 1.0,
                           "被夹到 1 了 ⇒ 后半段的「又过了一回合」读不出来")
        a = _turn_col(sb, "甲")
        sb.turn = 241
        self.assertNotAlmostEqual(a, _turn_col(sb, "甲"), delta=1e-9,
                                  msg="饱和了：回合 +1 这一列没动")

    def test_constant_within_episode_and_grows_by_one_per_turn(self):
        """④ 局内恒定（偏移不会自己变）、且每回合**恰好**涨 1/TURN_SCALE。"""
        sb = _sb(11)
        off0 = sb.turn_offset
        v0 = _turn_col(sb, "甲")
        self.assertEqual(sb.turn_offset, off0, "局内偏移变了")
        sb.turn += 1
        self.assertAlmostEqual(_turn_col(sb, "甲") - v0, 1.0 / V.TURN_SCALE, places=6,
                               msg="回合 +1 没有让这一列涨 1/TURN_SCALE ⇒ 增量读不出来")
        self.assertEqual(sb.turn_offset, off0, "推进回合把偏移也改了")

    def test_clone_carries_the_offset(self):
        """★ 试演副本里"现在几点"必须和真身一致（否则试演与真身看到的局面不同）。"""
        sb = _sb(11)
        c = sb.clone()
        self.assertEqual(c.turn_offset, sb.turn_offset)
        c.turn_offset = 0
        self.assertNotEqual(sb.turn_offset, 0, "改副本改到了真身的偏移")

    def test_drawing_the_offset_does_not_shift_the_map(self):
        """⑥ ★ **同一 seed 的地图不许因为"加了这一列"而变**（黄金值）。

        ★ 为什么值得钉：加了 `turn_offset` 之后，如果抽偏移顺带改变了
          `_random_starts()` 的取数，同一 seed 的地图就变了 ⇒
          **"加偏移前/后"两版无法 A/B 对比**（同一 seed 跑出来的不是同一个局），
          而**没有任何东西会报错**。黄金值让这种改动**当场响**。

        ★★ 诚实说明它**测不到**什么（我一开始就是按错的那个说法写的）：
          `_random_starts()` 每次都**自己 new 一个 `Random(self.seed)`**，
          所以"偏移借用同一条流"这种破坏**结构上不可能发生**，我也实测确认过
          —— 故意让偏移去消费 `Random(self.seed)`，这条守卫**不响**。
          它真正钉的是：**随机开局算法本身（或它的取种方式）不许悄悄改**
          —— 有意改了就一并更新黄金值。
        """
        sb = Sandbox(seed=11, size=12, n_nations=3, halls_known=True)
        self.assertEqual(sb._random_starts(), GOLDEN_STARTS_SEED11,
                         "seed=11 的开局核心变了 ⇒ 随机开局的取数被改了 "
                         "（有意改的话请一并更新黄金值；无意改的话，版间对比全废）")
        # ★ 反向对照：手动把偏移改成一个**完全不同的值**，开局核心必须一个格子都不动。
        sb.turn_offset = 0
        s0 = sb._random_starts()
        sb.turn_offset = V.TURN_OFFSET_SPAN - 1
        self.assertEqual(sb._random_starts(), s0, "开局核心跟着 `turn_offset` 变了")
        self.assertEqual(_sb(11)._random_starts(), s0, "`reset()` 的开局核心与直接抽的不一致")

    def test_reset_does_not_touch_the_global_rng(self):
        """⑥' ★ 抽偏移**必须走"按 seed 派生"的流**，不许碰**全局**随机流。

        ★ 这条是真能响的（黄金值那条不能）：写成 `random.randrange(...)`
          （模块级）就 ⇒ ① 同一 seed 抽到不同偏移（确定性丢）、
          ② **局与局之间通过全局流互相耦合**（前几局抽了什么会影响后面），
          ③ 谁在别处动了全局种子就静默改变训练数据。三种都不报错。
        """
        import random as _r
        st = _r.getstate()
        for s in (3, 5, 7):
            Sandbox(seed=s, size=12, n_nations=3, halls_known=True).reset()
        self.assertEqual(_r.getstate(), st,
                         "`reset()` 动了**全局**随机流 ⇒ 局与局耦合、且不可复现")
        # ★ 反向对照：全局种子换掉，同一 seed 的结果必须**一模一样**
        _r.seed(1234)
        a = Sandbox(seed=11, size=12, n_nations=3, halls_known=True).reset()
        _r.seed(9999)
        b = Sandbox(seed=11, size=12, n_nations=3, halls_known=True).reset()
        self.assertEqual(a.turn_offset, b.turn_offset, "偏移跟着全局种子变了")


if __name__ == "__main__":
    unittest.main()