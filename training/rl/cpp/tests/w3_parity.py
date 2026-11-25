"""Compare native W3 calls and settled states with Python and report H/J outcomes."""

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


def python_trace(left, right, seed, turns, bank):
    import mp
    from balance import BUILDINGS, UNIT_TYPES, RES_KEYS
    from rl.env import Env
    from rl.opponents import make_w1, make_w2, rule_turn
    from rl.war_teacher import make_w3
    import ruleai.v11plus.grouping as grouping

    grouping.clear()
    create = {"rule": lambda: rule_turn, "w1b": lambda: make_w1(40, 3), "w2": make_w2, "w3": make_w3}
    players = [create[left](), create[right]()]
    env = Env(13, 2, max_turns=turns, bank=bank).reset(seed)
    w = env.world
    names = {name: seat for seat, name in enumerate(w.order)}
    buildings, units, resources = list(BUILDINGS), list(UNIT_TYPES), list(RES_KEYS)
    capitals = {name: next(p for p in w.own_tiles(name) if w.tiles[p]["buildings"]["市政厅"]) for name in names}
    phase = None
    trace = []
    defense = Counter()
    watchtowers = Counter()

    def save(kind, *values):
        trace.append((kind, *values))

    def site(x, y):
        return x * w.size + y

    ops = {
        "build": ("build", lambda x, y, b: (site(x, y), buildings.index(b))),
        "demolish": ("demolish", lambda x, y, b: (site(x, y), buildings.index(b))),
        "recruit": ("recruit", lambda x, y, qty, k: (site(x, y), units.index(k))),
        "buy": ("buy", lambda r, qty: (resources.index(r), qty)),
        "sell": ("sell", lambda r, qty: (resources.index(r), qty)),
        "move": ("move", lambda aid, x, y: (aid, site(x, y))),
        "attack": ("attack", lambda aids, x, y: (site(x, y), *aids)),
        "spy": ("spy", lambda enemy: (names[enemy],)),
        "bank_sell_report": ("report", lambda enemy, period, text: (names[enemy], period)),
        "declare_war": ("declare", lambda enemy: (names[enemy],)),
    }

    def intercept(fn, kind, arguments):
        def wrapped(world, name, *args, **kwargs):
            answer = fn(world, name, *args, **kwargs)
            if world is w and name == phase and not kwargs.get("dry"):
                save(kind, int(answer[0]), *arguments(*args))
                if answer[0] and kind == "build" and args[2] == "瞭望塔":
                    xy = args[:2]
                    if xy == capitals[name]:
                        watchtowers["capital_orders"] += 1
                    else:
                        watchtowers["frontier_orders"] += 1
                if answer[0] and kind == "declare":
                    towers = [p for p in w.own_tiles(name) if p != capitals[name]
                              and (w.tiles[p]["buildings"]["瞭望塔"] or w.tiles[p]["pending"]["瞭望塔"])]
                    watchtowers["declarations_with_frontier" if towers else "declarations_without_frontier"] += 1
                    if any(w.tiles[p]["pending"]["瞭望塔"] for p in towers):
                        watchtowers["declared_on_order_turn"] += 1
                if answer[0] and kind in ("build", "move") and any(war["def"] == name for war in w.wars):
                    xy = args[:2] if kind == "build" else args[1:]
                    tile = w.tiles.get(xy)
                    if tile and tile["owner"] == name and tile["buildings"]["市政厅"]:
                        if kind == "move":
                            defense["hall_moves"] += 1
                        elif args[2] == "城堡":
                            defense["castle_orders"] += 1
            return answer
        return wrapped

    with ExitStack() as stack:
        for method, (kind, args) in ops.items():
            stack.enter_context(patch.object(mp.World, method, intercept(getattr(mp.World, method), kind, args)))
        while True:
            for seat, (role, player) in enumerate(zip((left, right), players, strict=True)):
                name = env.seat_name(seat)
                if role == "w3":
                    save("actions", w.turn, seat)
                    phase = name
                player(w, name, env._rngs[seat])
                phase = None
                w.end_turn(name)
            w.resolve_turn()
            save("settled", w.turn)
            for name, n in names.items():
                if name in w.nations:
                    ext = w.energy_report.get(name, (0, 0, False))
                    save("nation", n, 1, *(w.res(name, g) for g in resources), *ext)
                else:
                    save("nation", n, 0)
                for report in w.econ_reports.get(name, []):
                    if report["period_end"] == w.turn:
                        ratio = report["military_ratio"]
                        save("bank", n, report["report_turn"], report["gdp"], report["invest"],
                             report["assets"], int(ratio is not None), ratio or 0)
            for a in w.armies:
                if a["owner"] in names:
                    save("army", names[a["owner"]], a["id"], units.index(a["type"]), site(a["x"], a["y"]),
                         a["hp"], int(bool(a.get("engaged"))))
            for (x, y), tile in sorted(w.tiles.items()):
                if tile["owner"] in names:
                    save("tile", site(x, y), names[tile["owner"]],
                         *(tile["buildings"][b] for b in buildings))
            for s in w.spy_pending:
                save("pending_spy", names[s["from"]], names[s["to"]], s["arrive"])
            save("war", int(w.war_between(*w.order)))
            if len(w.nations) <= 1 or w.turn >= turns:
                break
            w.begin_turn()
    winner = names[next(iter(w.nations))] if len(w.nations) == 1 else -1
    save("done", winner, w.turn)
    return trace, defense, watchtowers


