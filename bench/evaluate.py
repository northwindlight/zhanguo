#!/usr/bin/env python3
"""stock-bench automatic evaluator — English output, model-agnostic.

One command sets up an episode, runs it (model seats, offline seats, or a mix), checks the
ledger and writes a report you can publish:

    export OPENAI_API_KEY=sk-...
    python bench/evaluate.py --model my-model --base-url https://api.example.com/v1 \
        --seats 4 --seed 2026111303 --turns 60

    # offline self-test of the whole pipeline (no API key, no cost)
    python bench/evaluate.py --client sim --seats 3 --turns 12 --ticks 4000

Outputs, in `--out` (default `bench/reports/<utc-stamp>-<model>/`):
    report.md      human-readable report
    report.json    machine-readable result (ledger, checks, hashes, seat detail)
    audit.txt      per-seat accounting identity lines

Exit codes: 0 all seats ran and every ledger check passed; 1 ran but a check failed;
2 configuration or startup error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from stock_eval.episode import PROTOCOL, digest, run_episode  # noqa: E402


def _stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def build_spec(args) -> dict:
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("STOCK_EVAL_API_KEY")
    base_url = args.base_url or os.environ.get("STOCK_EVAL_BASE_URL")
    model = args.model or os.environ.get("STOCK_EVAL_MODEL")
    client = args.client
    if client == "llm":
        if not model:
            raise SystemExit("error: --model (or STOCK_EVAL_MODEL) is required for --client llm")
        if not api_key:
            raise SystemExit("error: no API key: pass --api-key or export OPENAI_API_KEY")
        if not base_url:
            raise SystemExit("error: --base-url (or STOCK_EVAL_BASE_URL) is required for --client llm")
    seats = []
    for i in range(args.seats):
        seat = {"name": args.names[i] if i < len(args.names) else f"seat{i + 1}",
                "client": client}
        if client == "llm":
            seat.update(base_url=base_url, api_key=api_key, model=model,
                        provider=args.provider, temperature=args.temperature,
                        max_tokens=args.max_tokens, max_steps=args.max_steps)
            if args.thinking:
                seat["thinking"] = args.thinking
            if args.reasoning_effort:
                seat["reasoning_effort"] = args.reasoning_effort
        seats.append(seat)
    return {"protocol": PROTOCOL,
            "name": args.name or f"{client}-{model or 'offline'}",
            "seed": args.seed,
            "ticks_per_day": args.ticks,
            "turns": args.turns,
            "language": args.language,
            "seats": seats}


def money(value) -> str:
    return f"{value:,.2f}" if isinstance(value, (int, float)) else "—"


def write_report(result: dict, out: Path, spec: dict, elapsed: float) -> None:
    score = result["score"]
    seats = result["seats"]
    checks = result["checks"]
    passed = sum(1 for c in checks if c["ok"])
    failed = [c for c in checks if not c["ok"]]
    mode = spec["seats"][0].get("client", "replay")
    model = spec["seats"][0].get("model") if mode == "llm" else "offline policy seats"

    lines = []
    lines.append(f"# stock-bench/1 evaluation report")
    lines.append("")
    lines.append(f"- model / seats: **{model}**({len(seats)} seats, client `{mode}`)")
    lines.append(f"- seed: `{spec['seed']}` · protocol: {spec['ticks_per_day']} ticks/day × "
                 f"{spec['turns']} rounds · language: `{spec['language']}`")
    lines.append(f"- wall clock: {elapsed / 60:.1f} min")
    lines.append(f"- spec hash: `{result['spec_hash'][:16]}`")
    lines.append("")
    lines.append("## Score")
    lines.append("")
    lines.append(f"**{score['index']:.2f}** (index, 100 = initial cash of "
                 f"{100_000:,}) — mean final liquidation cash {money(score['mean_final_cash'])}")
    lines.append("")
    lines.append("## Seats")
    lines.append("")
    lines.append("| seat | final cash | rank | trading P/L | compute | intelligence | borrow | "
                 "interest | turnover | fills |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for s in sorted(seats, key=lambda x: -x["final_cash"]):
        lines.append(f"| {s['name']} | {money(s['final_cash'])} | {s['final_rank']} | "
                     f"{money(s['realized_pnl'])} | {money(s['compute_expenses'])} | "
                     f"{money(s['intelligence_expenses'])} | {money(s['borrow_fees'])} | "
                     f"{money(s['interest_paid'])} | {money(s['turnover'])} | {s['fills']} |")
    lines.append("")
    lines.append("## Ledger checks")
    lines.append("")
    lines.append(f"{passed}/{len(checks)} passed"
                 + ("" if not failed else " — **FAILED**: " + "; ".join(c["name"] for c in failed)))
    lines.append("")
    lines.append("Every seat must satisfy `final cash = 100,000 + realized P/L − compute − "
                 "intelligence − borrow fees − interest` and `fill cash flow = realized P/L` "
                 "with a residual of zero. A failure means the run is not publishable.")
    lines.append("")
    lines.append("## Replay")
    lines.append("")
    lines.append("| field | value |")
    lines.append("|---|---|")
    for key in ("ledger_hash", "fill_hash", "final_world_hash"):
        lines.append(f"| `{key}` | `{result['audit'][key]}` |")
    lines.append(f"| rounds executed | {result['audit']['rounds']} |")
    lines.append(f"| engine | {result['engine']['mods']} · pams {result['engine']['pams']} · "
                 f"commit {result['engine']['engine_commit']} |")
    lines.append("")
    lines.append("Re-running the stored spec must reproduce all three hashes byte for byte: "
                 "`python bench/run_eval.py verify --result report.json`.")
    lines.append("")
    lines.append("## What this number is, and is not")
    lines.append("")
    lines.append("It is **one exam score**: absolute liquidation cash under one random seed, "
                 "with in-game compute and information priced into the P/L. It is not a ranking "
                 "with confidence intervals, and it is not an estimate of the model's ability "
                 "distribution. Retakes are optional; the seed of every published run must be "
                 "disclosed and, for a leaderboard round, shared by every entrant.")
    lines.append("")
    lines.append(f"Disclosure — equal-weight buy & hold on this seed: "
                 f"{result['beta']['equal_weight_buy_hold']:.4%} (a β disclosure, not a "
                 "denominator; the market is two-sided).")
    lines.append("")

    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    audit = [f"{c['name']}: {'OK' if c['ok'] else 'FAIL'} ({c['detail']})" for c in checks]
    (out / "audit.txt").write_text("\n".join(audit) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="stock-bench-evaluate",
        description="Run one stock episode and write a publishable English report.")
    ap.add_argument("--client", choices=("llm", "sim"), default="llm",
                    help="llm = model seats (needs an API); sim = offline seats (self-test)")
    ap.add_argument("--model", help="model id, e.g. deepseek-v4-flash (env STOCK_EVAL_MODEL)")
    ap.add_argument("--base-url", help="OpenAI-compatible base URL (env STOCK_EVAL_BASE_URL)")
    ap.add_argument("--api-key", help="API key (or env OPENAI_API_KEY / STOCK_EVAL_API_KEY)")
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--max-steps", type=int, default=28)
    ap.add_argument("--thinking", default=None, help="optional provider thinking mode")
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--seats", type=int, default=4, help="1-4 seats (stock supports four)")
    ap.add_argument("--names", nargs="*", default=[], help="optional seat names")
    ap.add_argument("--seed", type=int, default=2026111303)
    ap.add_argument("--turns", type=int, default=60, help="rounds (60 = full protocol)")
    ap.add_argument("--ticks", type=int, default=20000, help="ticks per trading day")
    ap.add_argument("--language", default="en-US", choices=("en-US", "zh-CN"))
    ap.add_argument("--name", help="episode name, used in the report title")
    ap.add_argument("--out", help="output directory (default bench/reports/<stamp>-<model>)")
    ap.add_argument("--dry-run", action="store_true", help="validate arguments and exit")
    args = ap.parse_args(argv)

    if not 1 <= args.seats <= 4:
        raise SystemExit("error: --seats must be between 1 and 4")
    spec = build_spec(args)
    if args.dry_run:
        model = spec["seats"][0].get("model", "offline")
        print(f"configuration OK: client={args.client} model={model} seats={args.seats} "
              f"seed={args.seed} turns={args.turns} ticks={args.ticks} language={args.language}")
        return 0

    print(f"stock-bench/1 · client={args.client} · seats={args.seats} · seed={args.seed} · "
          f"{args.ticks} ticks/day × {args.turns} rounds", flush=True)
    started = time.perf_counter()
    result = run_episode(spec)
    elapsed = time.perf_counter() - started

    model = spec["seats"][0].get("model") if args.client == "llm" else "offline"
    out = Path(args.out) if args.out else HERE / "reports" / f"{_stamp()}-{model}"
    write_report(result, out, spec, elapsed)

    score = result["score"]
    print("")
    print(f"SCORE {score['index']:.2f}  mean final cash {score['mean_final_cash']:,.2f}  "
          f"({len(result['seats'])} seats)")
    for s in sorted(result["seats"], key=lambda x: -x["final_cash"]):
        print(f"  {s['name']:<10} cash={s['final_cash']:>12,.2f} rank={s['final_rank']} "
              f"pnl={s['realized_pnl']:>10,.2f} "
              f"cost={s['compute_expenses'] + s['intelligence_expenses']:>8,.2f}")
    failed = [c for c in result["checks"] if not c["ok"]]
    print(f"checks {len(result['checks']) - len(failed)}/{len(result['checks'])} ok"
          + (f"  FAILED: {[c['name'] for c in failed]}" if failed else ""))
    print(f"ledger {result['audit']['ledger_hash'][:16]}  "
          f"world {result['audit']['final_world_hash'][:16]}")
    print(f"wall {elapsed / 60:.1f} min -> {out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
