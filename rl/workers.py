# -*- coding: utf-8 -*-
"""★ rollout 并行化 —— 规格见 `rl/REFACTOR_WORKERS_SPEC.md`（V.3c 那笔"是重构，不是调参"）。

**形状 = 规格 §4 的"简单的那个"**：N 个 worker 进程，各自一份 `ZhanguoEnv` +
**一份模型副本**（GPU 上 N 份 8.4 MB 的权重绰绰有余），各跑各的整局收集
（env.step + tokenize + batch=1 前向，全在 worker 本地，GPU 时间片自动重叠）；
父进程只做两件事：**每块更新后广播新权重**（唯一同步点）与**合并 Rollout**。
中央批量推理（攒 batch=32 一次前向）**按规格先不做** —— "等简单版跑通、量到瓶颈还在
前向上，再考虑"（§4 原话）。

**RNG 归谁（规格 §3.3.1 / §3.2）**
- **采样（`torch.multinomial`）在 worker 本地**，吃 worker 自己那条 torch 流
  （开局 `torch.manual_seed(seed·100003 + worker_id)` 一次，**跨局连续不重置** ——
  与 N=1 全局流的形状逐字同构，只是流从一条变 N 条）。
- 前向不消耗 RNG；父进程的 torch RNG 在并行模式下**一步都不摸**。
- ⇒ **N>1 与 N=1 轨迹不可比，这是规格声明的预期，不是缺陷**；
  N>1 跑出的 ckpt **不与任何历史 ckpt 比数值**（连同 §V.3b 的跨栈铁律一起生效）。

**合并顺序（规格 §3.3.3）**
- 第 k 局分给 worker `k % N`，**种子按全局局序抽**（`_take_seed`/`seed+=1` 那条链
  与 N=1 逐步一致，规格 §3.3.2）。
- Rollout 灌入顺序 = **(worker_id, 该 worker 内的局序号)**，父进程按这个顺序收、
  按这个顺序 `add`。★不是形式主义：`Rollout._scale()` 是按 `add()` 顺序更新的
  running RMS，顺序变了奖励缩放的历史就变了。
- GAE 不依赖局的先后（`gae()` 在 `done` 处断链）⇒ 并行收集**每局都是整局**，
  所有末尾步 `done=True`，`last_value` 恒 0，自举问题不存在。

**边界（并行模式的三条限制，写死）**
- 只支持整局收集：`--rollout-episodes 0`（定步数切块）不开并行（train.py 直接报错）。
- **与 `--teacher-baseline` 不兼容**（用户 2026-09-13 拍板）：基准不是"采样"、规格没
  给它位置，而藏线程/同步跑两种接法都有代价 —— 要老师基准就回串行（N=1）。
- `--rollout-cap` 在并行模式是**软上限**（整局粒度 ⇒ 最多超出一局的步数），越线会在
  日志打一行 ⚠；串行模式（`--workers 1`，默认）**逐字是原来那段代码**（规格 §3.1），
  cap 仍是原来的硬语义。

**量具不动（规格 §3.4）**：`evaluate()` / `--eval-only` / `probe_*` 全走父进程里
原来的单 env 顺序 `act()`，一个字节没碰。
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
import traceback
from types import SimpleNamespace

import numpy as np


# ---------------------------------------------------------------- worker 侧
def _collect_episode(env, model, args, seed: int):
    """跑**一整局**训练收集，一步一个 step 记录攒着（对齐串行 while 循环体内的逐字语义）。

    与 train.py 串行路径同一组零件：`act()`（含软加权，走 `policy_logits` 那条公式）、
    `_win()`（窗口与 obs 同一瞬间取 —— 串行注释里立的规矩，这里照抄）。
    `cand["actions"]` 在打包时换成占位长度（动作对象不出 worker；父进程侧
    `collate` 只数它的长度）。★老师基准不在并行模式里（用户拍板不兼容，见文件头）。
    """
    from rl.ppo import act
    from rl.train import _win
    use_win = args.net in ("pool", "tf")
    obs = env.reset(seed)
    steps, ep_ret = [], 0.0
    while True:
        w = _win(env, obs, use_win)
        idx, logp, val = act(model, obs, win=w, use_exec=False)  # ★采样不加权，见 train.py 的 SAMPLING_USE_EXEC)
        keep = obs
        obs, r, done, info = env.step(keep.cand["actions"][idx])
        c = dict(keep.cand)
        c["n"] = len(c.pop("actions"))
        steps.append({"grid": keep.grid.astype(np.float16), "glob": keep.glob,
                      "cand": c, "win": w, "act": int(idx),
                      "logp": float(logp), "val": float(val), "rew": float(r),
                      "done": bool(done), "ok": bool(info["ok"]),
                      "turn": int(info["turn"])})
        ep_ret += float(r)
        if done:
            return steps, ep_ret, env.summary()


def _worker_main(conn, wid: int, args_ns) -> None:
    """一个 worker 的全部人生：装 env + 模型副本 → 收 (`w`, 权重) / (`ep`, seed) → 交整局。"""
    import torch
    torch.set_num_threads(1)                   # 采样期 batch=1（train.py 同口径：多线程实测更慢）
    torch.manual_seed(int(args_ns.seed) * 100_003 + wid)     # ★本 worker 的独立流，跨局连续
    from rl.device import pick_device
    from rl.train import build_env, build_model
    env = build_env(args_ns)
    model = build_model(env, args_ns)
    model = model.to(pick_device(args_ns.device))
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return                             # 父进程没了：体面退场（daemon 是兜底）
        cmd = msg[0]
        if cmd == "exit":
            return
        try:
            if cmd == "w":                     # ★权重广播（每块更新后一次，唯一同步点）
                model.load_state_dict(msg[1], strict=False)   # 旧 ckpt 缺 exec_head 也能进
            elif cmd == "ep":
                steps, ep_ret, summary = _collect_episode(
                    env, model, args_ns, int(msg[1]))
                conn.send(("ep_over", steps, ep_ret, summary))
            else:
                raise RuntimeError(f"未知指令 {cmd!r}")
        except Exception:
            traceback.print_exc()              # 栈进父进程 stderr，好定位
            conn.close()
            os._exit(1)


# ---------------------------------------------------------------- 父进程侧
class WorkerPool:
    """N 个 worker 的池：广播权重 → 分发整局 → 按 (worker_id, 局序) 合并进 Rollout。

    `run_block` 对齐串行 while 收满 `rollout-episodes` 局的语义；权重从调用方的
    `model.state_dict()` 现取（放在 CPU 上发 —— CUDA 张量跨进程不可 pickle）。
    """

    def __init__(self, args, n_workers: int, new_seed):
        self.args = args
        self.W = int(n_workers)
        self.new_seed = new_seed               # () -> int：train.py 的抽种链（§3.3.2）
        ctx = mp.get_context("spawn")          # 不继承父进程的 CUDA 上下文
        self.procs, self.conns = [], []
        for k in range(self.W):
            pc, cc = ctx.Pipe()
            p = ctx.Process(target=_worker_main, args=(cc, k, args), daemon=True,
                            name=f"rl-envw-{k}")
            p.start()
            self.conns.append(pc)
            self.procs.append(p)

    def close(self) -> None:
        for c in self.conns:
            try:
                c.send(("exit",))
            except OSError:
                pass
        for p in self.procs:
            p.join(timeout=10)
            if p.is_alive():
                p.terminate()

    def _recv(self, w: int):
        try:
            return self.conns[w].recv()
        except (EOFError, OSError) as e:
            codes = [f"{k}:{p.exitcode}" for k, p in enumerate(self.procs)]
            raise RuntimeError(f"worker #{w} 断链（exitcodes {codes}；traceback 在其 stderr）") from e

    def run_block(self, model, rollout, eps: list) -> dict:
        args = self.args
        R = int(args.rollout_episodes)
        t0 = time.perf_counter()
        # 全局局序 j：抽种按 j 序（与 N=1 逐字同链），落位 j % W；每槽按自己序收
        seeds_by_w: list[list[int]] = [[] for _ in range(self.W)]
        for _j in range(R):
            seeds_by_w[_j % self.W].append(int(self.new_seed()))
        sd = {k: v.detach().cpu() for k, v in model.state_dict().items()}
        for w, sl in enumerate(seeds_by_w):
            if not sl:
                continue                       # 没活计的 worker 不播不发（下块有活再播）
            self.conns[w].send(("w", sd))
            for s in sl:
                self.conns[w].send(("ep", s))
        # ---- 收 + 合并：**顺序 = (worker_id, 槽内局序)**（§3.3.3，别改成"谁先完收谁"）
        got = 0
        for w, sl in enumerate(seeds_by_w):
            for seq in range(len(sl)):
                msg = self._recv(w)
                _steps, ep_ret, summary = msg[1], msg[2], msg[3]
                for e in _steps:
                    cand = dict(e["cand"])
                    cand["actions"] = [None] * cand.pop("n")
                    obs = SimpleNamespace(grid=e["grid"], glob=e["glob"], cand=cand)
                    rollout.add(obs, e["act"], e["logp"], e["val"], e["rew"],
                                e["done"], win=e["win"], ok=e["ok"],
                                turn=e.get("turn", 0))
                summary = dict(summary)
                summary["ep_return"] = ep_ret
                summary["ep_steps"] = len(_steps)
                summary["seed"] = sl[seq]
                eps.append(summary)
                got += 1
        assert got == R
        dt = max(1e-9, time.perf_counter() - t0)
        out = {"steps": len(rollout), "secs": round(dt, 1),
               "episodes": got,                              # 规格 §2.5：每块 env_steps/sec
               "steps_per_s": round(len(rollout) / dt, 1)}
        if len(rollout) > int(args.rollout_cap):    # 并行 = 整局粒度，cap 是软上限
            out["cap_warn"] = f"⚠ 并行整局收集超出 rollout-cap（{len(rollout)} > {args.rollout_cap}）"
        return out
