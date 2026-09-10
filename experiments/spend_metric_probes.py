# -*- coding: utf-8 -*-
"""消费总量指标的对照实验（E1 攻击性验证 / E2 归属 / E3 朝贡）。

配套文档：`docs/消费总量评测指标论证.md`。三组实验全部确定性（同 seed → 同结果），
不含 LLM——验的是**指标本身的性质**，与被测主体是 LLM 还是脚本无关。

    python3 experiments/spend_metric_probes.py --exp e1      # 四种刷分攻击
    python3 experiments/spend_metric_probes.py --exp e2      # 外购消耗 vs 纯套利
    python3 experiments/spend_metric_probes.py --exp e3      # 朝贡体系
    python3 experiments/spend_metric_probes.py --exp all

复现时最容易踩的坑（E2/E3 构造贸易国/霸权国时要记牢）：
  1. **建筑位门槛按格累计**——兵营要求本地已用建筑位 ≥3，必须在**同一格**连续建造；
     分散建造永远开不出兵营，而没有兵营就征不了兵，"消费能力"无从谈起。
  2. **兵营要 1 点电**——本地或全局电力不足则永久停摆，建了也是白建。
  3. 每地块**每回合限建 1 座**，所以一天最多建出「已拥有地块数」座。
"""
from __future__ import annotations

import argparse
import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from game import BUILDINGS, TRADEABLE  # noqa: E402
from mp import World  # noqa: E402
from expand_rule_v6 import expand_rule_turn_v6  # noqa: E402

# 单回合内最多尝试多少个动作（防死循环，不是游戏规则）
MAX_ACT = 60


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _gold_price(w, good: str) -> float:
    """按当前市价把 1 单位 good 折成金。"""
    return w._mval(good, 1)


def _build_price(w, bn: str) -> float:
    """一座 bn 的**真实总价**（金 + 木折金）——挑"最贵"时要连木一起算。"""
    info = BUILDINGS[bn]
    c = info["cost"]
    gold = max(c) if isinstance(c, list) else c
    return gold + _gold_price(w, "木头") * info.get("wood", 0)


def _max_buyable(w, name: str, good: str, cap: int = 400) -> int:
    """在当前市价下用国库能买得起的最大数量（价格随单量走高，故线性探到买不动为止）。"""
    gold = w.res(name, "黄金")
    best = 0
    for n in range(1, cap + 1):
        _unit, total = w.market_quote(good, n, "buy")
        if total > gold:
            break
        best = n
    return best


def _buy_all(w, name: str, goods=TRADEABLE, cap: int = 400) -> bool:
    """把能买的都买了（囤积用）。"""
    did = False
    for g in goods:
        n = _max_buyable(w, name, g, cap)
        if n > 0:
            ok, _ = w.buy(name, g, n)
            did = did or ok
    return did


def _try_build(w, name: str, order: list[str]) -> bool:
    """按 order 给定的优先级，在任意自有地块上试建成功一座就返回。

    优先用**空位多**的地块（凑建筑位门槛要往同一格堆），再按坐标稳定排序，
    保证同 seed 可复现。
    """
    tiles = sorted(w.own_tiles(name),
                   key=lambda p: (-sum(w.tiles[p]["buildings"].values()), p))
    for bn in order:
        for (x, y) in tiles:
            ok, _ = w.build(name, x, y, bn)
            if ok:
                return True
    return False


# 兵营的前置：同一格先堆够 3 个建筑位（**按格累计**，分散建永远开不出来）。
# 凑位用瞭望塔（不挑资源、**不烧燃料**），其中一座换成木材能源厂——兵营要 1 点电，
# 没有电源则永久停摆。**别全用能源厂**：它每回合烧 1 木头，三座能把建兵营的木头烧光
# （实测 60→12，兵营永远建不起来），这是引导阶段最容易踩死的坑。
BOOTSTRAP = ["瞭望塔", "木材能源厂"]


def _barracks_up(w, name: str) -> bool:
    return any(w.tiles[p]["buildings"]["兵营"] or w.tiles[p]["pending"]["兵营"]
               for p in w.own_tiles(name))


def _ensure_wood(w, name: str, need: int) -> None:
    """木头不够就去市场补——买卖本身不计入指标，买来的木头用掉才计（这是合法路径）。"""
    short = need - w.res(name, "木头")
    if short > 0:
        n = _max_buyable(w, name, "木头", cap=max(short, 1) * 3)
        if n > 0:
            w.buy(name, "木头", n)


