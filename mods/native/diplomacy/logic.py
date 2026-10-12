from __future__ import annotations
from ..core import *
import inspect
from ..rules import letter_cost

COMMANDS = {
    "accept_peace": (10, "me"), "reject_peace": (11, "me"), "offer_peace": (12, "a"),
    "bloc_dissolve": (20, "a"), "bloc_leave": (21, "a"),
    "bloc_transfer": (22, "a"), "bloc_rename": (23, "a"),
    "propose_bloc": (24, "a"), "bloc_join": (25, "a"),
    "cancel_guarantee": (30, "a"), "break_pact": (31, "a"),
    "declare_guarantee": (32, "a"), "propose_pact": (33, "a"),
    "reject_pact": (34, "me"), "accept_pact": (35, "me"),
    "declare_war": (40, "a"),
    "send_mail": (50, "frm"), "gift": (51, "frm"),
    "share_map": (52, "frm"), "spy": (53, "frm"),
}

FREE = {"bloc_transfer", "bloc_rename", "bloc_dissolve"}


MUTABLE = ("nations", "tiles", "blocs", "pacts", "wars", "truce", "votes",
           "peace_offers", "proposals", "mail_pending", "gift_pending", "map_pending",
           "spy_pending", "flow_in", "flow_out", "_war_id", "_vote_id", "_offer_id",
           "diplomacy_queue", "approved_votes")

