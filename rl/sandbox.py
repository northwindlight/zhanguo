# -*- coding: utf-8 -*-
"""8×8 沙盒：**攻取国祚**（两国）。★ **军事动作全部嫁接 `v11plus` 的军事层**。

    用户 2026-09-24 的设计
    ──────────────────────
    · 地图 8×8，两国（甲 / 乙），**核心放对角**（`starts`）
    · 开局各 **5 个步兵**，摆在自己核心格（市政厅所在格）
    · **补员**：上限 `BASE_CAP + 国土数 // TILES_PER_CAP`；**每 5 回合触发一次**，
      补**足到上限**（不是每次 +1，不无限加兵）
    · **终局**：一方拿到对手的市政厅 ⇒ 立即结束；兜底 `T_MAX`
    · **奖励**：赢 ⇒ `1 − turns / T_MAX`（时间越短越高）；输 ⇒ −1；平 ⇒ 0

    ★★ 为什么嫁接（用户：「这个很难写，建议嫁接 v11plus 强大的军事层」）
    ──────────────────────────────────────────────────────────────
    沙盒**不自己写任何军事逻辑**（合法性判定 / 撞墙 / 寻路 / 进攻原子性），
    全部交给 `ruleai/v11plus/military.run` —— 它已经处理好了：
      · 落点一律从 `world._reachable`（引擎合法集）里挑 ⇒ **结构上不可能撞墙**
      · `attack` 是**原子**的（任一支到不了就整通全废）⇒ 它出手前**逐支复核**
      · 编组 / 难度判定 / 多回合代价场 —— 都是打磨过的
    只跑**军事层**，**不跑 `economy.run`**（沙盒里兵是白给的，经济层整个不进）。

    ★ `military.run` 返回 `ledger.acts` = `[(tool, args, ok, msg), …]`
      ⇒ **同一趟跑下来，既是执行、也是 BC 的训练标签**。见 `bc_data()` / `rollout()`。

    引擎口径（实测 2026-09-24，详见 `rl/PLAN.md` §3.2）
    ────────────────────────────────────────────────
    · `World(size=8, nations=[…], starts={…})` 生效；核心十字各 **5 格**（(1,1)/(6,6) 对角最远）
    · ★ **野人必须保留**：引擎的"可攻"判定 `_atk_target_ok` 要求**格上有驻军**，
      无主空格**不算**可攻 ⇒ 清掉野人 = **军队出不了自家十字**（实测过）。
      野人在本沙盒里就是**扩张成本**：8×8 上 54 个，每格一支。
    · 动作接口 **0-based**：`world.move/attack(name, …, x, y)`；而 `ledger.acts` 里
      的 `args` 是**给 LLM 看的 1-based**（`cell[0] + 1`）—— 两边别混。
    · ★ **开局必须宣战**：v11plus 候选池的源③ 是"**交战**敌国领土"，
      不宣战 ⇒ 对手领土一格都不进候选池 ⇒ 它根本不会朝对手走。
"""
from __future__ import annotations

from game import unit_max_hp

# ---------------------------------------------------------------- 规格常量
PLAYERS = ("甲", "乙")
STARTS = {"甲": (1, 1), "乙": (6, 6)}   # ★ 对角、最远；十字各 5 格完整（(0,0) 会缺两臂）
T_MAX = 200           # 兜底上限（只防僵局）
BASE_CAP = 5          # 补员上限基础值（= 开局兵数，自洽）
TILES_PER_CAP = 10    # 每控制这么多格国土，补员上限 +1
RESUPPLY_EVERY = 5    # 每几回合触发一次补员
UNLIMITED = 10 ** 9   # 动作额度（引擎早已删掉"看海 12 个"那条上限）


