"""阶段 0：从规则 AI 的实际动作里采示范（观测, 候选下标），只用来给策略一个"会做合法经济/扩张"的起点。

规则 AI 用 `Ledger.do(tool, args, fn, ...)` 执行动作，`on_action(tool, args)` 在执行**之前**触发：
那一刻编码观测 + 候选，再把规则 AI 的动作映射到候选下标（买卖数量取最近的档）。
"""
from __future__ import annotations

import random

from .candidates import QTY_BUCKETS


def _bucket(q: int) -> int:
    le = [b for b in QTY_BUCKETS if b <= q]
    return max(le) if le else QTY_BUCKETS[0]


def match(tool: str, args: dict, cands: list) -> int | None:
    key = None
    if tool == "build":
        x, y = map(int, args["tile"].split())
        key = ("build", (x - 1, y - 1, args["building"]))
    elif tool == "recruit":
        x, y = map(int, args["tile"].split())
        key = ("recruit", (x - 1, y - 1, args["unit"]))
    elif tool in ("buy", "sell"):
        key = (tool, (args["good"], _bucket(int(args["qty"]))))
    elif tool == "attack":
        key = ("attack", (args["army_ids"][0], args["x"] - 1, args["y"] - 1))
    elif tool == "move":
        key = ("move", (args["army_id"], args["x"] - 1, args["y"] - 1))
    if key is None:
        return None
    for i, c in enumerate(cands):
        if (c.kind, c.args) == key:
            return i
    return None


# 非规则 AI 的老师（W1/W2 = 规则经济 + 直接调引擎的宣战/补征/推进）没有 Ledger 钩子：
# 在 World 类方法上拦截"老师座位、最外层、非 dry"的动作，执行前映射成候选键。
_REC = {"build": lambda a: ("build", (a[0], a[1], a[2])),
        "demolish": lambda a: ("demolish", (a[0], a[1], a[2])),
        "recruit": lambda a: ("recruit", (a[0], a[1], a[3] if len(a) > 3 else "步")),
        "buy": lambda a: ("buy", (a[0], _bucket(int(a[1])))),
        "sell": lambda a: ("sell", (a[0], _bucket(int(a[1])))),
        "move": lambda a: ("move", (a[0], a[1], a[2])),
        "attack": lambda a: ("attack", (list(a[0]), a[1], a[2])),
        "retreat": lambda a: ("retreat", (a[0], a[1], a[2])),
        "declare_war": lambda a: ("declare_war", (a[0],)),
        "offer_peace": lambda a: ("offer_peace", (a[0], a[1], a[2] if len(a) > 2 else 0)),
        "spy": lambda a: ("spy", (a[0],)),
        "bank_sell_report": lambda a: ("buy_report", (a[0],)),
        "share_map": lambda a: ("share_map", (a[0],)),
        "gift": lambda a: ("gift", (a[0], a[1], min((20, 100), key=lambda g: abs(g - int(a[2]))))),
        "declare_guarantee": lambda a: ("guarantee", (a[0],)),
        "cancel_guarantee": lambda a: ("cancel_guarantee", (a[0],)),
        "release_vassal": lambda a: ("release_vassal", (a[0],))}


LAST: dict = {}                                          # 上一局 collect 的统计（映射率、胜者、回合数）


def _match_key(key, cands):
    kind, args = key
    for i, c in enumerate(cands):
        if c.kind != kind:
            continue
        if kind == "attack":                    # 同格成堆进攻：候选按任一军编号给出
            if c.args[0] in args[0] and c.args[1:] == args[1:]:
                return i
        elif c.args == args:
            return i
    return None


def _recording(world, seat, on_key):
    """上下文：World 类方法打补丁，只记 world 实例、seat 座位、最外层、非 dry 的调用。"""
    import contextlib
    import functools
    import mp as _mp
    depth = [0]
    orig = {}

    def wrap(meth, f):
        @functools.wraps(f)
        def g(self, name, *args, **kw):
            rec = self is world and name == seat and depth[0] == 0 and not kw.get("dry")
            if rec:
                on_key(_REC[meth](args))
            depth[0] += 1
            try:
                return f(self, name, *args, **kw)
            finally:
                depth[0] -= 1
        return g

    @contextlib.contextmanager
    def cm():
        for m in _REC:
            orig[m] = getattr(_mp.World, m)
            setattr(_mp.World, m, wrap(m, orig[m]))
        try:
            yield
        finally:
            for m, f in orig.items():
                setattr(_mp.World, m, f)
    return cm()


