# -*- coding: utf-8 -*-
"""联赛权重池 —— 让池子里的对手**分化**，别一池子都是同一个打法。

用户 2026-09-25 的口径（逐条落到实现，**别自己发明**）
──────────────────────────────────────────────────────
1.「**分化从原来 5 个来分化，而不是一个**」
   ⇒ 池子的起点是**原来那 5 份各自的血脉**（`--league-from` **按槽位**灌：
      `L0←nets[0]`、`L1←nets[1]`…）⇒ 5 份**继续各走各的**。
   ★★ 我第一版做错过：把 5 份**都从同一个快照**起跑 ——
      那等于**把 5 条血脉掐成 1 条**，跟"分化"正好相反。
2.「**老快照丢了，从新快照开始**」
   ⇒ 建池那一刻**不导入旧的冻结快照**；池子从这一炉的新快照起算。
      ★ 这句说的是**建池时**，不是运行时删除 —— 运行时的规矩是第 3 条。
3.「**只增不删**」
   ⇒ 成员**永不删除**。打不动只是 `active=0`（不再抽上场），**仍留在库里**。
4.「**随机抽 pt**」
   ⇒ 每局从 active 里**不重复**随机抽 k 份上场。
5.「**标记每个 pt 的胜率，永久化到数据库**」
   ⇒ **SQLite**（`rl/runs/league.db`，WAL）。★ 为什么是库不是 json：
      **为了以后并行** —— 「池子够大并行抽，起多个独立进程筛」。
      ⇒ 写入一律**原子自增**（`games=games+1`）而不是"读出来加一再写回"，
        这是多进程同时记战绩**唯一**不会互相覆盖的写法；
        并且每次读判据前先 `refresh()`，别人进程刚记的战绩这边看得见。
6.「**可以有两个固定主 pt，也可以没有**」
   ⇒ `--league-mains N`（缺省 2，可 0）：前 N 份成员钉为**主 pt** ——
      **不受淘汰规则约束**（它们是**基座本身**，不是候选，淘汰掉就没得炼了）。
7.「**打 10 局以上胜率低于 20% 的不再启用**」
   ⇒ `retire_min_games=10`、`retire_rate=0.20` ⇒ `active=0`。
   ★ **兜底**（否则会自己把自己搞死）：active 总数不得低于**当前最大国家数**
     （否则凑不齐一局），且**至少留 `min_learners` 份在训成员**。
   ★ **口径提醒**：K 国局里"随机"胜率是 `1/K` —— 3 国局 33%、5 国局 **20%**。
     ⇒ 固定 20% 这个阈值在 **5 国局里等于"和随机持平"**（偏严）。
     ★ 将来若发现"池子被淘汰空"，这里是第一嫌疑。

★ 为什么要有这个模块（用户的原话是「现在换家流完全占上风」）
  一池子同源同打法 ⇒ 训练里"对手"这一维是退化的，模型学不到"被克了怎么办"。
  让成员**分化**才是池子的意义；胜率账本是**判据**（谁还有用），不是目的。
  ★ 但注意：**换家流本身不是池子能治的** —— 8×8 上它是几何决定的结构性最优
    （见 PLAN §12.7）；池子治的是"一池子同一个打法"，不是"某个打法太强"。

★ 为**并行筛选**留的口子（用户：「为了以后并行，池子够大并行抽，起多个独立进程筛」）：
  · `draw()` 是只读的；`record()` 是原子自增 + 追加一行 `results`（带进程/iter/时刻）
    ⇒ **N 个独立进程各自"抽签→对局→记账"**，不互相覆盖。
  · `results` 表是**逐局流水**（不是只留聚合值）⇒ 将来"筛"的时候能按
    进程/时间窗口重算，也能看出"某个成员最近是不是不行了"。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
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

    def as_dict(self) -> dict:
        return {"mid": self.mid, "kind": self.kind, "born": int(self.born),
                "games": int(self.games), "wins": int(self.wins),
                "active": bool(self.active), "path": self.path,
                "rate": round(self.rate, 4)}

    def __str__(self) -> str:
        flag = "" if self.active else "×"
        return f"{self.mid}{flag}({self.kind},{self.wins}/{self.games})"


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members (
    mid     TEXT PRIMARY KEY,
    kind    TEXT    NOT NULL,          -- main | live | snap
    born    INTEGER NOT NULL,
    games   INTEGER NOT NULL DEFAULT 0,
    wins    INTEGER NOT NULL DEFAULT 0,
    active  INTEGER NOT NULL DEFAULT 1,
    path    TEXT,
    added   TEXT    NOT NULL
);
-- ★ 逐局流水：将来"并行筛"的时候按进程/时间窗口重算用（不只留聚合值）
CREATE TABLE IF NOT EXISTS results (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mid     TEXT    NOT NULL,
    won     INTEGER NOT NULL,
    iter    INTEGER,
    worker  TEXT,
    ts      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_mid ON results(mid);
"""


