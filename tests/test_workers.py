# -*- coding: utf-8 -*-
"""`rl/workers.py`（rollout 并行化，规格 `rl/REFACTOR_WORKERS_SPEC.md`）的守门测试。

规格 §5 的完整验收（md5 对拍、nvidia-smi、旧 ckpt --resume）**在生产区做** ——
这里只跑"秒级"的部分：拒绝分支不碰训练，冒烟只开一局小图（对应验收 3 的最小版）。
"""
from __future__ import annotations

import csv
import inspect
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(extra: list[str], out: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONHASHSEED": "0"}
    base = [sys.executable, "-m", "rl.train", "--net", "mlp", "--device", "cpu",
            "--threads", "1", "--eval-every", "0", "--out", str(out)]
    return subprocess.run(base + extra, cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=240, env=env)


class TestGuards:
    """拒绝分支：便宜、且必须响亮（规格边界三条里的两条）。"""

    def test_workers_rejects_steps_mode(self, tmp_path):
        r = _run(["--workers", "2", "--rollout-episodes", "0", "--rollout-steps", "50",
                  "--iterations", "1", "--turns", "2", "--seed", "1"], tmp_path / "a")
        assert r.returncode != 0
        assert "整局" in (r.stderr + r.stdout)

    def test_workers_rejects_teacher_baseline(self, tmp_path):
        r = _run(["--workers", "2", "--teacher-baseline", "--rollout-episodes", "2",
                  "--iterations", "1", "--turns", "2", "--seed", "1"], tmp_path / "b")
        assert r.returncode != 0
        assert "teacher-baseline" in (r.stderr + r.stdout)

    def test_default_workers_is_one(self):
        """默认 1，且串行 while 物理上仍是原来那段（抽查几条语句还在原位）。"""
        import rl.train as T
        src = inspect.getsource(T.main)
        assert '"--workers", type=int, default=1' in src.replace("\n", " ").replace("  ", " ") \
            or "default=1" in src.split('"--workers"')[1].split(")")[0]
        # 串行主体逐字可寻（不是"重写一版行为等价的"）
        assert "rollout.add(keep, idx, logp, val, r, done, win=_w, ok=info[\"ok\"])" in src
        assert "_par_new_seed" in src and "WorkerPool" in src


class TestParallelSmoke:
    def test_workers_2_runs_and_merges(self, tmp_path):
        """并行端到端最小样（规格验收 3 的缩小版）：跑通、收口、记账形状正确。"""
        out = tmp_path / "p"
        r = _run(["--map-size", "8", "--turns", "3", "--iterations", "2",
                  "--rollout-episodes", "2", "--workers", "2", "--seed", "7"], out)
        assert r.returncode == 0, (r.stdout[-1200:], r.stderr[-2000:])
        rows = list(csv.DictReader((out / "log.csv").open(encoding="utf-8")))
        assert len(rows) == 2
        assert int(rows[0]["episodes"]) == 2 and int(rows[1]["episodes"]) == 4
        # 规格 §2.5：每块能读到 env_steps/sec
        assert float(rows[-1]["steps_per_s"]) > 0
        # 并行收集不留 NaN 权重（退化批守卫 + 合并序的最低保障）
        import torch
        ck = torch.load(out / "last.pt", map_location="cpu", weights_only=False)
        assert all(torch.isfinite(v).all() for v in ck["model"].values())
        assert ck.get("iter") == 2

    def test_sequential_default_untouched_smoke(self, tmp_path):
        """不传 --workers（=1）：老路能跑，且日志**没有** steps_per_s 列（列集不变）。"""
        out = tmp_path / "s"
        r = _run(["--map-size", "8", "--turns", "3", "--iterations", "1",
                  "--rollout-episodes", "2", "--seed", "7"], out)
        assert r.returncode == 0, (r.stdout[-1200:], r.stderr[-2000:])
        head = (out / "log.csv").open(encoding="utf-8").readline()
        assert "steps_per_s" not in head
