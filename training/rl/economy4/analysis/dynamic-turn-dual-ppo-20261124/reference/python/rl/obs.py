"""观测编码：把一个座位此刻的**信息集**（带迷雾）编成定长特征的实体 token。

信息集纪律（与引擎给玩家的一致，`visible_to`/`visible_buildings`）：
- 地块静态属性（地形/资源）：**看见过**才知道（`known`），之后记住；
- 地块动态属性（归属/建筑/驻军）：**此刻在视野内**才有；他国地上只看得到城堡和市政厅（公共建筑）；
- 他国库存/GDP/军队总数不可见；只给公开的外交关系与"我看得见的"计数；
- 野人守军：视野内可见（血量），视野外不可见。
输出 dict（float32/int32 numpy）：globals / market / nations / tiles / armies / candidates + 指针。
"""
from __future__ import annotations

import numpy as np

import mp
from balance import BUILDINGS, MARKET, TRADEABLE, TERRAINS, UNIT_TYPES
from .candidates import FEAT_DIM, TYPE_ID, Candidate
from .intel import IntelMemory
from .score import REF, army_value

TERR = list(TERRAINS)                         # 地形 one-hot 顺序
RES5 = ("矿石", "黄金", "耕地", "石油", "木头")
BLD = list(BUILDINGS)
UNITS = list(UNIT_TYPES)

G_DIM, M_DIM, N_DIM, T_DIM, A_DIM = 32, 8, 40, 72, 20    # N 13→38、T 48→70：情报字段（rl/intel.py）
F_DIM = 80          # 回合摘要（喂给 GRU）的维度（候选类型 28→33 后由 72 加到 80）
from .candidates import TYPES as _TYPES



def visible_set(w: mp.World, name: str) -> set[tuple[int, int]]:
    """本方 + 盟友的地块各带相邻一圈（含对角）；瞭望塔半径圆内。一次性算整张图（比逐格 visible_to 快得多）。"""
    bloc = w.bloc_of(name)
    mates = set(bloc["members"]) if bloc else {name}
    mates.add(name)
    vis: set[tuple[int, int]] = set()
    r = building_r = None
    for (x, y), t in w.tiles.items():
        if t["owner"] in mates:
            vis.add((x, y))
            vis.update(w.neighbors(x, y))
            if t["buildings"].get("瞭望塔"):
                if r is None:
                    r = mp.building_effect("瞭望塔", "vision_radius")
                ri = int(r)
                for dx in range(-ri, ri + 1):
                    for dy in range(-ri, ri + 1):
                        if dx * dx + dy * dy <= r * r and 0 <= x + dx < w.size and 0 <= y + dy < w.size:
                            vis.add((x + dx, y + dy))
    return vis


