"""stock-bench/1 —— 一局确定性对局的最小可复现封装（无 LLM、无网络、无付费接口）。

设计约束（与论文 §8.2 Layer 1 一致）
------------------------------------
* 全员同卷：一局一份 spec，四席（或多席）共享同一 seed 与同一协议参数。
* 分数是绝对清算现金：不做归一化、不报超额。
* 可复核：结果里带账本哈希、成交哈希、逐回合世界哈希；`verify` 重跑比对。

脚本来源
--------
每个席位的动作由「盘前写入的受限 Python 脚本」决定（stock 沙盒，`def on_tick(): ...`）。
本模块只负责把 spec 里的脚本注入引擎并按回合推进，不执行任何宿主 Python 代码。
"""
from __future__ import annotations

import hashlib
import json
import platform
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]           # D:\code\zhanguo
INITIAL_CASH = 100_000.0
PROTOCOL = "stock-bench/1"

import sys
if str(ROOT) not in sys.path:                        # noqa: E402
    sys.path.insert(0, str(ROOT))

from host import GameHost                            # noqa: E402


# ---------------------------------------------------------------------------
# 规范化与哈希
# ---------------------------------------------------------------------------

def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def spec_hash(spec) -> str:
    return digest({k: v for k, v in spec.items() if k not in ("notes", "name")})


# ---------------------------------------------------------------------------
# spec 装载
# ---------------------------------------------------------------------------

def _read_source(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"空策略文件: {path}")
    return text


def load_spec(path: Path) -> dict:
    """读 spec JSON，并把 `policy` / `sources` 里的相对路径解析成脚本源码。

    seat 的写法三选一：
      "policy": "policies/cash.py"              —— 一份脚本，从第 1 回合起一直有效
      "sources": {"1": "a.py", "25": "b.py"}    —— 按回合改写（未写的回合沿用上一份）
      "scripts": {"1": "<源码>"}                 —— 直接内嵌源码（适合程序生成的提交）
    """
    path = path.resolve()
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec.setdefault("protocol", PROTOCOL)
    spec.setdefault("ticks_per_day", 20000)
    spec.setdefault("turns", 60)
    spec.setdefault("seats", [])
    base = path.parent
    seats = []
    for i, raw in enumerate(spec["seats"]):
        seat = dict(raw)
        seat["name"] = seat.get("name") or f"seat{i}"
        if "policy" in seat:
            seat["scripts"] = {"1": _read_source(base / seat.pop("policy"))}
        elif "sources" in seat:
            seat["scripts"] = {str(r): _read_source(base / p) for r, p in seat.pop("sources").items()}
        else:
            seat.setdefault("scripts", {})
        if not seat["scripts"]:
            raise ValueError(f"席位 {seat['name']} 没有任何脚本")
        seat["scripts"] = {str(k): str(v) for k, v in seat["scripts"].items()}
        seat.setdefault("purchases", {})
        seats.append(seat)
    spec["seats"] = seats
    if not 1 <= len(seats) <= 4:
        raise ValueError("席位数量必须在 1..4（stock 发行版上限 4）")
    return spec


def build_config(spec: dict) -> dict:
    nations = []
    for i, seat in enumerate(spec["seats"]):
        nations.append(dict(id=f"stock:player{i}", name=seat["name"], client="replay",
                            scripts=seat["scripts"]))
    cfg = dict(mods="mods.stock.toml", seed=int(spec["seed"]),
               max_turns=int(spec["turns"]), nations=nations)
    if spec.get("language"):
        # 只影响模型看到的散文（简报/工具描述），不进入世界状态与审计哈希。
        cfg["language"] = str(spec["language"])
    if spec.get("planning_workers") is not None:
        cfg["planning_workers"] = int(spec["planning_workers"])
    return cfg


# ---------------------------------------------------------------------------
# 一局
# ---------------------------------------------------------------------------

