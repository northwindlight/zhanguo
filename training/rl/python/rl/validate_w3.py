"""Replay W3 benchmarks and write turn-level acceptance evidence as JSONL."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path
from statistics import mean

from .env import Env
from .opponents import make_w1, make_w2, rule_turn
from .score import REF, army_value
from .war_teacher import EPS, forecast, inventory_value, make_w3, upkeep

OPPONENTS = ("rule", "w1b", "w2")
BASELINE = {"rule": 0.91, "w1b": 0.45, "w2": 0.38}


def battle(job):
    import ruleai.v11plus.grouping as grouping

    seat, opponent, seed, low_share, hoard_turns, near = job
    grouping.clear()
    rival = (make_w3(low_share=low_share, hoard_turns=hoard_turns, near=near) if opponent == "w3"
             else {"rule": rule_turn, "w1b": make_w1(40, 3), "w2": make_w2()}[opponent])
    rows, declarations, payments = [], [], []
    action = {}

    def declare(world, name, enemy, force, estimate, distance, intel_turn):
        actual = sum(army_value(a) for a in world.nation_armies(enemy))
        declarations.append(dict(turn=world.turn, enemy=enemy, distance=distance, force=force,
                                 estimate=estimate, intel_turn=intel_turn, actual=actual,
                                 actual_ratio=force / actual if actual else None,
                                 violation=distance > near or force + EPS < 1.3 * estimate))

    teacher = make_w3(low_share=low_share, hoard_turns=hoard_turns, near=near, on_declare=declare)

    def play(world, name, rng):
        action.clear()
        teacher(world, name, rng)
        prediction = forecast(world, name)
        action.update(stock_action=inventory_value(world, name), income_action=prediction.income,
                      power_action=prediction.power, demand_action=prediction.demand,
                      upkeep_action=upkeep(world, name))

    env = Env(13, 2, max_turns=150, opponents={seat: play, 1-seat: rival}).reset(seed)
    world = env.world
    name = env.seat_name(seat)
    initial_stock = inventory_value(world, name)
    resolve = world.resolve_turn
    spy = world.spy
    bank_report = world.bank_sell_report

    def record_intelligence(kind, fn, owner, *args):
        count = sum(a["type"] == "步" for a in world.nation_armies(owner))
        result = fn(owner, *args)
        if owner == name and result[0]:
            payments.append(dict(turn=world.turn, infantry=count, kind=kind, violation=count <= 6))
        return result

    def audited_resolve():
        alive = name in world.nations
        prediction = forecast(world, name) if alive else None
        need = upkeep(world, name) if alive else 0
        result = resolve()
        if prediction is not None and action:
            turn = world.turn
            income = prediction.income
            military = need * REF["补给"]
            income_action = float(action["income_action"])
            limit = hoard_turns * max(0, income_action)
            stock_action = float(action["stock_action"])
            previous = rows[-1]["stock_action"] if rows else initial_stock
            survived = name in world.nations
            stock_post = inventory_value(world, name) if survived else 0.0
            stars = result["famine"].get(name, (0, 0, 0))
            rows.append(dict(turn=turn, survived=survived, income=income, military=military, **action,
                             ratio=military / income if income > EPS else (None if military else 0.0),
                             early_excess=turn <= 50 and military > low_share * max(0, income) + EPS,
                             excess=military > 0.55 * max(0, income) + EPS,
                             stock_limit=limit, stock_post=stock_post,
                             stock_ratio=stock_action / income_action if income_action > EPS else None,
                             stock_violation=(2 <= turn <= 15 and stock_action > max(limit, previous) + EPS)
                                             or (turn >= 16 and stock_action > limit + EPS),
                             short=bool(world.energy_report.get(name, (0, 0, False))[2]),
                             famine=stars,
                             infantry=sum(a["type"] == "步" for a in world.nation_armies(name))))
            action.clear()
        return result

    world.spy = lambda owner, *args: record_intelligence("spy", spy, owner, *args)
    world.bank_sell_report = lambda owner, *args: record_intelligence("report", bank_report, owner, *args)
    world.resolve_turn = audited_resolve
    assert env.advance() is None
    return dict(seed=seed, seat=seat, opponent=opponent, bank=world.bank_on(),
                winner=env.winner(), turns=world.turn, initial_stock=initial_stock,
                intelligence=payments, declarations=declarations, timeline=rows)


def demo_case(job):
    from . import demo
    seed, opponent = job
    demo.collect(seed, 13, 150, opponent, teacher="w3")
    return dict(seed=seed, opponent=opponent, **demo.LAST)


def summarize(games, demos, mirrors, output, near):
    rows = [row for game in games for row in game["timeline"]]
    ninety = [row["infantry"] for row in rows if row["turn"] == 90 and row["survived"]]
    window = [[r for r in g["timeline"] if 51 <= r["turn"] <= 100] for g in games]
    entered = [w for w in window if w]
    late = sum(any(r["ratio"] is not None and r["ratio"] >= 0.45 for r in w) for w in entered)
    payments = [event for g in games for event in g["intelligence"]]
    declarations = [event for g in games for event in g["declarations"]]
    mapped = sum(d["matched"] for d in demos)
    total = sum(d["intercepted"] for d in demos)
    curves = defaultdict(list)
    for row in rows:
        if row["ratio"] is not None:
            curves[row["turn"]].append(row["ratio"])

    outcomes = {}
    for seat in (0, 1):
        for opponent in OPPONENTS:
            cases = [g for g in games if g["opponent"] == opponent and g["seat"] == seat]
            victories = [g["turns"] for g in cases if g["winner"] == seat]
            losses = sum(g["winner"] is not None and g["winner"] != seat for g in cases)
            outcomes[f"seat{seat}/{opponent}"] = dict(games=len(cases), wins=len(victories), losses=losses,
                win_rate=len(victories)/len(cases) if cases else None,
                win_turn_mean=mean(victories) if victories else None, baseline_seat0=BASELINE[opponent])

    mirror_wins = [sum(g["winner"] == seat for g in mirrors) for seat in (0, 1)]
    mirror_turns = [g["turns"] for g in mirrors if g["winner"] is not None]
    result = dict(
        A=dict(alive_at_90=len(ninety), with_10=sum(n >= 10 for n in ninety),
               distribution=dict(sorted(Counter(ninety).items()))),
        B=dict(before_51_excess=sum(r["early_excess"] for r in rows), max_55_excess=sum(r["excess"] for r in rows),
               late_ge_45=late, entered_late=len(entered), closed_before_late=len(games)-len(entered),
               mean_by_turn={turn: dict(mean=mean(ratios), samples=len(ratios))
                             for turn, ratios in sorted(curves.items())}),
        C=dict(opening_shorts=sum(r["short"] for r in rows if r["turn"] == 1),
               later_shorts=sum(r["short"] for r in rows if r["turn"] >= 2)),
        D=dict(famine_rounds=sum(bool(r["famine"][0]) for r in rows),
               armies_starved=sum(r["famine"][2] for r in rows)),
        E=dict(initial=max(g["initial_stock"] for g in games),
               stock_ratio_max=max((r["stock_ratio"] for r in rows if r["turn"] >= 16
                                    and r["stock_ratio"] is not None), default=None),
               action_violations=sum(r["stock_violation"] for r in rows),
               post_settlement_stock_peak=max(r["stock_post"] for r in rows)),
        F=dict(payments=len(payments), early=sum(p["violation"] for p in payments),
               first=min((p["turn"] for p in payments), default=None), bank_enabled=any(g["bank"] for g in games)),
        G=dict(declarations=len(declarations), violations=sum(d["violation"] for d in declarations), near=near,
               spy_based=sum(d["intel_turn"] is not None for d in declarations), actual_ratios=declarations),
        H=outcomes,
        I=dict(games=len(demos), intercepted=total, matched=mapped, mapping=mapped/total if total else None,
               spy=sum(d.get("spy", 0) for d in demos), reports=sum(d.get("bank_sell_report", 0) for d in demos),
               cases=demos),
        J=dict(games=len(mirrors), elimination_games=len(mirror_turns),
               seat_wins=mirror_wins, elimination_turns=dict(sorted(Counter(mirror_turns).items())),
               elimination_mean=mean(mirror_turns) if mirror_turns else None))
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    console = {**result, "B": {k: v for k, v in result["B"].items() if k != "mean_by_turn"},
               "G": {k: v for k, v in result["G"].items() if k != "actual_ratios"},
               "I": {k: v for k, v in result["I"].items() if k != "cases"}}
    print(json.dumps(console, ensure_ascii=False, indent=2, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base-seed", type=int, default=31000)
    parser.add_argument("--seat0", type=int, default=30)
    parser.add_argument("--seat1", type=int, default=10)
    parser.add_argument("--demos", type=int, default=10)
    parser.add_argument("--mirrors", type=int, default=60)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--low-share", type=float, default=0.15)
    parser.add_argument("--hoard-turns", type=float, default=3.0)
    parser.add_argument("--near", type=int, default=6)
    opts = parser.parse_args()
    if opts.seat0 < 1 or opts.seat1 < 1 or opts.demos < 0 or opts.mirrors < 0 or opts.workers < 1:
        parser.error("at least one game per seat and worker, and nonnegative demos")
    if opts.demos and (opts.low_share, opts.hoard_turns, opts.near) != (0.15, 3.0, 6):
        parser.error("demo recorder uses default W3 parameters; use --demos 0 for a custom policy")
    opts.output.mkdir(parents=True, exist_ok=False)
    cases = [(seat, opponent, opts.base_seed + idx, opts.low_share, opts.hoard_turns, opts.near)
             for seat, count in enumerate((opts.seat0, opts.seat1)) for opponent in OPPONENTS
             for idx in range(count)]
    demo_jobs = [(opts.base_seed + idx, OPPONENTS[idx % len(OPPONENTS)]) for idx in range(opts.demos)]
    mirror_jobs = [(0, "w3", opts.base_seed + idx, opts.low_share, opts.hoard_turns, opts.near)
                   for idx in range(opts.mirrors)]
    with ProcessPoolExecutor(max_workers=opts.workers) as pool, (opts.output / "episodes.jsonl").open("w") as log:
        games = []
        for game in pool.map(battle, cases):
            log.write(json.dumps(game, ensure_ascii=False, allow_nan=False) + "\n")
            log.flush()
            games.append(game)
        demos = list(pool.map(demo_case, demo_jobs))
        mirrors = []
        for game in pool.map(battle, mirror_jobs):
            log.write(json.dumps(game, ensure_ascii=False, allow_nan=False) + "\n")
            log.flush()
            mirrors.append(game)
    summarize(games, demos, mirrors, opts.output, opts.near)


if __name__ == "__main__":
    main()