class Observer:
    """每个座位一个：记住看见过的地块静态属性。"""

    def __init__(self, w: mp.World, name: str):
        self.name = name
        self.known: dict[tuple[int, int], tuple] = {}      # (x,y) → (地形序号, 资源5)
        self.halls: set[tuple[int, int]] = set()            # 永久厅记忆：发现过厅的格子（厅拆不掉、不会移动，只会易主）
        self.sight: dict[int, tuple] = {}                   # 此刻视野内的敌军 {番号: (x,y,hp,兵种序号,归属)}
        self.last_seen: dict[tuple[int, int], tuple] = {}  # (x,y) → (回合, 归属关系 0无主/1我/2他人, 敌军血量总和)
        self.size = w.size
        self.intel = IntelMemory(name)                      # 间谍回报 / 买到的报表 / 地图情报（看过就记得）

    def _static(self, w, x, y):
        k = self.known.get((x, y))
        if k is None:
            k = (TERR.index(w.tile_terrain(x, y)), [w.tile_resources(x, y).get(r, 0) for r in RES5])
            self.known[(x, y)] = k
        return k

    def encode(self, w: mp.World, cands: list[Candidate], max_turns: int) -> dict:
        me = self.name
        size = w.size
        vis = visible_set(w, me)
        self.intel.update(w)
        I = self.intel
        for p in vis:
            self._static(w, *p)
        order = [n for n in w.order]                                   # 含已亡国的座位序，稳定
        nidx = {n: i for i, n in enumerate(order)}
        wild = {}
        for a in w.armies:
            if a["owner"] == "野人" and (a["x"], a["y"]) in vis:
                wild[(a["x"], a["y"])] = a["hp"]

        enemy_hp: dict[tuple[int, int], float] = {}
        for a in w.armies:
            if a["owner"] not in ("野人", me) and (a["x"], a["y"]) in vis:
                enemy_hp[(a["x"], a["y"])] = enemy_hp.get((a["x"], a["y"]), 0.0) + a["hp"]
        self.sight = {a["id"] if False else (a["owner"], a["id"]): (a["x"], a["y"], a["hp"], UNITS.index(a["type"]))
                      for a in w.armies if a["owner"] not in ("野人", me) and (a["x"], a["y"]) in vis}
        for p in vis:
            if w.tiles.get(p) is not None and w.tiles[p]["buildings"].get("市政厅"):
                self.halls.add(p)
            o_ = w.owned_by(*p)
            self.last_seen[p] = (w.turn, 0 if o_ is None else (1 if o_ == me else 2), enemy_hp.get(p, 0.0))

        # ---- 全局 ----
        nat = w.nations[me]
        loan = w.bank.get("loans", {}).get(me)
        et, maint, short = w.energy_report.get(me, (0, 0, False))
        g = np.zeros(G_DIM, np.float32)
        vals = [w.turn / max_turns, (max_turns - w.turn) / max_turns, len(w.alive()) / max(1, len(order)),
                nat.res.get("黄金", 0) / 1000, *[nat.res.get(x, 0) / 100 for x in TRADEABLE],
                w.nation_gdp(me) / 100, w.bank_credit(me) / 500, (loan["principal"] if loan else 0) / 500,
                len(w.own_tiles(me)) / (size * size), len(w.nation_armies(me)) / 10,
                float(w.at_war(me)), float(w.bloc_of(me) is not None), float(me in w.vassals),
                float(any(v["overlord"] == me for v in w.vassals.values())),
                w.nation_building_count(me, "市政厅") / 3, float(bool(short)), et / 10, maint / 10,
                len([p for p in w.peace_offers if p["b"] == me]) / 3, len(w.proposals) / 3,
                len([v for v in w.votes if w.bloc_of(me) and v["bloc"] == w.bloc_of(me)["name"]]) / 3,
                len(w.diplo_pending) / 3, float(w.bank_on()),
                sum(1 for s_ in w.spy_pending if s_["from"] == me) / 3]
        g[: len(vals)] = vals

        # ---- 市场 ----
        m = np.zeros((len(TRADEABLE), M_DIM), np.float32)
        for i, good in enumerate(TRADEABLE):
            m[i] = [w.prices[good] / MARKET[good], w.market_inv[good] / mp.MARKET_DEPTH[good],
                    w.market_ref[good] / mp.MARKET_DEPTH[good], nat.res.get(good, 0) / 100, float(i) / 6, 1.0, 0, 0]

        # ---- 国家（含自己）----
        ns = np.zeros((len(order), N_DIM), np.float32)
        for n, i in nidx.items():
            alive = n in w.nations
            row = [float(n == me), float(alive)]
            if alive and n != me:
                row += [float(w.war_between(me, n)), float(w.active_truce(me, n) is not None),
                        float(w.entity_of(me) == w.entity_of(n)), float(w.dp_between(me, n)),
                        float(w.entity_of(n) in w.guarantee_of(me)), float(w.entity_of(me) in w.guarantee_of(n)),
                        float(w.vassals.get(n, {}).get("overlord") == me), float(w.vassals.get(me, {}).get("overlord") == n),
                        sum(1 for p in vis if w.owned_by(*p) == n) / (size * size),
                        sum(1 for a in w.nation_armies(n) if (a["x"], a["y"]) in vis) / 10,
                        float(w.bloc_of(n) is not None)]
                row += _intel_nation(w, I, me, n, size)          # [13:38]
            ns[i, : len(row)] = row

        # ---- 地块 ----
        tiles = np.zeros((size * size, T_DIM), np.float32)
        for x in range(size):
            for y in range(size):
                row = tiles[x * size + y]
                row[0], row[1] = x / size, y / size
                st = self.known.get((x, y))
                row[2] = float(st is not None)
                if st is not None:
                    row[3 + st[0]] = 1.0
                    row[10:15] = np.asarray(st[1], np.float32) / 5
                if (x, y) in self.halls:
                    row[47] = 1.0                      # 永久厅标记（看见过就一直在）
                if (x, y) not in vis and (x, y) in self.last_seen:
                    # 过期感知（确定性的空间记忆）：最后一次看到时的归属/敌军血量，以及"多久以前"
                    lt, lo, lh = self.last_seen[(x, y)]
                    row[41] = 1.0
                    row[42] = min(1.0, (w.turn - lt) / 50.0)
                    row[43 + lo] = 1.0
                    row[46] = lh / 100.0
                if (x, y) in vis:
                    row[15] = 1.0
                    o = w.owned_by(x, y)
                    row[16:19] = [o is None, o == me, o not in (None, me)]
                    row[19] = float(o is not None and o != me and w.entity_of(o) == w.entity_of(me))
                    if o is not None and o in nidx:
                        row[20] = nidx[o] / max(1, len(order))
                    t = w.tiles.get((x, y))
                    if t is not None:
                        bl = w.visible_buildings(me, x, y) if o != me else t["buildings"]
                        for bi, b in enumerate(BLD):
                            row[21 + bi] = (bl.get(b, 0) or 0) / 5
                        if o == me:
                            row[37] = sum((t.get("pending") or {}).values()) / 3
                        row[38] = float(t.get("core") == me)
                    if (x, y) in wild:
                        row[39], row[40] = 1.0, wild[(x, y)] / 100
                it = I.tile.get((x, y))
                if it is not None:
                    # 情报里的地块（间谍回报带全部建筑；地图只有城堡级与已用位）：来源国、城、位、多久以前
                    t_, src, c_, s_, bld_, pend_ = it
                    row[48] = 1.0
                    row[49] = min(1.0, (w.turn - t_) / 50.0)
                    row[50] = (nidx[src] + 1) / max(1, len(order)) if src in nidx else 0.0
                    row[51], row[52] = c_ / 5, s_ / 20
                    if bld_ is not None:
                        row[53] = 1.0
                        row[54:70] = np.asarray(bld_, np.float32) / 5
                        row[70] = pend_ / 3

        # ---- 军队（我的全部 + 视野内他人）----
        rows, aidx = [], {}
        for a in w.armies:
            if a["owner"] == "野人":
                continue
            if a["owner"] != me and (a["x"], a["y"]) not in vis:
                continue
            f = np.zeros(A_DIM, np.float32)
            f[UNITS.index(a["type"])] = 1.0
            f[3:10] = [a["hp"] / UNIT_TYPES[a["type"]]["hp"], a["x"] / size, a["y"] / size, a["owner"] == me,
                       a["owner"] != me and w.war_between(me, a["owner"]), float(bool(a.get("engaged"))),
                       float(a.get("moved_turn") == w.turn)]
            f[10] = float(bool(a.get("retreat_to")))
            f[11] = nidx.get(a["owner"], 0) / max(1, len(order))
            if a["owner"] == me:
                aidx[a["id"]] = len(rows)
            rows.append(f)
        armies = np.stack(rows) if rows else np.zeros((0, A_DIM), np.float32)

        # ---- 候选 + 指针（tile/army/nation 下标，-1=无）----
        K = len(cands)
        cf = np.zeros((K, FEAT_DIM), np.float32)
        ptr = -np.ones((K, 3), np.int32)
        for i, c in enumerate(cands):
            cf[i] = c.feat
            a = c.args
            k = c.kind
            if k in ("build", "demolish", "recruit"):
                ptr[i, 0] = a[0] * size + a[1]
            elif k in ("move", "attack", "retreat"):
                ptr[i, 0] = a[1] * size + a[2]
                ptr[i, 1] = aidx.get(a[0], -1)
            elif k == "disband":
                ptr[i, 1] = aidx.get(a[0], -1)
            elif a and isinstance(a[0], str) and a[0] in nidx:
                ptr[i, 2] = nidx[a[0]]
        return dict(globals=g, market=m, nations=ns, tiles=tiles, armies=armies, cand=cf, cand_ptr=ptr,
                    cand_exec=cf[:, 0].copy(), cand_type=np.array([TYPE_ID[c.kind] for c in cands], np.int32))


