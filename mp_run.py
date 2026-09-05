# -*- coding: utf-8 -*-
"""多国自动一局编排器（看海模式）。

读配置 → 开新局或续局 → 无人值守：
  每回合轮流唤醒各国 agent（各国用工具行动）→ 统一结算 → 投信 → 存 journal/存档。
Observer（你）能看到：每个国家的每个行动、每封信、每场战、每桩外交。

用法：
    python3 mp_run.py                     # 用 mp_config.json，无档则开新局
    python3 mp_run.py --config x.json     # 指定配置
    python3 mp_run.py --new               # 强制重开（覆盖存档）
    python3 mp_run.py --turns 5           # 只跑 5 回合
    python3 mp_run.py --save x.json       # 覆盖存档路径（默认 mp_save.json）
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import sys
import time
from pathlib import Path

from mp import World
from mp_ai import dummy_turn, observer_board, observer_map, run_openai_turn


def observer(world, out, header: str):
    """把 world 新增的历史事件刷给看海端（含每封信/每个行动/战报/外交）。"""
    lines = world.fresh_history()
    if not lines:
        return
    out.append("")
    out.append(header)
    for h in lines:
        who = h.get("nation") or ""
        tag = {"信件": "✉", "外交": "🕊", "灭国": "☠", "领土": "🏳"}.get(h["phase"], "")
        if tag and h["text"].startswith(tag):
            tag = ""  # 文本自带图标，避免 ✉✉/🕊🕊 之类双打
        out.append(f"  [{h['turn']}·{h['phase']}]{who} {tag} {h['text']}")


def flush(out, path: Path | None = None, echo: bool = True):
    text = "\n".join(out)
    if echo and text:
        print(text, flush=True)
    if path is not None and text:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    out.clear()


def make_world(cfg, force_new: bool, save_path: Path) -> tuple[World, bool]:
    if not force_new and save_path.exists():
        try:
            return World.load(save_path), False
        except Exception as e:
            print(f"读档失败({e})，重开新局")
    w = World(size=cfg.get("map_size", 60), seed=cfg.get("seed"),
              nations=[n["name"] for n in cfg["nations"]])
    w.save(save_path)
    return w, True


def run() -> None:
    ap = argparse.ArgumentParser(description="EU4-like 多国自动一局")
    ap.add_argument("--config", default="mp_config.json")
    ap.add_argument("--new", action="store_true", help="强制开新局")
    ap.add_argument("--turns", type=int, default=None, help="最多跑多少回合")
    ap.add_argument("--save", default=None)
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    save_path = Path(args.save or cfg.get("save", "mp_save.json"))
    journal_path = Path(cfg.get("journal", "mp_journal.md"))
    max_turns = args.turns if args.turns is not None else cfg.get("max_turns", 200)
    world, is_new = make_world(cfg, args.new, save_path)

    cfg_by_name = {n["name"]: n for n in cfg["nations"]}
    rng = random.Random(2026)
    out: list[str] = []

    def emit(s=""):
        out.append(s)

    if is_new:
        emit(f"新开一局 {world.size}x{world.size}（种子 {world.seed}）："
             + "、".join(world.alive()) + " 各占 5 块十字，四周是野人。")
        flush(out, journal_path)

    stop = {"flag": False}

    def _sig(sig, frm):
        stop["flag"] = True
        world.save(save_path)
        emit("（Ctrl-C：已存档，随后退出）")
        flush(out, journal_path, echo=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig)

    print(f"开始看海。存档 {save_path}，日志 {journal_path}。Ctrl-C 中断存档。")
    while not stop["flag"]:
        if len(world.alive()) < 2:
            emit("只剩一个国家——终局。")
            flush(out, journal_path)
            break
        if world.turn >= max_turns:
            emit(f"到达回合上限 {max_turns}，结束。")
            flush(out, journal_path)
            break

        delivered = world.begin_turn()
        if delivered:
            observer(world, out, f"——— 第 {world.turn} 回合 · 投信 {delivered} 封 ———")
        else:
            emit(f"——— 第 {world.turn} 回合 ———")
        flush(out, journal_path)
        # Observer 大面板：一屏看各国
        emit(observer_board(world))
        flush(out, journal_path)

        alive = world.alive()
        # 顺序回合制（公平：轮流先手，无人被永久排最后）
        start = (world.turn - 1) % len(alive)
        for k in range(len(alive)):
            name = alive[(start + k) % len(alive)]
            if name not in world.nations:
                continue
            ncfg = cfg_by_name.get(name, {})
            t0 = time.time()
            if ncfg.get("base_url") and ncfg.get("api_key"):
                done = run_openai_turn(world, name, ncfg,
                                       max_steps=ncfg.get("max_steps", 24))
            else:
                done = dummy_turn(world, name, rng,
                                  max_actions=ncfg.get("max_actions", 12))
            secs = time.time() - t0
            observer(world, out, f"◈ {name} 行动完毕（{done} 次工具调用，{secs:.0f}s）")
            flush(out, journal_path)
            if stop["flag"]:
                break
            if name not in world.nations:
                emit(f"（{name} 已在行动中被灭）")

        if stop["flag"]:
            break
        world.resolve_turn()
        observer(world, out, f"——— 第 {world.turn} 回合结算 ———")
        flush(out, journal_path)
        world.save(save_path)
        # 全景地图（Observer 看海用）：覆盖写 mp_map.txt
        Path("mp_map.txt").write_text(observer_map(world), encoding="utf-8")
        emit("🗺 地图已更新 mp_map.txt（字母=国家占领，小写=无人野地地形）")
        flush(out, journal_path)
        time.sleep(0.1)

    # 终局统计
    emit("")
    emit("## 终局")
    for n in world.alive():
        emit(f"- {n}: 国土 {len(world.own_tiles(n))} 块，"
             f"国库 {world.nations[n].res['黄金']}，军队 {len(world.nation_armies(n))} 支，{world.econ_summary.get(n,'')}")
    emit("灭国者：" + ("、".join(n for n in world.order if n not in world.nations) or "无"))
    world.save(save_path)
    flush(out, journal_path)


if __name__ == "__main__":
    run()
