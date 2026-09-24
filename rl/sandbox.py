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

    ★★ 军事层在哪（用户 2026-09-24 的口径：「我让你抄的是 v11plus 的**目标评估**、
    **编组逻辑**，谁让你一个字都不写了，直接拿一个半成品用?」）
    ──────────────────────────────────────────────────────────────
    ⇒ 军事动作走 **`rl/military.py`**：**抄** v11plus 的部件
      （`combat.assess` 目标评估 / `grouping._solve` 编组求解 / `pathfind` 寻路），
      **自己写**决策层（目标池 / 守家 / 侦察 / 出手时机）——
      那几样正是 v11plus 明确留空的（底稿 §十「守土/驻防/撤退」）。
    **不调** `ruleai.v11plus.military.run`（那是"整套拿一个半成品"，它只会平推：
    编组→能打就打→打不了朝目标走一格，既不会守也不会侦察）；
    但它留着当**对照线**：`ai_turn(name, teacher="v11plus")`。

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
END = "end"           # 「本方收手」的哨兵动作（换人 / 结算回合）


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
        self.pending = [n for n in PLAYERS if self.alive(n)]   # 本回合还轮到谁行动
        self.last_ok = True                                    # 上一步是否被引擎接受（进观测）
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

    # ============================================================ 环境接口（给 MCTS / RL）
    def clone(self) -> "Sandbox":
        """试演副本。★ 8×8 上 `deepcopy` 实测 **~1.8 ms** ⇒ MCTS 可以**真实试演**
        （不用搞"记录动作再重放"那套）。"""
        import copy
        sb = Sandbox.__new__(Sandbox)
        sb.seed, sb.size, sb.t_max, sb.war = self.seed, self.size, self.t_max, self.war
        sb.world = copy.deepcopy(self.world)
        sb.turn = self.turn
        sb.log = list(self.log)
        sb.pending = list(self.pending)
        sb.last_ok = self.last_ok
        return sb

    def current_player(self) -> str | None:
        """该谁动（`None` = 本局已结束）。"""
        return self.pending[0] if self.pending else None

    def legal(self) -> list[tuple]:
        """当前玩家的**合法动作**：`(aid, kind, x, y)` + 收手 `(END, None, None, None)`。

        **合法集在这里预先过滤干净**，别指望引擎报错：
        · `move` 的目标从 `world._reachable`（引擎合法集）里拿 ⇒ **结构上不会撞墙**
          （撞墙会 `_blind_cost` 烧掉整队移动额度 —— 那是"废动作"，不该进模型的选择集）
        · `attack` 只收"走不通、但够得着"的格（`_reachable(for_attack=True)` 里、不在 move 集里的）
        · 交战中的军 / 本回合已动过的军，直接不给动作
        """
        name = self.current_player()
        if name is None or not self.alive(name):
            return [(END, None, None, None)]
        out: list[tuple] = []
        for a in self.armies_of(name):
            if a.get("engaged") or a.get("moved_turn") == self.world.turn:
                continue                       # ★ 已用过的军 / 交战中的军：整支屏蔽
            here = (a["x"], a["y"])
            walk = self.world._reachable(name, a, for_attack=False)
            for cell in sorted(walk):
                if cell != here:
                    out.append((a["id"], "move", cell[0], cell[1]))
            for cell in sorted(self.world._reachable(name, a, for_attack=True)):
                if cell not in walk:
                    out.append((a["id"], "attack", cell[0], cell[1]))
            # ★ ③ **无视野的邻格：移动与进攻两条路都给**（用户 2026-09-24）
            #   引擎的规矩是「**敌国领土不能 mv，但允许 atk**」（`_mv_wall` 拒 mv / `attack` 收），
            #   而看不见的时候模型**无从知道那一格是什么** ⇒ 两条都给，让引擎当场判，
            #   并把它那句教学式错误消息（实测原话：「(5,5) 有敌军驻守，不能 mv 过去；
            #   **进攻请用 atk（会交战）**」）当成**侦察的情报来源**。
            for (x, y) in self._probe_cells(name, a, walk):
                out.append((a["id"], "move", x, y))
                out.append((a["id"], "attack", x, y))
        out.append((END, None, None, None))
        return out

    def _probe_cells(self, name: str, a: dict, walk) -> list[tuple]:
        """★ **允许撞墙的试探格**：该军周围（1 格移动力）里**视野外**的格（边界除外）。

        用户 2026-09-24：「**特殊撞墙 mv 允许存在**，在**无视野**的情况下，全部候选集允许
        （边界除外），**按现有的引擎设计返回错误消息**，为模型那里存在敌国领土」。

        ⇒ 看不见的地方**必须让模型摸得到**：摸了，引擎照报错、**额度照烧**
          （`mp.py` 的 `_blind_cost` 原话：「视野外撞墙 → 报错照给、额度照烧，**侦察要付钱**」），
          模型就**从那条错误消息里**学到"那儿走不进去 / 那儿是敌国领土"。
        ★ 把候选卡死在 `_reachable` 上等于**把侦察这条路由封死** —— 模型永远发现不了
          视野外的敌国领土（那正是它要去拔的厅所在的地方）。
        """
        from ruleai.v11plus import pathfind
        mask = pathfind.vision_mask(self.world, name)
        n = self.size
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                x, y = a["x"] + dx, a["y"] + dy
                if not (0 <= x < n and 0 <= y < n):
                    continue                   # ★ 边界除外
                if (x, y) in walk or (x, y) in mask:
                    continue                   # 已给过 / 有视野的引擎已判过
                out.append((x, y))
        return sorted(out)

    def step(self, action: tuple) -> tuple[bool, str]:
        """执行一个动作。`END` = 本方收手 ⇒ 换人；双方都收手 ⇒ 结算 + 开下一回合。"""
        name = self.current_player()
        if name is None:
            return False, "本局已结束"
        aid, kind, x, y = action
        if aid == END:
            self.pending.pop(0)
            if not self.pending:
                self.end_turn()
                self.pending = [n for n in PLAYERS if self.alive(n)]
            self.last_ok = True
            return True, "end"
        if kind == "move":
            ok, msg = self.world.move(name, aid, x, y)
        elif kind == "attack":
            ok, msg = self.world.attack(name, [aid], x, y)
        else:
            ok, msg = False, f"未知动作 {action!r}"
        self.last_ok = bool(ok)
        return ok, msg

    def is_terminal(self) -> bool:
        return self.done()

    # ============================================================ ★ 嫁接点
    def ai_turn(self, name: str, *, teacher: str = "mine", verbose: bool = False) -> list:
        """跑一方的军事回合，返回动作表 `[(tool, args, ok, msg), …]`（`args` 是 **1-based**）。

        `teacher="mine"` ⇒ **`rl/military.py`**（抄 v11plus 的评估/编组/寻路，
        **自己写**目标池与守家/侦察决策）—— 默认。
        `teacher="v11plus"` ⇒ 直接调 `ruleai.v11plus.military.run`，当**对照线**
        （它只会平推：不会守、不侦察）。
        """
        if not self.alive(name):
            return []
        enemy = next((n for n in PLAYERS if n != name), None)
        if teacher == "v11plus":
            from ruleai.v11plus import military as v11
            from ruleai.v11plus.ledger import Ledger
            ledger = Ledger(self.world, name, UNLIMITED)
            v11.run(ledger, self.world, name)
            return ledger.acts
        from . import military as mine
        return mine.run(self.world, name, enemy=enemy, verbose=verbose)

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