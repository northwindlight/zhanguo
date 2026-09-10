"""v8 军队移动审计：到底有没有"绕远路"。

盯两处代码事实（都是读代码读出来的嫌疑，这里量它的实际规模）：

  ① `movers` 没按距离排序 → 派去目标的可能是最远的兵。
     量法：每次移动时，看有没有**闲置的、离落点更近**的兵被跳过。
  ② 落点被限制成「必须是无主地且非山地」→ 军队不能走自家地，
     可能被卡住或只能绕。量法：统计"八个邻居全不能走"的军队数，
     以及同一个兵连续两回合走回头路（A→B→A）的次数。

用法：.venv/bin/python -m experiments.army_path_audit [seed] [turns]
"""
from __future__ import annotations

import random
import sys

from mp import World

import expand_rule_v8 as V8

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 0
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 150
V8.HORIZON = TURNS
N = 16

w = World(size=N, seed=SEED, nations=["秦"])
w.begin_turn()
rng = random.Random(SEED)


def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def unowned_count():
    return sum(1 for x in range(N) for y in range(N) if w.owned_by(x, y) is None)


orig_move = w.move
moves = []          # (turn, aid, from, to, 被跳过的更近的兵数, 最近闲置兵距离)
stuck_samples = []  # (turn, aid, pos) 八邻全不能走
prev_move: dict[int, tuple] = {}


def logged_move(name, aid, x, y):
    me = next((q for q in w.armies if q["id"] == aid and q["owner"] == name), None)
    if me is None:
        return orig_move(name, aid, x, y)
    frm = (me["x"], me["y"])
    dst = (x, y)

    # ① 有没有闲置的、离落点更近的兵被跳过
    idle = [q for q in w.armies
            if q["owner"] == name and q["id"] != aid and q["hp"] > 0
            and not q.get("engaged") and q.get("moved_turn") != w.turn]
    my_d = cheb(frm, dst)
    closer_idle = [q for q in idle if cheb((q["x"], q["y"]), dst) < my_d]
    nearest_idle = min((cheb((q["x"], q["y"]), dst) for q in idle), default=None)

    # ② 走不了的兵（八邻全是自家地或山地）
    stuck = 0
    for q in w.armies:
        if q["owner"] != name or q["hp"] <= 0:
            continue
        # 判据必须跟着 v8 走：落点 = 野地 或 **自家地**（引擎允许），只是绕山地
        ok = [p for p in w.neighbors(q["x"], q["y"])
              if w.owned_by(*p) in (None, name) and w.tile_terrain(*p) != "山地"]
        if not ok:
            stuck += 1
    stuck_samples.append((w.turn, stuck, len([q for q in w.armies
                                              if q["owner"] == name and q["hp"] > 0])))

    ok = orig_move(name, aid, x, y)
    okv = ok[0] if isinstance(ok, tuple) else ok
    if okv:
        moves.append((w.turn, aid, frm, dst, len(closer_idle), nearest_idle, my_d))
        prev_move[aid] = (w.turn, frm, dst)
    return ok


w.move = logged_move

for t in range(TURNS):
    V8.expand_rule_turn_v8(w, "秦", rng, max_actions=10**9)
    w.resolve_turn()
    if t + 1 < TURNS:
        w.begin_turn()

n = len(moves)
print(f"seed {SEED}  {TURNS} 回合   总移动 {n} 步   终局无主地 {unowned_count()} 格")
if not n:
    raise SystemExit

# ① 派错人
passed = [m for m in moves if m[4] > 0]
print(f"\n① 「派错人」：有闲置兵离落点更近、却派了更远的")
print(f"   {len(passed)}/{n} = {len(passed)/n:.1%} 的移动属于这种")
if passed:
    worst = max(passed, key=lambda m: m[6] - (m[5] if m[5] is not None else m[6]))
    t, aid, frm, dst, k, ni, md = worst
    print(f"   最夸张一例：回合{t} 军队#{aid} {frm}→{dst}，"
          f"它离落点 {md} 格，而最近的闲置兵只有 {ni} 格（差了 {md-(ni or md)} 格）")

# ② 走不了
stuck_any = [s for s in stuck_samples if s[1] > 0]
if stuck_any:
    mx = max(stuck_any, key=lambda s: s[1])
    print(f"\n② 「走不了」：八邻全不能走（自家地或山地）")
    print(f"   有军队被卡住的回合数 {len(stuck_any)}/{len(stuck_samples)}；"
          f"最惨 回合{mx[0]}：{mx[1]}/{mx[2]} 支动不了")

# ③ 回头路
back = 0
for aid, (t, frm, dst) in prev_move.items():
    for aid2, (t2, frm2, dst2) in prev_move.items():
        if aid == aid2 and t2 == t + 1 and frm2 == dst and dst2 == frm:
            back += 1
print(f"\n③ 「走回头路」：同一支兵连续两回合 A→B→A 的次数 {back}")
