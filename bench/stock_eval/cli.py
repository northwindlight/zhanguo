"""stock-bench/1 命令行：run / verify / score。

用法（仓库根目录，或任意目录用绝对路径）：
    python bench/run_eval.py run --spec bench/episodes/quick.json
    python bench/run_eval.py verify --result bench/results/<name>.json
    python bench/run_eval.py score  --result bench/results/<name>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .episode import PROTOCOL, digest, load_spec, run_episode, spec_hash

RESULTS = Path(__file__).resolve().parents[1] / "results"


def _write(result: dict, out: Path | None, compact: bool) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = out or RESULTS / f"{result['spec_hash'][:12]}-s{result['spec']['seed']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=None if compact else 1,
                              separators=(",", ":") if compact else None), encoding="utf-8")
    return out


def _summary(result: dict) -> str:
    s = result["score"]
    lines = [f"score={s['index']:.2f}  mean_final_cash={s['mean_final_cash']:.2f}  seats={s['seats']}",
             f"ledger_hash={result['audit']['ledger_hash'][:16]}  "
             f"final_world_hash={result['audit']['final_world_hash'][:16]}"]
    for seat in result["seats"]:
        lines.append(f"  {seat['name']:<12} cash={seat['final_cash']:>11.2f} "
                     f"rank={seat['final_rank']} pnl={seat['realized_pnl']:>10.2f} "
                     f"cost={seat['compute_expenses'] + seat['intelligence_expenses']:>9.2f}")
    bad = [c["name"] for c in result["checks"] if not c["ok"]]
    lines.append(f"checks: {len(result['checks']) - len(bad)}/{len(result['checks'])} ok"
                 + (f"  FAILED: {bad}" if bad else ""))
    return "\n".join(lines)


def cmd_run(args) -> int:
    spec = load_spec(Path(args.spec))
    started = time.perf_counter()
    result = run_episode(spec)
    if args.name:
        result["name"] = args.name
    out = _write(result, Path(args.out) if args.out else None, args.compact)
    print(_summary(result))
    print(f"wall={time.perf_counter() - started:.1f}s  -> {out}")
    return 0 if all(c["ok"] for c in result["checks"]) else 1


def cmd_verify(args) -> int:
    original = json.loads(Path(args.result).read_text(encoding="utf-8"))
    spec = original["spec"]
    again = run_episode(spec)
    pairs = [("spec_hash", spec_hash(spec), again["spec_hash"]),
             ("ledger_hash", original["audit"]["ledger_hash"], again["audit"]["ledger_hash"]),
             ("fill_hash", original["audit"]["fill_hash"], again["audit"]["fill_hash"]),
             ("final_world_hash", original["audit"]["final_world_hash"], again["audit"]["final_world_hash"]),
             ("score", original["score"], again["score"])]
    report = {"protocol": PROTOCOL, "result": str(Path(args.result).resolve()),
              "ok": all(a == b for _, a, b in pairs),
              "checks": [{"field": name, "match": a == b, "original": a, "rerun": b}
                         for name, a, b in pairs]}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for c in report["checks"]:
        mark = "OK  " if c["match"] else "DIFF"
        print(f"{mark} {c['field']}")
    print("VERIFIED" if report["ok"] else "MISMATCH")
    return 0 if report["ok"] else 1


def cmd_score(args) -> int:
    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    print(_summary(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="stock-bench", description="stock-bench/1 可复现评测封装")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="跑一局并写结果 JSON")
    p.add_argument("--spec", required=True, help="对局 spec JSON")
    p.add_argument("--out", help="结果输出路径（默认 bench/results/<spec_hash>-s<seed>.json）")
    p.add_argument("--name", help="给这一局起个名字（写进结果）")
    p.add_argument("--compact", action="store_true", help="紧凑 JSON（体积更小）")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("verify", help="重跑结果里的 spec，逐项比对哈希")
    p.add_argument("--result", required=True)
    p.add_argument("--out", help="把比对报告写到文件")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("score", help="打印已有结果")
    p.add_argument("--result", required=True)
    p.set_defaults(func=cmd_score)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
