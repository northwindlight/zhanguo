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


def _snap():
    """一张"量"的抽样快照：容器里的几种、标量、以及 env 侧的量档。"""
    return {
        "MARKET.木头": B.MARKET["木头"],
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
            # 两边都走 `_flat` 归一成同一形状再比（dict → tuple(sorted(items))）
            cur = _flat({k: now[k]})[k]
            want = _flat({k: (v * 10 if not isinstance(v, dict)
                              else {kk: vv * 10 for kk, vv in v.items()})})[k]
            if cur != want:
                bad.append(f"{k}: {v} → {cur}（应为 {want}）")
        self.assertEqual(bad, [], "有字段没跟着 ×S：\n" + "\n".join(bad))

    def test_idempotent(self):
        """幂等：从真值派生，不在上一次结果上再乘。"""
        scale.apply(3)
        scale.apply(10)
        self.assertEqual(_snap()["MARKET.木头"], 2 * 10)
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
        self.assertEqual(_snap()["MARKET.木头"], 2 * 10, "第一次 reset 后 ×S 就没了")
        env.reset(8)                       # ← 再来一局，最容易在这被抹掉
        self.assertEqual(_snap()["MARKET.木头"], 2 * 10, "第二次 reset 把 ×S 抹掉了")

    def test_env_s1_untouched(self):
        """scale=1 的 env 走一遍 reset，真值不许动。"""
        base = _flat(_snap())
        E.ZhanguoEnv(map_size=16, max_turns=20).reset(7)
        self.assertEqual(_flat(_snap()), base)

    def test_scale_with_jitter_refused(self):
        """缩放×抖动的组合未定义 ⇒ 构造时就炸，别等训练中途。"""
        with self.assertRaises(ValueError):
            E.ZhanguoEnv(map_size=16, max_turns=20, scale=10, rules_jitter=0.1)

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
            delta_units = (info["spend_total"] - before) / S        # 换回 S=1 单位
            want = delta_units * env.reward_scale - (0.0 if info["ok"]
                                                     else 20 * env.reward_scale)
            self.assertAlmostEqual(
                r, want, places=9,
                msg=f"S={S}：reward={r} 但按「消费÷S、惩罚原样」应为 {want}"
                    f"（差 {(r - want) / max(abs(want), 1e-12):.1%}）")

    def test_spend_units(self):
        """换算函数本身：S 越大，同样的引擎消费换算出的奖励单位越小。"""
        self.assertEqual(E.ZhanguoEnv(map_size=16, max_turns=10).spend_units(1000), 1000.0)
        e10 = E.ZhanguoEnv(map_size=16, max_turns=10, scale=10)
        self.assertEqual(e10.spend_units(1000), 100.0)

    def test_coverage_report_covers_all_scalars(self):
        """`coverage_report` 必须把 balance 里**每个**模块级标量都归到"缩放/不缩放"之一
        —— 这是防漏项的那道可见守卫，漏一个字段它就抓不到了。"""
        r = scale.coverage_report()
        got = set(r["scaled"]) | set(r["not_scaled"])
        want = {n for n in dir(B)
                if not n.startswith("_")
                and isinstance(getattr(B, n), (int, float))
                and not isinstance(getattr(B, n), bool)}
        self.assertEqual(got, want)
        self.assertEqual(set(r["scaled"]), {"SPY_COST", "DIPLO_COST", "DIPLO_CENTER_MIN_COST"})

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
            self.assertIn(n, r["not_scaled"],
                          f"{n} 已被缩放 ⇒ 那是修好了，请把这条测试改成断言它在 scaled 里")


if __name__ == "__main__":
    unittest.main()
