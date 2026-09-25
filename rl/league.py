# -*- coding: utf-8 -*-
"""联赛权重池 —— 让池子里的对手**分化**，别一池子都是同一个打法。

用户 2026-09-25 的口径（逐条落到实现，**别自己发明**）
──────────────────────────────────────────────────────
1.「**联赛池从最新快照开始分化**」
   ⇒ `--league-from <pt>`：**所有在训成员都从同一份快照起跑**（不是各随机初始化）。
      起点相同、每局抽到的对手组合不同 ⇒ **风格自己漂开**。
      （原来的做法是 5 份随机初始化，大部分生下来就是废的，谈不上"分化"。）
2.「**老快照丢了，从新快照开始**」
   ⇒ 建池那一刻**不导入旧线**；池子从这一炉的新快照起算。
      ★ 这句说的是**建池时**，不是运行时删除 —— 运行时的规矩是第 3 条。
3.「**只增不删**」
   ⇒ 成员**永不删除**。打不动只是 `active=False`（不再抽上场），**仍留在账上**。
4.「**随机抽 pt**」
   ⇒ 每局从 active 里**不重复**随机抽 k 份上场。
5.「**标记每个 pt 的胜率，永久化到一个数据库或者 json 文件**」
   ⇒ roster 落 `rl/runs/league.json`（原子写）。**跨重启活着** —— 定时重启
      （`--restart-after`）换进程也不丢战绩，这正是"永久化"要解决的问题。
6.「**可以有两个固定主 pt，也可以没有**」
   ⇒ `--league-mains N`（缺省 2，可 0）：前 N 份成员钉为**主 pt** ——
      **不受淘汰规则约束**（它们是**基座本身**，不是候选，淘汰掉就没得炼了）。
7.「**打 10 局以上胜率低于 20% 的不再启用**」
   ⇒ `retire_min_games=10`、`retire_rate=0.20` ⇒ `active=False`。
   ★ **兜底**（否则会自己把自己搞死）：active 总数不得低于**当前最大国家数**
     （否则凑不齐一局），且**至少留 `min_learners` 份在训成员**。
   ★ **口径提醒**：K 国局里"随机"胜率是 `1/K` —— 3 国局 33%、5 国局 **20%**。
     ⇒ 固定 20% 这个阈值在 **5 国局里等于"和随机持平"**（偏严）。
     用户原话就是 20%，照做；但将来若发现 5 国局把池子淘汰空了，就是这里。

★ 为什么要有这个模块（用户的原话是「现在换家流完全占上风」）
  一池子同源同打法 ⇒ 训练里"对手"这一维是退化的，模型学不到"被克了怎么办"。
  让成员**分化**才是池子的意义；胜率账本是**判据**（谁还有用），不是目的。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

from .model import build_model


# ================================================================ 一个成员
@dataclass
class Member:
    """池子里的**一份**权重（+ 它的战绩账）。

    `kind` 三类：
      - `main` —— **主 pt**：永久 active、不受淘汰约束（基座本身）；
      - `live` —— **在训成员**：每局被抽到就往梯度里攒经验（权重在主档里，`path=None`）；
      - `snap` —— **冻结快照**：只当对手，永不训练（权重在 `path` 指向的文件里）。
    """
    mid: str
    kind: str
    born: int
    games: int = 0
    wins: int = 0
    active: bool = True
    path: str | None = None

    @property
    def rate(self) -> float:
        """胜率。**没打过 = 0.0** —— 但淘汰规则要求 `games >= min_games`，所以
        不会被误淘汰（"没打过"和"打得很差"是两件事，别在计数上混）。"""
        return (self.wins / self.games) if self.games else 0.0

    def as_json(self) -> dict:
        return {"mid": self.mid, "kind": self.kind, "born": int(self.born),
                "games": int(self.games), "wins": int(self.wins),
                "active": bool(self.active), "path": self.path,
                "rate": round(self.rate, 4)}

    def __str__(self) -> str:
        flag = "" if self.active else "×"
        return f"{self.mid}{flag}({self.kind},{self.wins}/{self.games})"


# ================================================================ 池子
class League:
    """权重池。**只增不删** —— 唯一的"减"是 `active=False`（不再抽上场）。"""

    VERSION = 1

    def __init__(self, db: str | os.PathLike | None, *, mains: int = 2,
                 retire_min_games: int = 10, retire_rate: float = 0.20,
                 max_k: int = 5, min_learners: int = 1,
                 fingerprint: dict | None = None, log=print):
        # ★ `db=None` = **不落盘的池子**（权重与账本都留内存）——
        #   给测试和临时实验用。★ 注意这意味着"永久化"那条口径**没有生效**，
        #   所以真起炉必须给 `--league-db`（否则定时重启会把战绩清零，
        #   而 10 局的门槛永远够不到 ⇒ 淘汰规则变死代码，**还不报错**）。
        self.db = Path(db) if db is not None else None
        self.mains = max(0, int(mains))
        self.retire_min_games = int(retire_min_games)
        self.retire_rate = float(retire_rate)
        self.max_k = max(1, int(max_k))
        self.min_learners = max(0, int(min_learners))
        self.fingerprint = dict(fingerprint or {})
        self.log = log
        self.members: dict[str, Member] = {}
        self._nets: dict[str, object] = {}       # mid -> PolicyNet（懒加载 + 缓存）
        self.updated_iter = 0

    # ---------------------------------------------------------- 成员
    def bind_live(self, mid: str, net, *, born: int = 0) -> Member:
        """把**在训**的那份挂进来（权重对象就是训练循环里那一个，不复制）。

        ★ 必须**挂引用而不是复制**：在训成员的权重每 iter 都在变，
          复制一份就变成"冻结快照"了。
        ★ 已经挂过的（`load()` 从账本读回来）**保留它原来的 `kind`** ——
          否则重启后重算一遍"前 N 份当主 pt"，会把主 pt 的身份**漂掉**。
        """
        m = self.members.get(mid)
        if m is None or m.kind not in ("main", "live"):
            kind = "main" if len(self._live()) < self.mains else "live"
            m = Member(mid=mid, kind=kind, born=born)
            self.members[mid] = m
        m.path = None
        self._nets[mid] = net
        return m

    def add_snapshot(self, net, it: int, *, mid: str | None = None) -> Member:
        """**冻一份**当前权重进池子（只增不删）。返回新成员。

        ★ 存在的意义就是"分化"：这些快照是**不同代**的对手，
          池子里因此有多个打法，而不是一池子同一个自己。
        """
        mid = mid or f"S{int(it):05d}"
        if mid in self.members:                  # 同一 iter 重复冻 → 直接返回（幂等）
            return self.members[mid]
        w = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
        p = None
        if self.db is not None:
            fp = self._snap_dir() / f"{mid}.pt"
            fp.parent.mkdir(parents=True, exist_ok=True)
            tmp = fp.with_suffix(".pt.tmp")
            torch.save({"weights": w, "fingerprint": self.fingerprint}, tmp)
            os.replace(tmp, fp)                  # ★ 原子：别让"写了一半"被读走
            p = str(fp)
        else:
            frozen = build_model()               # 不落盘：冻结份留内存
            frozen.load_state_dict(w)
            frozen.eval()
            for q in frozen.parameters():
                q.requires_grad_(False)
            self._nets[mid] = frozen
        m = Member(mid=mid, kind="snap", born=int(it), path=p)
        self.members[mid] = m
        self.log(f"  ★ 池子 +1 → {mid}（冻结第 {it} iter 的权重，只增不删）")
        return m

    def net_of(self, mid: str):
        """取这一份的权重对象（快照懒加载 + 缓存）。"""
        if mid in self._nets:
            return self._nets[mid]
        m = self.members[mid]
        if not m.path:
            raise KeyError(f"{mid} 没有权重文件（在训成员应当已经 `bind_live` 过）")
        blob = torch.load(m.path, map_location="cpu", weights_only=False)
        fp = dict(blob.get("fingerprint") or {})
        bad = {k: (fp.get(k), v) for k, v in self.fingerprint.items()
               if k in fp and fp[k] != v}
        if bad:
            raise SystemExit(
                f"★ 池子成员 {mid} 的形状指纹对不上：{bad}\n"
                f"  ⇒ 它是**旧代码**训的，不能上场（铁律：会被新代码加载就必须重炼）")
        net = build_model()
        net.load_state_dict(blob["weights"])
        net.eval()
        for p in net.parameters():
            p.requires_grad_(False)
        self._nets[mid] = net
        return net

    # ---------------------------------------------------------- 抽签
    def active(self) -> list[str]:
        return [m.mid for m in self.members.values() if m.active]

    def draw(self, k: int, rng: np.random.Generator) -> list[str]:
        """**不重复**随机抽 k 份上场（用户：「随机抽 pt」）。

        ★ 不重复是硬要求：同一份在一局里扮两个国家 = **自己打自己**，
          对"学对抗"没有增量，还会把那一局的梯度混在一起。
        """
        pool = self.active()
        if len(pool) < k:
            raise RuntimeError(
                f"池子里 active 只有 {len(pool)} 份，凑不齐一局（要 {k} 份）—— "
                f"淘汰兜底本该拦住这件事，说明闸门漏了")
        return [pool[i] for i in rng.permutation(len(pool))[:k]]

    # ---------------------------------------------------------- 战绩
    def record(self, mid: str, won: bool) -> None:
        m = self.members[mid]
        m.games += 1
        m.wins += int(bool(won))

    def _live(self) -> list[Member]:
        return [m for m in self.members.values() if m.kind in ("main", "live")]

    def retire(self) -> list[str]:
        """应用淘汰规则，返回**这一轮被停用**的 mid 列表。

        规则（用户原话）：**打满 `retire_min_games` 局、胜率低于 `retire_rate`
        ⇒ 不再启用**。

        ★ 两条兜底，都是防"闸门把自己搞死"：
          ① active 总数不得低于 `max_k`（否则下一局凑不齐 k 份 ⇒ 直接崩）；
          ② **在训成员至少留 `min_learners` 份**（全停用 = 炉子没得炼）。
             —— `main`（主 pt）本来就免检，它们正是"基座"的保险。
        ★ 顺序：**胜率从低到高**淘汰，兜底先到先拦（谁最该走谁先走）。
        """
        cand = [m for m in self.members.values()
                if m.active and m.kind != "main"
                and m.games >= self.retire_min_games and m.rate < self.retire_rate]
        cand.sort(key=lambda m: (m.rate, -m.games))
        killed: list[str] = []
        n_active = len(self.active())
        n_live = len([m for m in self._live() if m.active])
        for m in cand:
            if n_active - 1 < self.max_k:            # 兜底①
                break
            if m.kind == "live" and n_live - 1 < self.min_learners:   # 兜底②
                continue
            m.active = False
            n_active -= 1
            if m.kind == "live":
                n_live -= 1
            killed.append(m.mid)
        return killed

    # ---------------------------------------------------------- 持久化
    def _snap_dir(self) -> Path:
        assert self.db is not None, "不落盘的池子没有快照目录"
        return self.db.parent / (self.db.stem + "_snaps")

    def save(self) -> None:
        """roster 落盘（**原子写**）。

        ★ 用户要的"永久化"就在这一行：胜率账本必须**跨进程重启活着**，
          否则 `--restart-after` 每 5 个 iter 换一次进程，账本永远攒不到 10 局，
          淘汰规则就成了死代码（而且**不报错**）。
        """
        if self.db is None:
            return                               # 不落盘（见 __init__ 的 ★）
        self.db.parent.mkdir(parents=True, exist_ok=True)
        blob = {"version": self.VERSION, "updated_iter": int(self.updated_iter),
                "mains": self.mains,
                "retire": {"min_games": self.retire_min_games,
                           "rate": self.retire_rate},
                "fingerprint": self.fingerprint,
                "members": [m.as_json() for m in self.members.values()]}
        tmp = self.db.with_suffix(self.db.suffix + ".tmp")
        tmp.write_text(json.dumps(blob, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.db)

    def load(self) -> bool:
        """读回 roster。返回**是否读到了**（第一次跑没有账本，正常）。"""
        if self.db is None or not self.db.exists():
            return False
        blob = json.loads(self.db.read_text(encoding="utf-8"))
        fp = dict(blob.get("fingerprint") or {})
        bad = {k: (fp.get(k), v) for k, v in self.fingerprint.items()
               if k in fp and fp[k] != v}
        if bad:
            raise SystemExit(
                f"★ 联赛账本 {self.db} 的形状指纹对不上：{bad}\n"
                f"  ⇒ 账本里的快照是**旧代码**训的，整池作废（别硬读）")
        for d in blob.get("members", []):
            m = Member(mid=d["mid"], kind=d["kind"], born=int(d["born"]),
                       games=int(d["games"]), wins=int(d["wins"]),
                       active=bool(d["active"]), path=d.get("path"))
            # ★ 在训成员（path=None）由 `bind_live` 重新挂 —— 权重在主档里，
            #   账本只负责**战绩**。这里先原样收进来，`bind_live` 会覆盖 kind。
            self.members[m.mid] = m
        self.updated_iter = int(blob.get("updated_iter", 0))
        return True

    # ---------------------------------------------------------- 报告
    def report(self) -> str:
        """一行战绩摘要（进日志）。**别只看在训的那些** —— 池子的健康度看全体。"""
        ms = sorted(self.members.values(), key=lambda m: (-m.rate, m.mid))
        live = [m for m in ms if m.kind in ("main", "live")]
        snap = [m for m in ms if m.kind == "snap"]
        dead = [m for m in ms if not m.active]
        head = " ".join(str(m) for m in live)
        return (f"池子{len(ms)}份(active {len(self.active())}) "
                f"| 在训 {head} | 快照{len(snap)}份 停用{len(dead)}份")