def _intel_nation(w, I, me, n, size) -> list[float]:
    """国家 token 的情报字段（25 维）：间谍回报、买到的报表、地图情报、在途间谍。"""
    t = w.turn
    out = []
    sp = I.spy.get(n)
    pend = [s_["arrive"] - t for s_ in w.spy_pending if s_["from"] == me and s_["to"] == n]
    if sp is not None:
        st, p = sp
        res = p["res"]
        stock = sum(res.get(g, 0.0) * v for g, v in REF.items())
        bld = [0] * len(BLD)
        for (_c, _s, b, _p) in p["tiles"].values():
            for k, v in enumerate(b):
                bld[k] += v
        cnt = [p["armies"].get(k, 0) for k in UNITS]
        aval = sum(c * army_value({"type": k, "hp": UNIT_TYPES[k]["hp"]}) for k, c in zip(UNITS, cnt))
        out += [1.0, min(1.0, (t - st) / 50.0), res.get("黄金", 0.0) / 1000, stock / 1000, float(p["short"]),
                len(p["tiles"]) / (size * size), sum(c for c, *_ in p["tiles"].values()) / 10,
                sum(s for _c, s, *_ in p["tiles"].values()) / 50, *[c / 10 for c in cnt], aval / 1000,
                bld[BLD.index("市政厅")] / 3, bld[BLD.index("兵营")] / 5, bld[BLD.index("瞭望塔")] / 3]
    else:
        out += [0.0] * (8 + len(UNITS) + 4)
    out += [min(pend) / 3 if pend else 0.0]
    rp = I.report.get(n)
    if rp is not None:
        rt, r = rp
        out += [1.0, min(1.0, (t - rt) / 50.0), (r.get("gdp") or 0) / 100, r.get("military_ratio") or 0.0,
                (r.get("assets") or 0) / 5000, (r.get("invest") or 0) / 500]
    else:
        out += [0.0] * 6
    mp_ = I.maps.get(n)
    out += [1.0, min(1.0, (t - mp_[0]) / 50.0)] if mp_ is not None else [0.0, 0.0]
    out += [float(bool(w.econ_reports.get(n)))]          # 对方已有公布的报表（每期定时公布，人人知道）
    return out


