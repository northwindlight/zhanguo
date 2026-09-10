# -*- coding: utf-8 -*-
"""内置规则 AI —— **游戏层**，不依赖 LLM 层。

没有工具 schema、没有文本面板、没有国策（plan）、不写看海日志——它只调 `World` 的方法。
两处用它：

- 看海 demo 的无 key 代打（`mp_ai.dummy_turn` 包一层，把动作写进日志）；
- RL 训练的脚本对手（`rl/env.py` 直接调）。

策略：补能源 → 屯田 → 兵营 → 征兵 → 打最近野人拓疆；缺钱卖矿石/粮食，缺装备买一点。
返回动作记录 `[(tool, args, ok, msg), …]`，由调用方决定是否落日志。
"""
from __future__ import annotations

import random

from game import BUILDINGS


def rule_turn(world, name: str, rng: random.Random, max_actions: int = 12,
              on_action=None, on_result=None) -> list:
    """行为克隆采样用的两个钩子：

    - `on_action(tool, args)`：**执行之前**回调（此刻的世界状态就是该动作的输入状态）
    - `on_result(tool, args, ok)`：**执行之后**回调。规则 AI 会尝试注定失败的动作
      （资源不够的建造），调用方应当只在 `ok=True` 时把样本入库。
    """
    rng = random.Random(rng.randrange(1 << 30))
    acts: list[tuple[str, dict, bool, str]] = []

    def do(tool: str, args: dict, fn, *a, **k) -> None:
        if len(acts) >= max_actions:
            return
        if on_action is not None:
            on_action(tool, args)
        ok, msg = fn(*a, **k)
        if on_result is not None:
            on_result(tool, args, ok)
        acts.append((tool, args, ok, msg))

    # 决定用：需要多少电（工厂/兵营/市政厅都耗电）
    need_energy = sum(
        (1 if BUILDINGS[bn]["kind"] in ("factory", "barracks", "townhall") else 0) * cnt
        for t in world.tiles.values() if t["owner"] == name
        for bn, cnt in t["buildings"].items()
    )
    r = world.nations[name].res
    own = world.own_tiles(name)
    lumber = sum(t["buildings"]["林场"] for t in world.tiles.values() if t["owner"] == name)
    plant_cnt = sum(t["buildings"]["木材能源厂"] + t["buildings"]["石油能源厂"]
                    for t in world.tiles.values() if t["owner"] == name)

    def _cand(bn: str) -> list:
        out = []
        for (x, y) in own:
            t = world.tiles[(x, y)]
            if t["built_this_turn"]:
                continue
            cr = BUILDINGS[bn]["cap_resource"]
            cnt = t["buildings"][bn]
            if cr is None:
                ms = BUILDINGS[bn].get("min_slots", 0)
                if ms and sum(t["buildings"].values()) < ms:
                    continue          # 兵营/市政厅等：需本地建筑位达标
                if cnt < 3:           # 不限资源的地（能源厂/工厂）：任地可建，留点节制
                    out.append((x, y))
            elif t["resources"].get(cr, 0) > cnt:
                out.append((x, y))
        return out

    def _build(bn: str) -> None:
        sites = _cand(bn)
        if sites:
            x, y = sites[0]
            do("build", {"tile": f"{x + 1} {y + 1}", "building": bn},
               world.build, name, x, y, bn)

    # 1) 木头是自用命脉：先保证至少 1 座林场
    if lumber == 0 or r["木头"] < 45:
        _build("林场")
        r = world.nations[name].res

    # 2) 电网：需要电且没电/停摆时，优先木头电厂（有林场管线），油电厂其次
    if need_energy > 0 and (plant_cnt == 0 or world.grid_short.get(name)):
        if _cand("木材能源厂") and (lumber >= plant_cnt + 1 or r["木头"] >= 20):
            _build("木材能源厂")
        elif _cand("石油能源厂"):
            _build("石油能源厂")

    # 3) 采集屯田：按需补 农场/矿场/黄金矿场（林场上面照顾过了）
    r = world.nations[name].res
    order: list[str] = []
    if r["粮食"] < 25:
        order += ["农场"] * 3
    if r["矿石"] < 20:
        order += ["矿场"] * 2
    if r["黄金"] < 350:
        order += ["黄金矿场"] * 2
    for bn in order:
        if len(acts) >= max_actions:
            break
        _build(bn)
        r = world.nations[name].res
    # 兵营：等粮食和黄金都稳了再造（每兵营每回合可征 1 军）
    if r["粮食"] >= 15 and r["黄金"] >= 500:
        _build("兵营")

    # 4) 征兵（兵营有空位 + 粮装够 + 电网正常）
    for (x, y) in own:
        t = world.tiles[(x, y)]
        if t["buildings"]["兵营"] > t["recruited_this_turn"] \
                and r["粮食"] >= 12 and r["装备"] >= 6 and not world.grid_short.get(name):
            do("recruit", {"tile": f"{x + 1} {y + 1}", "n": 1},
               world.recruit, name, x, y, 1, "步")
            r = world.nations[name].res
            if len(acts) >= max_actions:
                break

    # 5) 军事：身边够兵就打野人，否则朝最近的野人荒地挪一步
    my_armies = [a for a in world.armies if a["owner"] == name]
    if my_armies:
        guardians = [a for a in world.armies if a["owner"] == "野人"]
        for g in guardians[:6]:
            nearby = [a for a in my_armies if not a.get("engaged")
                      and max(abs(a["x"] - g["x"]), abs(a["y"] - g["y"])) <= 1]
            if len(nearby) >= 3:
                do("attack", {"army_ids": [a["id"] for a in nearby[:4]],
                              "x": g["x"] + 1, "y": g["y"] + 1},
                   world.attack, name, [a["id"] for a in nearby[:4]], g["x"], g["y"])
                break
        else:
            for a in my_armies:
                if a.get("engaged") or a.get("moved_turn") == world.turn or not guardians:
                    continue
                gx, gy = min(((g["x"], g["y"]) for g in guardians),
                             key=lambda p: max(abs(p[0] - a["x"]), abs(p[1] - a["y"])))
                cands = [p for p in world.neighbors(a["x"], a["y"]) if world.owned_by(*p) is None]
                if not cands:
                    continue
                step = min(cands, key=lambda p: max(abs(p[0] - gx), abs(p[1] - gy)))
                if max(abs(step[0] - gx), abs(step[1] - gy)) < max(abs(a["x"] - gx), abs(a["y"] - gy)):
                    do("move", {"army_id": a["id"], "x": step[0] + 1, "y": step[1] + 1},
                       world.move, name, a["id"], step[0], step[1])
                    break

    # 6) 市场调剂（不卖木头——要用来建设和发电）
    if len(acts) < max_actions and r["黄金"] < 350:
        if r["矿石"] >= 15:
            do("sell", {"good": "矿石", "qty": 10}, world.sell, name, "矿石", 10)
        elif r["粮食"] >= 25:
            do("sell", {"good": "粮食", "qty": 10}, world.sell, name, "粮食", 10)
    if len(acts) < max_actions and r["装备"] < 6 and r["黄金"] >= 300:
        do("buy", {"good": "装备", "qty": 2}, world.buy, name, "装备", 2)
    return acts
