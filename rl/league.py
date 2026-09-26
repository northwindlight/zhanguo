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

import hashlib
import json
import os
import re
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
      - `snap` —— **冻结版本**：权重在 `path` 指向的文件里。

    ★★ 2026-09-26 用户改了口径：**`snap` 不再"永不训练"** ——
      「**谁上场谁学，同时冻结**」「**学一个增一个**」「都进硬盘，每次上场都冻一份，
       这就是只增不删」。
      ⇒ 抽到的每一份（含老快照）都吃梯度；**每次学完就冻成一个新成员**
        （`freeze_trained`，mid 形如 `G<iter>@<父本>`），**父本本身不动**
        （它的 `.pt` 永远是它自己那一版）⇒ 「只增不删」= 池子按**版本**增长。
      ⇒ 账本口径因此天然干净：**一局胜负属于"上场的那一版"**（父本那一行），
        新生儿**带 0 战绩出生**，之后自己上场挣。
      ⇒ `parent` 记**父本的 mid**（用户：「以后能查这条线是从哪个基座、哪一代分出去的」）。
    """
    mid: str
    kind: str
    born: int
    games: int = 0
    wins: int = 0
    active: bool = True
    path: str | None = None
    parent: str | None = None

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
    added   TEXT    NOT NULL,
    parent  TEXT                       -- ★ 父本 mid（血脉可追；见 Member 的 docstring）
);
-- ★ 逐局流水：将来"并行筛"的时候按进程/时间窗口重算用（不只留聚合值）
CREATE TABLE IF NOT EXISTS results (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mid     TEXT    NOT NULL,
    won     INTEGER NOT NULL,
    iter    INTEGER,
    worker  TEXT,
    ts      TEXT    NOT NULL,
    game    TEXT               -- ★ 每局一个 id：同局的所有参与者共用（见 record 的 docstring）
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
                 retire_rating: float = 1400.0, retire_rd: float = 110.0,
                 max_k: int = 5, min_learners: int = 1, worker: str | None = None,
                 net_cap: int = 24, device: str = "cpu",
                 fingerprint: dict | None = None, log=print):
        self.db = None if db is None else Path(db)
        self.mains = max(0, int(mains))
        # ★★ 2026-09-26 用户改口径：「**也可以以 elo 方法退役，取消原来的退役机制**」
        #   ⇒ 判据从"打满 N 局 + 胜率 < p"换成"**评级低于阈值、且已经打出来（RD 小）**"。
        #   两条阈值的含义：
        #     · `retire_rating`：绝对评级（池子均值恒 ≈1500 ⇒ <1400 = 比平均水平低 100 分）；
        #     · `retire_rd`：**只有 RD 小到"打出来了"才许退** —— 这一条替代了原来的
        #       "打满 10 局"，而且更准：样本少 ⇒ RD 大 ⇒ 先别判（裸胜率做不到这点）。
        self.retire_rating = float(retire_rating)
        self.retire_rd = float(retire_rd)
        self.max_k = max(1, int(max_k))
        self.min_learners = max(0, int(min_learners))
        # ★ 快照权重缓存的上界（份数）。24 份 ≈ 130MB —— 见 `net_of` 的 ★★。
        self.net_cap = max(0, int(net_cap))
        # ★★ **快照的网络必须和在训的在同一台设备上**：`net_of` 出来的那份要跟
        #    `collect_episode` 里那个 batch 同设备，否则前向当场报
        #    "expected self and mask to be on the same device"。
        #    （在训成员由 `bind_live` 挂引用，设备由 `train` 那边负责。）
        self.device = device
        self.fingerprint = dict(fingerprint or {})
        self.worker = worker or f"pid{os.getpid()}"
        self.log = log
        self.members: dict[str, Member] = {}      # 缓存（判据前一律先 refresh）
        self._nets: dict[str, object] = {}       # mid -> PolicyNet（懒加载 + 缓存）
        # ★ 「学过但还没落盘」的成员（见 `mark_hot`）：**逐出时必须跳开**
        self._hot: set[str] = set()
        # ★★★ **本进程亲手 `bind_live` 过的那几份在训成员**（2026-09-26 加）。
        #   判"这份在训成员是不是我的"**只看这个集合，不去解析 mid 的格式** ——
        #   这样 mid 长什么样（`L0` 还是 `L0@mem1`）与这里的判据**解耦**，
        #   改命名不会静默改变语义。
        #   ★ 为什么必须有：在训成员的权重**只在本进程内存里**（`path=None`），
        #     而 `net_of` 对 `path=None` 是**直接抛 KeyError**（见它的 docstring）。
        #     多个 worker 共用一个库时，"别人的在训成员"也在这张表里
        #     ⇒ 不把它挡在 `drawable()` 之外，`draw()` 一抽中就**当场崩**。
        #   ★ 重启后要重新 `bind_live` 一遍（同一个 mid）⇒ 这个集合自然就补回来了。
        self._local_live: set[str] = set()
        self.updated_iter = 0

        if self.db is None:
            # ★★ **内存池也必须 `isolation_level=None`**（2026-09-25 修的真 bug）：
            #   缺省 `''` 模式下，第一条 INSERT/UPDATE 会**隐式开一个事务且不放手**
            #   ⇒ 后面 `retire()` 里的 `BEGIN IMMEDIATE` 当场抛
            #     `cannot start a transaction within a transaction`。
            #   ★ 而且这条**只在内存池上炸**（文件池本来就是 None）⇒ 症状是
            #     "探针/测试炸、真炉子没事"，最容易被误判成"探针写错了"。
            #   ★★ 更毒的是它**被测试掩盖过**：用例里设战绩的 `_stat()` 带 `conn.commit()`，
            #     顺手把那个隐式事务关掉了 ⇒ 23 条用例全绿，而**同样的路径在
            #     不调 `_stat` 时必炸**。（教训：测试用的辅助函数会把 bug 遮住，
            #     所以新增的守卫要**走最短的那条路**。）
            self.conn = sqlite3.connect(":memory:", isolation_level=None)
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
        # ★★ **迁移**：`parent` 列是 2026-09-26 加的（"学一个增一个"要记父本），
        #   而**已经跑着的池子库没有这一列** ⇒ `CREATE TABLE IF NOT EXISTS` 不会补它，
        #   之后任何 `SELECT parent` 都会当场报 `no such column`
        #   （而我这条线**铁律之一是"只增不删"** ⇒ 不能重建表、只能加列）。
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(members)")}
        if cols and "parent" not in cols:
            self.conn.execute("ALTER TABLE members ADD COLUMN parent TEXT")
        rcols = {r[1] for r in self.conn.execute("PRAGMA table_info(results)")}
        if rcols and "game" not in rcols:
            self.conn.execute("ALTER TABLE results ADD COLUMN game TEXT")
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
            # ★★ **主 pt 的份数是「每个 worker 各自」的**（用户 2026-09-26 拍的口径）：
            #   数**本进程已经绑过的那几份**，不是全库的。数全库的话，第二个 worker
            #   一上来就发现「库里有 2 份 main 了」⇒ 它自己的 L0/L1 全变 live
            #   （**不受退役保护**）—— 而它那两份才是它自己的基座。
            #   ★ 单 worker 下两种数法完全等价。
            n_local = len([x for x in self._live() if x.mid in self._local_live])
            kind = "main" if n_local < self.mains else "live"
            m = Member(mid=mid, kind=kind, born=born)
            self.members[mid] = m
            self.conn.execute(
                "INSERT INTO members(mid,kind,born,path,added) VALUES(?,?,?,?,?) "
                "ON CONFLICT(mid) DO UPDATE SET kind=excluded.kind, path=NULL",
                (mid, kind, int(born), None, _now()))
        m.path = None
        self._nets[mid] = net
        self._local_live.add(mid)          # ★★ 见 `_local_live` 上的那段
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
            frozen = self._new_net()             # 不落盘：冻结份留内存
            frozen.load_state_dict(w)
            frozen.to(self.device)
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

    def _new_net(self):
        """按**本池的形状**建一份空网 —— ★★ **不许用裸的 `build_model()`**。

        我踩过（2026-09-25，记忆臂跑到第 4 个 iter **当场崩**）：
        这里原来写的是 `build_model()`，而它的默认值是 `mem_slots=0`
        ⇒ **给记忆档建了一份马尔可夫网**，`load_state_dict` 报
        `Unexpected key(s) in state_dict: "mem0", "mem_write.q.weight", …`。
        最讽刺的是**上一行的指纹闸门刚刚放行** —— 它比的是"池子的形状"，
        两边一致 ⇒ 闸门说"这是记忆池"，紧接着按"0 槽"建网，**自己跟自己矛盾**。

        形状的**唯一出处**是 `self.fingerprint`（`_shape_fingerprint(mem_slots)`，
        它本来就带着 `mem_slots`）⇒ 从它取，别再让默认值插一脚。
        """
        return build_model(mem_slots=int(self.fingerprint.get("mem_slots", 0)))

    def mark_hot(self, mid: str) -> None:
        """把这份标成「**刚学过、内存里比盘上新**」⇒ **`_evict` 必须跳开它**。

        ★★ 为什么非有不可（用户 2026-09-26 拍的口径：「**钉住 + 下次冻结落盘**」）：
          `_evict` 的判据是"**盘上有副本**"（`path` 非空就能逐出、下次再读回来）。
          而"学过"的权重**恰恰比盘上新** ⇒ 一被逐出就**静默回到旧版本**
          （学习丢了，日志上什么都没有）。这不是理论风险：`net_cap` 默认 24、
          而"学一个增一个"之后池子长得快 ⇒ 逐出每天都在发生。
        """
        self._hot.add(mid)

    def freeze_trained(self, net, parent: str, it: int) -> Member:
        """★★ **「学一个增一个」**：把**学完之后**的权重冻成**新成员**（落盘 + 入册）。

        用户 2026-09-26 的原话：「**谁上场谁学，同时冻结**」「**学一个增一个**」
        「**都进硬盘，每次上场都冻一份，这就是只增不删**」。

        ★ **父本不动**：新生儿是**另一份**权重（父本学完的那一版），写进自己的
          `.pt`；父本自己的文件**永远是它出生时那一版** ⇒ 池子按**版本**增长。
        ★ **新生儿带 0 战绩出生**：那一局的胜负属于"**上场的那一版**"（父本那一行）；
          新生儿还没上过场 ⇒ 账本口径不会跨版本混（这是这条设计最要紧的地方）。
        ★ **父本学完的权重不再留在内存**（如果是快照）：它已经被归档成新生儿，
          继续留在缓存里只会让"它"和"它的下一代"变成同一个对象
          （**别名**：训下一代会顺手改父本，而两边账本各记各的 ⇒ 静默污染）。
          ⇒ 快照父本从缓存里**撤掉**（下次抽到它 ⇒ 从盘上读回它出生那一版）。
          **在训成员不撤**（它的权重对象就是训练回路里那个，撤了会崩）。
        ★ mid 用 `G<iter>@<父本缩写>_<worker>`：
          · `G` = "学出来的那一代"；
          · 缩写是为了 id **别一代比一代长**（每代 +30 字符，几十代就撑破文件名）；
          · **worker 不能省**（同 iter 同父本，两个 worker 会撞同一个文件名 ⇒
            互相覆盖，池子里两份"不同成员"其实是同一份权重 —— `add_snapshot` 踩过）；
          · **父本的准确身份另存 `parent` 列** ⇒ 血脉照旧可追。
        """
        # ★ 调用方**必须先 `mark_hot(parent)`** 再训、再冻 —— 这里只负责归档。
        short = hashlib.sha1(parent.encode("utf-8")).hexdigest()[:8]
        mid = f"G{int(it):05d}@{short}_{self.worker}"
        m = self.members.get(mid)
        if m is not None and m.path:                 # 幂等（同一 iter 同父本只冻一次）
            self._hot.discard(parent)
            return m
        w = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
        p = None
        if self.db is not None:
            fp = self._snap_dir() / f"{mid}.pt"
            fp.parent.mkdir(parents=True, exist_ok=True)
            tmp = fp.with_suffix(".pt.tmp")
            torch.save({"weights": w, "fingerprint": self.fingerprint}, tmp)
            os.replace(tmp, fp)                      # ★ 原子写（同 `add_snapshot`）
            p = str(fp)
        else:
            frozen = self._new_net()
            frozen.load_state_dict(w)
            frozen.to(self.device)
            frozen.eval()
            for q in frozen.parameters():
                q.requires_grad_(False)
            self._nets[mid] = frozen
        self.conn.execute(
            "INSERT OR IGNORE INTO members(mid,kind,born,path,added,parent) "
            "VALUES(?,?,?,?,?,?)",
            (mid, "snap", int(it), p, _now(), parent))
        self.conn.commit()
        m = Member(mid=mid, kind="snap", born=int(it), path=p, parent=parent)
        self.members[mid] = m
        # ★ 父本（如果是快照）学完的那一版已经归档 ⇒ 从缓存撤掉，回到它出生那一版
        pm = self.members.get(parent)
        if pm is not None and pm.kind == "snap":
            self._nets.pop(parent, None)
        self._hot.discard(parent)
        self._evict()
        self.log(f"  ★ 学一个增一个 → {mid}（父本 {parent}，第 {it} iter）")
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
        net = self._new_net()
        net.load_state_dict(blob["weights"])
        net.to(self.device)                  # ★ 快照也要上同一台设备（见 __init__ 的 ★★）
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
            # ★★ `k not in self._hot`：**刚学过、盘上还是旧版**的不许逐出
            #   （否则学习**静默**丢失 —— 见 `mark_hot` 的 docstring）
            victim = next((k for k in self._nets
                           if (k not in self._hot
                               and self.members.get(k) is not None
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
            "SELECT mid,kind,born,games,wins,active,path,parent FROM members"
        ).fetchall()
        seen = set()
        for mid, kind, born, games, wins, active, path, parent in rows:
            seen.add(mid)
            m = self.members.get(mid)
            if m is None:
                m = Member(mid=mid, kind=kind, born=born, path=path)
                self.members[mid] = m
            m.kind = kind
            m.games, m.wins, m.active = int(games), int(wins), bool(active)
            m.parent = parent
            if m.path is None:
                m.path = path
        # ★★ **库里没有的，缓存里也不许有。** 否则缓存会**撒谎**：
        #   库里那行被删了，缓存还留着它 ⇒ `active()`/`report()`/判据全都装作它还在，
        #   而且**不报错**。★ 这条是"只增不删"那个守卫逼出来的 ——
        #   把淘汰改成真 `DELETE` 时，测试原本是**绿的**（它只看了内存）。
        for mid in [k for k in self.members if k not in seen]:
            del self.members[mid]

    def active(self) -> list[str]:
        """**账本口径**：库里 `active=1` 的**全部**成员（含**别人的**在训成员）。

        ★★ 它和"我能不能抽上场"是**两个问题**，所以是两个方法（2026-09-26 分开）：
          · 这里 = 「**池子**里有几份启用的」（报表、下限不变量、外部只读观察）；
          · `drawable()` = 「**我这个进程**真能抽上场的」。
        ★★ 判据只能是 `m.active`，**不许**在这里解析 mid 的格式去猜"这份是谁的"
          —— 命名一改语义就静默变了（见 `_local_live` 那段）。
        ★★★ **曾经把两个问题合成一个方法**（把
          `m.path is not None or m.mid in self._local_live` 写进这里），
          后果是**静默废掉两条旧守卫**——记在这里，因为它是"改对了判据、
          却把别处的口径一起改了"的典型：
            · `test_draw_is_finished_but_not_recorded`：跑完 `train()` 后**另开**
              一个池子读账本 ⇒ 它从没 `bind_live` 过 ⇒ `active()` 变 **0**；
            · `test_concurrent_retire_never_breaks_the_floor`：6 个线程各自开池子
              淘汰，**同样没绑** ⇒ 每个线程看到的 active 都是 0
              ⇒ 兜底①当场 `break` ⇒ **一个成员都没淘汰**，而断言**照样全绿**
              —— 用例**变成空转**（正是最该防的"假绿"：它比红更坏）。
          ⇒ 教训：**"这个集合是给谁用的"要问清楚**；把判据收窄是对的，
            但收窄的**方法**只能给真正需要收窄的调用方（`draw()`）。
        """
        return [m.mid for m in self.members.values() if m.active]

    def drawable(self) -> list[str]:
        """**本进程口径**：我真能抽上场的那些 = **有盘上副本的** ∪ **我亲手绑的在训成员**。

        ★ 前者谁都能读回来（快照的 `path` 指向 `.pt`）；后者只有本进程内存里有
          （`path=None`）。别人的在训成员**两者都不是** ⇒ 抽中它 ⇒ `net_of` 拿不到
          权重 ⇒ **当场 `KeyError` 崩**（单 worker 时两种写法完全等价 ⇒
          这个 bug 只在并行时才显形，正是"静默"那一类）。
        ★ `retire()` 的兜底① 也按**这个**算（见那里）：兜底要防的是"**我**下一局
          凑不齐 k 份"，所以只能数我抽得到的。
        """
        return [m.mid for m in self.members.values()
                if m.active and (m.path is not None or m.mid in self._local_live)]

    def draw(self, k: int, rng: np.random.Generator) -> list[str]:
        """**不重复**随机抽 k 份上场（用户：「随机抽 pt」）。

        ★ 不重复是硬要求：同一份在一局里扮两个国家 = **自己打自己**，
          对"学对抗"没有增量，还会把那一局的梯度混在一起。
        ★★ 抽的是 `drawable()`（**本进程**口径），不是 `active()`（账本口径）——
          别人的在训成员在账本上是 active，但它的权重不在我这儿（抽中即崩）。
        """
        pool = self.drawable()
        if len(pool) < k:
            raise RuntimeError(
                f"本进程能抽的（drawable）只有 {len(pool)} 份，凑不齐一局（要 {k} 份）—— "
                f"淘汰兜底本该拦住这件事，说明闸门漏了")
        return [pool[i] for i in rng.permutation(len(pool))[:k]]

    # ---------------------------------------------------------- 战绩
    def record(self, mid: str, won: bool, *, it: int | None = None,
               game: str | None = None) -> None:
        """记一局战绩。★★ **原子自增**（不是"读出来加一再写回"）。

        并发下唯一正确的写法：两个进程同时记同一份，
        `games=games+1` 由 SQLite 串行化 ⇒ **一局都不会丢**；
        而"读-加-写回"会让后写的那个把先写的**抹掉**（而且看起来一切正常）。

        ★★ **平局（打满上限、没有胜方）不要调这里** —— 胜率的分母是「**有胜负的局**」。
          把"没赢"记成"输了"的话，一池子平局会把**所有人**的胜率压到 0
          ⇒ 淘汰规则把池子清空，而日志上看只是"大家都在输"（**静默**那一类）。
          调用方 `train()` 已经过滤了。

        ★★ `game` = **每局一个 id**（同一局的所有参与者共用同一个值）——
          这是给**离线重算**（Glicko-2 评级，`rl/elo.py`）留的口子：
          只靠 `(iter, worker, ts)` 分组是不够的，`ts` 只到秒，同一秒的两局会并成
          **6 行**，分不清"谁跟谁一局"⇒ 评级的输入就错了（而且是**静默**错的）。
        """
        w = int(bool(won))
        self.conn.execute("UPDATE members SET games=games+1, wins=wins+? WHERE mid=?",
                          (w, mid))
        self.conn.execute(
            "INSERT INTO results(mid,won,iter,worker,ts,game) VALUES(?,?,?,?,?,?)",
            (mid, w, None if it is None else int(it), self.worker, _now(), game))
        self.conn.commit()
        m = self.members.get(mid)
        if m is not None:                        # 本地缓存跟着走（判据前仍会 refresh）
            m.games += 1
            m.wins += w

    def _live(self) -> list[Member]:
        return [m for m in self.members.values() if m.kind in ("main", "live")]

    def retire(self) -> list[str]:
        """应用淘汰规则，返回**这一轮被停用**的 mid 列表。

        规则（用户 2026-09-26 改的口径）：**评级低于 `retire_rating`、且 RD ≤ `retire_rd`
        ⇒ 不再启用**（`active=0`，**仍留在库里** —— 只增不删）。

        ★★ 为什么换掉"打满 10 局 + 胜率 <20%"（原来的机制，**已取消**）：
          ① **裸胜率有混淆**：一份的胜率取决于它**抽到谁**；
          ② **"打满 10 局"在池子长大后几乎凑不齐**：期望要 ≈`1.7N` 个 iter
             （N=200 时 ≈340 iter）⇒ 那条规则慢慢变成死代码；
          ③ **RD 比"打满 N 局"更准**：它直接量"这个评级可不可信" ——
             样本少 ⇒ RD 大 ⇒ 先别判。这是原来那条规则**做不到**的事。
        ★ 两条兜底**照旧**（都是防"闸门把自己搞死"）：
          ① active 总数不得低于 `max_k`（否则下一局凑不齐 k 份 ⇒ 直接崩）；
          ② **在训成员至少留 `min_learners` 份**（全停用 = 炉子没得炼）。
             —— `main`（主 pt）本来就免检，它们正是"基座"的保险。
        ★ 顺序：**评级从低到高**淘汰，兜底先到先拦（谁最该走谁先走）。
        ★ 先 `refresh()`：判据用的是**库里**的账（并行时别人也记了账）。
        ★★ **整段"读判据 → 写停用"必须在一个 `BEGIN IMMEDIATE` 事务里**
          （2026-09-25 补）：否则两个 worker 各自 `refresh()` 后**都**看到
          "active=5、还能淘汰一个" ⇒ 各自 UPDATE 一个 ⇒ **一共淘汰两个**，
          而两条兜底（active ≥ max_k、在训 ≥ min_learners）**都被绕过**。
          ★ `UPDATE ... AND active=1` 是第二道锁：同一个成员被两个 worker 同时选中时，
            后到的那个**改 0 行**，不会重复计数。
        ★ 评级**在事务里现算**（读 `results` 的逐局流水 ⇒ 跑一遍 Glicko-2）：
          代价可忽略（几百局是毫秒级），换来的是判据**永远和账本一致** ——
          不存第二份"评级快照"就不会有两份对不上的那一天。
        """
        from . import elo as _elo                      # ★ 纯函数模块，无环依赖
        self.refresh()
        killed: list[str] = []
        self.conn.execute("BEGIN IMMEDIATE")        # ★ 拿写锁 ⇒ 别人得等
        try:
            # ★ 判据**在事务里重读**，不用事务外那份缓存
            # ★★ 2026-09-26：三处口径改成**"按本进程能抽到的池子"**算
            #   （多 worker 共用一个库时才显形；单 worker 下与旧行为**逐字等价**）。
            #   · 别人的**在训成员**由**他自己**停用，我不碰 —— 它的权重只在他内存里，
            #     我停用它等于替他改口径，而且那个进程还会继续训它（不报错、说不清）；
            #   · 兜底①/② 改数**本进程可抽的份数**：库里的 active 总数 ≥ max_k
            #     并**不保证**我这一侧抽得齐（别人的在训成员我抽不了）
            #     ⇒ 按全局数算会让 `draw()` 在本地池子空掉时崩。
            rows = self.conn.execute(
                "SELECT mid,kind,born,games,wins,active,path FROM members").fetchall()
            res = _elo.rate(_elo.group_rows(self.conn.execute(
                "SELECT mid,won,iter,worker,ts,game FROM results").fetchall()))
            mine = self._local_live
            # 「本进程能抽上场」= **`drawable()` 的口径**：有盘上副本（谁的都行）
            # 或 是我绑的在训成员。★ 判据必须与 `drawable()` **一致** ——
            # 兜底① 拦的是"`draw()` 下一局凑不齐"，两边口径一岔，闸门就拦错了对象
            # （而它是**静默**的：拦多了只是"少淘汰几个"，看日志看不出来）。
            n_draw = sum(1 for r in rows
                         if r[5] and (r[6] is not None or r[0] in mine))
            n_my_live = sum(1 for r in rows
                            if r[5] and r[1] in ("main", "live") and r[0] in mine)
            cand = []
            for r in rows:
                if not r[5] or r[1] == "main":        # 停用过的、主 pt ⇒ 免检
                    continue
                if r[1] == "live" and r[0] not in mine:
                    continue                          # ★ 别人的在训成员：他自己管
                got = res.get(r[0])
                if got is None:                       # 一局没打过 ⇒ 没有评级 ⇒ 不判
                    continue
                rating, rd_, _n = got
                if rd_ <= self.retire_rd and rating < self.retire_rating:
                    cand.append((rating, rd_, Member(mid=r[0], kind=r[1],
                                                     born=int(r[2]),
                                                     games=int(r[3]), wins=int(r[4]))))
            cand.sort(key=lambda x: x[0])             # ★ 评级从低到高
            for _rating, _rd, m in cand:                     # ★ 元素是 (评级, RD, Member)
                if n_draw - 1 < self.max_k:              # 兜底①（★ 本地口径）
                    break
                if m.kind == "live" and n_my_live - 1 < self.min_learners:  # 兜底②
                    continue
                cur = self.conn.execute(
                    "UPDATE members SET active=0 WHERE mid=? AND active=1",
                    (m.mid,))
                if cur.rowcount == 0:                    # 别人刚停用过它 ⇒ 不重复计数
                    continue
                n_draw -= 1
                if m.kind == "live":
                    n_my_live -= 1
                killed.append(m.mid)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        if killed:
            self.refresh()
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
        # ★★ 旧库告警（2026-09-26）：**在训成员的 mid 现在带 worker 身份**（`L0@mem1`），
        #   而旧代码写的是裸 `L0..L4` ⇒ 用新代码打开旧库时，那些旧行会变成
        #   **`path=None`、谁也读不回来的僵尸**（新的 `active()` 会把它们挡在抽签之外，
        #   **不会崩** —— 正因如此才要**大声说一句**，否则只是"池子份数看着不对"）。
        #   ★ 判据用 `^L\d+$` 这个**旧格式**：新格式一定带 `@`，重启后自己的行也对得上。
        stale = [m.mid for m in self.members.values()
                 if m.kind in ("main", "live") and not m.path
                 and re.fullmatch(r"L\d+", m.mid)]
        if stale:
            self.log(f"⚠ 联赛库 {self.db} 里有 **{len(stale)} 个旧格式的在训行**"
                     f"（{', '.join(sorted(stale))}）—— 那是**旧代码**留下的："
                     f"新代码的在训 mid 是 `L0@<身份>` ⇒ 这些旧行**永远不会被抽上场**"
                     f"（也不会崩），但会一直占着库。⇒ 建议**起新库**"
                     f"（旧库的战绩与快照仍读得回来）")
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