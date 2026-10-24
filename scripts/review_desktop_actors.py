"""Review squads, combat, towns and oil in an isolated offline Godot window.

The fixture explicitly adds test units to a fresh rule-only world. It is not
the default opening, a saved real game, or a call to a model service.
Run under xvfb-run when no desktop display is available.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def fixture(seed):
    sys.path.insert(0, str(ROOT))
    from scenarios.eight_nations import build
    from mods.ui.desktop_activity import difference

    host = build(seed=seed)
    actor = "scenario:qin"
    land = host.world.territory.owned(actor)
    camp = land[0].tile.position
    infantry = host.world.military.seed(actor, "native:infantry", *camp, 2)
    cavalry = host.world.military.seed(actor, "native:cavalry", *camp, 2)
    host.world.military.seed(actor, "native:militia", *land[-1].tile.position, 2)

    def frame():
        result = host.ui.desktop_frame(host.observer_snapshot())
        result["clients"] = [
            {"actor": a, "kind": c.kind, "version": c.version, "model": None}
            for a, c in host.clients.items()
        ]
        return result

    camp_frame = frame()
    candidates = [(camp[0] + dx, camp[1] + dy)
                  for dy in [-1, 0, 1] for dx in [-1, 0, 1] if (dx, dy) != (0, 0)]
    candidates = [p for p in candidates
                  if host.world.territory.terrain(*p).claimable
                  and not host.world.territory.cell(*p).tile.id]
    candidates.sort(key=lambda p: host.world.territory.terrain(*p).defense)
    for target in candidates:
        receipt = host.player(actor).submit("military", {
            "action": "attack", "ids": [u.id for u in infantry + cavalry],
            "x": target[0], "y": target[1],
        })
        if receipt["ok"]:
            break
    else:
        raise RuntimeError("样例种子没有可进攻的相邻野地；请使用默认种子")
    battle_frame = frame()
    host.world.military.battle()
    event = next(e for e in reversed(host.world.military.drain_events()) if e["kind"] == "battle")
    after = frame()
    beat = {
        "phase": "battle", "position": list(target), "title": "交锋样例",
        "text": "独立离线战斗样例，伤亡来自实际规则结算。", "duration": 4.6,
        "delta": {}, "combat": event["details"],
        "playback_scope": "settlement", "client_kind": None,
    }
    casualties = {**deepcopy(beat), "phase": "casualties", "duration": 2,
                  "delta": difference(battle_frame["scene"], after["scene"])}
    return {
        "fixture": "独立离线演出样例，测试兵力非默认开局",
        "camp": camp_frame, "camp_position": list(camp),
        "battle": battle_frame, "beat": beat, "casualties": casualties,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="新建输出目录，拒绝覆盖")
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = out / "fixture.json"
    source.write_text(json.dumps(fixture(args.seed), ensure_ascii=False) + "\n", encoding="utf-8")
    if args.prepare_only:
        return
    env = {**os.environ, "ZHANGUO_ACTOR_REVIEW_INPUT": str(source)}
    subprocess.run([sys.executable, str(ROOT / "desktop.py"),
                    "--actor-review-output", str(out / "actors.png"),
                    "--log-file", str(out / "godot.log")], cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