def _round_row(host, actors, player_set):
    market, clock = host.world.market, host.world.clock
    session = clock.round - 1
    trading = ((session - 1) % clock.cycle) < clock.trading_days
    row = dict(round=session, trading_day=bool(trading),
               close={s: round(p, 4) for s, p in market.close.items()}, actors={})
    for actor in actors:
        acc = host.world.portfolio.accounts[actor]
        row["actors"][actor] = dict(cash=acc["cash"], debt=acc["debt"], alive=acc["alive"])
    if trading:
        picks = [[f["execution_id"], f["actor"], f["side"], f["symbol"], f["quantity"],
                  f["price"], f["fee"], f["tick"], f["order_id"]]
                 for f in market.last_fills if f["actor"] in player_set]
        row["fills"] = picks
        row["fills_hash"] = digest(picks)
    else:
        row["fills"] = []
        row["fills_hash"] = digest([])
    return row


def run_episode(spec: dict) -> dict:
    cfg = build_config(spec)
    actors = [f"stock:player{i}" for i in range(len(spec["seats"]))]
    player_set = set(actors)
    started = time.perf_counter()
    host = GameHost.new(cfg)
    host.world.clock.session_ticks = int(spec["ticks_per_day"])
    initial_symbols = sorted(host.world.market.stocks)
    rounds, world_hashes = [], []
    while not host.finished():
        for i, seat in enumerate(spec["seats"]):
            actor = actors[i]
            if host.world.clock.can_act(actor):
                for item in seat.get("purchases", {}).get(str(host.clock()), []) or []:
                    host.player(actor).submit("compute", item)
                if seat.get("purchase_pool"):
                    host.player(actor).submit("compute", {"action": "buy", "kind": "pool"})
        host.cycle()
        rounds.append(_round_row(host, actors, player_set))
        world_hashes.append(digest(host.runtime.save_mod_state(host.world)))
    elapsed = time.perf_counter() - started
    return _assemble(spec, host, actors, initial_symbols, rounds, world_hashes, elapsed)


# ---------------------------------------------------------------------------
# 结果装配
# ---------------------------------------------------------------------------

def _side_cashflow(side: str, amount: float, fee: float) -> float:
    if side in ("buy", "cover"):
        return -amount - fee
    return amount - fee