def rename_pending_refs(value, old, new):
    """改名只改外交引用；信件正文与审计文字保持原样。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                if item == ent_bloc(old):
                    value[key] = ent_bloc(new)
                elif key in ('bloc', 'bloc_name') and item == old:
                    value[key] = new
            elif isinstance(item, (dict, list)):
                rename_pending_refs(item, old, new)
    elif isinstance(value, list):
        for item in value:
            rename_pending_refs(item, old, new)

def queued(method):
    """装饰显式公共方法，签名保持不变；私有生效函数只供结算和初始化使用。"""
    from functools import wraps
    @wraps(method)
    def submit(self, *args, **kwargs):
        params = inspect.signature(method).bind(self, *args, **kwargs)
        params.apply_defaults()
        return self.submit_diplomacy(method.__name__, {k: v for k, v in params.arguments.items() if k != "self"})
    return submit

class DiplomacyLogic:
    def bloc_of(self, name: str) -> dict | None:
        """name 所属联盟（一国同时只属一个联盟）；无则 None。"""
        for b in self.blocs:
            if name in b["members"]:
                return b
        return None


    def bloc_by_name(self, name: str) -> dict | None:
        for b in self.blocs:
            if b["name"] == name:
                return b
        return None


    def bloc_chief(self, bloc: dict | None) -> str | None:
        """联盟盟主。以 chief 字段为准；字段缺失/失效（旧档、盟主已不在）时退回最早加入者。"""
        if not bloc or not bloc.get("members"):
            return None
        c = bloc.get("chief")
        return c if c in bloc["members"] else bloc["members"][0]


    def entity_of(self, name: str) -> str:
        """该国的外交实体：在盟 → 其联盟；否则 → 它自己（独立国家）。"""
        b = self.bloc_of(name)
        return ent_bloc(b["name"]) if b is not None else ent_nation(name)


    def entity_members(self, ent: str) -> list[str]:
        """实体包含的现存国家（联盟=全体成员，加入序；独立国家=[自己]；已灭=[]）。"""
        if is_bloc_ent(ent):
            b = self.bloc_by_name(ent_key(ent))
            return [m for m in b["members"] if m in self.nations] if b else []
        n = ent_key(ent)
        return [n] if n in self.nations else []


    def entity_chief(self, ent: str) -> str | None:
        """实体的对外代表：联盟=盟主，独立国家=它自己。"""
        if is_bloc_ent(ent):
            return self.bloc_chief(self.bloc_by_name(ent_key(ent)))
        n = ent_key(ent)
        return n if n in self.nations else None


    def entity_label(self, ent: str) -> str:
        """日志/面板用的人话名。"""
        if is_bloc_ent(ent):
            b = self.bloc_by_name(ent_key(ent))
            return f"联盟「{ent_key(ent)}」" if b else f"联盟「{ent_key(ent)}」（已散）"
        return ent_key(ent)


    def entities(self) -> list[str]:
        """全部现存外交实体（独立国家 + 联盟），**sorted** —— 面板与闭包都不许裸集合迭代。"""
        out = [ent_nation(n) for n in self.alive() if self.bloc_of(n) is None]
        out += [ent_bloc(b["name"]) for b in self.blocs
                if self.entity_members(ent_bloc(b["name"]))]
        return sorted(out)


    def entity_at_war(self, ent: str) -> bool:
        """实体是否卷入战争（任一国在打就算）——战时锁死与缔约禁令都看它。"""
        return any(self.at_war(m) for m in self.entity_members(ent))


    def _war_brief_ent(self, ent: str) -> str:
        for m in self.entity_members(ent):
            if self.at_war(m):
                return self._war_brief(m)
        return f"{self.entity_label(ent)} 正在交战"


    def _same_camp(self, x: str, y: str) -> bool:
        """x、y 是否"该并肩"：同属一个实体，或有共同防御（宣战并入战线的判据）。"""
        ex, ey = self.entity_of(x), self.entity_of(y)
        return ex == ey or self.has_pact("共同防御", ex, ey)


    def has_pact(self, kind: str, a: str, b: str) -> bool:
        """a、b 都是**实体 id**。保障看方向（a 保障 b）；共同防御对称。"""
        if kind == "保障":
            return any(p["kind"] == kind and p["a"] == a and p["b"] == b for p in self.pacts)
        return any(p["kind"] == kind and {p["a"], p["b"]} == {a, b} for p in self.pacts)


    def pacts_of(self, ent: str) -> list[dict]:
        """与该实体有关的一切条约（面板/断约/亡国清理用）。"""
        return [p for p in self.pacts if ent in (p["a"], p["b"])]


    def guaranteed_by(self, ent: str) -> list[str]:
        """ent 保障的实体（**sorted**）。面板用。"""
        return sorted(p["b"] for p in self.pacts if p["kind"] == "保障" and p["a"] == ent)


    def guarantors_of(self, ent: str) -> list[str]:
        """保障 ent 的实体（**sorted**：闭包要按它迭代，裸集合序会毁复现性）。"""
        return sorted(p["a"] for p in self.pacts if p["kind"] == "保障" and p["b"] == ent)


    def defense_partners_of(self, ent: str) -> list[str]:
        """与 ent 有共同防御的实体（**sorted**，同上）。"""
        return sorted(p["b"] if p["a"] == ent else p["a"]
                      for p in self.pacts
                      if p["kind"] == "共同防御" and ent in (p["a"], p["b"]))


    def _add_pact(self, kind: str, a: str, b: str) -> None:
        x, y = (a, b) if kind == "保障" else tuple(sorted((a, b)))
        self.pacts.append({"kind": kind, "a": x, "b": y, "turn": self.turn})


    def _drop_pact(self, kind: str, a: str, b: str) -> bool:
        """删一条条约（a、b 是实体 id）。返回是否真删到。"""
        for p in list(self.pacts):
            if p["kind"] != kind:
                continue
            hit = (p["a"] == a and p["b"] == b) if kind == "保障" else ({p["a"], p["b"]} == {a, b})
            if hit:
                self.pacts.remove(p)
                return True
        return False


    def drop_pacts_of(self, ent: str) -> list[str]:
        """清掉与该实体有关的一切条约（入盟吞掉个人条约、国家灭亡）。返回人话描述。"""
        gone = []
        for p in list(self.pacts):
            if ent in (p["a"], p["b"]):
                self.pacts.remove(p)
                gone.append(f"{p['kind']}（{self.entity_label(p['a'])}—{self.entity_label(p['b'])}）")
        return gone


    def _pact_block(self, kind: str, A: str, B: str) -> str | None:
        """准入：两实体能否签这种条约。返回拒绝理由，None=可以签。"""
        if A == B:
            return (f"{self.entity_label(B)} 与你同属一个实体——盟内互通领土/互不攻击/互卫"
                    "是联盟自带效果，无须也无法单独缔约")
        if kind == "保障":
            if self.has_pact("保障", A, B):
                return f"{self.entity_label(A)} 已保障 {self.entity_label(B)}，无须重复"
            if self.has_pact("共同防御", A, B):
                return f"两实体已有共同防御（更高一档），无须再保障"
            return None
        if self.has_pact("共同防御", A, B):
            return "两实体已是共同防御"
        return None


    def _conclude_pact(self, kind: str, A: str, B: str) -> tuple[bool, str]:
        """真正落条约（双方实体都已同意）。缔结共同防御时自动解除低档的保障。"""
        low = []
        if kind == "共同防御":
            for x, y in ((A, B), (B, A)):
                if self._drop_pact("保障", x, y):
                    low.append(f"{self.entity_label(x)} 对 {self.entity_label(y)} 的保障")
        self._add_pact(kind, A, B)
        up = f"（自动解除低档：{'、'.join(low)}）" if low else ""
        self.proclaim(f"🕊 {self.entity_label(A)} 与 {self.entity_label(B)} 缔结{kind}{up}")
        return True, f"{self.entity_label(A)} 与 {self.entity_label(B)} 缔结{kind}{up}"


    def _break_pacts_between(self, e1: str, e2: str) -> list[str]:
        """开战前清约束：解除两实体之间的一切保障/共同防御（含双向保障）。"""
        out = []
        for kind, x, y in (("共同防御", e1, e2), ("保障", e1, e2), ("保障", e2, e1)):
            if self._drop_pact(kind, x, y):
                out.append(f"解除 {self.entity_label(x)} 与 {self.entity_label(y)} 的{kind}")
        for line in out:
            self.proclaim(f"💔 {line}（双方开战，自动解除）")
        return out


    def _pact_exit_wars(self, side: list[str], other: list[str]) -> list[dict]:
        """断约退战：跟随方不想打的退出通道——昔日盟友参与的战线，其跟随方身份随之解除。
        side/other 是**国家名单**（实体成员）；名单序来自联盟成员序，不做集合迭代。"""
        exited = []
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if not any(o in atk + dfs for o in other):
                continue
            hit = False
            for m in side:
                if m in w["followers"]:
                    w["followers"].remove(m)
                    hit = True
                if m in w.get("atk_followers", []):
                    w["atk_followers"].remove(m)
                    hit = True
            if hit:
                exited.append(w)
        for m in side:      # 已不属任何战线 → 解除交战，回合末自动遣返
            still = any(m in ([x["atk"]] + list(x.get("atk_followers", []))
                              + [x["def"]] + list(x["followers"])) for x in self.wars)
            if not still:
                self.runtime.object_call(self,'army.units','disengage',[m])
        return exited


    def at_war(self, name: str) -> bool:
        """该国是否正卷入任何战争（进攻/防御主导者或跟随方）。"""
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if name in atk or name in dfs:
                return True
        return False


    def set_truce(self, a: str, b: str, until: int) -> bool:
        """写一条休战（**唯一写入口**）。a/b 给**国名**（按它所在的实体存）或**实体 id**。

        **只许延长、不许缩短**（`max`）：全天下强制休战是防雪球的地基，不该被一纸短约抹掉。
        同实体（盟内）不写，返回 False。
        """
        ea = self.entity_of(a) if a in self.nations else _truce_key_id(a)
        eb = self.entity_of(b) if b in self.nations else _truce_key_id(b)
        if ea == eb:
            return False
        p = _pair(ea, eb)
        self.truce[p] = max(self.truce.get(p, 0), int(until))
        return True


    def active_truce(self, a: str, b: str) -> int | None:
        """a、b 之间**有效**的休战 → 到期回合（已到期的顺手清掉，None = 没有）。

        ★★ 休战表**按外交实体存**（与条约表 `self.pacts` 同一口径，2026-10-11 修）：
        键是实体 id（独立国＝`国:x`，联盟＝`盟:名字`）。

        · 同属一个实体（联盟）⇒ None：**盟内互不攻击**比和约更强，那纸约不独立成立；
        · 否则查**两个实体之间**那条 ⇒ **成员个人的和约在盟内自动冻结**（它的键是
          `国:成员`，而此刻这个国家属于 `盟:某盟`，键对不上），**退出即自动恢复**
          （键又对上了）——表里那条**一个字都不用删**。

        ★★ 为什么不能"查盟主"（我第一版就是这么写的，被用户当场判错）：
        「移交盟主，没有移交联盟关系，变成了被移交者的个人关系」——把实体的约**派生**自
        盟主身上那条，等于**换盟主就换掉了全盟的对外关系**：新盟主跟谁有私约，全盟就跟谁
        有约。**联盟关系是联盟关系，个人关系是个人关系**：前者记在联盟身上、只随投票变，
        后者冻结在成员自己身上、随进退盟解冻。现在换盟主**动不了**任何一条。
        """
        ea, eb = self.entity_of(a), self.entity_of(b)
        if ea == eb:
            return None                      # 同实体：盟内互不攻击
        p = _pair(ea, eb)
        t = self.truce.get(p)
        if t is None:
            return None
        if self.turn > t:
            self.truce.pop(p, None)
            return None
        return t


    def truces_of(self, name: str) -> list[tuple[str, int]]:
        """该国（**其所在实体**）还在休战期内的全部对手 → [(对手, 到期回合)]，按对手排序。

        ★ 含**亡国时压给全天下的强制休战**（`FALL_TRUCE_TURNS`）——用户 2026-09-20 口径：
        那条也算"和约"。它只拦**宣战**，不拦结盟（见 `active_truce`）。
        返回的是**实体 id**（"盟:周天下" / "国:燕"）；成员个人冻结中的私约不在此列。
        """
        me_ent = self.entity_of(name)
        out: dict[str, int] = {}
        for p in list(self.truce):
            x, y = sorted(p)          # ★ sorted：p 是 frozenset，裸迭代序受 PYTHONHASHSEED 影响
            if x != me_ent and y != me_ent:
                continue
            other = y if x == me_ent else x
            # 陈旧条目：成员**入盟前**跟这个联盟签的那条（键是 国:成员↔盟:本盟）。
            # 入盟那一刻它就等于"联盟跟自己"，不该再作为对手出现在面板上。
            if other == me_ent or (other.startswith("国:")
                                   and self.entity_of(other[2:]) == me_ent):
                continue
            t = self.truce.get(p)
            if t is None or self.turn > t:
                if t is not None:
                    self.truce.pop(p, None)
                continue
            out[other] = max(out.get(other, 0), t)
        return sorted(out.items())


    def _suspended_truces(self, members: list[str]) -> list[str]:
        """这批国家手里那些**从此暂停**的和约 → 人话（只**读表**报事实，不改表）。

        ★ 2026-10-11：实体的和约＝**盟主**的和约 ⇒ 成员自己签的那些一律暂停（对内对外
        都一样）。表里那条根本没动，所以"退出自动恢复"不需要任何额外机制——
        但不报一句就成了"结果不告诉当事人"。
        """
        out: list[str] = []
        seen: set = set()
        for m in members:
            for p in sorted(self.truce, key=sorted):
                if p in seen or ent_nation(m) not in p:
                    continue
                u = self.truce[p]
                if self.turn > u:
                    continue
                seen.add(p)
                out.append(f"{'↔'.join(sorted(p))}（原休战至第 {u} 回合）")
        return out


    def _vote_stakeholders(self, v: dict) -> list[str]:
        """一条投票的**当事人**：盟内全体成员 + **盟外**相关方（申请人／对方实体／被邀方）。

        ★ 用户 2026-10-11：「**投票是否成功，或者被取消，盟外和盟内都需要通知的**，
        例如某个国家提请加入结果中途爆发战争，应该被通知」。
        此前这些结局只挂 `nation=proposer`——**申请人（恰恰常在盟外）什么都收不到**：
        他提了入盟申请，盟主一票否决／票没过／联盟解散把票清了，他一无所知，
        只能对着 `diplomacy` 面板里消失的投票号猜。
        """
        pl = v.get("payload") or {}
        out: list[str] = []
        b = self.bloc_by_name(v.get("bloc") or "")
        if b:
            out += list(b["members"])
        if v.get("proposer") in self.nations:
            out.append(v["proposer"])
        for key in ("candidate", "to", "target", "offer_id"):
            # candidate=入盟申请人（国名）；to=议和对象（国名）；target/A/B=实体 id（下面统一展开）
            if pl.get(key) in self.nations:
                out.append(pl[key])
        for ent in pl.values():
            # 实体 id（"盟:x"/"国:y"）：认不出的值返回 []，所以"pact"/"cancel"这些也不怕
            if isinstance(ent, str):
                out += self.entity_members(ent)
        out += [x for x in (pl.get("invitees") or []) if x in self.nations]
        seen: set = set()
        res: list[str] = []
        for x in out:
            if x in self.nations and x not in seen:
                seen.add(x)
                res.append(x)
        return sorted(res)


    def _cancel_vote(self, v: dict, why: str) -> None:
        """作废一条投票（若还在表里）并**通知盟内盟外全部当事人**。

        ★ **计票数字不进这条**：用户 2026-09-19 的"商议过程不公开"没被推翻——
        要通知的是**结果**（成没成、废没废），不是票数。票数照旧只在当事国的**回复**里
        给到发起人（`_tally` 的返回值），盟内其他人的面板里进行中的票本来就带票数。
        """
        if v in self.votes:
            self.votes.remove(v)
        self.log(f"🗳 「{v['bloc']}」投票#{v['id']}（{v['kind']}）作废：{why}",
                 phase="外交", parties=self._vote_stakeholders(v))


    def _void_pending_treaties(self, why: str, all_kinds: bool = False) -> list[str]:
        """开战 ⇒ **战前还没通过的外交条约一律当场作废**（2026-10-11 用户口径）。

        `all_kinds=True` 是给**强制和平**用的（用户 2026-10-11 报的洞：「强制和平后，
        还没有敲定的和平条约没有自动作废」）：亡国那一刻**所有战线当场终止 + 全天下压上
        强制休战**，在途的一切（宣战/议和投票、求和提议）全都失去前提，一律作废；
        而开战时只清"缔约/入盟"，**宣战与议和投票要留着**——那是战争本身的操作。

        「战时条约冻结」（缔结与解除都不行，见 `propose_pact` / `break_pact`）早就有，
        但**在途的表决与邀约是绕过它的后门**：缔约投票、入盟投票、结盟提议、
        共同防御/保障邀约——统统是"**战前发起、战后再落地**"：发起那一刻还没打仗，
        通过那一刻已经在打了。开战即清，谁都别想搭这趟车。
        ★ **宣战与议和投票不动**：那是战争本身的操作，清了等于把出口也堵死。
        """
        votes = [v for v in self.votes
                 if all_kinds or v["kind"] in ("缔约", "入盟")]
        props = list(self.proposals)
        offers = list(self.peace_offers) if all_kinds else []
        if not votes and not props and not offers:
            return []
        killed: list[str] = []
        for v in votes:              # 每条投票**单独通知**它的盟内盟外当事人（含申请人）
            killed.append(f"{v['kind']}投票#{v['id']}（「{v['bloc']}」）")
            self._cancel_vote(v, f"{why}——{'战线已全部终止' if all_kinds else '战时条约冻结'}")
        for o in offers:
            # 在途的求和提议：强制和平之后它已经**没有战线可停**了，留着只会在落地那一刻
            # 把全天下强制休战覆盖成它自己的短休战（2026-10-11 报的洞）。
            killed.append(f"求和提议#{o['id']}（{o['a']}→{o['b']}）")
            self.peace_offers.remove(o)
            self.log(f"🕊 {o['a']} 对 {o['b']} 的求和提议#{o['id']}作废：{why}"
                     "（战线已终止，无须再谈；强制休战期照旧）", phase="外交",
                     parties=sorted({o["a"], o["b"]} & set(self.nations)))
        if props:
            killed += [f"{p['kind']}提议#{p['id']}" for p in props]
            parties: list[str] = []
            for p in props:
                for key in ("a", "asker"):
                    if p.get(key) in self.nations:
                        parties.append(p[key])
                parties += [x for x in (p.get("invitees") or []) if x in self.nations]
                for ent in (p.get("A"), p.get("B")):
                    if isinstance(ent, str):
                        parties += self.entity_members(ent)
            self.proposals = []
            self.log(f"🕊 {why} ⇒ **战前未通过的外交条约当场作废**：{'、'.join(killed)}"
                     "（战时条约冻结；停战后可重新提）", phase="外交",
                     parties=sorted(set(parties)))
        return killed


    def _truce_blocks_war_join(self, countries: list[str], opponents: list[str]) -> bool:
        """这批国家里有没有谁与**对面**还在休战期内 ⇒ 不能把它拖进这场战争。

        用在自动参战（联盟/共同防御/保障的守侧一跳）上：用户 2026-09-20
        「有和约时，防御条约和独立保障应该无法执行」——**已有的条约不触发**，
        和约不能因为盟友开战就当场作废。
        """
        return any(self.active_truce(m, o) for m in countries for o in opponents)


    def _war_brief(self, name: str) -> str:
        """给"战争期禁止外交"类错误提示用的战线简述。"""
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if name in atk:
                return f"{name} 正与 {'、'.join(dfs)} 交战"
            if name in dfs:
                return f"{name} 正与 {'、'.join(atk)} 交战"
        return ""


    def bloc_desc(self) -> str:
        """看海/面板用：全部联盟一览。"""
        if not self.blocs:
            return "无联盟"
        parts = []
        for b in self.blocs:
            chief = self.bloc_chief(b) or "?"
            parts.append(f"「{b['name']}」（盟主 {chief}；成员：{'、'.join(b['members'])}）")
        return "  ".join(parts)


    def _apply_send_mail(self, frm: str, to: str, text: str) -> tuple[bool, str]:
        if frm not in self.nations or to not in self.nations:
            return False, "收发双方都必须是现存国家"
        if to == frm:
            return False, "不能给自己写信"
        self.mail_pending.append({"from": frm, "to": to, "text": text, "arrive": self.turn + 1})
        # 信件正文不单独记一条（信件=一次行动，正文已在行动行里）；送达时另有"收到信"事件+收件箱
        return True, f"信已发出，{to} 将于第 {self.turn+1} 回合收到"


    def mystery_letter(self, to: str, text: str) -> tuple[bool, str]:
        """Observer 从看海终端给任意国家寄一封『神秘人』来信（下回合到其信箱）。

        发件人恒为「神秘人」，各国 AI 无从判断是谁；只记一条观察者可见的
        日志（nations 收不到），收件人下回合在信箱看到。
        """
        if to not in self.nations:
            return False, f"国家 {to} 不存在"
        text = (text or "").strip()
        if not text:
            return False, "神秘来信内容不能为空"
        self.mail_pending.append({"from": "神秘人", "to": to, "text": text,
                                  "arrive": self.turn + 1})
        self.log(f"🕵️ 神秘人来信 → {to}：{text}", phase="事件")  # 观察者可见，各国近讯收不到
        return True, f"神秘人来信已寄给 {to}，将于第 {self.turn+1} 回合送达其信箱"


    def _apply_gift(self, frm: str, to: str, good: str, n: int) -> tuple[bool, str]:
        """馈赠：把本国储备赠与他国（粮木矿油装补给或黄金）。本回合垫支扣出，下回合到账。"""
        if frm not in self.nations or to not in self.nations:
            return False, "馈赠双方都必须是现存国家"
        if to == frm:
            return False, "不能赠给自己"
        if good not in RES_KEYS:
            return False, f"不可赠送：{good}（可赠：{'/'.join(RES_KEYS)}）"
        if not isinstance(n, int) or n <= 0:
            return False, "数量需为正整数"
        have = self.res(frm, good)
        if have < n:
            return False, f"你{good}不够：现 {have}，赠不出 {n}"
        self.add_res(frm, good, -n)  # 先垫支扣出（锁住）
        self.gift_pending.append({"from": frm, "to": to, "good": good, "n": n,
                                  "arrive": self.turn + 1})
        return True, f"已赠 {to} {good}×{n}（你余 {self.res(frm, good)}），将于第 {self.turn+1} 回合到账"


    def _map_snapshot(self, n: str) -> str:
        """把某国的"已知地图"做成文本：全部国土块 + 边界外可见块，全部带坐标（不截断）。"""
        own = self.own_tiles(n)
        fr = sorted(self.frontier_of(n))
        L = [f"{n} 已知地图：国土 {len(own)} 块、边界外可拓地 {len(fr)} 格（全部坐标）"]
        for p in own:
            t = self.tiles[p]
            L.append(f"  {t.get('name', '?')} {t['terrain']}({p[0] + 1},{p[1] + 1}) "
                     f"城L{t['buildings']['城堡']} 位{sum(t['buildings'].values())}")
        if fr:
            L.append("  边界外可见（未占）:")
            for p in fr:
                L.append(f"  {self.tile_terrain(*p)}({p[0] + 1},{p[1] + 1})")
        return "\n".join(L)


    def _apply_share_map(self, frm: str, to: str) -> tuple[bool, str]:
        """把你的已知地图发给别国（交换情报/展示势力），下回合到对方【地图情报】。"""
        if frm not in self.nations or to not in self.nations:
            return False, "交换地图双方都必须是现存国家"
        if to == frm:
            return False, "不能把地图发给自己"
        self.map_pending.append({"from": frm, "to": to, "text": self._map_snapshot(frm),
                                 "arrive": self.turn + 1})
        return True, f"已把你的地图发给 {to}，将于第 {self.turn+1} 回合到账"


    def _econ_snapshot(self, n: str) -> str:
        """目标国当前完整底细：国库/储备 + 收入结算 + 全部地块建设(含在建) + 粗略军情。"""
        r = self.nations[n].res
        res_txt = " ".join(f"{k}{r.get(k, 0)}" for k in RES_KEYS)
        et, mt, short = self.energy_report.get(n, (0, 0, False))
        grid = "停摆" if short else f"产{et}/需{mt}"
        L = [f"【{n} 情报】国库/储备: {res_txt} | 电网: {grid}"]
        summ = self.econ_summary.get(n)
        if summ:
            L.append(f"  上回合收入结算: {summ}")
        # 外交关系（2026-09-19 用户「间谍要包含外交关系」）：给的是**该国自己的视角**——
        # 它与各国的 交战/共同防御/保障/联盟成员/中立，加上它的联盟与战线。
        # 只给"关系"（谁跟谁是什么），不给"商议"（投票/求和来回/信件）——与公开口径一致。
        L.append("  外交关系（该国的视角）: " + self.rel_desc(n))
        bl = self.bloc_of(n)
        if bl is not None:
            L.append(f"    联盟「{bl['name']}」（盟主 {self.bloc_chief(bl)}）"
                     f"成员：{'、'.join(m for m in bl['members'] if m in self.nations)}")
        fronts = []
        for w in self.wars:
            sides = self._war_sides(w)
            if n in sides[0] or n in sides[1]:
                fls = list(w["followers"]) + list(w.get("atk_followers", []))
                fronts.append(f"{w['atk']}↔{w['def']}" + (f"(跟随 {'、'.join(fls)})" if fls else ""))
        L.append("    战线: " + ("；".join(fronts) if fronts else "无"))
        L.append(f"  全部地块建设情况（{len(self.own_tiles(n))} 块）:")
        for (x, y) in self.own_tiles(n):
            t = self.tiles[(x, y)]
            b = t["buildings"]
            p = t.get("pending") or {}
            built = " ".join(f"{bn}×{c}" for bn, c in b.items() if c) or "无"
            pend = " ".join(f"{bn}×{c}(在建)" for bn, c in p.items() if c)
            extra = ("；" + pend) if pend else ""
            L.append(f"    {t.get('name', '?')} {t['terrain']}({x + 1},{y + 1}) "
                     f"城L{b['城堡']} 位{sum(b.values())}/20 建筑[{built}{extra}]")
        # 粗略军情：只有各兵种数量——位置/血量/番号不外泄（间谍能探到敌国在扩军，但别想精准侦察）
        kinds: dict[str, int] = {}
        for a in self.nation_armies(n):
            k = unit_kind(a)
            kinds[k] = kinds.get(k, 0) + 1
        L.append("  军情（仅数量，位置未知）: " + ("、".join(f"{k}×{c}" for k, c in kinds.items()) or "无军队"))
        return "\n".join(L)


    def _apply_spy(self, frm: str, to: str) -> tuple[bool, str]:
        """派间谍刺探别国（花 SPY_COST 金），SPY_TURNS 回合后盗回其经济底细 +
        外交关系（该国视角：与谁 交战/共同防御/保障、在哪个联盟、哪条战线）+
        粗略军情（仅各兵种数量，位置/血量/番号不外泄）+ 整张已知地图。不能对自己用。"""
        if frm not in self.nations or to not in self.nations:
            return False, "间谍双方都必须是现存国家"
        if to == frm:
            return False, "不能派间谍刺探自己"
        if self.res(frm, "黄金") < SPY_COST:
            return False, f"国库不足：派间谍需 {SPY_COST} 金，你现 {self.res(frm, '黄金')}"
        self.add_res(frm, "黄金", -SPY_COST)
        self.spy_pending.append({"from": frm, "to": to, "arrive": self.turn + SPY_TURNS})
        return True, (f"已派间谍前往 {to}（-{SPY_COST}金），"
                      f"将于第 {self.turn + SPY_TURNS} 回合拿回其情报（经济底细+外交关系+粗略军情+地图）")


    def _next_offer_id(self) -> int:
        self._offer_id += 1
        return self._offer_id


    def _apply_propose_pact(self, kind: str, a: str, b: str) -> tuple[bool, str]:
        """提议『共同防御』。**签约方是外交实体**：a 在盟 → 先过联盟投票再对外发邀约；
        a 独立 → 直接发邀约。对方若是联盟，接受时同样要过它的联盟投票。"""
        if kind == "同盟":
            return False, "双边同盟已由多边联盟取代：用 bloc_found(name=联盟名, tos=[创始成员]) 发起结盟（需起名，全体创始成员同意）"
        if kind != "共同防御":
            return False, f"未知盟约类型：{kind}（可选：共同防御；联盟请用 bloc_found）"
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if not self.runtime.permits(self, b, "accept.treaties"):
            return False, f"{b} 是游牧政体，不接受盟约（别在它身上花外交费）"
        A, B = self.entity_of(a), self.entity_of(b)
        bad = self._pact_block(kind, A, B)
        if bad:
            return False, bad
        if self.entity_at_war(A):
            return False, f"战争期间不能缔结{kind}：{self._war_brief_ent(A)}（先议和）"
        if self.entity_at_war(B):
            return False, f"{self.entity_label(B)} 正在交战，战争期间不能与它缔结{kind}（先议和）"
        if any(p["kind"] == kind and {p.get("A"), p.get("B")} == {A, B} for p in self.proposals):
            return False, "该提议已在桌上"
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("缔约", bl["name"], a,
                               {"pact": kind, "A": A, "B": B, "offer": True})
            self.log(f"🗳 {a} 发起联盟投票（「{bl['name']}」）：向 {self.entity_label(B)} "
                     f"提议{kind}（投票#{v['id']}）", phase="外交", nation=a)
            return True, (f"已发起联盟投票（投票#{v['id']}）：通过后由 {self.entity_label(A)} "
                          f"向 {self.entity_label(B)} 提议{kind}；成员用 vote {v['id']} true/false 表态")
        self.proposals.append({"id": self._next_offer_id(), "kind": kind, "A": A, "B": B,
                               "asker": a, "turn": self.turn})
        return True, f"{self.entity_label(A)} 向 {self.entity_label(B)} 提议{kind}，等对方接受"


    def _apply_accept_pact(self, me: str, offer_id: int) -> tuple[bool, str]:
        """接受收到的邀约。对方若是**联盟**，接受本身也要过联盟投票（一盟一票制）。"""
        p = next((x for x in self.proposals if x["id"] == offer_id), None)
        if p is None:
            return False, "没有这个邀约"
        if p["kind"] == "联盟":
            if me not in p.get("invitees", []):
                return False, "没有这个给你的邀约"
            return self._accept_bloc_founding(p, me)
        A, B = p.get("A"), p.get("B")
        if B is None or self.entity_of(me) != B:
            return False, "没有这个给你的邀约"
        if self.entity_of(p.get("asker", "")) != A:
            self.proposals.remove(p)
            return False, "提出方已变更外交实体（入盟/退盟），提议作废"
        kind = p["kind"]
        bad = self._pact_block(kind, A, B)
        if bad:
            self.proposals.remove(p)
            return False, bad
        if self.entity_at_war(A) or self.entity_at_war(B):
            self.proposals.remove(p)
            return False, "战争期间不能缔结盟约（提议作废，先议和）"
        if is_bloc_ent(B):
            if any(v["kind"] == "缔约" and v["payload"].get("offer_id") == p["id"]
                   for v in self.votes):
                return False, f"「{ent_key(B)}」已在表决这个邀约（投票进行中，用 vote 表态）"
            v = self._new_vote("缔约", ent_key(B), me,
                               {"pact": kind, "A": A, "B": B, "offer_id": p["id"]})
            self.log(f"🗳 {me} 把 {self.entity_label(A)} 的{kind}邀约提交「{ent_key(B)}」表决"
                     f"（投票#{v['id']}）", phase="外交", nation=me)
            return True, (f"已把邀约提交「{ent_key(B)}」表决（投票#{v['id']}）：赞成 > 反对"
                          f"即通过与 {self.entity_label(A)} 缔约")
        self.proposals.remove(p)
        return self._conclude_pact(kind, A, B)


    def _pact_parties(self, p: dict) -> list[str]:
        """一条缔约/结盟邀约的**当事人**：两边外交实体的全体现存成员（含无盟时的本国），
        结盟提议再带上各位创始成员。**"被拒了"必须让对面知道**——不广播（"怎么谈的"
        不公开），但对面一直在等这条邀约的结果（用户 2026-09-20 报的洞）。"""
        out: list[str] = []
        for ent in (p.get("A"), p.get("B")):
            for c in (self.entity_members(ent) if ent else []):
                if c not in out:
                    out.append(c)
        for c in [p.get("a")] + list(p.get("invitees", [])):
            if c and c in self.nations and c not in out:
                out.append(c)
        return out


    def _apply_reject_pact(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.proposals if x["id"] == offer_id), None)
        if p is None:
            return False, "没有这个邀约"
        if p["kind"] == "联盟":
            if me not in p.get("invitees", []):
                return False, "没有这个给你的邀约"
            self.proposals.remove(p)
            self.log(f"💔 {me} 拒绝了 {p['a']} 的结盟提议——「{p['name']}」创始流产（全体创始成员须一致同意）",
                     phase="外交", parties=self._pact_parties(p))
            return True, f"你拒绝了 {p['a']} 的结盟提议「{p['name']}」（创始流产）"
        A, B = p.get("A"), p.get("B")
        if B is None or self.entity_of(me) != B:
            return False, "没有这个邀约"
        if is_bloc_ent(B) and self.entity_chief(B) != me:
            self.proposals.remove(p)
            self.log(f"💔 {self.entity_label(B)}（盟主 {self.entity_chief(B)}）拒绝了 "
                     f"{self.entity_label(A)} 的{p['kind']}", phase="外交",
                     parties=self._pact_parties(p))
            return True, (f"盟主 {self.entity_chief(B)} 代表 {self.entity_label(B)} 拒绝了 "
                          f"{self.entity_label(A)} 的{p['kind']}")
        self.proposals.remove(p)
        self.log(f"💔 {self.entity_label(B)} 拒绝了 {self.entity_label(A)} 的{p['kind']}",
                 phase="外交", parties=self._pact_parties(p))
        return True, f"你拒绝了 {self.entity_label(A)} 的{p['kind']}"


    def _apply_break_pact(self, kind: str, a: str, b: str) -> tuple[bool, str]:
        """解除共同防御。在盟国家须联盟投票。

        ★ **战争期间一律不准解除**（2026-09-18 改）：条约在战时**冻结**——平时缔结不了、
        战时也解不掉。原先是"断约顺带退出该条约带来的战线"（`_pact_exit_wars`），
        那等于留了一条「打不过就背弃盟友跑路」的脱战通道；现在只有**议和**能停战。"""
        if kind == "同盟":
            return False, "双边同盟已由多边联盟取代（bloc_found 结盟 / bloc_leave 退盟）"
        if kind != "共同防御":
            return False, f"未知盟约类型：{kind}（可选：共同防御）"
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        A, B = self.entity_of(a), self.entity_of(b)
        if not self.has_pact("共同防御", A, B):
            return False, f"{self.entity_label(A)} 与 {self.entity_label(B)} 并无共同防御"
        if self.entity_at_war(A) or self.entity_at_war(B):
            who = A if self.entity_at_war(A) else B
            return False, (f"战争期间不能解除共同防御：{self._war_brief_ent(who)}"
                           "（**条约在战时冻结**，缔结与解除都不行——先议和停战）")
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("缔约", bl["name"], a,
                               {"pact": kind, "A": A, "B": B, "cancel": True})
            self.log(f"🗳 {a} 发起联盟投票（「{bl['name']}」）：解除与 {self.entity_label(B)} "
                     f"的共同防御（投票#{v['id']}）", phase="外交", nation=a)
            return True, (f"已发起联盟投票（投票#{v['id']}）：通过后解除与 "
                          f"{self.entity_label(B)} 的共同防御；成员用 vote {v['id']} true/false 表态")
        self._drop_pact(kind, A, B)
        exited = self._pact_exit_wars(self.entity_members(A), self.entity_members(B))
        extxt = f"，并退出 {self.entity_label(B)} 所在的 {len(exited)} 条战线" if exited else ""
        self.proclaim(f"💔 {self.entity_label(A)} 单方面解除与 {self.entity_label(B)} 的{kind}{extxt}"
                      f"（{'、'.join(self.entity_members(A))} 在他国境内的军队将全部撤出）")
        return True, (f"{self.entity_label(A)} 已解除与 {self.entity_label(B)} 的{kind}{extxt}。"
                      "如你在他国境内，会于回合末自动遣返回国")


    def _apply_propose_bloc(self, a: str, name: str, invitees: list[str]) -> tuple[bool, str]:
        """发起结盟：起名 + 邀全体创始成员，所有人接受才成立（全体成员同意）。
        发起方即盟主；战争期间不得缔结同盟（避免战时抱团脱战）。"""
        if a not in self.nations:
            return False, "发起方必须是现存国家"
        if self.at_war(a):
            return False, f"战争期间不能缔结同盟：{self._war_brief(a)}（先议和再谈结盟）"
        # ★ 休战（和约）**不拦**结盟（2026-10-07）：和约只拦"对休战对手宣战"，见 active_truce
        if self.bloc_of(a) is not None:
            return False, f"你已在联盟「{self.bloc_of(a)['name']}」中（一国同时只属一个联盟）"
        name = (name or "").strip()
        if not name or " " in name or len(name) > BLOC_NAME_MAX:
            return False, f"联盟名需为 1~{BLOC_NAME_MAX} 字、不含空格（name 参数）"
        if self.bloc_by_name(name) is not None:
            return False, f"联盟名「{name}」已被占用"
        inv = []
        for x in invitees:
            if x == a or x in inv:
                continue
            if x not in self.nations:
                return False, f"创始成员 {x} 不是现存国家"
            if self.bloc_of(x) is not None:
                return False, f"{x} 已在联盟「{self.bloc_of(x)['name']}」中"
            if not self.runtime.permits(self, x, "accept.bloc"):
                return False, f"{x} 是游牧政体，不参与结盟（邀它只会让提议悬空）"
            if self.at_war(x):
                return False, f"创始成员 {x} 正在交战，战争期间不能缔结同盟（先议和）"
            inv.append(x)
        if not inv:
            return False, "至少邀请一个创始成员（tos=[国名,…]）；单国无需结盟"
        for i in range(len([a] + inv)):
            for j in range(i + 1, len([a] + inv)):
                x, y = ([a] + inv)[i], ([a] + inv)[j]
                if self.war_between(x, y):
                    return False, f"创始成员 {x} 与 {y} 正在交战，不能结盟（先议和）"
        if any(p["kind"] == "联盟" and p["a"] == a and p["name"] == name for p in self.proposals):
            return False, "该结盟提议已在桌上"
        self.proposals.append({"id": self._next_offer_id(), "kind": "联盟", "a": a, "b": "",
                               "name": name, "invitees": inv, "turn": self.turn})
        self.log(f"🕊 {a} 发起结盟「{name}」：邀 {'、'.join(inv)} 为创始成员（全体同意才成立）",
                 phase="外交", nation=a)
        return True, (f"已发起结盟「{name}」：等 {'、'.join(inv)} 全部 respond_proposal 接受后成立；"
                      f"任一拒绝即流产")


    def _accept_bloc_founding(self, p: dict, me: str) -> tuple[bool, str]:
        """创始成员接受结盟提议；全体接受即立盟。"""
        p.setdefault("accepted", [])
        if me in p["accepted"]:
            return False, "你已接受过该提议"
        p["accepted"].append(me)
        self.log(f"🕊 {me} 接受加入联盟「{p['name']}」", phase="外交", nation=me)
        pending = [x for x in p["invitees"] if x not in p["accepted"]]
        if pending:
            return True, f"你已接受结盟「{p['name']}」：还差 {'、'.join(pending)} 同意"
        # 全体同意 → 立盟
        self.proposals.remove(p)
        members = [p["a"]] + list(p["invitees"])
        for x in members:
            if x not in self.nations or self.bloc_of(x) is not None:
                return False, f"立盟失败：{x} 已不在可入盟状态（提议作废）"
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if self.war_between(members[i], members[j]):
                    return False, f"立盟失败：{members[i]} 与 {members[j]} 已交战（提议作废）"
        self.blocs.append({"name": p["name"], "chief": p["a"], "members": members,
                           "turn": self.turn})
        # ★ 入盟即放弃个人条约：创始成员手里原有的保障/共同防御一律作废（它们本来就
        #   是"国家级"的，而入盟后这个国家不再是签约主体）。别想带着条约入伙。
        absorbed = self._absorb_personal_pacts(members)
        ab = f"（{'、'.join(absorbed)} 自动作废）" if absorbed else ""
        # ★ 和约随实体走（2026-10-11）：**本盟的对外和约＝盟主那一份**，成员自己的从这一刻
        #   起暂停（退盟/解散按原到期回合自动恢复）。表里那条一个字没动。
        sus = self._suspended_truces(members)
        sg = (f"；本盟的对外和约以**盟主 {p['a']}** 为准，成员自己的以下和约在盟内暂停"
              f"（退盟即恢复原到期回合）：{'、'.join(sus)}") if sus else \
             f"；本盟的对外和约以**盟主 {p['a']}** 为准（成员自己的和约在盟内暂停，退盟恢复）"
        self.proclaim(f"🕊 联盟「{p['name']}」成立！成员：{'、'.join(members)}（盟主 {p['a']}）{ab}{sg}")
        return True, (f"联盟「{p['name']}」成立！成员：{'、'.join(members)}，盟主 {p['a']}"
                      f"（你为创始成员）{ab}{sg}")


    def _absorb_personal_pacts(self, members: list[str]) -> list[str]:
        """入盟清账：把成员手里的**个人**条约全部作废（成员不再是签约主体）。
        返回人话描述，供日志与提示。"""
        out = []
        for m in members:
            out.extend(self.drop_pacts_of(ent_nation(m)))
        if out:
            self.proclaim(f"💔 入盟即放弃个人条约：{'、'.join(out)} 作废")
        return out


    def _apply_bloc_leave(self, a: str) -> tuple[bool, str]:
        """退盟：普通成员单方面退出，立即生效。**战争期间一律不准退**——盟员在战时被锁死
        （2026-09-18 外交改革：坚壁到底，不给"打不过就跑"的通道），直到整盟停战。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        if self.bloc_chief(bloc) == a:
            return False, ("你是盟主，不能退盟——请用 bloc_transfer(to=成员) 移交盟主之位，"
                           "或 bloc_dissolve 解散联盟")
        fighting = [m for m in bloc["members"] if self.at_war(m)]
        if fighting:
            return False, (f"战争期间不能退盟：{'、'.join(fighting)} 正在交战"
                           "（盟员在战时被锁死，直到整盟停战——先议和）")
        bloc["members"].remove(a)
        self.proclaim(f"💔 {a} 单方面退出联盟「{bloc['name']}」")
        if len(bloc["members"]) < 2:   # 只剩盟主一人 → 联盟自动解散
            chief = self.bloc_chief(bloc)
            self.blocs.remove(bloc)
            for _v in [v for v in self.votes if v["bloc"] == bloc["name"]]:
                self._cancel_vote(_v, "联盟解散")   # ★ 当事人（含盟外申请人）要收到
            self.proclaim(f"💔 联盟「{bloc['name']}」仅剩盟主 {chief}，自动解散")
            return True, f"你已退出「{bloc['name']}」——联盟只剩盟主，随之解散"
        return True, (f"你已单方面退出「{bloc['name']}」。"
                      "（滞留他国领土的军队将自回合末起自动遣返）")


    def _apply_bloc_transfer(self, a: str, to: str) -> tuple[bool, str]:
        """盟主移交：把盟主之位让给本联盟另一成员（只有现任盟主能调）。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        chief = self.bloc_chief(bloc)
        if chief != a:
            return False, f"只有盟主能移交（现任盟主是 {chief}）"
        if to == a:
            return False, "不能把盟主之位移交给自己"
        if to not in bloc["members"]:
            return False, f"{to} 不是本联盟成员（成员：{'、'.join(bloc['members'])}）"
        bloc["chief"] = to
        self.proclaim(f"👑 {a} 把联盟「{bloc['name']}」盟主之位移交给 {to}")
        return True, f"已把「{bloc['name']}」盟主之位移交给 {to}"


    def _apply_bloc_rename(self, a: str, new_name: str) -> tuple[bool, str]:
        """盟主给联盟改名（只有盟主能改）。命名规则与立盟一致：1~12 字、不含空格、全局唯一。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        chief = self.bloc_chief(bloc)
        name = (new_name or "").strip()
        if chief != a:
            return False, f"只有盟主能给联盟改名（现任盟主是 {chief}）"
        if not name or " " in name or len(name) > BLOC_NAME_MAX:
            return False, f"联盟名需为 1~{BLOC_NAME_MAX} 字、不含空格（name 参数）"
        if name == bloc["name"]:
            return False, f"你的联盟已经叫「{name}」了"
        if self.bloc_by_name(name) is not None:
            return False, f"联盟名「{name}」已被占用"
        old = bloc["name"]
        bloc["name"] = name
        self._rename_bloc_refs(old, name)
        self.proclaim(f"🏷 盟主 {a} 把联盟「{old}」改名为「{name}」")
        return True, f"联盟已改名：「{old}」→「{name}」"


    def _rename_bloc_refs(self, old: str, new: str) -> None:
        """联盟改名 → 把按**实体 id** 索引的引用整体同步（条约表），顺手同步按联盟名索引的
        投票。实体 id 里嵌着联盟名（"盟:<名>"），漏一处就会出现指向不存在实体的条约。"""
        for p in self.pacts:
            for k in ("a", "b"):
                if p[k] == ent_bloc(old):
                    p[k] = ent_bloc(new)
        for v in self.votes:        # 进行中的投票按联盟名索引，一并改掉，免得面板/日志对不上
            if v["bloc"] == old:
                v["bloc"] = new
        for collection in (self.diplomacy_queue, self.approved_votes, self.proposals, self.peace_offers):
            rename_pending_refs(collection, old, new)
        for k in list(self.truce):  # ★ 休战表也是按实体 id 存的（2026-10-11）：漏这条＝改名即失约
            if ent_bloc(old) in k:
                u = self.truce.pop(k)
                self.truce[_pair(*[ent_bloc(new) if x == ent_bloc(old) else x
                                   for x in sorted(k)])] = u


    def _apply_bloc_dissolve(self, a: str) -> tuple[bool, str]:
        """盟主解散联盟（只有盟主能调）。**战争期间一律不准解散**（盟员在战时被锁死），
        防止盟主用解散脱战坑盟友。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        chief = self.bloc_chief(bloc)
        if chief != a:
            return False, f"只有盟主能解散联盟（现任盟主是 {chief}）"
        fighting = [m for m in bloc["members"] if self.at_war(m)]
        if fighting:
            return False, (f"战争期间不能解散联盟：{'、'.join(fighting)} 正在交战"
                           "（坚壁到底——先议和停战再谈解散）")
        members = list(bloc["members"])
        self.blocs.remove(bloc)
        for _v in [v for v in self.votes if v["bloc"] == bloc["name"]]:
            self._cancel_vote(_v, "联盟解散")   # ★ 当事人（含盟外申请人）要收到
        self.proclaim(f"💔 盟主 {a} 解散联盟「{bloc['name']}」（原成员：{'、'.join(members)}）")
        return True, (f"已解散联盟「{bloc['name']}」：原成员 {'、'.join(members)} 恢复各自独立"
                      "（他们此后才是各自的外交实体）")


    def _apply_bloc_join(self, a: str, bloc_name: str) -> tuple[bool, str]:
        """申请入盟：联盟现成员投票（赞成 > 反对，无盟主否决），下一回合末计票后入盟。
        战争期间不能入盟（先议和）。"""
        if a not in self.nations:
            return False, "申请方必须是现存国家"
        if self.at_war(a):
            return False, f"战争期间不能缔结同盟：{self._war_brief(a)}（先议和再谈入盟）"
        bloc = self.bloc_by_name(bloc_name)
        if bloc is None:
            return False, f"联盟「{bloc_name}」不存在（可用面板查联盟列表）"
        if self.bloc_of(a) is not None:
            return False, f"你已在联盟「{self.bloc_of(a)['name']}」中（一国同时只属一个联盟）"
        if any(self.war_between(a, m) for m in bloc["members"]):
            return False, "你与该联盟成员正在交战，不能入盟（先议和）"
        v = self._new_vote("入盟", bloc["name"], a, {"candidate": a})
        self.log(f"🗳 {a} 申请加入联盟「{bloc_name}」（投票#{v['id']}，赞成>反对通过，盟主可否决）",
                 phase="外交", nation=a)
        return True, (f"已向「{bloc_name}」申请入盟（投票#{v['id']}）：成员用 "
                      f"vote {v['id']} yes/no/abstain 表态；赞成多于反对即通过（下一回合表决、回合末统一计票）")


    def _new_vote(self, kind: str, bloc: str, proposer: str, payload: dict) -> dict:
        self._vote_id += 1
        v = {"id": self._vote_id, "kind": kind, "bloc": bloc, "proposer": proposer,
             "payload": payload, "votes": {}, "turn": self.turn, "voting_turn": self.turn + 1}
        self.votes.append(v)
        return v


    def cast_vote(self, me: str, vote_id: int, choice) -> tuple[bool, str]:
        """仅登记投票；下一回合末统一计票，无盟主否决权。"""
        if self.phase != "行动":
            return False, "仅能在行动阶段投票"
        if not self.runtime.permits(self, me, "accept.treaties"):
            return False, "匈奴不搞这套外交"
        v = next((x for x in self.votes if x["id"] == vote_id), None)
        if v is None:
            return False, "没有这个投票"
        bloc = self.bloc_by_name(v["bloc"])
        if bloc is None or self.bloc_of(me) is not bloc:
            return False, "该投票不属于你所在的联盟"
        if self.turn != v["voting_turn"]:
            return False, f"仅第 {v['voting_turn']} 回合可表决"
        if v["kind"] == "入盟" and v["payload"].get("candidate") == me:
            return False, "申请人不投票"
        if choice is not None and type(choice) is not bool:
            return False, "投票需为赞成/反对/弃权"
        v["votes"][me] = choice
        self.state_version += 1
        label = {True: "赞成", False: "反对", None: "弃权"}[choice]
        self.log(f"投票#{vote_id} {me} 已提交{label}，本回合末计票", phase="投票", nation=me)
        return True, f"已提交投票#{vote_id}（{label}）；第 {v['voting_turn']} 回合末统一计票"


    @staticmethod
    def _vote_counts(v: dict, members: list[str]) -> tuple[int, int, int, int]:
        """(赞成, 反对, 弃权, 未投)。未投 = 到期即弃权。"""
        yes = sum(1 for m in members if v["votes"].get(m) is True)
        no = sum(1 for m in members if v["votes"].get(m) is False)
        abst = sum(1 for m in members if m in v["votes"] and v["votes"][m] is None)
        return yes, no, abst, len(members) - yes - no - abst


    def _pass_vote(self, v: dict, members: list[str], yes: int, no: int, abst: int) -> tuple[bool, str]:
        parties = self._vote_stakeholders(v)
        self.votes.remove(v)
        v["stakeholders"] = parties
        self.approved_votes.append(v)
        self.log(f"投票#{v['id']}（{v['kind']}）通过，待外交阶段执行", phase="计票", parties=parties)
        self.log(f"投票#{v['id']} 赞成{yes}/反对{no}/弃权{abst}", phase="计票", nation=v["proposer"])
        return True, "投票通过，待外交阶段执行"


    def _tally(self, v: dict) -> tuple[bool, str]:
        return True, f"投票#{v['id']} 第 {v['voting_turn']} 回合末统一计票"


    def _expire_votes(self) -> None:
        for v in list(self.votes):
            if self.turn < v["voting_turn"]:
                continue
            bloc = self.bloc_by_name(v["bloc"])
            if bloc is None:
                self._cancel_vote(v, "联盟已不存在")
                continue
            members = [m for m in bloc["members"] if m in self.nations]
            yes, no, abst, pending = self._vote_counts(v, members)
            if yes > no:
                self._pass_vote(v, members, yes, no, abst + pending)
            else:
                self._cancel_vote(v, "未通过（未投按弃权）")


    def _execute_vote(self, v: dict) -> tuple[bool, str]:
        """投票通过后的实际执行。"""
        pl = v["payload"]
        if v["kind"] == "宣战":
            bloc = self.bloc_by_name(v["bloc"])
            if bloc is None:
                return False, "联盟已散，宣战落空"
            target = pl.get("target")          # ★ 实体 id（不是国名）
            if not self.entity_members(ent_bloc(v["bloc"])) or not self.entity_members(target):
                return False, f"目标 {self.entity_label(target)} 已不在，宣战落空"
            return self._declare_war_internal(ent_bloc(v["bloc"]), target, v["proposer"])
        if v["kind"] == "缔约":
            return self._execute_pact_vote(v)
        if v["kind"] == "入盟":
            bloc = self.bloc_by_name(v["bloc"])
            cand = pl.get("candidate")
            if bloc is None or cand not in self.nations:
                return False, "入盟条件已变，申请落空"
            if self.bloc_of(cand) is not None or self.at_war(cand) or self.entity_at_war(ent_bloc(v["bloc"])):
                return False, f"{cand} 已入他盟/与成员交战，入盟落空"
            absorbed = self._absorb_personal_pacts([cand])
            bl = f"（入盟即放弃个人条约：{'、'.join(absorbed)} 作废）" if absorbed else ""
            sus = self._suspended_truces([cand])
            sg = (f"；{cand} 自己的和约在盟内暂停（退盟即恢复）：{'、'.join(sus)}") if sus else ""
            bloc["members"].append(cand)
            self.proclaim(f"🕊 {cand} 加入联盟「{bloc['name']}」{bl}{sg}")
            return True, f"{cand} 正式加入「{bloc['name']}」{bl}{sg}"
        if v["kind"] == "议和":
            if pl.get("type") == "offer":
                w = next((x for x in self.wars if x["id"] == pl.get("war_id")), None)
                if w is None:
                    return False, "战争已结束，议和投票落空"
                a_side = "atk" if v["proposer"] in self._war_sides(w)[0] else "def"
                if self._peace_rep(w, a_side) != v["proposer"]:
                    return False, "你已不是本方谈判代表，议和落空"
                return self._record_peace_offer(w, v["proposer"], pl["to"], pl["kind"],
                                                pl["gold"], pl.get("note", ""), pl.get("truce", 0))
            if pl.get("type") == "accept":
                p = next((x for x in self.peace_offers if x["id"] == pl.get("offer_id")), None)
                if p is None:
                    return False, "求和提议已不在，接受落空"
                return self._do_accept_peace(p)
        return False, f"未知投票类型 {v['kind']}"


    def _execute_pact_vote(self, v: dict) -> tuple[bool, str]:
        """联盟「缔约」投票通过后的执行：签保障 / 签共同防御 / 撤回保障 / 解除共同防御 /
        对外发出共同防御邀约。**条约的表决与执行都只走这条路径**——成员个人签不了。"""
        pl = v["payload"]
        kind, A, B = pl["pact"], pl["A"], pl["B"]
        if not self.entity_members(A):
            return False, f"{self.entity_label(A)} 已不存在，{kind}表决落空"
        if not self.entity_members(B):
            return False, f"{self.entity_label(B)} 已不存在，{kind}表决落空"
        if pl.get("cancel"):                       # 撤回保障 / 解除共同防御
            if self.entity_at_war(A) or self.entity_at_war(B):
                return False, "战争期间条约冻结：表决通过也失效了（先议和停战）"
            if self._drop_pact(kind, A, B):
                self.proclaim(f"💔 {self.entity_label(A)} 经联盟表决解除与 "
                              f"{self.entity_label(B)} 的{kind}")
                return True, f"{self.entity_label(A)} 已解除与 {self.entity_label(B)} 的{kind}"
            return False, f"条约已不在，解除{kind}落空"
        if self.entity_at_war(A) or self.entity_at_war(B):
            return False, "战时条约冻结，执行失败"
        if pl.get("offer"):                        # 我方实体对外提议共同防御
            p = {"id": self._next_offer_id(), "kind": kind, "A": A, "B": B,
                 "asker": v["proposer"], "turn": self.turn}
            self.proposals.append(p)
            self.log(f"🗳 {self.entity_label(A)} 经联盟表决，向 {self.entity_label(B)} "
                     f"提议{kind}（邀约#{p['id']}）", phase="外交")
            return True, (f"已以 {self.entity_label(A)} 的名义向 {self.entity_label(B)} 发出"
                          f"{kind}邀约（#{p['id']}），等对方回应")
        oid = pl.get("offer_id")                   # 对方邀约的表决通过 → 落条约
        if oid is not None:
            self.proposals = [x for x in self.proposals if x["id"] != oid]
        bad = self._pact_block(kind, A, B)
        if bad:
            return False, bad
        return self._conclude_pact(kind, A, B)


    def _snapshot_cores(self, participants: list[str]) -> None:
        """战争结束：参战各国（含跟随方）实际持有的地块重算为其核心领土（议和即对现状追认）。"""
        for p in set(participants):   # 序无关：每格只有一个 owner，写入键互斥（区别于战争闭包/弃城那两处需 sorted 的 set 迭代）
            if p not in self.nations:
                continue
            for (x, y) in self.own_tiles(p):
                self.tiles[(x, y)]["core"] = p


    def _same_front(self, a: str, b: str) -> bool:
        """a 与 b 是否处于同一场战争的同一侧（同战线，含跟随方）。"""
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if (a in atk and b in atk) or (a in dfs and b in dfs):
                return True
        return False


    def _return_core(self, x: int, y: int, by: str) -> str:
        """同战线盟友自动归还核心领土：by 刚占领 (x,y)，若此地是同联盟且同战线的
        盟友的核心，则立即归还盟友（驻军原地不动，盟国领土合法停留）。返回附加说明。"""
        t = self.tiles.get((x, y))
        core = t.get("core") if t else None
        if not core or core == by or core not in self.nations or core == t.get("owner"):
            return ""
        if self.bloc_of(by) is None or self.bloc_of(by) is not self.bloc_of(core):
            return ""
        if not self._same_front(by, core):
            return ""
        t["owner"] = core
        return f"——此乃盟友 {core} 的核心领土，已自动归还（同战线·联盟「{self.bloc_of(core)['name']}」）"


    def _apply_declare_guarantee(self, a: str, b: str) -> tuple[bool, str]:
        """a 宣布保障 b 独立。**签约方是外交实体**：a 在盟 → 由它的联盟出面，须联盟投票通过；
        a 独立 → 直接生效（它自己就是实体）。"""
        if a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        A, B = self.entity_of(a), self.entity_of(b)
        bad = self._pact_block("保障", A, B)
        if bad:
            return False, bad
        if self.entity_at_war(A):
            return False, f"战争期间不能提供保障独立：{self._war_brief_ent(A)}（先议和）"
        if self.entity_at_war(B):
            return False, f"{self.entity_label(B)} 正在交战，战争期间不能保障它（先议和）"
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("缔约", bl["name"], a, {"pact": "保障", "A": A, "B": B})
            self.log(f"🗳 {a} 发起联盟投票（「{bl['name']}」）：{self.entity_label(A)} 保障 "
                     f"{self.entity_label(B)} 独立（投票#{v['id']}）", phase="外交", nation=a)
            return True, (f"已发起联盟投票（投票#{v['id']}）：通过后由 {self.entity_label(A)} "
                          f"保障 {self.entity_label(B)} 独立；成员用 vote {v['id']} true/false 表态")
        return self._conclude_pact("保障", A, B)


    def _apply_cancel_guarantee(self, a: str, b: str) -> tuple[bool, str]:
        """撤回保障（方向敏感：只有保障方能撤）。在盟国家须联盟投票。"""
        if a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        A, B = self.entity_of(a), self.entity_of(b)
        if A == B:
            return False, "你与它同属一个实体，没有保障可撤"
        if not self.has_pact("保障", A, B):
            return False, f"{self.entity_label(A)} 并未保障 {self.entity_label(B)}"
        if self.entity_at_war(A) or self.entity_at_war(B):
            who = A if self.entity_at_war(A) else B
            return False, (f"战争期间不能撤回独立保障：{self._war_brief_ent(who)}"
                           "（**条约在战时冻结**，缔结与撤回都不行——先议和停战）")
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("缔约", bl["name"], a,
                               {"pact": "保障", "A": A, "B": B, "cancel": True})
            self.log(f"🗳 {a} 发起联盟投票（「{bl['name']}」）：撤回对 {self.entity_label(B)} 的保障"
                     f"（投票#{v['id']}）", phase="外交", nation=a)
            return True, (f"已发起联盟投票（投票#{v['id']}）：通过后撤回对 "
                          f"{self.entity_label(B)} 的独立保障")
        self._drop_pact("保障", A, B)
        self.proclaim(f"{self.entity_label(A)} 撤回对 {self.entity_label(B)} 的独立保障")
        return True, f"{self.entity_label(A)} 已撤回对 {self.entity_label(B)} 的保障"


    def _apply_declare_war(self, a: str, b: str) -> tuple[bool, str]:
        """宣战。**交战方是外交实体**：在盟国家不能擅自开战，调用即转为联盟宣战投票
        （多数决通过后全盟参战）；独立国家直接开战。

        守侧传导 = 保障 / 共同防御 / 联盟，**以实体为单位、只传一跳**（被宣战实体的直接
        保障国与直接共同防御伙伴参战，它们自己的盟友不再跟着上）；"无限跳"只指**联盟
        收成一个点**——打盟员 = 打联盟，全盟一起上。"""
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if self.war_between(a, b):
            return False, "你们已经在交战"
        t = self.active_truce(a, b)      # 到期的顺手清掉；同属一个实体 ⇒ 和约已被联盟合并
        if t is not None:
            return False, f"休战中：你与 {b} 约定休战至第 {t} 回合（还剩 {t - self.turn} 回合），不得再宣战"
        A, B = self.entity_of(a), self.entity_of(b)
        if A == B:
            return False, (f"{b} 与你同属 {self.entity_label(A)}，不能宣战"
                           "（盟内互不攻击是联盟自带效果）")
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("宣战", bl["name"], a, {"target": B})
            self.log(f"🗳 {a} 发起联盟宣战投票（「{bl['name']}」）：对 {self.entity_label(B)} "
                     f"宣战（投票#{v['id']}，多数决）", phase="外交", nation=a)
            return True, (f"已发起联盟宣战投票（投票#{v['id']}）：多数同意后全盟对 "
                          f"{self.entity_label(B)} 宣战；成员用 vote {v['id']} true/false 表态")
        return self._declare_war_internal(A, B, a)


    def _declare_war_internal(self, atk_ent: str, def_ent: str,
                              proposer: str) -> tuple[bool, str]:
        """实际开战。atk_ent / def_ent 都是**外交实体**（独立国家或联盟）；proposer=发起国名。

        守侧传导：从 def_ent 出发的 保障/共同防御，**一跳不级联**（援军的盟友不动）——
        联盟成员整体落在守侧（打成员=打联盟，这是"无限跳"的唯一含义）。
        ★ 战线条目本身仍记为**国家**（atk=进攻主导国=盟主、def=目标实体代表、
        followers=守侧跟随国），作战/结算/遣返/迷雾那一堆既有代码不必换口径。
        """
        members = self.entity_members(atk_ent)
        def_members = self.entity_members(def_ent)
        if not members:
            return False, f"{self.entity_label(atk_ent)} 已不存在，宣战落空"
        if not def_members:
            return False, f"目标 {self.entity_label(def_ent)} 已灭亡，宣战落空"
        b = self.entity_chief(def_ent) or def_members[0]
        # 休战检查：**实体对实体**（2026-10-11）——实体的和约＝**盟主**的和约，
        # 成员的私约在盟内暂停（`truce_holder`）。旧版逐国对逐国，于是**一个成员的和约
        # 能给整个联盟挡刀**（楚 与 韩 休战 ⇒ 韩 连对「周天下」宣战都发不出去）。
        t = self.active_truce(b, self.entity_chief(atk_ent) or members[0])
        if t is not None:
            return False, (f"休战中：{self.entity_label(atk_ent)} 与 {self.entity_label(def_ent)}"
                           f"约定休战至第 {t} 回合（{b} 与 "
                           f"{self.entity_chief(atk_ent) or members[0]} 签的那一份），不得开战")
        # ★ 过了这一关就是**真的开打**了 ⇒ 战前未通过的外交条约当场作废（用户 2026-10-11）。
        #   放这里是因为往下三条路（守侧并入 / 随攻并入 / 开新战线）都算"发生战争"。
        self._void_pending_treaties(
            f"{self.entity_label(atk_ent)} 对 {self.entity_label(def_ent)} 开战")
        # 并入现有战线（不开平行战争）：目标实体正在攻打我方实体的盟友/共同防御对象 → 守侧并入
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if any(d in atk for d in def_members) and any(
                    self._same_camp(m, d) for m in members for d in dfs):
                self._break_pacts_between(atk_ent, def_ent)
                added = [m for m in members if m not in dfs
                         and not any(self.war_between(m, x) for x in atk)
                         # 与这条战线的攻方有和约 ⇒ 不并进去打自己的休战对手
                         and not self._truce_blocks_war_join([m], atk)]
                w["followers"].extend(added)
                for m in added:
                    self.proclaim(f"⚔ {m} 对 {self.entity_label(def_ent)} 宣战：盟友正被它攻打，"
                                  "并入该战线当防守方跟随方（不开第二场战争；跟随方不能单独议和）")
                return True, (f"对 {self.entity_label(def_ent)} 宣战：并入既有战线当防守方"
                              f"（新增 {'、'.join(added) or '无'}；跟随方不能单独议和，"
                              "主导者议和则整条战线停战）")
        # 目标实体正被我方实体的盟友/共同防御对象攻打 → 随攻并入
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if any(d in dfs for d in def_members) and any(
                    self._same_camp(m, x) for m in members for x in atk):
                self._break_pacts_between(atk_ent, def_ent)
                added = [m for m in members if m not in atk
                         and not any(self.war_between(m, x) for x in dfs)
                         # 同上：与守方有和约 ⇒ 不并进去随攻
                         and not self._truce_blocks_war_join([m], dfs)]
                w.setdefault("atk_followers", []).extend(added)
                for m in added:
                    self.proclaim(f"⚔ {m} 对 {self.entity_label(def_ent)} 宣战：盟友正在攻打它，"
                                  "并入该战线随攻（不开第二场战争；跟随方不能单独议和）")
                return True, (f"对 {self.entity_label(def_ent)} 宣战：并入既有战线随攻"
                              f"（新增 {'、'.join(added) or '无'}）")
        # 两实体之间的保障/共同防御自动解除（不打自己人）
        self._break_pacts_between(atk_ent, def_ent)
        # 守侧传导（**一跳，不级联**）：只有 def_ent 的**直接**保障国与**直接**共同防御
        # 伙伴被拉进守侧；这些援军**自己身上的条约不再往下传**——A 保 B、B 与 C 共防时，
        # 打 B 只有 C 上，C 的其它盟友不动。（用户 2026-10-07 定案：「不无限跳，无限跳指
        # 联盟」。旧实现在这里放了个 `while stack` 做传递闭包，于是"魏被打→韩参战→韩的
        # 共防伙伴楚也参战"——楚与魏之间根本没有条约。条约的触发条件是**它被打**，
        # 不是**它被卷进来**。）
        # ★ 「无限跳」在本作里只剩一个含义：**联盟收成一个点**——打盟员 = 打联盟，
        #   全盟一起上（`entity_members(def_ent)` 一次取齐），联盟内部没有双边条约、
        #   因而也不存在"经由某个成员把盟外国家串成一串"的链条。
        def_side = {def_ent}
        skipped: list[str] = []          # 因"和约在身"没被拖进来的援军（事后要通知）
        cands = set(self.guarantors_of(def_ent)) | set(self.defense_partners_of(def_ent))
        # ★ 必须 sorted：循环体读**增长中的 def_members**（下面的 war_between 剪枝），
        #   集合迭代序受 PYTHONHASHSEED 影响 → 同 seed 换进程可能收编不同的跟随方，
        #   整条历史分叉——"同种子同结果"就毁在这一行裸迭代上。
        for c in sorted(cands):
            if c in def_side or c == atk_ent:
                continue
            cm = self.entity_members(c)
            if not cm:
                continue
            if any(self.war_between(m, x2) for m in cm for x2 in members):
                continue        # 已与攻方交战，不并入守侧
            if any(self.war_between(m, d) for m in cm for d in def_members):
                continue        # 已与守侧某员交战，不并入
            if self._truce_blocks_war_join(cm, members):
                # ★ **已有的保障/共同防御在休战期内不触发**（用户 2026-09-20：
                #   「有和约时，防御条约和独立保障应该无法执行」）——它正与攻方某国
                #   休战中，就不把它拖进这场战争：**和约优先于盟约**，否则盟友一开战
                #   和约当场作废（那"和约"就白签了）。
                skipped.append(c)
                continue
            def_side.add(c)
            def_members = def_members + cm
        followers = [c for c in def_members if c != b]
        # 和约拦下的援军：**通知当事人与守方**（"预期中的援军为什么没来"——
        # 不说就成了第三个"结果不告诉当事人"的洞）
        for c in skipped:
            self.log(f"⚠ {self.entity_label(c)} 的保障/共同防御义务**未触发**：它与攻方有和约在身"
                     f"（和约优先，未参战）", phase="外交",
                     parties=self.entity_members(c) + [b])
        # 防守义务优先：守侧成员与进攻侧实体的保障/共同防御自动解除；逐国通知
        for c in followers:
            self._break_pacts_between(self.entity_of(c), atk_ent)
            self.proclaim(f"⚔ {c} 因联盟/共同防御/保障义务被拖入守侧，自动参战打 "
                          f"{'、'.join(members)}（跟随方不能单独议和，主导者议和则整条战线停战）")
        leader = self.entity_chief(atk_ent) or members[0]
        self.wars.append({"id": self._next_war_id(), "atk": leader, "def": b,
                          "followers": followers,
                          "atk_followers": [m for m in members if m != leader],
                          "turn": self.turn})
        mtxt = "、".join(members)
        self.proclaim(f"⚔ {proposer} 发起：{mtxt}（{self.entity_label(atk_ent)}）对 "
                      f"{self.entity_label(def_ent)} 宣战！")
        if leader == proposer and len(members) == 1:
            self.proclaim(f"⚔ {proposer} 对 {b} 宣战！{b} 必须应战")
        jtxt = f"；守侧传导参战：{'、'.join(followers)}" if followers else ""
        return True, f"对 {self.entity_label(def_ent)} 宣战（进攻侧：{mtxt}）{jtxt}"


    def _peace_rep(self, w: dict, side: str) -> str:
        """某侧的谈判代表：主导者本人；主导者属联盟时 = 联盟主体（盟主）——
        议和必须由联盟主体身份出面，普通成员/跟随方不能谈。"""
        leader = w["atk"] if side == "atk" else w["def"]
        if leader in self.nations:
            bloc = self.bloc_of(leader)
            if bloc is not None:
                chief = self.bloc_chief(bloc)
                if chief in self.nations and chief != leader:
                    return chief
        return leader


    def _apply_offer_peace(self, a: str, b: str, kind: str, gold: int = 0, note: str = "",
                    truce: int = 0) -> tuple[bool, str]:
        if kind not in ("pay", "demand", "white"):
            return False, "kind 须为 pay(我方赔款) / demand(要求对方赔款) / white(白和)"
        if not isinstance(truce, int) or truce < 0:
            return False, "休战回合数须为非负整数（0=不休战，自行约定）"
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        w = self._war_of(a, b)
        if w is None:
            return False, "你们并不在交战"
        # 谈判代表：每侧=主导者；主导者有联盟时=其盟主（联盟主体身份）
        a_side = "atk" if a in self._war_sides(w)[0] else "def"
        b_side = "def" if a_side == "atk" else "atk"
        rep_a, rep_b = self._peace_rep(w, a_side), self._peace_rep(w, b_side)
        if a != rep_a:
            hint = f"（你方主体是盟主 {rep_a}，由其出面并经联盟投票）" if rep_a != w["atk" if a_side == "atk" else "def"] else ""
            return False, f"议和必须由本方谈判代表 {rep_a} 出面{hint}"
        if b != rep_b:
            return False, f"求和对象须为对方谈判代表 {rep_b}（主导者或其盟主）"
        if gold < 0 or (kind in ("pay", "demand") and gold == 0):
            if kind == "white":
                gold = 0
            else:
                return False, "赔款量需为正整数（white 则不带赔款）"
        bloc = self.bloc_of(a)
        # 判断"是否联盟主体出面"必须用权威 bloc_chief，不能用 members[0]——
        # bloc_transfer 后盟主已换人，members[0] 仍是创始旧盟主，会让新盟主绕过投票私签和平。
        if bloc is not None and self.bloc_chief(bloc) == a:
            # 联盟主体议和须先过联盟投票（多数决）
            v = self._new_vote("议和", bloc["name"], a,
                               {"type": "offer", "war_id": w["id"], "to": rep_b,
                                "kind": kind, "gold": gold, "note": note, "truce": truce})
            self.log(f"🗳 {a} 发起联盟议和投票（「{bloc['name']}」）：向 {rep_b} 求和（投票#{v['id']}，多数决）",
                     phase="外交", nation=a)
            return True, (f"已发起联盟议和投票（投票#{v['id']}）：多数同意后正式向 {rep_b} 提出；"
                          f"成员用 vote {v['id']} true/false 表态")
        return self._record_peace_offer(w, a, b, kind, gold, note, truce)


    def _record_peace_offer(self, w: dict, a: str, b: str, kind: str, gold: int,
                            note: str, truce: int) -> tuple[bool, str]:
        """记录求和提议（投票通过后或非联盟主体直接提出）。"""
        self.peace_offers.append({"id": self._next_offer_id(), "a": a, "b": b,
                                  "kind": kind, "gold": gold, "note": note, "turn": self.turn,
                                  "war_id": w["id"], "truce": truce})
        k = {"pay": f"{a} 愿赔 {gold} 金求和", "demand": f"{a} 要求 {b} 赔 {gold} 金",
             "white": "白和（不赔不索）"}[kind]
        if truce > 0:
            k += f"，约定休战 {truce} 回合"
        self.log(f"🕊 求和提议：{k}" + (f"——{note}" if note else ""), phase="外交", nation=a)
        return True, f"已向 {b} 提出：{k}，等 {b} 在下一回合回应（accept/reject 议和 id）"


    def _apply_accept_peace(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.peace_offers if x["id"] == offer_id and x["b"] == me), None)
        if p is None:
            return False, "没有这个给你的求和提议"
        bloc = self.bloc_of(me)
        # 同 offer_peace：认盟主用 bloc_chief，防 bloc_transfer 后新盟主绕过投票直接接受
        if bloc is not None and self.bloc_chief(bloc) == me:
            # 联盟主体接受议和须先过联盟投票（多数决）
            v = self._new_vote("议和", bloc["name"], me, {"type": "accept", "offer_id": offer_id})
            self.log(f"🗳 {me} 发起联盟议和投票（「{bloc['name']}」）：接受 {p['a']} 的求和（投票#{v['id']}）",
                     phase="外交", nation=me)
            return True, (f"已发起联盟议和投票（投票#{v['id']}）：多数同意后正式接受；"
                          f"成员用 vote {v['id']} true/false 表态")
        return self._do_accept_peace(p)


    def _do_accept_peace(self, p: dict) -> tuple[bool, str]:
        a, b, kind, gold = p["a"], p["b"], p["kind"], p["gold"]
        me = p["b"]
        w = self._war_of(a, b)
        if w is None:
            self.peace_offers.remove(p)
            return False, "你们已不在交战（提议作废）"
        if kind == "pay":
            if self.res(a, "黄金") < gold:
                return False, f"{a} 国库不足 {gold}，赔不起这个价"
            self.add_res(a, "黄金", -gold)
            self.add_res(b, "黄金", gold)
        elif kind == "demand":
            if self.res(b, "黄金") < gold:
                return False, f"你的国库不足 {gold}，付不起这笔赔款（可回提 pay 求和）"
            self.add_res(b, "黄金", -gold)
            self.add_res(a, "黄金", gold)
        self.peace_offers.remove(p)
        self.peace_offers = [x for x in self.peace_offers if x.get("war_id") != w["id"]]
        members = [w["atk"], w["def"]] + list(w["followers"]) + list(w.get("atk_followers", []))
        self.wars.remove(w)
        # 战争结束：参战各国实际持有的地块重算为核心领土（议和即对现状追认）
        self._snapshot_cores(members)
        # 自行约定休战期：主导者议和时定的 truce 覆盖整条战线（含跟随方）
        truce_n = int(p.get("truce") or 0)
        until = 0
        if truce_n > 0:
            until = self.turn + truce_n
            for x in [w["atk"]] + list(w.get("atk_followers", [])):
                for y in [w["def"]] + list(w["followers"]):
                    # ★ **只许延长、不许缩短**（用户 2026-10-11 报的洞：强制和平期间在途的
                    #   和平提议落地，会把「全天下强制休战」覆盖成它自己那条短的）。
                    #   2026-10-07 起"亡国 ⇒ 全天下强制休战"是防雪球的地基，谁都不该用
                    #   一纸短休战把它抹掉——那条路只能是 `max`（收在 `set_truce` 里）。
                    self.set_truce(x, y, until)
        self.runtime.object_call(self,'army.units','disengage',members)
        extra = {"pay": f"{a} 付 {b} {gold} 金", "demand": f"{b} 赔 {a} {gold} 金", "white": "白和"}[kind]
        fls = list(w["followers"]) + list(w.get("atk_followers", []))
        who = ("（含跟随方 " + "、".join(fls) + "）") if fls else ""
        truce_txt = f"，休战 {truce_n} 回合（至第 {until} 回合）" if truce_n > 0 else ""
        self.proclaim(f"🕊 {a} 与 {b} 议和停战{who}{truce_txt}（{extra}）")
        return True, (f"停战议成：{extra}{truce_txt}。整条战线已停{who}，各方军队解除交战；"
                      f"各方实际持有地块重算为核心领土；滞留在对方领土的军队将于回合末遣返")


    def _apply_reject_peace(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.peace_offers if x["id"] == offer_id and x["b"] == me), None)
        if p is None:
            return False, "没有这个求和提议"
        self.peace_offers.remove(p)
        # ★ 必须回到提议方（用户 2026-09-20：「外交拒绝不会回应到对应国家」）：
        #   原先这里**一条纪事都不写**，于是提议方既不知道被拒、也无法据"被拒"重估
        #   （面板里那条"你提的求和#N"下回合直接消失，等于无声无息）。拒绝**不广播**
        #   （"怎么谈的"不公开），只给桌上的两家。
        self.log(f"💔 {p['b']} 拒绝了 {p['a']} 的求和提议（战争继续）", phase="外交",
                 parties=[p["a"], p["b"]])
        return True, f"你拒绝 {p['a']} 的求和（战争继续）"


    def allied_between(self, a: str, b: str) -> bool:
        """a、b 是否同属一个外交实体（同一联盟的成员，或本来就是同一个国家）。"""
        return self.entity_of(a) == self.entity_of(b)


    def _war_sides(self, w: dict) -> tuple[list[str], list[str]]:
        """进攻方名单 / 防御方名单（含跟随方；atk_followers=随攻的进攻侧跟随方）。"""
        return ([w["atk"]] + list(w.get("atk_followers", [])),
                [w["def"]] + list(w["followers"]))


    def _war_of(self, a: str, b: str) -> dict | None:
        """a 与 b 处于对立双方的战争冲突；不在交战返回 None。"""
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if (a in atk and b in dfs) or (a in dfs and b in atk):
                return w
        return None


    def _next_war_id(self) -> int:
        self._war_id += 1
        return self._war_id


    def war_between(self, a: str, b: str) -> bool:
        return self._war_of(a, b) is not None


    def dp_between(self, a: str, b: str) -> bool:
        """两国**所在实体**之间是否有共同防御。"""
        return self.has_pact("共同防御", self.entity_of(a), self.entity_of(b))


    def guarantee_of(self, b: str) -> list[str]:
        """保障 b 所在实体的实体 id 列表（面板用 entity_label 转人话）。"""
        return self.guarantors_of(self.entity_of(b))


    def rel_desc(self, me: str) -> str:
        """本国视角的全员关系一览（实体口径：条约挂在实体上，不挂在国家上）。"""
        my = self.entity_of(me)
        out = []
        for n in self.alive():
            if n == me:
                continue
            them = self.entity_of(n)
            tags = []
            if them == my:
                tags.append(f"联盟成员（同属{self.entity_label(my)}）")
            if self.war_between(me, n):
                tags.append("交战")
            if self.has_pact("共同防御", my, them):
                tags.append("共同防御")
            if self.has_pact("保障", my, them):
                tags.append("本实体保障它")
            if self.has_pact("保障", them, my):
                tags.append("它保障本实体")
            out.append(f"{n}=" + ("、".join(tags) if tags else "中立"))
        return "  ".join(out) if out else "（只有你一个国了）"


    @queued
    def send_mail(self, frm: str, to: str, text: str) -> tuple[bool, str]:
        pass


    @queued
    def gift(self, frm: str, to: str, good: str, n: int) -> tuple[bool, str]:
        pass


    @queued
    def share_map(self, frm: str, to: str) -> tuple[bool, str]:
        pass


    @queued
    def spy(self, frm: str, to: str) -> tuple[bool, str]:
        pass


    @queued
    def propose_pact(self, kind: str, a: str, b: str) -> tuple[bool, str]:
        pass


    @queued
    def accept_pact(self, me: str, offer_id: int) -> tuple[bool, str]:
        pass


    @queued
    def reject_pact(self, me: str, offer_id: int) -> tuple[bool, str]:
        pass


    @queued
    def break_pact(self, kind: str, a: str, b: str) -> tuple[bool, str]:
        pass


    @queued
    def propose_bloc(self, a: str, name: str, invitees: list[str]) -> tuple[bool, str]:
        pass


    @queued
    def bloc_leave(self, a: str) -> tuple[bool, str]:
        pass


    @queued
    def bloc_transfer(self, a: str, to: str) -> tuple[bool, str]:
        pass


    @queued
    def bloc_rename(self, a: str, new_name: str) -> tuple[bool, str]:
        pass


    @queued
    def bloc_dissolve(self, a: str) -> tuple[bool, str]:
        pass


    @queued
    def bloc_join(self, a: str, bloc_name: str) -> tuple[bool, str]:
        pass


    @queued
    def declare_guarantee(self, a: str, b: str) -> tuple[bool, str]:
        pass


    @queued
    def cancel_guarantee(self, a: str, b: str) -> tuple[bool, str]:
        pass


    @queued
    def declare_war(self, a: str, b: str) -> tuple[bool, str]:
        pass


    @queued
    def offer_peace(self, a: str, b: str, kind: str, gold: int = 0, note: str = "",
                    truce: int = 0) -> tuple[bool, str]:
        pass


    @queued
    def accept_peace(self, me: str, offer_id: int) -> tuple[bool, str]:
        pass


    @queued
    def reject_peace(self, me: str, offer_id: int) -> tuple[bool, str]:
        pass


    def _command_fork(self):
        fork = copy.copy(self)
        fork.__dict__.update(copy.deepcopy({k: self.__dict__[k] for k in MUTABLE}))
        fork.history = []
        fork.__dict__["units"] = copy.deepcopy(self.units).bind_state(fork,"units")
        return fork


    def diplomacy_cost(self, actor, to=None, *, incoming=False):
        if to and self.allied_between(actor, to):
            return 0
        if incoming and to and self.nation_building_count(to, "外交中心"):
            return 0
        cost = DIPLO_COST
        for _ in range(self.nation_building_count(actor, "外交中心")):
            cost = max(DIPLO_CENTER_MIN_COST, cost // 2)
        return cost


    def _submission_cost(self, method, actor, params):
        if method in FREE:
            return 0
        if method == "spy":
            return SPY_COST
        if method == "send_mail":
            return letter_cost(params["text"], allied=self.allied_between(actor, params["to"]),
                diplo_centers=self.nation_building_count(actor, "外交中心"))
        target = params.get("b", params.get("to"))
        if method in ("accept_pact", "reject_pact"):
            offer = next((p for p in self.proposals if p["id"] == params["offer_id"]), {})
            target = offer.get("a", offer.get("asker"))
        if method == "propose_bloc":
            target = next((n for n in params["invitees"] if self.nation_building_count(n, "外交中心")), None)
        return self.diplomacy_cost(actor, target,
            incoming=method in ("propose_pact", "propose_bloc", "offer_peace"))


    def submit_diplomacy(self, method, params):
        if self.phase != "行动":
            return False, "失败：仅能在行动阶段提交国家间外交"
        actor = params[COMMANDS[method][1]]
        if actor not in self.nations:
            return False, "失败：行动者已亡国/不存在"
        if not self.runtime.permits(self, actor, "action." + method):
            return False, "失败：匈奴不搞这套外交"
        for command in self.diplomacy_queue:
            if command["actor"] == actor and command["method"] == method and command["params"] == params:
                return True, f"已提交（待生效）外交#{command['id']}；相同待执行请求不重复扣费"
        try:
            cost = self._submission_cost(method, actor, params)
            if cost and self.res(actor, "黄金") < cost:
                return False, f"失败：国库不足，需预留 {cost} 金" + ("（超字费不打折）" if method == "send_mail" else "")
            fork = self._command_fork()
            if method != "spy":
                fork.add_res(actor, "黄金", -cost)
            ok, why = getattr(fork, "_apply_" + method)(**params)
        except (ValueError, TypeError, KeyError) as exc:
            return False, f"失败：参数非法（{exc}）"
        if not ok:
            return False, "失败：" + why
        reserved = {"黄金": cost} if cost else {}
        if method == "gift":
            good, amount = params["good"], params["n"]
            reserved[good] = reserved.get(good, 0) + amount
        for good, amount in reserved.items():
            self.add_res(actor, good, -amount)
        self.command_id += 1
        targets = {k: self.entity_of(params[k]) for k in ("a", "b", "me", "frm", "to")
                   if params.get(k) in self.nations}
        command = {"id": self.command_id, "actor": actor, "method": method,
                   "params": copy.deepcopy(params), "turn": self.turn, "status": "已提交",
                   "reserved": reserved, "entities": targets}
        self.diplomacy_queue.append(command)
        self.state_version += 1
        self.log(f"外交#{command['id']} 已提交（待生效）：{method}", phase="提交", nation=actor)
        detail = f"；{len(params['text'])} 字，预留 {cost} 金" if method == "send_mail" else ""
        return True, f"已提交（待生效）外交#{command['id']}；本轮外交阶段处理，预留 {reserved or '无'}{detail}"


    def _finish_command(self, command, ok, msg, *, cancelled=False):
        command["status"] = "已生效" if ok else ("取消" if cancelled else "失败")
        command["result"] = msg
        command["resolved_turn"] = self.turn
        # 历史结果不继续持有活动预留；原值留作审计。
        self.command_results.append(copy.deepcopy(command))
        self.state_version += 1
        parties = [command["actor"]]
        for ent in command.get("entities", {}).values():
            parties.extend(self.entity_members(ent))
        self.log(f"外交#{command['id']} {command['status']}：{msg}", phase="外交", parties=sorted(set(parties)))


    def _refund_command(self, command):
        if command["actor"] in self.nations:
            for good, amount in command["reserved"].items():
                self.add_res(command["actor"], good, amount)


    def _stage_diplomacy(self, ctx):
        commands = sorted(self.diplomacy_queue, key=lambda c: (
            COMMANDS[c["method"]][0], c["actor"], c["id"]))
        self.diplomacy_queue = []  # 消费一次；后面的异常必须有失败结果。
        # 已通过议案也在这里生效，依类型排列，不在计票阶段偷跑。
        approved = self.approved_votes
        self.approved_votes = []
        tasks = [(COMMANDS[c["method"]][0], c["actor"], c["id"], "command", c) for c in commands]
        ranks = {"议和": 10, "入盟": 25, "缔约": 32, "宣战": 40}
        tasks += [(ranks[v["kind"]], v["proposer"], v["id"], "vote", v) for v in approved]
        tasks.sort(key=lambda t: t[:4])
        for task_index, (_, _, _, kind, item) in enumerate(tasks):
            if kind == "vote":
                fork = self._command_fork()
                try:
                    ok, msg = fork._execute_vote(item)
                except Exception as exc:
                    ok, msg = False, f"{type(exc).__name__}: {exc}"
                if ok:
                    self._commit_command_fork(fork)
                self.command_id += 1
                result = {"id": self.command_id, "actor": item["proposer"],
                    "method": "vote:" + item["kind"], "vote_id": item["id"],
                    "params": copy.deepcopy(item["payload"]), "entities": {},
                    "reserved": {}, "turn": item["turn"]}
                self._finish_command(result, ok, msg)
                self.log(f"投票#{item['id']} 执行{'已生效' if ok else '失败'}：{msg}",
                         phase="外交", parties=item["stakeholders"])
                continue
            command = item
            actor = command["actor"]
            changed = any(params_key in command["params"] and
                self.entity_of(command["params"][params_key]) != ent
                for params_key, ent in command["entities"].items())
            if actor not in self.nations or changed:
                self._refund_command(command)
                self._finish_command(command, False, "当事人不存在或外交实体已变；预留退回存活发送方", cancelled=True)
                continue
            fork = self._command_fork()
            # 把锁定的赠送物资交给原动作，费用仍锁定；间谍动作自行扣费。
            if command["method"] == "gift":
                fork.add_res(actor, command["params"]["good"], command["params"]["n"])
            if command["method"] == "spy":
                fork.add_res(actor, "黄金", command["reserved"].get("黄金", 0))
            try:
                ok, msg = getattr(fork, "_apply_" + command["method"])(**command["params"])
            except Exception as exc:
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            if ok:
                for pending_name in ('mail_pending', 'gift_pending', 'map_pending', 'spy_pending'):
                    old = len(getattr(self, pending_name))
                    for task in getattr(fork, pending_name)[old:]:
                        task['command_id'] = command['id']
                        task['sent_turn'] = self.turn
                self._commit_command_fork(fork)
                if command['method'] == 'bloc_rename':
                    old = command['entities']['a'].removeprefix('盟:')
                    new = command['params']['new_name']
                    for remaining in tasks[task_index+1:]:
                        rename_pending_refs(remaining[4], old, new)
            else:
                self._refund_command(command)
            self._finish_command(command, ok, msg)


    def _commit_command_fork(self, fork):
        for key in MUTABLE:
            self.__dict__[key] = fork.__dict__[key]
        self.history.extend(fork.history)
        self.runtime.object_call(self,"army.units","restore",fork.units.to_save())


    def _delivery_status(self, task, status, message):
        for command in self.command_results:
            if command['id'] == task.get('command_id'):
                command['delivery'] = {'status': status, 'turn': self.turn, 'result': message}
                break
        self.log(message, phase='投递', parties=[task['from'], task['to']])


    def _stage_tally(self, ctx):
        self._expire_votes()


    def initialize_diplomacy(self, method, *args, **kwargs):
        """剧本初始化专用；回合零且未开始行动时允许预置关系。"""
        if self.turn != 0 or self.phase != "行动" or self.diplomacy_queue:
            raise ValueError("初始化外交只能用于未行动的回合零")
        if method not in COMMANDS:
            raise ValueError("未知初始化动作")
        return getattr(self, "_apply_" + method)(*args, **kwargs)
