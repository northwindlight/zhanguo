"""候选动作（plan.md §4.2）：每一步由环境给出一组**候选**，模型对候选打分并选一个。

★ 不做硬掩码（照上次 economy 训练的做法）：所有"形式上存在"的动作都可选，哪怕当前不可执行；
  「是否可执行」(`executable`) 作为**候选 token 的一个输入特征**交给模型，同时作为辅助监督目标；
  选了不可执行的 = 这一步无效（环境照常返回失败原因、消耗一次步数预算），模型自己学会避开。
  只有**补位（padding）**才会被网络屏蔽。要硬掩码只在评测/部署时作为可选开关（`executable` 就在手边）。

特征向量布局（定长 `FEAT_DIM`）：
  [0] executable   [1] 类型序号/类型数   [2:6] 通用参数 a,b,c,d（按类型含义，见各生成器）
  [6:6+NB] 建筑/兵种/商品 one-hot（NB=16）   [22:] 位置、成本、价格等
"""
from __future__ import annotations

from dataclasses import dataclass, field

import mp
from balance import BUILDINGS, TRADEABLE, UNIT_TYPES

TYPES = ["end_turn", "build", "demolish", "recruit", "buy", "sell", "move", "attack", "retreat", "disband",
         "declare_war", "offer_peace", "accept_peace", "reject_peace", "guarantee", "propose_defense",
         "accept_proposal", "reject_proposal", "bloc_found", "bloc_join", "bloc_leave", "vote",
         "propose_vassalage", "submit_vassalage", "release_vassal", "independence_war", "borrow", "gift",
         "spy", "buy_report", "share_map", "cancel_guarantee", "break_defense"]
TYPE_ID = {t: i for i, t in enumerate(TYPES)}
BLD_NAMES = list(BUILDINGS)
UNIT_NAMES = list(UNIT_TYPES)
NB = 16
FEAT_DIM = 40
QTY_BUCKETS = (1, 5, 20, 60)
PEACE_GOLD = (0, 50, 200)
GIFT_GOLD = (20, 100)


@dataclass
class Candidate:
    kind: str
    args: tuple = ()
    executable: bool = True
    feat: list = field(default_factory=list)
    why: str = ""            # 不可执行时的原因（调试/辅助监督，不进观测）


def _vec(kind, executable, a=0.0, b=0.0, c=0.0, d=0.0, onehot=None, extra=()):
    f = [0.0] * FEAT_DIM
    f[0] = 1.0 if executable else 0.0
    f[1] = TYPE_ID[kind] / len(TYPES)
    f[2:6] = [a, b, c, d]
    if onehot is not None:
        f[6 + onehot] = 1.0
    for i, v in enumerate(extra[: FEAT_DIM - 22]):
        f[22 + i] = v
    return f


def diplo_key(w: mp.World, name: str):
    """常规手续费分档与固定创盟费分别决定现金侧的缓存失效。"""
    gold = w.res(name, "黄金")
    fee_ok = min(int(gold), mp.DIPLO_COST)
    found_fee = mp.DIPLO_QUEUED["propose_bloc"][4]
    return (w.turn, name, len(w.diplo_pending), len(w.proposals), len(w.peace_offers), len(w.votes), len(w.blocs),
            len(w.wars), len(w.vassals), fee_ok, gold >= found_fee, len(w.nation_armies(name)) > 0)


