"""Compare ordered candidates, visibility memory and training tensors against legacy-rl."""

import argparse
import io
from pathlib import Path
import subprocess
import sys

import numpy as np


def row(stream, *values):
    print(*values, file=stream)


def snapshot(stream, w, name, reset, hist, invalid, steps):
    import re
    from rl.intel import parse_map, parse_spy

    names = {n: i for i, n in enumerate(w.order)}
    blocs = {b["name"]: i + 1 for i, b in enumerate(w.blocs)}
    nation = lambda n: names.get(n, -1)

    def entity(e):
        if e.startswith("盟:"):
            return 1000 + blocs[e[2:]] if e[2:] in blocs else -1
        return nation(e.removeprefix("国:"))

    row(stream, nation(name), int(reset), 0)
    row(stream, w.size, w.turn, w.max_turns, int(w.bank_on()))
    row(stream, len(names))
    for n, i in names.items():
        energy, used, short = w.energy_report.get(n, (0, 0, False))
        loan = w.bank["loans"].get(n, {})
        row(stream, i, int(n in w.nations), w.nation_gdp(n), energy, used, int(short),
            int(w.diplo_built.get(n, 0) > 0), loan.get("principal", 0),
            *(w.res(n, g) if n in w.nations else 0 for g in ["黄金", *TRADEABLE]))
    row(stream, w.size ** 2)
    for x in range(w.size):
        for y in range(w.size):
            t = w.tiles.get((x, y), {})
            res = t["resources"] if "resources" in t else w.tile_resources(x, y)
            row(stream, nation(t.get("owner")), nation(t.get("core")), TERR.index(w.tile_terrain(x, y)),
                int(t.get("built_this_turn", 0)), t.get("recruited_this_turn", 0),
                t.get("militia_recruited_this_turn", 0), *(res.get(g, 0) for g in RES5),
                *(w.tile_resources(x, y).get(g, 0) for g in RES5),
                *(t.get("buildings", {}).get(b, 0) for b in BLD), *(t.get("pending", {}).get(b, 0) for b in BLD))
    row(stream, len(w.armies))
    for i, a in enumerate(w.armies, 1):
        row(stream, i, a["id"], nation(a["owner"]), UNITS.index(a.get("type", "步")), a["x"], a["y"],
            a["hp"], a.get("moved_turn", -1), int(bool(a.get("engaged"))), int(bool(a.get("retreat_to"))))
    for attr in ("prices", "market_inv", "market_ref"):
        row(stream, *(getattr(w, attr)[g] for g in TRADEABLE))
    carries = [(names[n], v) for n, v in w.gold_carry.items() if n in names]
    row(stream, len(carries))
    for a in carries:
        row(stream, *a)
    volumes = [(names[n], int(side == "sell"), TRADEABLE.index(g), v)
               for (n, side, g), v in w.market_volume.items() if n in names]
    row(stream, len(volumes))
    for a in volumes:
        row(stream, *a)
    row(stream, len(w.blocs))
    for b in w.blocs:
        row(stream, blocs[b["name"]], nation(b["chief"]), len(b["members"]), *(nation(n) for n in b["members"]))
    row(stream, len(w.wars))
    for war in w.wars:
        for side in w._war_sides(war):
            ents = sorted({entity(w.entity_of(n)) for n in side})
            row(stream, len(ents), *ents)
    row(stream, len(w.pacts))
    for p in w.pacts:
        row(stream, int(p["kind"] == "共同防御"), entity(p["a"]), entity(p["b"]))
    row(stream, len(w.truce))
    for (a, b), until in w.truce.items():
        row(stream, entity(a), entity(b), until)
    row(stream, len(w.peace_offers))
    for p in w.peace_offers:
        row(stream, p["id"], nation(p["a"]), nation(p["b"]), PEACE.index(p["kind"]), p["gold"])
    row(stream, len(w.proposals))
    for p in w.proposals:
        kind = 1 if p["kind"] == "共同防御" else 4 if p["kind"] == "联盟" else 3 if p.get("subject") else 2
        invited = p.get("invitees", ())
        accepted = p.get("accepted", ())
        row(stream, p["id"], kind, entity(p.get("A") or ""), entity(p.get("B") or ""), nation(p.get("asker")),
            len(invited), *(nation(n) for n in invited), len(accepted), *(nation(n) for n in accepted))
    row(stream, len(w.votes))
    for v in w.votes:
        row(stream, v["id"], blocs[v["bloc"]], v.get("deadline", v["turn"] + 1))
    row(stream, len(w.diplo_pending))
    row(stream, len(w.vassals))
    for s, v in w.vassals.items():
        row(stream, nation(s), nation(v["overlord"]))
    row(stream, len(w.spy_pending))
    for s in w.spy_pending:
        row(stream, nation(s["from"]), nation(s["to"]), s["arrive"])
    for n in names:
        reports = w.econ_reports.get(n, [])
        row(stream, len(reports))
        for r in reports:
            ratio = r["military_ratio"]
            row(stream, r["report_turn"], r["gdp"], r["assets"], r["invest"], int(ratio is not None), ratio or 0)
        intel = w.econ_intel.get(n, [])
        row(stream, len(intel))
        for e in intel:
            spy = parse_spy(e["text"])
            report = re.match(r"🏦【央行售出】.+? 第 (\d+) 回合经济报表", e["text"])
            row(stream, nation(e["from"]), e["turn"], int(report[1]) if report else 0, int(bool(spy and spy["short"])),
                int(spy["et"]) if spy else 0, int(spy["mt"]) if spy else 0,
                *(spy["res"].get(g, 0) if spy else 0 for g in ["黄金", *TRADEABLE]),
                *(spy["armies"].get(k, 0) if spy else 0 for k in UNITS))
            tiles = spy["tiles"] if spy else {}
            row(stream, len(tiles))
            for (x, y), (castle, slots, built, pending) in tiles.items():
                row(stream, x * w.size + y, castle, slots, pending, *built)
        maps = w.maps.get(n, [])
        row(stream, len(maps))
        for m in maps:
            stolen = m["from"].endswith("(间谍)")
            row(stream, nation(m["from"].removesuffix("(间谍)")), m["turn"], int(stolen))
            tiles = parse_map(m["text"])
            row(stream, len(tiles))
            for (x, y), (castle, slots) in tiles.items():
                row(stream, x * w.size + y, castle, slots, 0, *(0 for _ in BLD))
    row(stream, *hist, invalid, steps)
    return names, blocs


