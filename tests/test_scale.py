# -*- coding: utf-8 -*-
"""`rl/scale.py` 的护栏：**三个静默坑各一条**（用户 2026-09-17：「把抖动改成缩放」）。

为什么要有这个文件：缩放这种"整体乘一个数"的改动，**错了不会报错，只会在行为上显形**
（领土 158→25 那种）。三个坑全是 2026-09-14 实测栽出来的，所以每条都配一个决定性断言：

1. `energy` 漏网 ⇒ 电网凭空充足（断言：**所有**"量"字段都跟着 ×S，包括 energy）
2. jitter 基线反噬 ⇒ 每次 `env.reset()` 把 ×S 悄悄抹掉（断言：**reset 之后仍是 ×S**）
3. `AMOUNTS` 也是量 ⇒ 相对流量小 10 倍（断言：候选量档也 ×S）

外加两条不变式：`S=1` 是真 no-op；`apply` 幂等（在旧结果上再乘是最容易犯的错）。
"""
from __future__ import annotations

import unittest

import balance as B
import mp
import rl.env as E
from rl import scale


# ★每个抽样字段的**次数**（物=1、金=2、价=1）—— 与 rl/scale.py 的表同源，别各写一份
_DEG_OF = {
    "MARKET_DEPTH.木头": 1, "装备厂.cost": 2, "装备厂.energy": 1,
    "补给厂.energy": 1, "补给厂.inputs": 1, "市政厅.gold_per_slot": 2,
    "步.supply": 1, "步.recruit": 1, "START_RES.黄金": 2, "SPY_COST": 2,
    "DIPLO_COST": 2, "AMOUNTS[3]": 1,
}


def _snap():
    """一张"量"的抽样快照：容器里的几种、标量、以及 env 侧的量档。"""
    return {
        # ★`MARKET`（基准单价）**故意不在**这一列：金与物量都 ×S 之后单价若也 ×S，
        #   物值就成了 S² 次而金是 S 次 ⇒ 游戏本身变了（见 test_market_price_not_scaled）
        "MARKET_DEPTH.木头": B.MARKET_DEPTH["木头"],
        "装备厂.cost": B.BUILDINGS["装备厂"]["cost"],
        "装备厂.energy": B.BUILDINGS["装备厂"]["energy"],        # ← 坑 1
        "补给厂.energy": B.BUILDINGS["补给厂"]["energy"],        # ← 坑 1
        "补给厂.inputs": dict(B.BUILDINGS["补给厂"].get("inputs") or {}),
        "市政厅.gold_per_slot": B.BUILDINGS["市政厅"]["effects"]["gold_per_slot"],
        "步.supply": B.UNIT_TYPES["步"]["supply"],
        "步.recruit": dict(B.UNIT_TYPES["步"]["recruit"]),
        "START_RES.黄金": B.START_RES["黄金"],
        "SPY_COST": mp.SPY_COST,
        "DIPLO_COST": mp.DIPLO_COST,
        "AMOUNTS[3]": E.AMOUNTS[3],                              # ← 坑 3
    }


def _flat(d):
    return {k: (tuple(sorted(v.items())) if isinstance(v, dict) else v) for k, v in d.items()}