class Sandbox:
    """一局 8×8 攻取国祚。**规则归沙盒、动作归 v11plus**。"""

    def __init__(self, seed: int = 0, size: int = 8, t_max: int = T_MAX,
                 war: bool = True):
        self.seed = seed
        self.size = size
        self.t_max = t_max
        self.war = war                    # 开局是否宣战（★不宣战 v11plus 不会进攻）
        self.world = None
        self.turn = 0
        self.log: list[str] = []

    # ============================================================ 建局
    def reset(self) -> "Sandbox":
        from mp import World
        from ruleai.v11plus import grouping
        w = World(size=self.size, seed=self.seed, nations=list(PLAYERS), starts=dict(STARTS))
        w.max_turns = self.t_max
        self.world = w
        self.turn = 0
        self.log = []
        if self.war:
            w.declare_war(PLAYERS[0], PLAYERS[1])
        for name in PLAYERS:
            # ★ 沙盒**不管经济** ⇒ 补给必须管够：引擎每回合收军粮（步1/骑2），
            #   断粮则**每军扣 HP**、扣到 0 饿毙。资源给 0 的话军队是**饿死**的不是战死的
            #   （实测踩过：无人交战却每回合稳定掉 35 hp，全灭后靠补员复活 ⇒ 死循环）。
            w.nations[name].res["补给"] = 10 ** 6
            self.spawn(name, BASE_CAP)
        # ★ 编组状态是**模块内存**：每局开始必须清，否则上一局的编组漏进来
        #   （`ruleai/v11plus/__init__.py` 明文要求）
        grouping.clear()
        return self

    def spawn(self, name: str, n: int) -> None:
        """在**核心格**摆 `n` 支步兵。

        ★ 别用 `own_tiles[0]` —— 那是字典序第一格（实测 (3,2) 之类），**不是核心**。
        """
        core = self.core_of(name)
        if core is None:
            return
        x, y = core
        for _ in range(n):
            gid, seq = self.world._new_army(name)
            self.world.armies.append({
                "id": seq, "gid": gid, "name": f"{name}{seq}", "type": "步",
                "hp": unit_max_hp({"type": "步"}), "x": x, "y": y,
                "owner": name, "moved_turn": -1, "engaged": False,
            })

    # ============================================================ 查询
    def core_of(self, name: str) -> tuple[int, int] | None:
        """该国的**市政厅格**（= 核心 = 国祚）。`None` ⇒ 已亡。"""
        for cell, t in sorted(self.world.tiles.items()):
            if t["owner"] == name and t["buildings"].get("市政厅", 0) > 0:
                return cell
        return None

    def armies_of(self, name: str) -> list[dict]:
        return [a for a in self.world.armies if a["owner"] == name and a.get("hp", 0) > 0]

    def tiles_of(self, name: str) -> int:
        return sum(1 for t in self.world.tiles.values() if t["owner"] == name)

    def cap_of(self, name: str) -> int:
        """补员上限 = `BASE_CAP + 国土数 // TILES_PER_CAP`。"""
        return BASE_CAP + self.tiles_of(name) // TILES_PER_CAP

    def alive(self, name: str) -> bool:
        return name in self.world.nations and self.world.has_townhall(name)

    def done(self) -> bool:
        if not (self.alive(PLAYERS[0]) and self.alive(PLAYERS[1])):
            return True
        return self.turn >= self.t_max

    def winner(self) -> str | None:
        """`None` = 平局 / 超时。"""
        a, b = PLAYERS
        if not self.alive(b):
            return a
        if not self.alive(a):
            return b
        return None

    def reward(self, for_player: str) -> float:
        """★ 赢 ⇒ `1 − turns / T_MAX`（时间越短越高）；输 ⇒ −1；平 ⇒ 0。"""
        win = self.winner()
        if win is None:
            return 0.0
        if win != for_player:
            return -1.0
        return 1.0 - self.turn / self.t_max

    # ============================================================ ★ 嫁接点
    def ai_turn(self, name: str) -> list:
        """**跑 v11plus 的军事层**（不跑经济层），返回动作表 `ledger.acts`。

        返回的 `[(tool, args, ok, msg), …]` **同时就是 BC 的标签**：
        `tool ∈ {"move","attack"}`，`args` 里带 `army_id` / `army_ids` / `x` / `y`（**1-based**）。
        """
        from ruleai.v11plus import military
        from ruleai.v11plus.ledger import Ledger
        if not self.alive(name):
            return []
        ledger = Ledger(self.world, name, UNLIMITED)
        military.run(ledger, self.world, name)
        return ledger.acts

    # ============================================================ 回合推进
    def resupply(self) -> list[str]:
        """每 `RESUPPLY_EVERY` 回合**触发一次**：把每国军队**补足到上限**。"""
        notes = []
        if self.turn == 0 or self.turn % RESUPPLY_EVERY != 0:
            return notes
        for name in PLAYERS:
            if not self.alive(name):
                continue
            cap = self.cap_of(name)
            gap = cap - len(self.armies_of(name))
            if gap > 0:
                self.spawn(name, gap)
                notes.append(f"{name} 补员 +{gap}（上限 {cap}）")
        return notes

    def end_turn(self) -> None:
        """双方都行动完 ⇒ 引擎结算 → **开下一回合** → 补员。

        ★ **`begin_turn()` 不能漏**：`world.turn += 1` 在 `begin_turn`（`mp.py:2597`）里，
          **不在 `resolve_turn`** 里。漏了它 ⇒ `world.turn` 永远是 0 ⇒ 每支军的
          `moved_turn == world.turn` 恒成立 ⇒ `military.run` 认为"本回合都动过了"
          ⇒ **全军一步不走**（实测踩过：30 回合原地不动，无任何动作）。
          引擎的回合模型是 `begin_turn → 各国行动 → resolve_turn`。
        """
        self.world.resolve_turn()
        self.world.begin_turn()
        self.turn = self.world.turn          # ★ 与引擎同步，别自己数（免得漂）
        for note in self.resupply():
            self.log.append(f"[T{self.turn}] {note}")

    # ============================================================ 整局
    def rollout(self, verbose: bool = False) -> dict:
        """**双方都由 v11plus 军事层驱动**打完整局。

        这一个函数干两件事：① 验证沙盒能跑通并正确判定胜负；
        ② 生成 BC 数据 —— 每回合每方的 `ledger.acts` 就是标签。
        """
        turns = []
        while not self.done():
            for name in PLAYERS:
                acts = self.ai_turn(name)
                if acts:
                    turns.append((self.turn, name, acts))
                if verbose and acts:
                    ok = sum(1 for _, _, good, _ in acts if good)
                    self.log.append(f"[T{self.turn}] {name} {len(acts)} 个动作（成功 {ok}）")
            self.end_turn()
            if self.turn > self.t_max + 5:        # 保险丝：不该走到这
                break
        return {"turns": self.turn, "winner": self.winner(),
                "actions": turns, "log": self.log}

    # ============================================================ BC 数据
    def bc_rows(self, acts: list) -> list[dict]:
        """把 `ledger.acts` 转成 BC 样本行（**这里先只落"动作 + 成功与否"**）。

        ⚠ 观测还没接（那是任务 2/分词器的事）—— 现在先把"标签"定型：
        `args` 是 **1-based**（给 LLM 的写法），落库时统一转 **0-based** 免得下游踩。
        """
        rows = []
        for tool, args, ok, msg in acts:
            if not ok:
                continue                       # 只学成功动作（被拒的是废动作）
            row = {"tool": tool, "ok": ok, "msg": msg}
            if "x" in args and "y" in args:
                row["x"] = int(args["x"]) - 1
                row["y"] = int(args["y"]) - 1
            if "army_id" in args:
                row["army_id"] = int(args["army_id"])
            if "army_ids" in args:
                row["army_ids"] = [int(i) for i in args["army_ids"]]
            rows.append(row)
        return rows