def _assemble(spec, host, actors, initial_symbols, rounds, world_hashes, elapsed) -> dict:
    accounts = host.world.portfolio.accounts
    final_close = {s: round(p, 4) for s, p in host.world.market.close.items()}
    seats, checks = [], []

    for i, actor in enumerate(actors):
        acc = accounts[actor]
        name = spec["seats"][i]["name"]
        fills = [f for row in rounds for f in row["fills"] if f[1] == actor]
        liq = acc.get("final_liquidations") or []
        flow, turnover, fees = 0.0, 0.0, 0.0
        sides = {"buy": 0, "sell": 0, "short": 0, "cover": 0}
        for f in fills:
            amount = f[4] * f[5]
            flow += _side_cashflow(f[2], amount, f[6])
            turnover += amount
            fees += f[6]
            sides[f[2]] = sides.get(f[2], 0) + 1
        for close in liq:
            amount = float(close.get("amount") or 0.0)
            fee = float(close.get("fee") or 0.0)
            flow += _side_cashflow(close.get("side", "sell"), amount, fee)
            turnover += amount
            fees += fee
            sides["final_" + str(close.get("side"))] = sides.get("final_" + str(close.get("side")), 0) + 1
        final_cash = acc.get("final_cash")
        slot = (host.world.scripts.rows.get(actor) or {}).get("1") or {}
        script_result = slot.get("result") or {}
        identity = (INITIAL_CASH + (acc.get("realized_pnl") or 0.0)
                    - (acc.get("compute_expenses") or 0.0)
                    - (acc.get("intelligence_expenses") or 0.0)
                    - (acc.get("borrow_fees") or 0.0)
                    - (acc.get("interest_paid") or 0.0))
        seats.append(dict(
            id=actor, name=name, final_cash=final_cash, final_rank=acc.get("final_rank"),
            realized_pnl=acc.get("realized_pnl"),
            compute_expenses=acc.get("compute_expenses"),
            intelligence_expenses=acc.get("intelligence_expenses"),
            borrow_fees=acc.get("borrow_fees"),
            interest_paid=acc.get("interest_paid"),
            turnover=round(turnover, 2), fees=round(fees, 2),
            fills=len(fills), sides=sides,
            final_liq_count=len(liq),
            holdings_end=[dict(symbol=p["symbol"], quantity=p["quantity"],
                               price=p["price"], market_value=p["market_value"])
                          for p in host.world.portfolio.holdings(actor)["positions"]],
            equity_curve=[dict(round=p["round"], wealth=p["wealth"]) for p in acc["equity"]],
            script=dict(status=script_result.get("status"),
                        error_type=(script_result.get("error") or {}).get("type"),
                        error_message=(script_result.get("error") or {}).get("message"),
                        steps=script_result.get("steps"), calls=script_result.get("calls"),
                        actions=script_result.get("actions"),
                        revision=slot.get("revision"), output=script_result.get("output")),
        ))
        checks.append(dict(name=f"{name} 会计恒等", ok=abs(identity - final_cash) < 0.02,
                           detail=f"{identity:.2f} vs {final_cash:.2f}"))
        checks.append(dict(name=f"{name} 成交现金流 = realized_pnl",
                           ok=abs(flow - (acc.get("realized_pnl") or 0.0)) < 0.02,
                           detail=f"{flow:.2f} vs {acc.get('realized_pnl')}"))

    mean_cash = sum(s["final_cash"] for s in seats) / len(seats)
    entry_close = rounds[0]["close"]
    common = [s for s in initial_symbols if s in entry_close and s in final_close and entry_close[s]]
    beta = (sum(final_close[s] / entry_close[s] - 1 for s in common) / len(common)) if common else None
    # 账本哈希只覆盖「读数的定义」：逐回合收盘与成交哈希、每席终局数字与成本。
    # 刻意不含 elapsed_sec / 脚本输出 / 净值曲线这类运行期元数据，否则重放会因为
    # 机器快慢或 harness 版本改写而假失配。
    ledger = dict(seed=spec["seed"], ticks=spec["ticks_per_day"], turns=spec["turns"],
                  rounds=[dict(round=r["round"], close=r["close"], fills_hash=r["fills_hash"],
                               actors={a: [row["cash"], row["debt"]] for a, row in r["actors"].items()})
                          for r in rounds],
                  seats=[{k: s[k] for k in ("id", "final_cash", "realized_pnl", "compute_expenses",
                                            "intelligence_expenses", "borrow_fees", "interest_paid",
                                            "turnover", "fees", "fills", "sides")} for s in seats])
    return dict(
        protocol=spec["protocol"],
        spec=spec,
        spec_hash=spec_hash(spec),
        score=dict(index=round(mean_cash / (INITIAL_CASH / 100), 4),
                   mean_final_cash=round(mean_cash, 2), seats=len(seats)),
        seats=seats,
        checks=checks,
        audit=dict(ledger_hash=digest(ledger), fill_hash=digest([f for r in rounds for f in r["fills"]]),
                   final_world_hash=world_hashes[-1], world_hashes=world_hashes,
                   rounds=len(rounds), elapsed_sec=round(elapsed, 3)),
        beta=dict(label="该 seed 的被动 β 披露（不参与评分）", equal_weight_buy_hold=beta),
        engine=dict(mods="mods.stock.toml", pams=_pams_version(), python=platform.python_version(),
                    engine_commit=_engine_commit()),
    )


def _pams_version() -> str:
    try:
        from importlib.metadata import version
        return version("pams")
    except Exception:
        return "unknown"


def _engine_commit() -> str:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"