class TestScale(unittest.TestCase):
    def tearDown(self):
        scale.restore()

    def test_s1_is_noop(self):
        """S=1 必须逐项等于真值（默认路径不变）。"""
        before = _flat(_snap())
        scale.apply(1)
        self.assertEqual(_flat(_snap()), before)

    def test_every_quantity_scales(self):
        """★坑 1：`energy` 与其余"量"一起 ×S（漏它就是电网凭空充足）。"""
        base = _snap()
        scale.apply(10)
        now = _snap()
        bad = []
        for k, v in base.items():
            mult = 10 ** _DEG_OF[k]          # ★按**次数**：物 ×10、金 ×100
            cur = _flat({k: now[k]})[k]
            want = _flat({k: (v * mult if not isinstance(v, dict)
                              else {kk: vv * mult for kk, vv in v.items()})})[k]
            if cur != want:
                bad.append(f"{k}(次数{_DEG_OF[k]}): {v} → {cur}（应为 {want}）")
        self.assertEqual(bad, [], "有字段没跟着 ×S：\n" + "\n".join(bad))

    def test_market_price_not_scaled(self):
        """★★基准单价**不缩放** —— 这是 2026-09-18 实测定的（用户：「修」）。

        金与物量都 ×S 之后，单价若也 ×S：`物值 = (S·q)(S·p) = S²·qp` 而 `金 = S·G`
        ⇒ **物相对金贵了 S 倍** ⇒ 游戏本身变了，老师/ROI 排序/买不买得起全随 S 变
        （实测：S=10 老师第 4 回合不去建兵营、第 5 回合不征兵）。
        单价不变则 `物值 = (S·q)·p = S·(qp)` 与 `S·G` 同度 ⇒ 整个游戏等比放大。

        **验收实测**（`/tmp/scale_teacher_diff.py`）：改前 S=1/S=10 从第 4 回合起分叉；
        **改后 1~5 回合逐条同决策、数量整 ×10**，第 6 回合起只剩取整残差。
        """
        base = dict(B.MARKET)
        scale.apply(10)
        self.assertEqual({k: v / 10 for k, v in B.MARKET.items()}, base,
                         "MARKET 单价必须 ×S（b/a=S）—— 不缩放则抖动抖不动，缩放错了则物值与金不同度")
        self.assertEqual(B.MARKET_DEPTH["木头"], 24 * 10)

    def test_idempotent(self):
        """幂等：从真值派生，不在上一次结果上再乘。"""
        scale.apply(3)
        scale.apply(10)
        self.assertEqual(_snap()["MARKET_DEPTH.木头"], 24 * 10)       # 物 ×S
        self.assertEqual(_snap()["装备厂.cost"], 210 * 100)           # 金 ×S²
        self.assertEqual(_snap()["装备厂.energy"], 1 * 10)

    def test_restore_clean(self):
        base = _flat(_snap())
        scale.apply(10)
        scale.restore()
        self.assertEqual(_flat(_snap()), base)

    def test_survives_env_reset(self):
        """★坑 2：`env.reset()` 会调 `jitter.apply(ms, 0)`（=restore）——
        若 jitter 的基线没被重抓成缩放后的表，×S 会被悄悄抹掉。"""
        env = E.ZhanguoEnv(map_size=16, max_turns=20, scale=10)
        env.reset(7)
        self.assertEqual(_snap()["MARKET_DEPTH.木头"], 24 * 10, "第一次 reset 后 ×S 就没了")
        env.reset(8)                       # ← 再来一局，最容易在这被抹掉
        self.assertEqual(_snap()["MARKET_DEPTH.木头"], 24 * 10, "第二次 reset 把 ×S 抹掉了")

    def test_env_s1_untouched(self):
        """scale=1 的 env 走一遍 reset，真值不许动。"""
        base = _flat(_snap())
        E.ZhanguoEnv(map_size=16, max_turns=20).reset(7)
        self.assertEqual(_flat(_snap()), base)

    def test_scale_with_jitter_refused(self):
        """缩放×抖动的组合未定义 ⇒ 构造时就炸，别等训练中途。"""
        with self.assertRaises(ValueError):
            E.ZhanguoEnv(map_size=16, max_turns=20, scale=10, rules_jitter=0.1)

    def test_homogeneous(self):
        """★★齐次性：**物值必须与金同度**（都 ×S²）。

        这是整套缩放成立的充要条件（文件头 §一）：`物值 = (S·q)(S·p)`，`金 = S²·G`。
        不成立时游戏本身变了 —— 实测 S=10 老师不建兵营、不征兵（见 `test_market_price_not_scaled`）。
        """
        g1 = B.START_RES["木头"] * B.MARKET["木头"]        # 一个"物值"
        gold1 = B.START_RES["黄金"]
        scale.apply(10)
        g10 = B.START_RES["木头"] * B.MARKET["木头"]
        gold10 = B.START_RES["黄金"]
        self.assertEqual(g10 / g1, gold10 / gold1,
                         f"物值 ×{g10/g1} 而金 ×{gold10/gold1} —— 不同度，游戏变了")

    def test_reward_is_scale_invariant(self):
        """★★用户 2026-09-18 点出来的：「缩放也会让奖励变大，你考虑过吗」。

        `reward = Δ消费 × reward_scale`，而缩放把**消费整体 ×S** ⇒ 奖励跟着 ×S；
        可 `--invalid-penalty` 是**配置常量**（以 S=1 的"消费"为单位）**不跟着放大**
        ⇒ 等于偷偷把学习率 ×S、把惩罚 ÷S，所有按 S=1 标定的超参全部失效。

        判据（决定性）：**同一个消费增量**在 S=1 与 S=10 下必须给出**同样**的 reward，
        且惩罚那项**不随 S 变**。逐项对公式，两个 S 都验。
        """
        for S in (1, 10):
            env = E.ZhanguoEnv(map_size=16, max_turns=20, scale=S, invalid_penalty=20)
            env.reset(7)
            obs = env._obs()
            before = env.world.spend_total(env.agent)
            _o2, r, _d, info = env.step(obs.cand["actions"][0])
            delta_units = (info["spend_total"] - before) / (S * S)   # 消费是**金** ⇒ ÷S²
            want = delta_units * env.reward_scale - (0.0 if info["ok"]
                                                     else 20 * env.reward_scale)
            self.assertAlmostEqual(
                r, want, places=9,
                msg=f"S={S}：reward={r} 但按「消费÷S、惩罚原样」应为 {want}"
                    f"（差 {(r - want) / max(abs(want), 1e-12):.1%}）")

    def test_spend_units(self):
        """换算函数本身：消费是**金** ⇒ ÷S²（不是 ÷S）。"""
        self.assertEqual(E.ZhanguoEnv(map_size=16, max_turns=10).spend_units(1000), 1000.0)
        e10 = E.ZhanguoEnv(map_size=16, max_turns=10, scale=10)
        self.assertEqual(e10.spend_units(1000), 10.0)   # 金 ⇒ ÷S²

    def test_coverage_report_covers_all_scalars(self):
        """`coverage_report` 必须把 balance 里**每个**模块级标量都归到"缩放/不缩放"之一
        —— 这是防漏项的那道可见守卫，漏一个字段它就抓不到了。"""
        r = scale.coverage_report()
        got = {n for n, d in r["scalars"].items() if d != "不缩放"} | \
              {n for n, d in r["scalars"].items() if d == "不缩放"}
        want = {n for n in dir(B)
                if not n.startswith("_")
                and isinstance(getattr(B, n), (int, float))
                and not isinstance(getattr(B, n), bool)}
        self.assertEqual(got, want)
        self.assertEqual({n for n, d in r["scalars"].items() if d == 2},
                         {"SPY_COST", "DIPLO_COST", "DIPLO_CENTER_MIN_COST",
                          "LETTER_COST", "LETTER_COST_ALLY", "LETTER_CENTER_DISCOUNT",
                          "LETTER_COST_MIN"})

    def test_known_gap_is_registered(self):
        """★**已登记的已知缺口**：`LETTER_*` 是金量、但没进缩放。

        它们只在外交信件里花，而本 RL 线是**单国独局、外交一次都不触发**（分支契约），
        所以对本线无影响 —— 但它是真缺口，**在这里登记**，免得日后（a）被当成新 bug 重查，
        或（b）无人知道而悄悄上线。
        真要开外交线，先把这几个加进 `_SCALARS` 并重跑本文件。
        """
        r = scale.coverage_report()
        for n in ("LETTER_COST", "LETTER_COST_ALLY", "LETTER_CENTER_DISCOUNT",
                  "LETTER_COST_MIN"):
            self.assertEqual(r["scalars"][n], 2,
                             f"{n} 是金量，必须按次数 2 缩放（2026-09-18 已补齐）")


if __name__ == "__main__":
    unittest.main()