def collect(seed: int, size: int = 13, max_turns: int = 120, opp: str = "rule", teacher: str = "rule",
            teacher_seat: int = 0):
    """一局示范：座位 teacher_seat = 老师（被录），另一座 = 对手。返回 (decs, xs, A, Mk, reward, turns)。
    teacher="rule" 走规则 AI 的 Ledger 钩子；"w1"/"w1b"/"w2"/"w3" 走 World 方法拦截（好战老师，局里有灭国胜负）。
    镜像对局（w3 打 w3）用 `collect_seats(..., seats=(0, 1))` 两边同时录、只留赢家，省一半对局。"""
    r = collect_seats(seed, size, max_turns, opp, teacher, seats=(teacher_seat,))[0]
    LAST.update(r["info"])
    return r["decs"], r["xs"], r["A"], r["Mk"], r["reward"], r["turns"]


def collect_seats(seed: int, size: int = 13, max_turns: int = 120, opp: str = "rule", teacher: str = "rule",
                  seats=(0,)):
    """录 `seats` 里的每个座位（都由 teacher 驱动）；其余座位由 opp 驱动。每个被录座位返回一份 dict。
    镜像（teacher==opp, seats=(0,1)）与分两局各录一边是同一局面分布，但一局得到两份轨迹。"""
    import ruleai.v11plus.grouping as g
    from .env import Env
    from .obs import Observer
    from .opponents import make_w1, make_w2, rule_turn
    import rule_ai
    _, fa = rule_ai.resolve("v11plus")
    pool = dict(rule=rule_turn, w1=make_w1(25, 2), w1b=make_w1(40, 3), w2=make_w2())
    if "w3" in (opp, teacher):
        from .war_teacher import make_w3                 # 用户 2026-11-21：BC 老师 = W3，W3 打 W3
        pool["w3"] = make_w3()
        if len(seats) > 1:
            pool["w3b"] = make_w3()                      # 两个被录座位各一份闭包状态（W3 有记忆）
    g.clear()
    seats = tuple(seats)
    env = Env(size, 2, max_turns=max_turns,
              opponents={k: pool[opp] for k in range(2) if k not in seats}).reset(seed)
    from .candidates import TYPE_ID, TYPES
    from .obs import M_AUX, aggregate_lost, turn_features
    w = env.world
    drv = {k: pool["w3b"] if (teacher == "w3" and j == 1) else pool[teacher] for j, k in enumerate(seats)}
    st = {k: dict(ob=Observer(w, env.seat_name(k)), rng=random.Random(seed if j == 0 else f"{seed}:rec:{k}"),
                  out=[], xs=[], auxs=[], hist=[0] * len(TYPES), prev_sight={}, stats=[0, 0])
          for j, k in enumerate(seats)}
    while True:
        s = env.advance()
        if s is None:
            break
        S = st[s]
        ob, out = S["ob"], S["out"]
        name = env.seat_name(s)
        cs0 = env.candidates(s)
        o0 = ob.encode(w, cs0, max_turns)                  # 回合开头：摘要与上一回合的后见辅助目标
        S["xs"].append(turn_features(o0, S["hist"], 0, sum(S["hist"]), size))
        if len(S["xs"]) > 1:
            S["auxs"][-1] = aggregate_lost(S["prev_sight"], ob.sight, size)
        S["auxs"].append(None)
        S["prev_sight"] = dict(ob.sight)
        ti = len(S["xs"]) - 1
        cur = [0] * len(TYPES)
        stats = S["stats"]

        def hook(tool, args, s=s, ob=ob, out=out, ti=ti, cur=cur):
            cs = env.candidates(s)
            i = match(tool, args, cs)
            if i is not None:
                out.append((ob.encode(w, cs, max_turns), i, ti))
                cur[TYPE_ID[cs[i].kind]] += 1

        def on_key(key, s=s, ob=ob, out=out, ti=ti, cur=cur, stats=stats):
            stats[1] += 1
            cs = env.candidates(s)
            i = _match_key(key, cs)
            if i is not None:
                stats[0] += 1
                out.append((ob.encode(w, cs, max_turns), i, ti))
                cur[TYPE_ID[cs[i].kind]] += 1
        if teacher == "rule":
            fa(w, name, S["rng"], max_actions=10 ** 9, on_action=hook)
        else:
            with _recording(w, name, on_key):
                drv[s](w, name, S["rng"])
        cs = env.candidates(s)
        out.append((ob.encode(w, cs, max_turns), 0, ti))   # 结束回合（候选 0 恒为 end_turn）
        cur[0] += 1
        S["hist"] = cur
        env.end_turn(s)
    import numpy as _np
    res = []
    rw = env.rewards()
    for k in seats:
        S = st[k]
        A = _np.zeros((len(S["xs"]), M_AUX), _np.float32)
        Mk = _np.zeros(len(S["xs"]), bool)
        for j, v in enumerate(S["auxs"]):
            if v is not None:
                A[j], Mk[j] = v, True
        res.append(dict(decs=S["out"], xs=_np.stack(S["xs"]), A=A, Mk=Mk, reward=rw[k], turns=w.turn, seat=k,
                        info=dict(matched=S["stats"][0], intercepted=S["stats"][1], winner=env.winner(),
                                  turns=w.turn, seat=k)))
    return res