def candidate_key(c, names, blocs):
    a = c.args
    if c.kind in ("build", "demolish", "recruit"):
        a = (*a[:2], (UNITS if c.kind == "recruit" else BLD).index(a[2]))
    elif c.kind in ("buy", "sell"):
        a = (TRADEABLE.index(a[0]), a[1])
    elif c.kind in ("declare_war", "guarantee", "propose_defense", "propose_vassalage", "submit_vassalage",
                    "release_vassal", "bloc_found", "spy", "buy_report", "share_map", "cancel_guarantee", "break_defense"):
        a = (names[a[0]],)
    elif c.kind == "gift":
        a = (names[a[0]], ["黄金", *TRADEABLE].index(a[1]), a[2])
    elif c.kind == "offer_peace":
        a = (names[a[0]], PEACE.index(a[1]), a[2])
    elif c.kind == "vote":
        a = (a[0], -1 if a[1] is None else int(a[1]))
    elif c.kind == "bloc_join":
        a = (blocs[a[0]],)
    return (TYPE_ID[c.kind], int(c.executable), *a, *(0 for _ in range(3 - len(a))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", type=Path)
    parser.add_argument("--python-root", type=Path, default=Path.home() / "zhanguo-rl")
    args = parser.parse_args()
    sys.path.insert(0, str(args.python_root))

    from rl.env import Env
    from rl.opponents import make_w1, make_w2, rule_turn
    from rl.score import power_score
    from rl.obs import Observer, aggregate_lost, turn_features
    from rl.candidates import build_candidates
    global BLD, UNITS, RES5, TERR, TRADEABLE, TYPES, TYPE_ID, PEACE
    from rl.obs import BLD, UNITS, RES5, TERR, TRADEABLE
    from rl.candidates import TYPES, TYPE_ID
    PEACE = ["white", "pay", "demand", "submit", "subjugate"]

    stream = io.StringIO()
    expected = []
    observers, sight = {}, {}

    def add(w, n, reset, label):
        if reset:
            observers.clear()
            sight.clear()
        hist = [(w.turn + 2 * i) % 25 for i in range(len(TYPES))]
        invalid, steps = w.turn % 11, w.turn % 31
        names, blocs = snapshot(stream, w, n, reset, hist, invalid, steps)
        ob = observers.setdefault(n, Observer(w, n))
        cs = build_candidates(w, n)
        o = ob.encode(w, cs, w.max_turns)
        o["candidates"] = np.asarray([candidate_key(c, names, blocs) for c in cs], np.int32)
        o["summary"] = turn_features(o, hist, invalid, steps, w.size)
        o["lost"] = aggregate_lost(sight.get(n, {}), ob.sight, w.size)
        sight[n] = ob.sight
        o["score"] = np.array([power_score(w, n)])
        expected.append((label, o))

    for seed, opponent in ((7, rule_turn), (537, make_w1(25, 2)), (953, make_w2())):
        env = Env(13, 2, max_turns=80, opponents={0: opponent, 1: opponent}).reset(seed)
        w = env.world
        for turn in (1, 2, 4, 9, 16, 25, 35, 50):
            while w.turn < turn and all(n in w.nations for n in w.order):
                for n, rng in zip(w.order, env._rngs, strict=True):
                    opponent(w, n, rng)
                    w.end_turn(n)
                w.resolve_turn()
                w.begin_turn()
            for i, n in enumerate(w.alive()):
                add(w, n, turn == 1 and i == 0, f"seed={seed}/turn={w.turn}/{n}")

    # An allied capital and cavalry leave view. Their static terrain, capital and last seen fields persist.
    env = Env(16, 3, max_turns=90).reset(123)
    w = env.world
    a, b, c = w.order
    bp = next(p for p in w.own_tiles(b) if w.tiles[p]["buildings"]["市政厅"])
    w.blocs.append(dict(name="甲", chief=a, members=[a, b]))
    w.votes.append(dict(id=1, bloc="甲", turn=w.turn, deadline=w.turn, votes={}))
    w.bank_enable()
    w.bank["loans"][a] = dict(principal=40)
    w.armies.append(dict(owner=b, id=1, type="骑", hp=63.125, x=bp[0], y=bp[1], moved_turn=0, engaged=False))
    w.armies.append(dict(owner=a, id=1, type="骑", hp=91, x=bp[0], y=bp[1], moved_turn=0, engaged=False))
    add(w, a, True, "alliance/visible")
    w.blocs.clear()
    w.votes.clear()
    w.turn += 5
    add(w, a, False, "alliance/lost")
    assert expected[-1][1]["tiles"][bp[0] * w.size + bp[1], 47] == 1
    assert expected[-1][1]["lost"][0] != 0

    w = Env(13, 3, max_turns=60).reset(700).world
    a, b, c = w.order
    assert w.spy(a, b)[0] and w.share_map(c, a)[0]
    add(w, a, True, "intel/pending")
    for _ in range(10):
        w.resolve_turn()
        w.begin_turn()
        if w.turn in (2, 4):
            add(w, a, False, f"intel/arrival-{w.turn}")
    assert w.bank_sell_report(a, b, w.econ_reports[b][-1]["report_turn"], "")[0]
    add(w, a, False, "intel/report")
    assert w.bank_sell_report(a, c, w.econ_reports[c][-1]["report_turn"], "")[0]
    assert w.bank_sell_report(a, c, w.econ_reports[c][-1]["report_turn"], "")[0]
    add(w, a, False, "intel/evicted")
    assert expected[-1][1]["nations"][1, 13] == 1
    assert expected[-1][1]["nations"][1, 29] == 1

    data = subprocess.run([args.probe.resolve()], input=stream.getvalue(), text=True, stdout=subprocess.PIPE,
                          check=True).stdout.splitlines()
    cursor = 0
    failures = []
    for label, o in expected:
        for _ in range(len(o)):
            key, _, values = data[cursor].partition(" ")
            cursor += 1
            want = o[key]
            actual = np.fromstring(values, dtype=np.float64, sep=" ")
            if actual.size != want.size:
                failures.append(f"{label}/{key}: {actual.size} values vs {want.size}")
                continue
            actual = actual.reshape(want.shape)
            if want.dtype == np.float32:
                bad = np.argwhere(actual.astype(np.float32).view(np.uint32) != want.view(np.uint32))
            elif key == "score":
                bad = np.argwhere(np.abs(actual - want) > 1e-4)
            else:
                bad = np.argwhere(actual != want)
            if len(bad):
                i = tuple(bad[0])
                failures.append(f"{label}/{key}{i}: C++ {actual[i]} Python {want[i]} ({len(bad)} differences)")
    assert cursor == len(data)
    if failures:
        print(*failures[:24], sep="\n", file=sys.stderr)
        raise SystemExit(f"{len(failures)} mismatched arrays in {len(expected)} observations")
    print(f"PASS {len(expected)} observations, ordered candidates, tensors, GRU inputs and score")


if __name__ == "__main__":
    main()