def build_candidates(w: mp.World, name: str, *, diplomacy: bool = True, cache: dict | None = None) -> list[Candidate]:
    out: list[Candidate] = [Candidate("end_turn", (), True, _vec("end_turn", True))]
    size = float(w.size)
    gold, wood = w.res(name, "黄金"), w.res(name, "木头")

    # ---- 经济：建设 / 拆除 / 征兵 ----
    for (x, y) in w.own_tiles(name):
        t = w.tiles[(x, y)]
        res = [t["resources"].get(r, 0) for r in ("矿石", "黄金", "耕地", "石油", "木头")]
        for bi, b in enumerate(BLD_NAMES):
            ok, why = w.build(name, x, y, b, dry=True)
            info = BUILDINGS[b]
            cost = info["cost"] if isinstance(info["cost"], int) else info["cost"][min(t["buildings"].get(b, 0), len(info["cost"]) - 1)]
            out.append(Candidate("build", (x, y, b), ok, _vec("build", ok, x / size, y / size, cost / 200, info.get("wood", 0) / 100, bi,
                                                             extra=[r / 5 for r in res] + [sum(t["buildings"].values()) / 20]), why))
        for bi, b in enumerate(BLD_NAMES):
            if t["buildings"].get(b, 0) or (t.get("pending") or {}).get(b, 0):
                # 拆最后一座市政厅 = 自己灭国（国祚）。这不是"不合法"，而是纯自杀，不进候选集
                # （否则随机策略第 4 回合就自灭，整局回报恒为 0，没有任何学习信号）。
                if b == "市政厅" and w.nation_building_count(name, "市政厅") <= 1:
                    continue
                # 按名拆城堡拆的总是最高一级（main 2026-11 差分：L1 城堡可拆；原 legacy「满级才能拆」是 bug，已删）
                out.append(Candidate("demolish", (x, y, b), True, _vec("demolish", True, x / size, y / size, onehot=bi)))
        for ui, k in enumerate(UNIT_NAMES):
            ok, why = w.recruit(name, x, y, 1, k, dry=True)
            out.append(Candidate("recruit", (x, y, k), ok, _vec("recruit", ok, x / size, y / size, onehot=ui), why))

    # ---- 市场 ----
    for gi, g in enumerate(TRADEABLE):
        price = w.market_price(g)
        for q in QTY_BUCKETS:
            unit, exact = w.market_walk(g, q, "buy", name)
            cash, _ = w._market_cash(name, exact, "buy")
            ok_b = gold >= cash and q <= w.market_inv[g]
            out.append(Candidate("buy", (g, q), ok_b, _vec("buy", ok_b, q / 60, price / 10, cash / 300, onehot=gi),
                                 "" if ok_b else "现金或市场库存不足"))
            have = w.res(name, g)
            ok_s = have >= q
            out.append(Candidate("sell", (g, q), ok_s, _vec("sell", ok_s, q / 60, price / 10, have / 100, onehot=gi),
                                 "" if ok_s else "库存不足"))

    # ---- 军队：每支军 → 可达格（移动/进攻）、撤退、遣散 ----
    for a in w.nation_armies(name):
        moved = a.get("moved_turn") == w.turn
        hp = a["hp"] / UNIT_TYPES[a["type"]]["hp"]
        base = dict(a=a["x"] / size, b=a["y"] / size, c=hp)
        if a.get("engaged"):
            for (nx, ny) in w.neighbors(a["x"], a["y"]):
                ok = (not moved) and w._retreat_legal(name, nx, ny)
                out.append(Candidate("retreat", (a["id"], nx, ny), ok, _vec("retreat", ok, d=(nx - a["x"] + 1) / 2, **base)))
        if not moved and not a.get("engaged"):
            reach = w._reachable(name, a, for_attack=True)
            for (nx, ny), cost in reach.items():
                if (nx, ny) == (a["x"], a["y"]):
                    continue
                o = w.owned_by(nx, ny)
                mine = (o == name) or (o is not None and w.allied_between(name, o))
                walls = w._mv_wall(name, nx, ny)
                d = (abs(nx - a["x"]) + abs(ny - a["y"])) / 2
                if mine:
                    ok = not walls
                    out.append(Candidate("move", (a["id"], nx, ny), ok, _vec("move", ok, d=d, **base)))
                else:
                    # 无主地/敌国地：要占地只能 attack（无守军则直接进驻；有守军则交战）；
                    # 无主地另可 move 路过（野人只打进攻方，不打驻留者）
                    out.append(Candidate("attack", (a["id"], nx, ny), True, _vec("attack", True, d=d, **base)))
                    if o is None:
                        ok = not walls
                        out.append(Candidate("move", (a["id"], nx, ny), ok, _vec("move", ok, d=d, **base)))
        out.append(Candidate("disband", (a["id"],), not a.get("engaged"), _vec("disband", not a.get("engaged"), **base)))

    if diplomacy:
        out += _intel(w, name)
        if cache is None:
            out += _diplomacy(w, name)
        else:
            k = diplo_key(w, name)
            if cache.get("key") != k:
                cache["key"], cache["val"] = k, _diplomacy(w, name)
            out += cache["val"]
    return out


