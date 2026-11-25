"""多座位回合制环境：每个座位要么是脚本对手（callable），要么由外部（学习者）驱动。

流程（与 main 的 `cycle` 一致）：`begin_turn` → 各座位**按座位序**依次行动 → `resolve_turn`。
学习者座位的动作直接调 `env.world` 的方法（或之后的动作接口）；学完调用 `end_turn(seat)`。
"""
from __future__ import annotations

import math
import random
from typing import Callable

import mp
from .score import power_score

Opponent = Callable[[mp.World, str, random.Random], None]


def ring_starts(size: int, n: int) -> list[tuple[int, int]]:
    """起点与新引擎一致：以图心为圆心、半径 0.31×size 的圆上等分（第一个在正上方）。"""
    c, r = size / 2.0, size * 0.31
    out = []
    for i in range(n):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        out.append((int(math.floor(c + r * math.cos(ang) + 0.5)), int(math.floor(c + r * math.sin(ang) + 0.5))))
    return out


class Env:
    def __init__(self, size: int = 13, n_nations: int = 2, max_turns: int = 300,
                 opponents: dict[int, Opponent] | None = None, seed: int = 0, bank: bool = True):
        self.size, self.n, self.max_turns = size, n_nations, max_turns
        self.opponents = dict(opponents or {})         # 座位 → 脚本；缺的座位 = 外部驱动
        self.seed = seed
        self.bank = bank                               # 世界央行：main 0.2 默认发行版恒开（初始利率 0）
        self.world: mp.World | None = None
        self.names = [f"国{i}" for i in range(n_nations)]
        self.done = False
        self._seat = 0                                 # 本回合下一个待行动的座位
        self._rngs: list[random.Random] = []
        self._dcache: dict = {}
        self.steps: dict = {}
        self.invalid: dict = {}

    # ---- 生命周期 ----
    def reset(self, seed: int | None = None) -> "Env":
        if seed is not None:
            self.seed = seed
        starts = dict(zip(self.names, ring_starts(self.size, self.n)))
        self.world = mp.World(size=self.size, seed=self.seed, nations=self.names, starts=starts,
                              max_turns=self.max_turns)
        if self.bank:
            self.world.bank_enable()
        self._rngs = [random.Random(f"{self.seed}:opp:{i}") for i in range(self.n)]
        self.done = False
        self._seat = 0
        self._dcache, self.steps, self.invalid = {}, {}, {}
        self.world.begin_turn()
        return self

    def alive_seats(self) -> list[int]:
        return [i for i, n in enumerate(self.names) if n in self.world.nations]

    def seat_name(self, seat: int) -> str:
        return self.names[seat]

    # ---- 推进 ----
    def advance(self) -> int | None:
        """推进到下一个需要外部行动的座位并返回它；对局结束返回 None。
        脚本座位在这里自动行动。"""
        w = self.world
        while not self.done:
            while self._seat < self.n:
                seat = self._seat
                name = self.names[seat]
                if name not in w.nations:
                    self._seat += 1
                    continue
                if seat in self.opponents:
                    self.opponents[seat](w, name, self._rngs[seat])
                    w.end_turn(name)
                    self._seat += 1
                    continue
                return seat
            self._finish_turn()
        return None

    def end_turn(self, seat: int) -> None:
        """外部座位结束行动（含：自动拒绝未回应的邀约、自动交易规则）。"""
        assert seat == self._seat, f"现在轮到座位 {self._seat}，不是 {seat}"
        self.world.end_turn(self.names[seat])
        self._seat += 1

    def _finish_turn(self) -> None:
        w = self.world
        w.resolve_turn()
        alive = self.alive_seats()
        if len(alive) <= 1 or w.turn >= self.max_turns:
            self.done = True
            return
        w.begin_turn()
        self._seat = 0

    # ---- 候选动作（见 rl/candidates.py；不做硬掩码，可执行性是特征）----
    def candidates(self, seat: int, diplomacy: bool = True):
        from .candidates import build_candidates
        cache = self._dcache.setdefault(seat, {})
        return build_candidates(self.world, self.names[seat], diplomacy=diplomacy, cache=cache)

    def act(self, seat: int, cand) -> tuple[bool, str]:
        """执行一个候选。失败照常返回（不是异常），并计入 `invalid` 计数。"""
        from .candidates import apply
        ok, msg = apply(self.world, self.names[seat], cand)
        self.steps[seat] = self.steps.get(seat, 0) + 1
        if not ok:
            self.invalid[seat] = self.invalid.get(seat, 0) + 1
        return ok, msg

    # ---- 终局 ----
    def scores(self) -> list[float]:
        return [power_score(self.world, n) for n in self.names]

    def rewards(self) -> list[float]:
        """r_i = S_i / max_j S_j；已亡国 S=0。只在终局有意义。"""
        s = self.scores()
        top = max(s) or 1.0
        return [x / top for x in s]

    def winner(self) -> int | None:
        """灭国为胜：只剩一个座位存活才有胜者。"""
        alive = self.alive_seats()
        return alive[0] if len(alive) == 1 else None