def _bootstrap_step(w, name: str) -> bool:
    """往**同一格**堆建筑，够 3 位就开兵营。每地块每回合限建 1 座（引擎限制），
    所以这是个跨回合的过程——调用方每回合调一次，返回 True 表示兵营已到位。
    """
    if _barracks_up(w, name):
        return True
    tile = sorted(w.own_tiles(name))[0]          # 固定一格，别换
    x, y = tile
    slots = (sum(w.tiles[tile]["buildings"].values())
             + sum(w.tiles[tile]["pending"].values()))
    if slots >= 3:
        bn = "兵营"
    else:
        bn = BOOTSTRAP[slots] if slots < len(BOOTSTRAP) else BOOTSTRAP[-1]
    _ensure_wood(w, name, BUILDINGS[bn].get("wood", 0))
    if w.build(name, x, y, bn)[0]:
        return bn == "兵营" and _barracks_up(w, name)
    return False


# --------------------------------------------------------------------------
# E1：四种刷分攻击
# --------------------------------------------------------------------------

def play_v6(w, name, rng):
    """诚实复利基线：项目内的扩张流规则 AI。"""
    expand_rule_turn_v6(w, name, rng, max_actions=10 ** 9)


def play_costly(w, name, rng):
    """攻击 build：合法建筑里永远挑**总价最高**的建。

    预期死法：最贵的建筑（市政厅、外交中心、兵营）都有建筑位门槛，够不着；
    实际只能反复建城堡/瞭望塔这类不产出的东西 → 无产能 → 无收入 → 再也建不起。
    """
    order = sorted(BUILDINGS, key=lambda b: -_build_price(w, b))
    for _ in range(MAX_ACT):
        if not _try_build(w, name, order):
            break


def play_arbitrage(w, name, rng):
    """攻击流通量：低买高卖、反复形式变换，**不消耗任何东西**。

    预期：买进立刻卖回，只损失价差；使用价值从未被消耗 → 定义性地 0 分。
    """
    for _ in range(MAX_ACT):
        did = False
        for g in TRADEABLE:
            n = _max_buyable(w, name, g, cap=60)
            if n > 0:
                ok, _ = w.buy(name, g, n)
                if ok:
                    w.sell(name, g, n)
                    did = True
        if not did:
            break


def play_hoard(w, name, rng):
    """攻击持有量：疯狂买入囤积，永不消耗。"""
    for _ in range(MAX_ACT):
        if not _buy_all(w, name):
            break


def play_recruit_spam(w, name, rng):
    """攻击 recruit + supply：先搭出兵营，之后疯狂征兵；粮/装备靠买，养不起就饿死，死了再征。

    **搭兵营期间不许花别的钱**——先去买粮会把搭兵营的本钱抽干，那就不叫"攻击失败"，
    而叫"我没让它攻击"。
    """
    if not _bootstrap_step(w, name):
        return
    for _ in range(MAX_ACT):
        _buy_all(w, name, ("粮食", "装备"), cap=200)
        did = False
        for (x, y) in sorted(w.own_tiles(name)):
            if w.recruit(name, x, y, 1, "步")[0]:
                did = True
                break                    # 一回合先征一支，剩下的动作留给下一轮买粮
        if not did:
            break


E1_STRATS = {"v6": play_v6, "costly": play_costly, "arbitrage": play_arbitrage,
             "hoard": play_hoard, "recruit_spam": play_recruit_spam}


def run_solo(strat, seed: int, size: int, turns: int):
    w = World(size=size, seed=seed, nations=["秦"])
    w.begin_turn()
    rng = random.Random(seed)
    for t in range(turns):
        E1_STRATS[strat](w, "秦", rng)
        w.resolve_turn()
        if t + 1 < turns:
            w.begin_turn()
    return {"消费": w.spend_total("秦"),
            "建造": w._spend("秦")["build"],
            "征兵": w._spend("秦")["recruit"],
            "军费": w._spend("秦")["supply"],
            "领土": len(w.own_tiles("秦")),
            "军队": len(w.nation_armies("秦"))}