def _diplo_fee(w, method, args) -> int:
    """外交手续费：与 mp 排队包装（`_install_diplo_queue.base_fee`）同一公式。
    干跑副本 `_diplo_now=True` 直接调内层方法，会跳过包装里的"现金够不够付手续费"检查，这里补上。"""
    spec = mp.DIPLO_QUEUED.get(method)
    if spec is None:
        return 0
    _, a_i, t_i, free_c, fixed = spec
    if fixed is not None:
        return fixed
    return _base_fee(w, args[a_i], args[t_i] if t_i is not None else None, free_c)


def _base_fee(w, actor, target, free_c=False) -> int:
    """mp `_install_diplo_queue.base_fee` 的同一公式（排队外交与即时收费动作共用）。"""
    if target and target in w.nations and actor in w.nations and w.entity_of(actor) == w.entity_of(target):
        return 0
    centers = lambda n: w.nation_building_count(n, "外交中心") if n in w.nations else 0
    if free_c and target and centers(target):
        return 0
    fee = mp.DIPLO_COST
    for _ in range(centers(actor)):
        fee = max(mp.DIPLO_CENTER_MIN_COST, fee // 2)
    return fee


def _dry(w, name, method, *args):
    """外交动作的可执行性：先查手续费（包装层的检查），再在世界副本上干跑（与提交时的校验同一套）。"""
    spec = mp.DIPLO_QUEUED.get(method)
    if spec is not None:
        # 包装层：同一命令已在本回合待结算队列中 → 直接返回成功（不再扣费、不干跑）
        key = [args[spec[1]], spec[0], [repr(x) for x in args], []]
        if any(c["key"] == key for c in w.diplo_pending):
            return True, "该命令已在本回合待结算队列中"
    fee = _diplo_fee(w, method, args)
    if fee and w.res(name, "黄金") < fee:
        return False, f"现金不足以支付外交手续费：需 {fee} 金"
    d = w._draft(light=True)
    return getattr(d, method)(*args)


def _proposal_for(w, p, name) -> bool:
    """邀约是否发给本国（与 accept_pact / reject_pact / accept_vassalage 的收件人判定一致）。"""
    if p["kind"] == "联盟":
        return name in p.get("invitees", [])
    if p["kind"] == "附庸":
        return p.get("B") == mp.ent_nation(name)
    return p.get("B") is not None and w.entity_of(name) == p.get("B")


def _diplomacy(w: mp.World, name: str) -> list[Candidate]:
    out: list[Candidate] = []
    others = [n for n in w.alive() if n != name]
    for oi, o in enumerate(others):
        at_war = w.war_between(name, o)
        ok, why = _dry(w, name, "declare_war", name, o)
        out.append(Candidate("declare_war", (o,), ok, _vec("declare_war", ok, oi / 8, at_war), why))
        if at_war:
            for ki, kind in enumerate(("white", "pay", "demand", "submit", "subjugate")):
                for g in (PEACE_GOLD if kind in ("pay", "demand") else (0,)):
                    ok, why = _dry(w, name, "offer_peace", name, o, kind, g, "", 0)
                    out.append(Candidate("offer_peace", (o, kind, g), ok, _vec("offer_peace", ok, oi / 8, ki / 5, g / 200), why))
        ok, why = _dry(w, name, "declare_guarantee", name, o)
        out.append(Candidate("guarantee", (o,), ok, _vec("guarantee", ok, oi / 8), why))
        ok, why = _dry(w, name, "propose_pact", "共同防御", name, o)
        out.append(Candidate("propose_defense", (o,), ok, _vec("propose_defense", ok, oi / 8), why))
        ok, why = _dry(w, name, "propose_vassalage", name, o)
        out.append(Candidate("propose_vassalage", (o,), ok, _vec("propose_vassalage", ok, oi / 8), why))
        ok, why = _dry(w, name, "submit_vassalage", name, o)
        out.append(Candidate("submit_vassalage", (o,), ok, _vec("submit_vassalage", ok, oi / 8), why))
        # 条约管理：只在关系存在时出现（与 accept_peace 一样，不存在的关系不进候选集）
        if w.entity_of(name) in w.guarantee_of(o):               # guarantee_of 返回实体 id（国:名）
            ok, why = _dry(w, name, "cancel_guarantee", name, o)
            out.append(Candidate("cancel_guarantee", (o,), ok, _vec("cancel_guarantee", ok, oi / 8), why))
        if w.dp_between(name, o):
            ok, why = _dry(w, name, "break_pact", "共同防御", name, o)
            out.append(Candidate("break_defense", (o,), ok, _vec("break_defense", ok, oi / 8), why))
        if w.vassals.get(o, {}).get("overlord") == name:
            ok, why = _dry(w, name, "release_vassal", name, o)
            out.append(Candidate("release_vassal", (o,), ok, _vec("release_vassal", ok, oi / 8), why))
        ok, why = _dry(w, name, "propose_bloc", name, f"{name}盟", [o])
        out.append(Candidate("bloc_found", (o,), ok, _vec("bloc_found", ok, oi / 8), why))
    for p in w.peace_offers:
        if p["b"] == name:
            ok, why = _dry(w, name, "accept_peace", name, p["id"])
            out.append(Candidate("accept_peace", (p["id"],), ok, _vec("accept_peace", ok, p["gold"] / 200), why))
            ok, why = _dry(w, name, "reject_peace", name, p["id"])
            out.append(Candidate("reject_peace", (p["id"],), ok, _vec("reject_peace", ok), why))
    for p in w.proposals:
        if not _proposal_for(w, p, name):          # 不是发给本国的邀约不进候选（原来全列，Codex 指出）
            continue
        ok, why = _dry(w, name, "accept_pact", name, p["id"])
        out.append(Candidate("accept_proposal", (p["id"],), ok, _vec("accept_proposal", ok), why))
        ok, why = _dry(w, name, "reject_pact", name, p["id"])
        out.append(Candidate("reject_proposal", (p["id"],), ok, _vec("reject_proposal", ok), why))
    for v in w.votes:
        bloc = w.bloc_by_name(v["bloc"])
        if bloc and name in bloc["members"] and w.turn == v.get("deadline", v["turn"] + 1):
            for choice in (True, False, None):
                out.append(Candidate("vote", (v["id"], choice), True, _vec("vote", True, 1.0 if choice else 0.0, 0.5 if choice is None else 0.0)))
    if w.bloc_of(name) is not None:
        ok, why = _dry(w, name, "bloc_leave", name)
        out.append(Candidate("bloc_leave", (), ok, _vec("bloc_leave", ok), why))
    for b in w.blocs:
        ok, why = _dry(w, name, "bloc_join", name, b["name"])
        out.append(Candidate("bloc_join", (b["name"],), ok, _vec("bloc_join", ok), why))
    if name in w.vassals:
        ok, why = _dry(w, name, "independence_war", name)
        out.append(Candidate("independence_war", (), ok, _vec("independence_war", ok), why))
    return out


def _vassal_gated(w, frm, to) -> bool:
    """mp `vassal_gate`：普通附庸不能对宗主实体之外赠礼/换图/派间谍。"""
    v = w.vassals.get(frm)
    return bool(v and v.get("kind", "ordinary") == "ordinary" and w.entity_of(frm) != w.entity_of(to))


def _intel(w: mp.World, name: str) -> list[Candidate]:
    """谍报/情报/馈赠：即时生效、只看金币与附庸限制，判定便宜，不进外交缓存（随金币变化）。
    可执行性与 mp 包装层同一套：先手续费（immediate_fee），再附庸限制（vassal_gate），再方法本身。"""
    out: list[Candidate] = []
    gold = w.res(name, "黄金")
    bank = w.bank_on()
    if bank:
        # 借款（不进外交缓存：借到手后金币可能仍 ≥10、键不变，缓存会过期）。
        # 与 bank_loan 同一套：手续费（immediate_fee，无目标）→ 未还清不能再借 → 授信 = GDP×5 > 0
        fee, credit = _base_fee(w, name, None), w.bank_credit(name)
        ok = name not in w.bank.get("loans", {}) and credit > 0 and (fee == 0 or gold >= fee)
        out.append(Candidate("borrow", (), ok, _vec("borrow", ok, credit / 500, fee / 10),
                             "" if ok else "未还清、授信为 0 或手续费不足"))
    for oi, o in enumerate(n for n in w.alive() if n != name):
        gated = _vassal_gated(w, name, o)
        pend = sum(1 for s in w.spy_pending if s["from"] == name and s["to"] == o)
        ok = gold >= mp.SPY_COST and not gated
        out.append(Candidate("spy", (o,), ok, _vec("spy", ok, oi / 8, min(pend, 3) / 3, mp.SPY_COST / 200),
                             "" if ok else ("普通附庸没有对外外交权" if gated else "金币不足")))
        if bank:
            ok = gold >= mp.BUY_REPORT_COST and bool(w.econ_reports.get(o))
            out.append(Candidate("buy_report", (o,), ok, _vec("buy_report", ok, oi / 8, mp.BUY_REPORT_COST / 200),
                                 "" if ok else "金币不足或对方尚无报表"))
        fee = _base_fee(w, name, o)
        ok = (fee == 0 or gold >= fee) and not gated          # 费用为 0 时包装层不查金币（金币可为负：贷款强扣）
        out.append(Candidate("share_map", (o,), ok, _vec("share_map", ok, oi / 8, fee / 10),
                             "" if ok else "手续费不足或附庸限制"))
        for g in GIFT_GOLD:
            ok = gold >= fee + g and not gated
            out.append(Candidate("gift", (o, "黄金", g), ok, _vec("gift", ok, oi / 8, g / 200, fee / 10),
                                 "" if ok else "金币不足或附庸限制"))
    return out


def _report_text(r: dict) -> str:
    """买到的报表正文（引擎只记钱与库，排版由调用方给；观测按结构化字段读，不解析这段文字）。"""
    keys = ("gdp", "military", "military_ratio", "invest", "assets", "export_gold", "import_gold", "trade_ratio")
    return " ".join(f"{k}={r.get(k)}" for k in keys)


def apply(w: mp.World, name: str, c: Candidate):
    """执行一个候选；不可执行的照样尝试（返回失败原因），由调用方计步数/无效计数。"""
    k, a = c.kind, c.args
    if k == "end_turn":
        return True, "结束行动"
    if k == "build":
        return w.build(name, *a)
    if k == "demolish":
        return w.demolish(name, *a)
    if k == "recruit":
        return w.recruit(name, a[0], a[1], 1, a[2])
    if k == "buy":
        return w.buy(name, *a)
    if k == "sell":
        return w.sell(name, *a)
    if k == "move":
        return w.move(name, *a)
    if k == "attack":
        aid, x, y = a
        arm = w._army(name, aid)
        same = [m["id"] for m in w.nation_armies(name) if arm and (m["x"], m["y"]) == (arm["x"], arm["y"])
                and m.get("moved_turn") != w.turn and not m.get("engaged")] or [aid]
        return w.attack(name, same, x, y)          # 同格能动的军一起上（成堆推进）
    if k == "retreat":
        return w.retreat(name, *a)
    if k == "disband":
        return w.disband(name, list(a))
    simple = {"declare_war": "declare_war", "accept_peace": "accept_peace", "reject_peace": "reject_peace",
              "accept_proposal": "accept_pact", "reject_proposal": "reject_pact", "bloc_leave": "bloc_leave",
              "propose_vassalage": "propose_vassalage", "submit_vassalage": "submit_vassalage",
              "independence_war": "independence_war", "guarantee": "declare_guarantee", "bloc_join": "bloc_join"}
    if k in simple:
        return getattr(w, simple[k])(name, *a)
    if k == "offer_peace":
        o, kind, g = a
        return w.offer_peace(name, o, kind, g, "", 0)
    if k == "propose_defense":
        return w.propose_pact("共同防御", name, a[0])
    if k == "vote":
        return w.cast_vote(name, a[0], a[1])
    if k == "borrow":
        return w.bank_loan(name)
    if k == "spy":
        return w.spy(name, a[0])
    if k == "buy_report":
        reps = w.econ_reports.get(a[0]) or []
        if not reps:
            return False, "对方尚无已公布的报表"
        return w.bank_sell_report(name, a[0], reps[-1]["report_turn"], _report_text(reps[-1]))
    if k == "share_map":
        return w.share_map(name, a[0])
    if k == "gift":
        return w.gift(name, *a)
    if k == "cancel_guarantee":
        return w.cancel_guarantee(name, a[0])
    if k == "break_defense":
        return w.break_pact("共同防御", name, a[0])
    if k == "release_vassal":
        return w.release_vassal(name, a[0])
    if k == "bloc_found":
        return w.propose_bloc(name, f"{name}盟", [a[0]])
    return False, f"未知候选 {k}"
