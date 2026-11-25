"""PPO 训练循环：C++ 采样器（Codex 的 zhanguo_torch）采样 + Python 更新（ppo_update.py）。

每轮：导出 fp32 策略 → C++ 采 N 局 → ppo_update 产出下一个检查点。KL 锚固定为 BC 检查点。
日志格式与 rl.train 的 log.jsonl 兼容（reward/win/invalid/by_opp/turns/dec_per_s），供看守脚本使用。

用法（Colab）：
  python3 -m rl.cxloop --bc runs/bcS/bc.pt --cpp /content/cx/zhanguo-cpp \
      --bin /content/build-zhanguo/zhanguo_torch --out runs/cxS --minutes 120
"""
import argparse
import collections
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import torch


def run(cmd, log):
    cmd = [str(c) for c in cmd]
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    log.write(f"$ {' '.join(map(str, cmd))}  ({time.time() - t:.1f}s, rc={r.returncode})\n")
    if r.returncode != 0:
        log.write(r.stdout[-3000:] + r.stderr[-3000:])
        log.flush()
        raise RuntimeError(f"command failed: {cmd[0]} rc={r.returncode}")
    return r.stdout


def sample_stats(text):
    games = [dict(re.findall(r"(\w+)=(\S+)", l)) for l in text.splitlines() if l.startswith("game ")]
    tail = dict(kv for l in text.splitlines() if not l.startswith("game ") for kv in re.findall(r"(\w+)=(\S+)", l))
    dec = sum(int(g["decisions"]) for g in games)
    by = collections.defaultdict(list)
    for g in games:
        by[g["opp"]].append(float(g["reward"]))
    seat_of = lambda g: int(g["seat"])
    return dict(
        games=len(games), decisions=dec,
        reward=sum(float(g["reward"]) for g in games) / max(1, len(games)),
        win=sum(int(g["winner"]) == seat_of(g) for g in games) / max(1, len(games)),
        lose=sum(int(g["winner"]) not in (-1, seat_of(g)) for g in games) / max(1, len(games)),
        turns=sum(int(g["turns"]) for g in games) / max(1, len(games)),
        invalid=sum(int(g["invalid"]) for g in games) / max(1, dec),
        by_opp={k: sum(v) / len(v) for k, v in by.items()},
        dec_per_s=float(tail.get("decisions_per_second", 0)),
        infer_s=float(tail.get("inference_seconds", 0)),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bc", required=True, help="BC 检查点（初始化 + 固定 KL 锚）")
    ap.add_argument("--cpp", required=True, help="zhanguo-cpp 源码目录（export_policy.py / ppo_update.py）")
    ap.add_argument("--bin", required=True, help="zhanguo_torch 可执行文件")
    ap.add_argument("--out", default="runs/cxS")
    ap.add_argument("--minutes", type=float, default=120)
    ap.add_argument("--games", type=int, default=96, help="每次更新的对局数")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--size", type=int, default=13)
    ap.add_argument("--max-turns", type=int, default=150)
    ap.add_argument("--seed0", type=int, default=20000)
    ap.add_argument("--lr", type=float, default=1e-5,
                    help="L4 扫描：2e-5 时 4 个 minibatch 就 KL(0.03) 早停，5e-6 用到约 70%；用户 2026-11-21：可适当提高")
    ap.add_argument("--target-kl", type=float, default=0.05, help="更新器的 KL 早停阈值（配合提高的学习率，原 0.03）")
    ap.add_argument("--mode", default="native", choices=["native", "fp32"], help="采样精度；native=bf16 主干")
    ap.add_argument("--bf16-tol", type=float, default=1.0, help="导出自检容差（更新器重算旧 logp，采样端精度不进比值）")
    ap.add_argument("--updater", default="ppo_update_v2.py", help="需重算旧 logp 的版本")
    ap.add_argument("--amp", action="store_true", help="更新端 bf16 autocast")
    ap.add_argument("--keep-every", type=int, default=10)
    ap.add_argument("--gae-lambda", type=float, default=None, help="回合级 GAE 的 λ；不给=终局蒙特卡洛")
    ap.add_argument("--invalid-penalty", type=float, default=0.0,
                    help="无效动作的优势扣分；默认 0（用户：无效动作算探索，无效率只作诊断）")
    a = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "loop.log", "a")
    ck = out / "ck0000.pt"
    if not ck.exists():                                   # 只留模型与配置：不把 BC 的 Adam 状态带进 PPO
        bc = torch.load(a.bc, map_location="cpu", weights_only=True)
        torch.save(dict(model=bc["model"], cfg=bc["cfg"]), ck)
    py = sys.executable
    t_start = time.time()
    t_end = t_start + a.minutes * 60
    i = 0
    while time.time() < t_end:
        t0 = time.time()
        pol, roll, nxt = out / f"pol{i:04d}.pt", out / f"roll{i:04d}", out / f"ck{i + 1:04d}.pt"
        for p in (pol, nxt):
            p.unlink(missing_ok=True)
        shutil.rmtree(roll, ignore_errors=True)
        run([py, f"{a.cpp}/export_policy.py", ck, pol, "--python-root", root]
            + (["--native-bf16", "--bf16-tol", a.bf16_tol] if a.mode == "native" else []), log)
        t1 = time.time()
        s = sample_stats(run([a.bin, "sample", pol, "cuda", a.games, a.games, a.size, a.max_turns,
                              a.seed0 + i * a.games, a.threads, a.mode, roll], log))
        t2 = time.time()
        u = json.loads(run([py, f"{a.cpp}/{a.updater}", ck, roll, nxt, "--python-root", root,
                            "--device", "cuda", "--reference", a.bc, "--aux-weight", "0", "--lr", a.lr,
                            "--seed", i, "--invalid-penalty", a.invalid_penalty, "--target-kl", a.target_kl]
                           + (["--amp"] if a.amp else [])
                           + (["--gae-lambda", a.gae_lambda] if a.gae_lambda is not None else []),
                           log).strip().splitlines()[-1])
        t3 = time.time()
        row = dict(upd=i + 1, minutes=(time.time() - t_start) / 60, **s,
                   ppo_kl=u["approx_kl"], ppo_loss=u["loss"], minibatches=u["minibatches"],
                   sampler_dlogp=u.get("sampler_dlogp"), clip_frac=u.get("clip_frac"),
                   export_s=t1 - t0, sample_s=t2 - t1, update_s=t3 - t2)
        with open(out / "log.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")
        log.write(json.dumps(row) + "\n")
        log.flush()
        shutil.rmtree(roll, ignore_errors=True)
        pol.unlink(missing_ok=True)
        if i % a.keep_every != 0:
            ck.unlink(missing_ok=True)
        shutil.copyfile(nxt, out / "ckpt.pt")
        ck = nxt
        i += 1
    print("done", i, "updates", flush=True)


if __name__ == "__main__":
    main()