def exp_e1(size: int, turns: int, seeds: list[int]) -> None:
    print(f"\n{'=' * 100}\nE1 攻击性验证  {size}×{size} · {turns} 回合 · 单国 · seed {seeds}\n{'=' * 100}")
    print("消费按**中位**报（跨图方差极大，均值不稳，实测 v6 在 20 个 seed 上从 13k 到 195k）。")
    print(f"\n{'策略':<13}{'消费·中位':>11}  {'各 seed(k)':<40}{'建造':>9}{'征兵':>8}{'军费':>9}"
          f"{'领土':>7}{'军队':>7}{'相对v6':>9}")
    base_med = base_min = None
    collected: dict[str, list[dict]] = {}
    for strat in E1_STRATS:
        rows = [run_solo(strat, s, size, turns) for s in seeds]
        collected[strat] = rows
        cons = sorted(r["消费"] for r in rows)
        med = st.median(cons)
        if strat == "v6":
            base_med, base_min = med, min(cons)
        rel = f"{med / base_med:>8.1%}" if base_med else ""
        seed_str = " ".join(f"{c / 1000:.1f}" for c in cons)
        print(f"{strat:<13}{med:>11,.0f}  {seed_str:<40}"
              f"{st.mean(r['建造'] for r in rows):>9,.0f}"
              f"{st.mean(r['征兵'] for r in rows):>8,.0f}"
              f"{st.mean(r['军费'] for r in rows):>9,.0f}"
              f"{st.mean(r['领土'] for r in rows):>7.1f}"
              f"{st.mean(r['军队'] for r in rows):>7.1f}{rel}")

    # 不依赖误差棒的判据：攻击**最好的一次**，够得着基线**最差的一次**吗？
    print(f"\n判据（绕开误差棒）：诚实基线最差的一次是 {base_min:,.0f}")
    for strat in E1_STRATS:
        if strat == "v6":
            continue
        best = max(r["消费"] for r in collected[strat])
        print(f"  {strat:<14} 最好一次 {best:>9,.0f}   = 基线最差一次的 {best / base_min:>6.1%}")


# --------------------------------------------------------------------------
# E2：归属实验
# --------------------------------------------------------------------------

def play_trade(w, name, rng, *, buy_supply: bool, arbitrage: bool):
    """不事生产的贸易国：只建到能开兵营的最低限，其余全靠外购。

    三个变体只差在补给上：买来吃掉（真实消耗外购物资）/ 不买（军队饿死）/
    买了立刻卖回（纯套利，从未消耗）。
    """
    if arbitrage:
        play_arbitrage(w, name, rng)      # 纯套利：不建任何东西，也不消耗任何东西
        return
    if not _bootstrap_step(w, name):
        return                            # 兵营没搭起来之前不花别的钱
    for _ in range(MAX_ACT):
        # 能源厂每回合烧木头，断粮则电力归零、兵营停摆——所以**每回合都得先补木头**，
        # 否则后面买再多粮和补给也征不出兵，实验会假阴（这是最容易踩的坑）。
        _ensure_wood(w, name, 8)
        if buy_supply:
            _buy_all(w, name, ("补给",), cap=100)     # 军粮优先，先于征兵原料
        _buy_all(w, name, ("粮食", "装备"), cap=100)
        if not any(w.recruit(name, x, y, 1, "步")[0]
                   for (x, y) in sorted(w.own_tiles(name))):
            break


def exp_e2(size: int, turns: int, seeds: list[int]) -> None:
    print(f"\n{'=' * 78}\nE2 归属实验  {size}×{size} · {turns} 回合 · "
          f"秦(生产国 v6) + 商(贸易国) · seed {seeds}\n{'=' * 78}")
    variants = [("买补给并吃掉", dict(buy_supply=True, arbitrage=False)),
                ("不买补给(军队饿死)", dict(buy_supply=False, arbitrage=False)),
                ("纯套利(买了立刻卖回)", dict(buy_supply=False, arbitrage=True))]
    print(f"{'变体':<22}{'建造':>10}{'征兵':>10}{'军费':>10}{'商·合计':>10}{'秦·消费':>12}")
    for label, kw in variants:
        rows = []
        for s in seeds:
            w = World(size=size, seed=s, nations=["秦", "商"])
            w.begin_turn()
            rng = random.Random(s)
            for t in range(turns):
                expand_rule_turn_v6(w, "秦", rng, max_actions=10 ** 9)
                play_trade(w, "商", rng, **kw)
                w.resolve_turn()
                if t + 1 < turns:
                    w.begin_turn()
            sp = w._spend("商")
            rows.append({"建造": sp["build"], "征兵": sp["recruit"], "军费": sp["supply"],
                         "合计": w.spend_total("商"), "秦": w.spend_total("秦")})
        avg = {k: st.mean(r[k] for r in rows) for k in rows[0]}
        print(f"{label:<22}{avg['建造']:>10,.0f}{avg['征兵']:>10,.0f}"
              f"{avg['军费']:>10,.0f}{avg['合计']:>10,.0f}{avg['秦']:>12,.0f}")
        for s, r in zip(seeds, rows):
            print(f"    seed {s}: 商 建造 {r['建造']:>7,.0f} 征兵 {r['征兵']:>6,.0f} "
                  f"军费 {r['军费']:>6,.0f} 合计 {r['合计']:>8,.0f}")


# --------------------------------------------------------------------------
# E3：朝贡体系
# --------------------------------------------------------------------------

TRIBUTE_RATE = 0.40          # 附庸每回合上缴「当前存量」的比例（保留 60% 自用）