# ================================================================ 池子
class League:
    """权重池。**只增不删** —— 唯一的"减"是 `active=0`（不再抽上场）。

    ★ 存储是 **SQLite（WAL）**：多进程并发读写安全，写入走**原子自增**。
      `db=None` ⇒ 内存库（`:memory:`，测试用）。
    """

    VERSION = 2                       # 1=旧的 json（已弃），2=sqlite

    def __init__(self, db: str | os.PathLike | None, *, mains: int = 2,
                 retire_min_games: int = 10, retire_rate: float = 0.20,
                 max_k: int = 5, min_learners: int = 1, worker: str | None = None,
                 net_cap: int = 24, fingerprint: dict | None = None, log=print):
        self.db = None if db is None else Path(db)
        self.mains = max(0, int(mains))
        self.retire_min_games = int(retire_min_games)
        self.retire_rate = float(retire_rate)
        self.max_k = max(1, int(max_k))
        self.min_learners = max(0, int(min_learners))
        # ★ 快照权重缓存的上界（份数）。24 份 ≈ 130MB —— 见 `net_of` 的 ★★。
        self.net_cap = max(0, int(net_cap))
        self.fingerprint = dict(fingerprint or {})
        self.worker = worker or f"pid{os.getpid()}"
        self.log = log
        self.members: dict[str, Member] = {}      # 缓存（判据前一律先 refresh）
        self._nets: dict[str, object] = {}         # mid -> PolicyNet（懒加载 + 缓存）
        self.updated_iter = 0

        if self.db is None:
            self.conn = sqlite3.connect(":memory:")
        else:
            self.db.parent.mkdir(parents=True, exist_ok=True)
            # ★★ `isolation_level=None` = **自动提交**。这是并行下的关键：
            #   Python 的 sqlite3 缺省会在第一条 INSERT/UPDATE 上**隐式开一个事务**
            #   并一直攥着不放手 —— 两个进程一起跑时，第二个写就撞
            #   `database is locked`（实测就是这么踩到的，而且它是**偶发**的）。
            #   自动提交下每条语句自成事务，写得快、放锁快。
            self.conn = sqlite3.connect(str(self.db), timeout=30.0,
                                        isolation_level=None)
        if self.db is not None:
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        # ★★ 形状指纹**只在建库时写一次，此后永不覆盖** ——
        #   若每次 save 都用"当前指纹"盖上去，那么"代码改了、旧池子还在"这件事
        #   会**被自己抹平**，`load()` 永远比得中 ⇒ 旧池子静默上场（铁律要拒的正是这个）。
        if self.fingerprint and self._meta_get("fingerprint") is None:
            self._meta_set("fingerprint", json.dumps(self.fingerprint))

    # ---------------------------------------------------------- 元信息
    def _meta_get(self, k: str) -> str | None:
        r = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def _meta_set(self, k: str, v: str) -> None:
        self.conn.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                          "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    # ---------------------------------------------------------- 成员
    def bind_live(self, mid: str, net, *, born: int = 0) -> Member:
        """把**在训**的那份挂进来（权重对象就是训练循环里那一个，不复制）。

        ★ 必须**挂引用而不是复制**：在训成员的权重每 iter 都在变，
          复制一份就变成"冻结快照"了。
        ★ 已经入册的（`load()` 从库里读回来）**保留它原来的 `kind`** ——
          否则重启后重算一遍"前 N 份当主 pt"，会把主 pt 的身份**漂掉**。
        """
        m = self.members.get(mid)
        if m is None or m.kind not in ("main", "live"):
            kind = "main" if len(self._live()) < self.mains else "live"
            m = Member(mid=mid, kind=kind, born=born)
            self.members[mid] = m
            self.conn.execute(
                "INSERT INTO members(mid,kind,born,path,added) VALUES(?,?,?,?,?) "
                "ON CONFLICT(mid) DO UPDATE SET kind=excluded.kind, path=NULL",
                (mid, kind, int(born), None, _now()))
        m.path = None
        self._nets[mid] = net
        return m

    def add_snapshot(self, net, it: int, *, mid: str | None = None) -> Member:
        """**冻一份**当前权重进池子（只增不删）。返回新成员。

        ★ 存在的意义就是"分化"：这些快照是**不同代**的对手，
          池子里因此有多个打法，而不是一池子同一个自己。
        """
        # ★★ mid **必须带 worker 标识**：并行的世界里，两个 worker 会在**同一 iter**
        #   冻**同一槽位** —— 只用 `S{iter}L{槽位}` 的话，DB 那行被 `INSERT OR IGNORE`
        #   挡住（先到先得）没问题，但**权重文件会被后写的覆盖**
        #   ⇒ 有一份快照**静默地**变成别人的（池子里两份"不同成员"其实是同一份权重）。
        #   ⇒ 缺省 mid 里带上 `self.worker`（缺省 `pid<pid>`，一个进程一个）。
        mid = mid or f"S{int(it):05d}{self._slot_guess(net)}_{self.worker}"
        if mid in self.members:                  # 同一 mid 重复冻 → 幂等
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
            self._evict()                     # ★ 内存池里逐不出东西（没 path），但保持一致
        # ★ `INSERT OR IGNORE`：多进程同时冻同一 mid 时**先到先得**，不炸也不覆盖
        self.conn.execute(
            "INSERT OR IGNORE INTO members(mid,kind,born,path,added) VALUES(?,?,?,?,?)",
            (mid, "snap", int(it), p, _now()))
        self.conn.commit()
        m = Member(mid=mid, kind="snap", born=int(it), path=p)
        self.members[mid] = m
        self.log(f"  ★ 池子 +1 → {mid}（冻结第 {it} iter 的权重，只增不删）")
        return m

    def net_of(self, mid: str):
        """取这一份的权重对象（快照懒加载 + **有上界的缓存**）。

        ★★ **必须有上界**（`net_cap`）：池子是**只增不删**的，而每份权重 ≈ **5.5MB**
          （1.383M 参数）。不设上界的话，池子一大，**训练进程自己就 OOM 了** ——
          正是我们一路在躲的那个病，而且这次是池子养的。
          ⇒ LRU：用得少的那份被逐出（从盘上还在，下次用到再读回来）。
        ★★ **在训成员永不许逐出**：它们的权重就是训练循环里那个对象
           （`path=None`，盘上没有副本）⇒ 逐出会让 `net_of` 直接崩。
        """
        if mid in self._nets:
            net = self._nets.pop(mid)
            self._nets[mid] = net            # ★ LRU：用到就挪到队尾
            return net
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
        self._evict()
        return net

    def _evict(self) -> None:
        """把缓存按 LRU 逐出到 `net_cap` 以内。

        ★★ **只逐出"能读回来的"** —— 两条都必须满足：
          ① **有盘上副本**（`member.path` 非空）。`db=None` 的**内存池**里，
             冻结份**只有内存这一份** ⇒ 逐出 = **永久丢掉**（而池子"只增不删"）。
          ② 是**快照**。**在训成员**的权重就是训练循环里那个对象（`path=None`），
             逐出会让 `net_of` 当场崩。
        """
        if self.net_cap <= 0:
            return
        while len(self._nets) > self.net_cap:
            victim = next((k for k in self._nets
                           if (self.members.get(k) is not None
                               and self.members[k].kind == "snap"
                               and self.members[k].path)), None)
            if victim is None:
                return                        # 剩下的全都逐不得 ⇒ 停手
            del self._nets[victim]

    # ---------------------------------------------------------- 抽签
    def refresh(self) -> None:
        """★ 从库里重读战绩 —— **判据（抽签/淘汰/报告）之前一律先跑一次**。

        ★ 为什么必须：并行的世界里，别人进程刚记的战绩只存在于库里。
          读自己的内存缓存 = 拿**过时的胜率**去淘汰/抽签，而且**不报错**。
        """
        rows = self.conn.execute(
            "SELECT mid,kind,born,games,wins,active,path FROM members").fetchall()
        seen = set()
        for mid, kind, born, games, wins, active, path in rows:
            seen.add(mid)
            m = self.members.get(mid)
            if m is None:
                m = Member(mid=mid, kind=kind, born=born, path=path)
                self.members[mid] = m
            m.kind = kind
            m.games, m.wins, m.active = int(games), int(wins), bool(active)
            if m.path is None:
                m.path = path
        # ★★ **库里没有的，缓存里也不许有。** 否则缓存会**撒谎**：
        #   库里那行被删了，缓存还留着它 ⇒ `active()`/`report()`/判据全都装作它还在，
        #   而且**不报错**。★ 这条是"只增不删"那个守卫逼出来的 ——
        #   把淘汰改成真 `DELETE` 时，测试原本是**绿的**（它只看了内存）。
        for mid in [k for k in self.members if k not in seen]:
            del self.members[mid]

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
    def record(self, mid: str, won: bool, *, it: int | None = None) -> None:
        """记一局战绩。★★ **原子自增**（不是"读出来加一再写回"）。

        并发下唯一正确的写法：两个进程同时记同一份，
        `games=games+1` 由 SQLite 串行化 ⇒ **一局都不会丢**；
        而"读-加-写回"会让后写的那个把先写的**抹掉**（而且看起来一切正常）。

        ★★ **平局（打满上限、没有胜方）不要调这里** —— 胜率的分母是「**有胜负的局**」。
          把"没赢"记成"输了"的话，一池子平局会把**所有人**的胜率压到 0
          ⇒ 淘汰规则把池子清空，而日志上看只是"大家都在输"（**静默**那一类）。
          调用方 `train()` 已经过滤了。
        """
        w = int(bool(won))
        self.conn.execute("UPDATE members SET games=games+1, wins=wins+? WHERE mid=?",
                          (w, mid))
        self.conn.execute(
            "INSERT INTO results(mid,won,iter,worker,ts) VALUES(?,?,?,?,?)",
            (mid, w, None if it is None else int(it), self.worker, _now()))
        self.conn.commit()
        m = self.members.get(mid)
        if m is not None:                        # 本地缓存跟着走（判据前仍会 refresh）
            m.games += 1
            m.wins += w

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
        ★ 先 `refresh()`：判据用的是**库里**的胜率（并行时别人也记了账）。
        """
        self.refresh()
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
            self.conn.execute("UPDATE members SET active=0 WHERE mid=?", (m.mid,))
            m.active = False
            n_active -= 1
            if m.kind == "live":
                n_live -= 1
            killed.append(m.mid)
        if killed:
            self.conn.commit()
        return killed

    def _slot_guess(self, net) -> str:
        """这份权重是**哪个槽位**的在训成员（只为让 mid 可读；找不到就 `x`）。

        ★ 只在**缺省 mid** 里用；`train` 那边一直显式传 `mid`，所以这里不影响训练。
        """
        for mid, n in self._nets.items():
            if n is net:
                return mid          # 在训成员的 mid 本来就是 `L{i}` ⇒ 直接拼上
        return "Lx"                 # 没 `bind_live` 过的（测试/临时）—— 别让它变空

    # ---------------------------------------------------------- 持久化
    def _snap_dir(self) -> Path:
        assert self.db is not None, "不落盘的池子没有快照目录"
        return self.db.parent / (self.db.stem + "_snaps")

    def save(self) -> None:
        """提交 + 落 `updated_iter`。

        ★ 用户要的"永久化"就在这条路上：胜率账本必须**跨进程重启活着**，
          否则 `--restart-after` 每 5 个 iter 换一次进程，账本永远攒不到 10 局，
          淘汰规则就成了死代码（而且**不报错**）。
        """
        self._meta_set("updated_iter", self.updated_iter)
        self._meta_set("version", self.VERSION)
        self._meta_set("worker", self.worker)
        self.conn.commit()

    def load(self) -> bool:
        """读回池子。返回**库里原本有没有成员**（第一次跑没有，正常）。"""
        fp = json.loads(self._meta_get("fingerprint") or "{}")
        bad = {k: (fp.get(k), v2) for k, v2 in self.fingerprint.items()
               if k in fp and fp[k] != v2}
        if bad:
            raise SystemExit(
                f"★ 联赛库 {self.db} 的形状指纹对不上：{bad}\n"
                f"  ⇒ 库里的快照是**旧代码**训的，整池作废（别硬读）")
        self.updated_iter = int(self._meta_get("updated_iter") or 0)
        self.refresh()
        return bool(self.members)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ---------------------------------------------------------- 报告
    def report(self) -> str:
        """一行战绩摘要（进日志）。**别只看在训的那些** —— 池子的健康度看全体。"""
        self.refresh()
        ms = sorted(self.members.values(), key=lambda m: (-m.rate, m.mid))
        live = [m for m in ms if m.kind in ("main", "live")]
        snap = [m for m in ms if m.kind == "snap"]
        dead = [m for m in ms if not m.active]
        head = " ".join(str(m) for m in live)
        return (f"池子{len(ms)}份(active {len(self.active())}) "
                f"| 在训 {head} | 快照{len(snap)}份 停用{len(dead)}份"
                f" | 权重缓存{len(self._nets)}/{self.net_cap or '∞'}")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")