# ================================================================ 自测
def _playout(seed: int = 0, verbose: bool = True) -> dict:
    sb = Sandbox(seed=seed).reset()
    if verbose:
        print(f"建局：核心 {{'甲': {sb.core_of('甲')}, '乙': {sb.core_of('乙')}}}"
              f"  tiles={len(sb.world.tiles)}"
              f"  野人={sum(1 for a in sb.world.armies if a['owner'] == '野人')}"
              f"  步兵={[len(sb.armies_of(n)) for n in PLAYERS]}"
              f"  交战={sb.world.war_between(*PLAYERS)}")
    out = sb.rollout(verbose=verbose)
    if verbose:
        print(f"结果：{out['turns']} 回合  winner={out['winner']}"
              f"  奖励 甲={sb.reward('甲'):+.3f} 乙={sb.reward('乙'):+.3f}")
        for n in PLAYERS:
            print(f"  {n}: 国土 {sb.tiles_of(n)}  军队 {len(sb.armies_of(n))}"
                  f"  上限 {sb.cap_of(n)}  国祚 {sb.alive(n)}  核心 {sb.core_of(n)}")
        n_acts = sum(len(a) for _, _, a in out["actions"])
        print(f"  动作总数 {n_acts}")
        for line in out["log"][-6:]:
            print("   ", line)
    return out


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    for s in range(n):
        print(f"─── 种子 {s} ───")
        _playout(seed=s)