def collate(batch: list[dict]) -> dict:
    """补位成 [B, N, D] 与布尔 padding mask（模型只屏蔽 padding，不做合法性硬掩码）。"""
    out = {}
    for key in ("globals", "market", "nations", "tiles", "armies", "cand"):
        n = max(b[key].shape[0] if b[key].ndim > 1 else 1 for b in batch)
        d = batch[0][key].shape[-1]
        arr = np.zeros((len(batch), n, d), np.float32)
        mask = np.zeros((len(batch), n), bool)
        for i, b in enumerate(batch):
            x = b[key] if b[key].ndim > 1 else b[key][None]
            arr[i, : len(x)] = x
            mask[i, : len(x)] = True
        out[key], out[key + "_mask"] = arr, mask
    k = max(len(b["cand_ptr"]) for b in batch)
    ptr = -np.ones((len(batch), k, 3), np.int32)
    ex = np.zeros((len(batch), k), np.float32)
    ty = np.zeros((len(batch), k), np.int32)
    for i, b in enumerate(batch):
        n = len(b["cand_ptr"])
        ptr[i, :n], ex[i, :n], ty[i, :n] = b["cand_ptr"], b["cand_exec"], b["cand_type"]
    out["cand_ptr"], out["cand_exec"], out["cand_type"] = ptr, ex, ty
    return out


