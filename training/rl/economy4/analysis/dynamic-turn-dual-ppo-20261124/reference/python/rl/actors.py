"""环境进程：循环打局，每个决策点把观测发给主进程（GPU 批量推理），等动作下标回来。

协议（Pipe）：worker→main  ("req", wid, obs, meta) / ("done", wid, summary)
              main→worker   int 候选下标；None = 停止
学习者座位随机；对手每局从对手池抽样（规则 AI / W1 好战的几种参数）。
"""
from __future__ import annotations

import random
import time

MAX_STEPS = 30          # 每回合最多决策步数（超出强制结束回合）


def make_pool():
    from .opponents import make_w1, make_w2, rule_turn
    return [("rule", rule_turn), ("w1_25_2", make_w1(25, 2)), ("w1_40_3", make_w1(40, 3)),
            ("w2", make_w2())]


def episode(conn, wid, seed, size, max_turns, weights):
    import ruleai.v11plus.grouping as g
    from .env import Env
    from .obs import Observer
    pool = make_pool()
    rng = random.Random(seed)
    oname, opp = rng.choices(pool, weights=weights)[0]
    seat = rng.randrange(2)
    g.clear()
    env = Env(size, 2, max_turns=max_turns, opponents={1 - seat: opp}).reset(seed)
    ob = Observer(env.world, env.seat_name(seat))
    nd = 0
    t0 = time.time()
    from .candidates import TYPE_ID, TYPES
    from .obs import aggregate_lost, turn_features
    hist, inv_prev, steps_prev, prev_sight, n_turn = [0] * len(TYPES), 0, 0, {}, 0
    while True:
        s = env.advance()
        if s is None:
            break
        cur = [0] * len(TYPES)
        inv_cur = steps_cur = 0
        for step in range(MAX_STEPS + 1):
            cands = env.candidates(s)
            o = ob.encode(env.world, cands, max_turns)
            meta = dict(turn=env.world.turn, xt=None, aux_prev=None)
            if step == 0:                                   # 回合开头：回合摘要 + 上一回合的后见辅助目标
                meta["xt"] = turn_features(o, hist, inv_prev, steps_prev, size)
                if n_turn > 0:
                    meta["aux_prev"] = aggregate_lost(prev_sight, ob.sight, size)
                prev_sight = dict(ob.sight)
                n_turn += 1
            force_end = step >= MAX_STEPS
            conn.send(("req", wid, o, meta))
            idx = conn.recv()
            if idx is None:
                return False
            nd += 1
            c = cands[idx]
            cur[TYPE_ID[c.kind]] += 1
            if c.kind == "end_turn" or force_end:
                break
            ok, _msg = env.act(s, c)
            steps_cur += 1
            inv_cur += (not ok)
        hist, inv_prev, steps_prev = cur, inv_cur, steps_cur
        env.end_turn(s)
    rw = env.rewards()
    summary = dict(seat=seat, opp=oname, reward=rw[seat], winner=env.winner(), turns=env.world.turn,
                   decisions=nd, invalid=env.invalid.get(seat, 0), steps=env.steps.get(seat, 0),
                   secs=time.time() - t0)
    conn.send(("done", wid, summary))
    return True


def worker_main(conn, wid, size, max_turns, base_seed, weights):
    n = 0
    while True:
        seed = base_seed + wid * 1_000_003 + n * 7919
        n += 1
        if not episode(conn, wid, seed, size, max_turns, weights):
            return