def compare(job):
    probe, left, right, seed, turns, bank = job
    try:
        py, defense, watchtowers = python_trace(left, right, seed, turns, bank)
        result = subprocess.run([probe, str(seed), str(turns), left, right, str(int(bank))],
                                check=True, capture_output=True, text=True)
        native = [(kind, *map(float, nums)) for kind, *nums in (line.split() for line in result.stdout.splitlines())]
        first = None
        at = None
        action_rows = {"build", "demolish", "buy", "sell", "recruit", "move", "attack", "declare", "report", "spy"}
        for i, (a, b) in enumerate(zip(py, native)):
            if a[0] in ("actions", "settled"):
                at = a
            if a[0] != b[0] or len(a) != len(b) or any(abs(x - y) > (1e-8 if a[0] in ("army", "nation") else 0)
                                                        for x, y in zip(a[1:], b[1:])):
                first = dict(line=i, phase=at, python=a, cpp=b)
                break
        if first is None and len(py) != len(native):
            first = dict(python_rows=len(py), cpp_rows=len(native))
        return dict(seed=seed, left=left, right=right, bank=bank, python=list(py[-1][1:]),
                    cpp=list(map(int, native[-1][1:])), first=first,
                    calls=sum(r[0] in action_rows for r in py), defense=defense, watchtowers=watchtowers,
                    intels=Counter(r[0] for r in py if r[0] in ("report", "spy", "declare") and r[1]))
    except Exception as exc:
        raise RuntimeError(f"seed {seed}, {left} vs {right}, bank={bank}") from exc


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("probe", type=Path)
    cli.add_argument("--python-root", type=Path, required=True)
    cli.add_argument("--seeds", type=int, default=20)
    cli.add_argument("--mirrors", type=int, help="number of mirror seeds, defaulting to --seeds")
    cli.add_argument("--base-seed", type=int, default=31000)
    cli.add_argument("--turns", type=int, default=150)
    cli.add_argument("--workers", type=int, default=4)
    cli.add_argument("--opponents", default="rule,w1b,w2,w3")
    cli.add_argument("--bank", choices=["on", "off"], default="on")
    cli.add_argument("--output", type=Path)
    options = cli.parse_args()
    mirrors = options.seeds if options.mirrors is None else options.mirrors
    if min(options.seeds, mirrors, options.workers, options.turns) < 1:
        cli.error("seeds, mirrors, workers and turns must be positive")
    sys.path.insert(0, str(options.python_root.resolve()))
    pairs = []
    for opponent in options.opponents.split(","):
        pairs.append(("w3", opponent))
        if opponent != "w3":
            pairs.append((opponent, "w3"))
    jobs = [(str(options.probe.resolve()), *pair, options.base_seed + offset, options.turns,
             options.bank == "on") for pair in pairs
            for offset in range(mirrors if pair == ("w3", "w3") else options.seeds)]
    with ProcessPoolExecutor(max_workers=options.workers) as pool:
        results = list(pool.map(compare, jobs))
    if options.output:
        options.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results))

    failures = [r for r in results if r["first"]]
    for row in failures[:12]:
        print(row["seed"], f"{row['left']}/{row['right']}", json.dumps(row["first"], ensure_ascii=False))
    for left, right in pairs:
        group = [r for r in results if (r["left"], r["right"]) == (left, right)]
        print(f"{left}/{right}", "games", len(group), "exact", sum(not r["first"] for r in group),
              "wins_py", dict(Counter(r["python"][0] for r in group)),
              "wins_cpp", dict(Counter(r["cpp"][0] for r in group)),
              "intels", dict(sum((r["intels"] for r in group), Counter())),
              "defense", dict(sum((r["defense"] for r in group), Counter())),
              "watchtowers", dict(sum((r["watchtowers"] for r in group), Counter())))
        if left == right == "w3":
            for backend in ("python", "cpp"):
                finishes = Counter(r[backend][1] for r in group if r[backend][0] >= 0)
                print(backend, "mirror_elimination_turns", dict(sorted(finishes.items())))
    print(f"{len(results)} games, {len(failures)} differ, {sum(r['calls'] for r in results)} W3 calls")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
