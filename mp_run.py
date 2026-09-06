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
import queue
import random
import signal
import sys
import threading
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


def start_stdin_thread() -> queue.Queue:
    """后台线程立刻捕获终端输入（按 Enter 即入队），回合边界统一处理。

    避免"回合正在跑 LLM（几十秒~几分钟）时输入的命令等不到反应"。
    """
    q: queue.Queue = queue.Queue()

    def _run():
        try:
            buf = None  # 多行 send 的累积缓冲
            while True:
                line = sys.stdin.readline()
                if not line:
                    if buf is not None:
                        q.put("\n".join(buf))  # EOF 前没收 END 也发出
                    break
                s = line.rstrip("\n")
                st = s.strip()
                if buf is not None:
                    if st.upper() == "END":
                        q.put("\n".join(buf))
                        buf = None
                    else:
                        buf.append(s)  # 保留正文原始换行
                    continue
                if st.startswith("send ") or st in ("寄", "写信", "神秘信"):
                    toks = st.split()
                    if len(toks) == 2:  # 恰为 `send 国家` → 进入多行模式
                        buf = [st]
                        continue
                if st:
                    q.put(st)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return q


def make_world(cfg, force_new: bool, save_path: Path) -> tuple[World, bool]:
    if not force_new and save_path.exists():
        try:
            return World.load(save_path), False
        except Exception as e:
            print(f"读档失败({e})，重开新局")
    # 带 polity 的条目=待加入国（如匈奴），不作为开局国家；用 `add` 中途加入
    start_names = [n["name"] for n in cfg["nations"] if not n.get("polity")]
    w = World(size=cfg.get("map_size", 60), seed=cfg.get("seed"), nations=start_names)
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
    # 中途加国的 AI 模板：复用第一个配置了 base_url/api_key 的国家（如 arkcoding+glm-5.3）
    _template = next((n for n in cfg["nations"] if n.get("base_url") and n.get("api_key")), {})
    cmd_queue = start_stdin_thread()
    rng = random.Random(2026)
    # 待加入国（带 polity）自动登场：只在开新局生效；在 enable_turn..enable_turn_max 区间随机挑一回合
    standby_at: dict[str, int] = {}
    if is_new:
        for n in cfg["nations"]:
            if n.get("polity"):
                lo = int(n.get("enable_turn") or 0)
                hi = int(n.get("enable_turn_max") or lo)
                standby_at[n["name"]] = rng.randint(lo, hi) if hi >= lo else lo
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

    print(f"开始看海。存档 {save_path}，日志 {journal_path}。Ctrl-C 中断存档。"
          f"命令：`add 国名 [匈奴]` 中途加国；`send 国家 内容` 寄神秘来信"
          f"（多行先 `send 国家` 粘贴正文以 END 收尾；或 `send 国家 @文件路径` 从文件读）。")
    while not stop["flag"]:
        cmds = []
        while True:
            try:
                cmds.append(cmd_queue.get_nowait())
            except queue.Empty:
                break
        for cmd in cmds:
            parts = cmd.split(maxsplit=2)  # send 正文可含换行，只拆前两段
            if parts[0] in ("add", "加", "加入"):
                if len(parts) < 2:
                    emit("用法：add 国名 [匈奴]，如 `add 匈奴` / `add 秦`")
                else:
                    nm = parts[1]
                    polity = parts[2] if len(parts) > 2 else (
                        "huns" if nm in ("匈奴", "huns", "hun")
                        else cfg_by_name.get(nm, {}).get("polity", ""))
                    try:
                        ok, msg = world.add_nation(nm, polity)
                        if ok:
                            # 中途加的国也走 LLM：复用配置模板（可被 config 里预写的同名项覆盖）
                            cfg_by_name.setdefault(nm, dict(_template))
                            cfg_by_name[nm]["name"] = nm
                    except Exception as e:
                        ok, msg = False, f"加国失败：{type(e).__name__}: {e}"
                    emit(msg if ok else f"⚠ {msg}")
            elif parts[0] in ("send", "寄", "写信", "神秘信"):
                if len(parts) < 3:
                    emit("用法：send 国家 内容（同行）；或多行先 `send 国家` 粘贴正文以 END 收尾；"
                         "或 `send 国家 @文件路径` 从文件读全文")
                else:
                    try:
                        to = parts[1]
                        text = parts[2]  # 多行正文原样保留换行
                        if text.startswith("@"):
                            p = Path(text[1:].strip())
                            text = p.read_text(encoding="utf-8")
                        ok, msg = world.mystery_letter(to, text)
                        emit(msg if ok else f"⚠ {msg}")
                    except Exception as e:
                        emit(f"⚠ 神秘来信失败：{type(e).__name__}: {e}")
            else:
                emit(f"未知命令：{cmd}（支持 add 国名 [匈奴] / send 国家 神秘人内容）")
        if cmds:
            flush(out, journal_path)
            continue
        if len(world.alive()) < 2:
            emit("只剩一个国家——终局。")
            flush(out, journal_path)
            break
        if world.turn >= max_turns:
            emit(f"到达回合上限 {max_turns}，结束。")
            flush(out, journal_path)
            break

        delivered = world.begin_turn()
        # 待加入国到点自动登场（仅新局；旧存档不自动，只走手动 `add`）
        if is_new and standby_at:
            _added = False
            for _nm, _at in standby_at.items():
                if _nm in world.nations or world.turn < _at:
                    continue
                _st = cfg_by_name.get(_nm, {})
                _ok, _msg = world.add_nation(_nm, _st.get("polity", ""))
                emit(_msg if _ok else f"⚠ 自动登场失败：{_msg}")
                _added = True
            if _added:
                flush(out, journal_path)
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