def play_consume(w, name, rng):
    """不事生产的纯消费者：把到手的一切（含刚收到的贡品）换成能**花掉**的东西。

    贡品是**货物不是金**，所以第一步必须先卖货换金——这正是"价值要经过流通才能被
    实现"的字面演示。卖完再搭兵营、征兵、建最贵的东西，总之把金花出去。
    这是 §2.3「推论一：来源中立」的直接检验：完全不生产，消费能不能很高？
    """
    for g in TRADEABLE:
        n = w.res(name, g) - (30 if g in ("粮食", "补给") else 0)   # 留点口粮别饿死
        if n > 0:
            w.sell(name, g, n)
    if not _bootstrap_step(w, name):
        return
    for _ in range(MAX_ACT):
        _ensure_wood(w, name, 8)
        _buy_all(w, name, ("粮食", "装备"), cap=50)
        if any(w.recruit(name, x, y, 1, "步")[0] for (x, y) in sorted(w.own_tiles(name))):
            continue
        if not _try_build(w, name, sorted(BUILDINGS, key=lambda b: -_build_price(w, b))):
            break


def pay_tribute(w, frm: str, to: str) -> None:
    """按存量比例上缴：对每种可交易品，送走 floor(存量 × TRIBUTE_RATE)。"""
    for g in TRADEABLE:
        n = int(w.res(frm, g) * TRIBUTE_RATE)
        if n > 0:
            w.gift(frm, to, g, n)


def exp_e3(size: int, turns: int, seeds: list[int]) -> None:
    print(f"\n{'=' * 78}\nE3 朝贡实验  {size}×{size} · {turns} 回合 · "
          f"霸 + 附1/附2 · 上缴率 {TRIBUTE_RATE:.0%} · seed {seeds}\n{'=' * 78}")
    scenes = [("no_tribute", False, False),   # 霸权不生产；附庸不朝贡
              ("tribute", False, True),       # 霸权不生产；附庸朝贡
              ("autarky", True, False),       # 霸权生产；附庸不朝贡
              ("heg_tribute", True, True)]    # 霸权生产；附庸朝贡
    print(f"{'场景':<14}{'霸权消费':>11}{'霸权领土':>10}{'霸权军队':>10}"
          f"{'附庸总消费':>12}{'系统总量':>11}")
    for label, heg_produces, tribute in scenes:
        rows = []
        for s in seeds:
            w = World(size=size, seed=s, nations=["霸", "附1", "附2"])
            w.begin_turn()
            rng = random.Random(s)
            for t in range(turns):
                if heg_produces:
                    expand_rule_turn_v6(w, "霸", rng, max_actions=10 ** 9)
                else:
                    play_consume(w, "霸", rng)      # 不生产，但把手里的都花掉
                expand_rule_turn_v6(w, "附1", rng, max_actions=10 ** 9)
                expand_rule_turn_v6(w, "附2", rng, max_actions=10 ** 9)
                if tribute:
                    pay_tribute(w, "附1", "霸")
                    pay_tribute(w, "附2", "霸")
                w.resolve_turn()
                if t + 1 < turns:
                    w.begin_turn()
            alive = [n for n in ("附1", "附2") if n in w.nations]
            rows.append({"霸权": w.spend_total("霸") if "霸" in w.nations else 0.0,
                         "领土": len(w.own_tiles("霸")),
                         "军队": len(w.nation_armies("霸")),
                         "附庸": sum(w.spend_total(n) for n in alive),
                         "系统": sum(w.spend_total(n) for n in w.nations),
                         "存活": len(alive)})
        avg = {k: st.mean(r[k] for r in rows) for k in rows[0]}
        print(f"{label:<14}{avg['霸权']:>11,.0f}{avg['领土']:>10.1f}{avg['军队']:>10.1f}"
              f"{avg['附庸']:>12,.0f}{avg['系统']:>11,.0f}")
        for s, r in zip(seeds, rows):
            print(f"    seed {s}: 霸权 {r['霸权']:>8,.0f}  附庸 {r['附庸']:>8,.0f}  "
                  f"系统 {r['系统']:>8,.0f}  附庸存活 {r['存活']}/2")


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="消费总量指标的对照实验")
    ap.add_argument("--exp", default="all", choices=("e1", "e2", "e3", "all"))
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--e1-size", type=int, default=16)
    ap.add_argument("--e1-turns", type=int, default=300)
    ap.add_argument("--e2-size", type=int, default=20)
    ap.add_argument("--e2-turns", type=int, default=250)
    ap.add_argument("--e3-size", type=int, default=24)
    ap.add_argument("--e3-turns", type=int, default=200)
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    if args.exp in ("e1", "all"):
        exp_e1(args.e1_size, args.e1_turns, seeds)
    if args.exp in ("e2", "all"):
        exp_e2(args.e2_size, args.e2_turns, seeds)
    if args.exp in ("e3", "all"):
        exp_e3(args.e3_size, args.e3_turns, seeds)
    print()


if __name__ == "__main__":
    main()