def turn_features(o: dict, prev_hist, prev_invalid: int, prev_steps: int, size: int) -> np.ndarray:
    """回合摘要 x_t（GRU 的输入，原始特征，不含网络输出）：
    全局向量(32) + 上回合我执行的各类动作计数(28) + 视野内敌军/敌地统计(7) + 上回合无效/总步数(2)。
    GRU 在这些摘要序列上积累"趋势与意图"（对手在囤兵？战线在推进？我的损失？），决策时作为一个记忆 token。"""
    x = np.zeros(F_DIM, np.float32)
    x[:G_DIM] = o["globals"]
    h = np.asarray(prev_hist, np.float32)
    x[G_DIM:G_DIM + len(_TYPES)] = np.minimum(h, 20) / 5.0
    arm, til = o["armies"], o["tiles"]
    base = G_DIM + len(_TYPES)
    if len(arm):
        en = arm[arm[:, 6] < 0.5]
        if len(en):
            mine = til[til[:, 17] > 0.5]
            d = 1.0
            if len(mine):
                dx = np.abs(en[:, 4][:, None] - mine[:, 0][None, :]) + np.abs(en[:, 5][:, None] - mine[:, 1][None, :])
                d = float(dx.min())
            x[base:base + 4] = [len(en) / 10.0, float(en[:, 3].mean()), d, float(en[:, 7].mean())]
    vis_enemy = (til[:, 18] > 0.5) & (til[:, 15] > 0.5)
    x[base + 4:base + 7] = [vis_enemy.sum() / (size * size), til[vis_enemy][:, 21].sum() / 5.0 if vis_enemy.any() else 0.0,
                            til[vis_enemy][:, 32].sum() / 3.0 if vis_enemy.any() else 0.0]
    x[base + 7:base + 9] = [prev_invalid / 30.0, prev_steps / 30.0]
    # 本回合新到手的情报（间谍回报 / 地图；age 字段=0 的国家数）
    nat = o["nations"]
    x[base + 9] = float(((nat[:, 13] > 0) & (nat[:, 14] == 0)).sum() + ((nat[:, 35] > 0) & (nat[:, 36] == 0)).sum()) / 3
    return x


M_AUX = 8


def aggregate_lost(prev: dict, cur: dict, size: int) -> np.ndarray:
    """辅助目标（后见之明，只作监督，**不进观测**）：上一回合视野内、这一回合已不在视野内的敌军，
    聚合成定长向量 = [支数, 总血量, 位置和 dx, dy（相对图心）, 三种兵种各几支]。
    用"和"而不是均值（均值会把"丢 1 支"和"丢 4 支恰好平均到同一点"抹成同一个目标）；
    什么都没丢时目标全 0（让记忆学会区分"该记"和"不用记"，而不是把看见的一切都塞进去）。"""
    out = np.zeros(M_AUX, np.float32)
    lost = [v for k, v in prev.items() if k not in cur]
    if not lost:
        return out
    c = size / 2.0
    out[0] = min(1.0, len(lost) / 4.0)
    out[1] = min(1.0, sum(v[2] for v in lost) / 400.0)
    out[2] = sum((v[0] - c) / size for v in lost) / 4.0
    out[3] = sum((v[1] - c) / size for v in lost) / 4.0
    for v in lost:
        out[4 + v[3]] = min(1.0, out[4 + v[3]] + 0.25)
    return out
