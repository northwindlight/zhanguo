from __future__ import annotations
from ..core import *



class EconomyLogic:
    def _stage_market(self, ctx):
        # 6) 市场：按本回合世界供需算均衡价，市价向均衡价回归
        self._update_market()


    def _stage_bank(self, ctx):
        # 6.8) 世界央行：储蓄结息 + 贷款计息/到期强制扣款（开关关着就是空转）
        self._bank_settle()


    def _stage_reports(self, ctx):
        prod = ctx["prod"]
        gold_in = ctx["gold_in"]
        famine = ctx["famine"]
        flat_lines = ctx["flat_lines"]
        runrate_from = ctx["runrate_from"]
        # 7) 各国结算摘要（供 agent 看）
        for n in self.alive():
            et, mt, short = self.energy_report.get(n, (0, 0, False))
            parts = [f"{k}+{v}" for k, v in prod[n].items() if v]
            if gold_in[n]:
                parts.append(f"金+{gold_in[n]}")
            fam = f"，⚠断粮缺{famine[n][0]}" if famine.get(n) else ""
            grid = "停摆" if short else f"电{et}/{mt}"
            armies = len(self.nation_armies(n))
            churn = self.churn_brief(n)        # 本回合的空转（同商品又买又卖）——没有就空串
            if churn:
                parts.append(churn)
            self.econ_summary[n] = (
                f"产出 {' '.join(parts) if parts else '无'} | 电网 {grid} | 军队 {armies} 支{fam} | "
                f"国库{self.res(n,'黄金')} 木{self.res(n,'木头')} 补给仓{self.res(n,'补给')}"
            )

        # 7.4) 每回合的 GDP run-rate（央行授信按"**当时**的 GDP"放款，见 World.bank_credit）。
        # 报表每 REPORT_EVERY 回合才结一期，太稀了；这里用与报表**同一条公式**每回合记一份。
        # ★ 必须赶在 7.5 之前算：报表结完会把本期账本清零，之后再来读增量就全是 0 了。
        flows = self._turn_flows(runrate_from)
        self.gdp_run = {n: round(self._flows_gdp(f), 1) for n, f in flows.items()}

        # 7.5) 经济报表：每 REPORT_EVERY 回合自动结一期（第 11/21/31… 回合开局可查）
        # 报表是派生数据（坏了不影响世界状态），所以这里兜底：出岔子只跳过本期并记进纪事，
        # 绝不把异常抛进 resolve_turn 拖垮整局（与「不静默」原则一致——日志里看得见）。
        if self.turn and self.turn % REPORT_EVERY == 0:
            try:
                self._close_report_period(runrate_from, flows)
            except Exception as e:
                self.log(f"⚠ 经济报表生成失败，本期跳过：{type(e).__name__}: {e}", phase="内政")

        # 8) 空转追踪**过回合**：把 `churn` 清空，下一回合重新从零累计。
        #    ★ 空转**不是在这里判的**——判在每一次 buy/sell 里（`_record_trade`：成交即比对，
        #      买不提示、随后那笔卖当场提示、再买又提示）。这里只是划"本回合"的界：
        #      不分回合的话，"上回合卖、这回合买"也会被算成倒手——而跨回合分批恰恰是**对的**做法。
        #    生命周期＝行动阶段（一次结算到下一次结算之间），与 flow_in/flow_out（结算内部
        #    累积、`_update_market` 里清）不是一回事。派生暂态、不进存档（存档永远落在结算后）。
        self.churn = {}
        ctx["result"] = {"war_lines": flat_lines, "famine": famine}


    _RUNRATE_FIELDS = ('prod_value', 'mid_value', 'fuel_value', 'gold_in', 'supply_eaten')


    def _ledger(self, n: str) -> dict:
        """取（或新建）该国本期经济账本。投资/贸易/资产从这里按整期出数；
        GDP/军费不读整期值——由 _close_report_period 取**结报回合的增量**
        （run-rate，见 _ledger_runrate_mark），账本怎么攒、首期缺几回合都不影响它们。"""
        led = self.ledger.get(n)
        if led is None:
            led = self.ledger[n] = {**{k: 0.0 for k in LEDGER_FIELDS},
                                    "_since": max(1, self.turn)}
        return led


    def _spend(self, n: str) -> dict:
        """取（或新建）该国总消费账（全期累计，不随报表清零）。"""
        d = self.spend.get(n)
        if d is None:
            d = self.spend[n] = {k: 0.0 for k in SPEND_FIELDS}
        return d


    def spend_total(self, n: str) -> float:
        """该国累计总消费（建造+征兵+军费）——RL 的回报就是这个数的终局值。"""
        return float(sum(self._spend(n).values()))


    def _mval(self, good: str, amt: int) -> float:
        """按当前市价把 amt 单位 good 折成金（黄金按 MARKET['黄金']）。"""
        if amt <= 0:
            return 0.0
        if good == "黄金":
            return amt * MARKET["黄金"]
        return amt * self.prices.get(good, float(MARKET.get(good, 0)))


    def nation_assets(self, n: str) -> float:
        """该国全部建筑的重置成本（造价金 + 木×现价；城堡按已升到的级数累计投入）。
        与 settlement.py 同口径，只是木头用现价——夺来的地同样计入（报表里会注明）。"""
        wp = self.prices.get("木头", float(MARKET["木头"]))
        total = 0.0
        for t in self.tiles.values():
            if t.get("owner") != n:
                continue
            for bname, cnt in t["buildings"].items():
                if not cnt:
                    continue
                info = BUILDINGS[bname]
                if info["kind"] == "castle":
                    total += sum(info["cost"][:cnt])          # 逐级累计，不是 cnt×单价
                else:
                    total += cnt * (info["cost"] + info["wood"] * wp)
        return total


    @staticmethod
    def _growth(cur: float, base: float | None) -> float | None:
        """环比增长率；无上期或上期非正 → None（报表里显示「—」）。"""
        if base is None or base <= 0:
            return None
        return (cur - base) / base


    def _ledger_runrate_mark(self) -> dict:
        """结算开头为每国抓一份 GDP/军费相关字段的快照，供本回合增量（_turn_flows）用。"""
        return {n: {k: self._ledger(n).get(k, 0.0) for k in self._RUNRATE_FIELDS}
                for n in self.alive()}


    def _turn_flows(self, runrate_from: dict) -> dict[str, dict[str, float]]:
        """**本回合**各国账本的增量（GDP/军费口径的五个字段）：{国: {字段: 增量}}。

        结算开头抓的 `runrate_from` 快照（生产/军耗发生之前）与此刻的账本逐字段相减，
        得到"这一回合实际发生了多少"——与报表口径同源：**报表每 REPORT_EVERY 回合用一次，
        央行授信每回合都要用一次**（`gdp_run`），所以抽出来共用，免得两处各写一条公式。
        """
        out = {}
        for n in self.alive():
            led = self._ledger(n)
            s0 = runrate_from.get(n) or {}
            out[n] = {k: led.get(k, 0.0) - s0.get(k, 0.0) for k in self._RUNRATE_FIELDS}
        return out


    @staticmethod
    def _flows_gdp(f: dict) -> float:
        """GDP = 本回合生产增加值（市价，**不含军费**）：采集/工厂产出 + 金矿与市政厅金
        − 中间投入 − 能源燃料。不除天数、不攒期累计——就是当前的产出速率（run-rate）。"""
        return (f.get("prod_value", 0.0) - f.get("mid_value", 0.0)
                - f.get("fuel_value", 0.0) + f.get("gold_in", 0.0))


    def _close_report_period(self, runrate_from: dict,
                             flows: dict | None = None) -> None:
        """把本期账本结成一期快照并清零账本。只在回合结算末尾调用——AI 无手动入口。
        runrate_from：本回合结算开头（生产/军耗发生之前）的账本快照；
        flows：本回合增量（`_turn_flows` 的结果）；不传就现算（口径完全一样，
        留着是为了让单独调用本函数的测试/工具不必先备好 flows）。
        GDP 与军费只反映**本回合**（报表所结算的那一回合）的实际增量。"""
        if flows is None:
            flows = self._turn_flows(runrate_from)
        for n in self.alive():
            led = self._ledger(n)
            start = int(led.get("_since", self.turn - REPORT_EVERY + 1))
            days = max(1, min(REPORT_EVERY, self.turn - start + 1))   # 覆盖回合数（仅供展示）
            f = flows.get(n) or {}
            # GDP = 本回合生产增加值（市价，不含军费）——不除天数、不攒期累计
            gdp = self._flows_gdp(f)
            # 军费 = 本回合军队实际吃掉的补给 × 现价（不看来源：自产/外购一视同仁）
            military = f.get("supply_eaten", 0.0) * self.prices.get("补给", float(MARKET["补给"]))
            invest = led["invest_gold"] + led["invest_wood_value"]      # 整期累计（不 run-rate）
            assets = self.nation_assets(n)
            supply_total = led["prod_value"] + led["import_gold"]
            trade = ((led["export_gold"] + led["import_gold"]) / supply_total
                     if supply_total > 0 else 0.0)
            # 上期快照：首期没有（prev={}）→ 各增长率取 None，报表显示「—」；
            # 用 .get 兜底，避免旧档/损坏数据里缺字段时把异常抛进回合结算
            prev = (self.econ_reports.get(n) or [{}])[-1]
            if not isinstance(prev, dict):
                prev = {}
            snap = {
                "period_end": self.turn, "report_turn": self.turn + 1,
                "period_start": start, "span": days,
                "gdp": round(gdp, 1), "gdp_growth": self._growth(gdp, prev.get("gdp")),
                "military": round(military, 1),
                "military_ratio": round(military / gdp, 4) if gdp > 0 else None,
                "invest": round(invest, 1),
                "invest_growth": self._growth(invest, prev.get("invest")),
                "assets": round(assets, 1),
                "assets_growth": self._growth(assets, prev.get("assets")),
                "export_gold": round(led["export_gold"], 1),
                "import_gold": round(led["import_gold"], 1),
                "supply_eaten": int(led["supply_eaten"]),           # 整期累计（供展示）
                "supply_eaten_turn": int(f.get("supply_eaten", 0.0)),  # 本回合吃掉（军费口径）
                "trade_ratio": round(trade, 4),
            }
            self.econ_reports.setdefault(n, []).append(snap)
            # 公告一条（看海终端可见；该国在【近讯】里也能看到 → 提醒它去 report 查）
            mr = snap["military_ratio"]
            self.log(f"📊 第 {snap['report_turn']} 回合经济报表已生成："
                     f"本回合 GDP {snap['gdp']:.0f} 金、军费占 GDP "
                     f"{f'{mr * 100:.0f}%' if mr is not None else '—'}、"
                     f"总资产 {snap['assets']:.0f}（report 看明细 / report all=true 看趋势）",
                     phase="内政", nation=n)
        self.ledger = {}


    @staticmethod
    def _remove_pair(lst: list, name: str):
        lst[:] = [p for p in lst if name not in p]


    def market_price(self, good: str) -> float:
        return round(self.prices[good], 1)


    def market_depth(self, good: str) -> int:
        """该商品的市场深度（单位数）：每卖光这么多单位，市价大约被压掉「基准价×PRICE_IMPACT」。
        深度 = MARKET_DEPTH[g] × max(1, 现存国家数) ÷ MARKET_DEPTH_NATIONS_DIV
        ——国家越多市场越深（N 国为基准档）。"""
        return max(1, round(MARKET_DEPTH[good] * max(1, len(self.alive())) / MARKET_DEPTH_NATIONS_DIV))


    def market_tick(self, good: str) -> float:
        """每单位推动（金/单位）：基准价 × PRICE_IMPACT ÷ 深度。"""
        return MARKET[good] * PRICE_IMPACT / self.market_depth(good)


    def price_floor(self, good: str) -> float:
        """该商品的**地板价** = max(基准价 × PRICE_MIN_RATIO, PRICE_MIN_ABS)。

        两级并存（2026-09-21）：倍率地板管"贵货跌到基准价的一成"，**绝对地板**管"便宜货
        也别跌穿 0.5 金"——粮食基准 2，一成只有 0.2，那已经比一份补给的口粮还便宜，
        面板与 AI 估价都会失去意义。两处夹价（`_clamp_price` 与回合末 `_update_market`）
        一律走这个函数，别各写各的。
        """
        return max(MARKET[good] * PRICE_MIN_RATIO, PRICE_MIN_ABS)


    def _clamp_price(self, good: str, p: float) -> float:
        base = MARKET[good]
        return min(max(p, self.price_floor(good)), base * PRICE_MAX_RATIO)


    def market_walk(self, good: str, n: int, side: str) -> tuple[float, float, float]:
        """沿价格曲线走 n 单位。返回 (成交单价含价差, 成交后中间价 p1, 总额)。
        成交单价 = 沿曲线均价 (p0+p1)/2 再加/减半个价差——不再整笔按 p1 结算。"""
        p0 = self._clamp_price(good, self.prices[good])
        tick = self.market_tick(good) * (1 if side == "buy" else -1)
        p1 = self._clamp_price(good, p0 + tick * n)
        if n <= 0: return p0, p0, 0.0
        # 先沿线性区间积分；触顶/触底后的剩余成交量保持边界价。
        linear = min(float(n), abs((p1-p0)/tick)) if tick else 0.0
        integral = linear * (p0+p1)/2 + (n-linear) * p1
        multiplier = 1 + MARKET_SPREAD/2 if side == 'buy' else 1 - MARKET_SPREAD/2
        total = integral * multiplier
        return total/n, p1, total


    def _settle_gold(self, name: str, exact: float, *, commit: bool = True) -> int:
        """把**精确**成交额（浮点）结算成**整数金币**：不足 1 金的零头存进余额，下次接着算。

        ★ 为什么要有它（2026-10-09 用户报的真实 bug：「市场截断，每次都舍入，多次单买和
          一次批量价格不同」）：原先 `buy`/`sell` 各写 `int(round(total))`，**每笔成交各舍一次**
          ⇒ 拆 n 笔就舍 n 次，而便宜货每笔都被**进位**：实测粮×100 一笔付 171、拆 100 笔付 200
          （**+17%**）；粮×10 是 16 对 20。玩家的钱被"拆单"偷走了。

        ⇒ 改成**攒零钱**：`Σ 各笔实付 = floor(Σ 各笔精确额)`——**与怎么拆单无关**。
          （价格曲线是线性的，拆单与批量的精确总额本来就逐分相等；见 `market_walk`。）

        ★ 口径必须是 `floor`（零头留在余额里），不是 `round`：只有 floor 可累加、
          与切分点无关；`round` 的半整数进位会随切分点漂移，那是同一个 bug 的另一种写法。
        ★ 余额恒 < 1 金，**进存档**（否则跨存档拆单又对不上）。
        """
        w = self.gold_carry.get(name, 0.0) + exact
        pay = int(math.floor(w + 1e-9))      # 1e-9：抹掉浮点累加的尾差，别让 0.9999999 少付 1
        if commit:
            carry = w - pay
            self.gold_carry[name] = 0.0 if carry < 1e-9 else carry
        return pay


    def market_quote(self, good: str, n: int, side: str,
                     name: str | None = None) -> tuple[float, int]:
        """试算：不实际成交，返回 (成交单价, 总额)。供面板显示「卖 N 实收多少」。

        `name` 给定时**把该国的小数余额算进去** ⇒ 面板报的总额与真成交**分毫不差**
        （不给只能按 floor 估，可能差 1 金——见 `_settle_gold`）。
        """
        unit, _p1, total = self.market_walk(good, n, side)
        if name is None:
            return unit, int(math.floor(total + 1e-9))
        return unit, self._settle_gold(name, total, commit=False)


    def buy(self, name: str, good: str, n: int) -> tuple[bool, str]:
        if good not in TRADEABLE:
            return False, f"「{good}」不可交易（可交易：{'、'.join(TRADEABLE)}）"
        if n <= 0:
            return False, "数量需为正整数"
        p0 = self.prices[good]
        unit, p1, total = self.market_walk(good, n, "buy")
        # ★ 先"只看不扣"地试算（要含该国的找零余额），**验过国库才落账**——
        #   买不起就不许动余额（见 `_settle_gold`）。
        cost = self._settle_gold(name, total, commit=False)
        if self.res(name, "黄金") < cost:
            return False, (f"黄金不足：买 {good}×{n}（均价 {unit:.2f}）需 {cost}，"
                           f"国库 {self.res(name,'黄金')}")
        self._settle_gold(name, total)          # 成交才落账（余额只在真成交时推进）
        self.add_res(name, "黄金", -cost)
        self.add_res(name, good, n)
        self.prices[good] = p1
        self._ledger(name)["import_gold"] += cost   # 经济报表：进口额（外贸占比用）
        return True, (f"购入 {good}×{n}（中间价 {p0:.2f}→{p1:.2f}，含价差均价 {unit:.2f}，"
                      f"实付 {cost}），余{self.res(name,good)}"
                      + self._record_trade(name, good, "buy", n, cost))


    def sell(self, name: str, good: str, n: int) -> tuple[bool, str]:
        if good not in TRADEABLE:
            return False, f"「{good}」不可交易"
        if n <= 0:
            return False, "数量需为正整数"
        if self.res(name, good) < n:
            return False, f"储备不足：{good} 现有 {self.res(name,good)}"
        p0 = self.prices[good]
        unit, p1, total = self.market_walk(good, n, "sell")
        gold = self._settle_gold(name, total)   # ★ 攒零钱：拆单与批量的总价逐分相同
        self.add_res(name, good, -n)
        self.add_res(name, "黄金", gold)
        self.prices[good] = p1
        self._ledger(name)["export_gold"] += gold   # 经济报表：出口额
        return True, (f"售出 {good}×{n}（中间价 {p0:.2f}→{p1:.2f}，含价差均价 {unit:.2f}，"
                      f"实收 {gold}），余{self.res(name,good)}"
                      + self._record_trade(name, good, "sell", n, gold))


    def churn_state(self, name: str, good: str) -> dict:
        """该国**本回合**在 good 上的累计成交（买/卖的数量与所付/所收金）。"""
        return self.churn.setdefault(name, {}).setdefault(
            good, {"buy_n": 0, "buy_gold": 0, "sell_n": 0, "sell_gold": 0})


    @staticmethod
    def churn_loss(t: dict) -> tuple[int, float]:
        """(空转量, 空转损益)：空转量 = min(买, 卖)——两边重叠的那部分才是**纯倒手**
        （多出来的那截是真实仓位变化，不算空转）；损益 = 空转量 ×(买均价 − 卖均价)，
        **正 = 亏**、负 = 竟然赚了（只有市价在你两笔之间被外人推动时才可能，如实报，不硬说成亏）。"""
        c = min(t["buy_n"], t["sell_n"])
        if c <= 0:
            return 0, 0.0
        return c, c * (t["buy_gold"] / t["buy_n"] - t["sell_gold"] / t["sell_n"])


    def churn_note(self, name: str, good: str) -> str:
        """空转提示（人话一句）。没发生空转就返回空串。"""
        t = self.churn_state(name, good)
        c, loss = self.churn_loss(t)
        if not c:
            return ""
        head = (f"⚠ 市场空转：本回合你在 {good} 上又买又卖"
                f"（买 {t['buy_n']} 均价 {t['buy_gold'] / t['buy_n']:.2f}、"
                f"卖 {t['sell_n']} 均价 {t['sell_gold'] / t['sell_n']:.2f}）"
                f"——重叠的 {c} 单位是**纯倒手**")
        if loss >= 0:
            tail = (f"，净亏 {int(round(loss))} 金（一进一出＝白付两趟买卖价差，外加自己推价的冲击）。"
                    "要买就直接买、要卖就直接卖，别拿市场当仓库周转。")
        else:
            tail = (f"，反倒赚了 {int(round(-loss))} 金（市价在你两笔之间被动过）——"
                    "别把它当生意：价差是照付的，长期必亏。")
        return head + tail


    def _record_trade(self, name: str, good: str, side: str, n: int, gold: int) -> str:
        """记一笔成交；若由此构成/延续空转，返回提示（前面带换行，直接拼进回执）。"""
        t = self.churn_state(name, good)
        t[f"{side}_n"] += n
        t[f"{side}_gold"] += gold
        note = self.churn_note(name, good)
        if note:
            self.log(note, phase="市场", nation=name)
            return "\n" + note
        return ""


    def churn_brief(self, name: str) -> str:
        """结算摘要里的一句本回合空转（没有就空串；多商品按损失从大到小，最多列两个）。"""
        rows = []
        for good, t in (self.churn.get(name) or {}).items():
            c, loss = self.churn_loss(t)
            if c:
                amt = (f"亏 {int(round(loss))} 金" if loss >= 0
                       else f"赚 {int(round(-loss))} 金")
                rows.append((loss, f"空转{good}×{c} {amt}"))
        if not rows:
            return ""
        rows.sort(key=lambda r: -r[0])
        return "⚠ " + "、".join(r[1] for r in rows[:2])


    def _update_market(self) -> None:
        """每回合末：按全世界本回合流量算供需均衡价，市价向均衡价回归（而非死盯基准价）。
        产多耗少 → 均衡价低（全世界都在产，自然便宜）；战时军需耗大 → 均衡价高。"""
        for g in TRADEABLE:
            base = MARKET[g]
            prod, use = self.flow_in.get(g, 0), self.flow_out.get(g, 0)
            if prod and use:
                gap = (use - prod) / (use + prod)
            elif prod or use:
                gap = -MARKET_GAP_ONE_SIDE if prod else MARKET_GAP_ONE_SIDE
            else:
                gap = 0.0
            eq = base * (1 + MARKET_SENS * gap)
            eq = min(max(eq, base * MARKET_EQ_MIN_RATIO), base * MARKET_EQ_MAX_RATIO)
            self.equilibrium[g] = round(eq, 3)
            p = self.prices[g]
            self.prices[g] = round(min(max(eq + (p - eq) * PRICE_REVERT, self.price_floor(g)),
                                       base * PRICE_MAX_RATIO), 3)
        self.flow_in = {g: 0 for g in TRADEABLE}
        self.flow_out = {g: 0 for g in TRADEABLE}


    def bank_on(self) -> bool:
        return bool(self.bank.get("on"))


    def bank_enable(self) -> bool:
        """[配置开关] 打开世界央行，返回"本次是否真的打开了"。

        ★ 口径（2026-09-19 用户）：「银行**只能在配置文件开、不能关**，**可以中途加**」
        ⇒ 单向：`on` 只允许 false → true。配置说 true 就在本局打开（续局中途开也行）；
        配置说 false **也不会**把已经开着的关掉——一局里边开边关，账目与贷款期限就对不上了。
        """
        if self.bank.get("on"):
            return False
        self.bank["on"] = True
        return True


    def bank_rate(self) -> float:
        """储蓄利率（观察者设，可为负）。"""
        return float(self.bank.get("rate", 0.0))


    def bank_loan_rate(self) -> float:
        """贷款利率 = 储蓄利率 + BANK_SPREAD（可为负 ⇒ 欠款每回合**缩水**）。"""
        return self.bank_rate() + BANK_SPREAD


    def nation_gdp(self, name: str) -> float:
        """该国**当前 GDP**（金/回合）= 上一回合结算记下的 run-rate（`gdp_run`）。

        口径与 `report` 报表里的 GDP 是**同一条公式**（见 `_flows_gdp`），只是每回合都记，
        不像报表那样每 REPORT_EVERY 回合才结一期——央行授信要的是"**当时**的 GDP"。

        旧档兜底：`gdp_run` 缺该国（v4 之前的档、或本回合还没结算过）→ 退回**最新一期
        经济报表的 GDP**；连报表都没有（开局头几回合）→ 0（央行还没看到这个国家的账）。
        """
        if name in self.gdp_run:
            return float(self.gdp_run[name])
        reps = self.econ_reports.get(name) or []
        if reps and isinstance(reps[-1], dict):
            return float(reps[-1].get("gdp", 0.0))
        return 0.0


    def bank_credit(self, name: str) -> int:
        """央行给该国的**授信额度**（金）= 当前 GDP × `BANK_LOAN_GDP_MULT`，向下取整。"""
        return max(0, int(self.nation_gdp(name) * BANK_LOAN_GDP_MULT))


    def bank_set_rate(self, rate: float) -> tuple[bool, str]:
        """[观察者] 设储蓄利率；**变了就全世界播报**。

        播报口径按用户原话：**上调＝抑制通货膨胀、下调＝减少紧缩**，带**变动幅度 + 现值**。
        越界会夹到 [BANK_RATE_MIN, BANK_RATE_MAX]，并在回执里说明夹过。
        """
        if not self.bank_on():
            return False, "本局没开世界央行（mp_config.json 的 world_bank=true 才生效）"
        r = float(rate)
        clamped = max(BANK_RATE_MIN, min(BANK_RATE_MAX, r))
        old = self.bank_rate()
        if abs(clamped - old) < 1e-9:
            return False, f"利率没变（还是 {old:+.1%}）"
        self.bank["rate"] = clamped
        d = abs(clamped - old)
        if clamped > old:
            msg = f"🏦 世界央行公告：为了抑制通货膨胀，上调了 {d:.1%} 利率（现为 {clamped:+.1%}）"
        else:
            msg = f"🏦 世界央行公告：为了减少紧缩，下调了 {d:.1%} 利率（现为 {clamped:+.1%}）"
        if abs(clamped - r) > 1e-9:
            msg += f"（{r:+.1%} 超出可设区间，已夹到 {clamped:+.1%}）"
        self.broadcast(msg)
        return True, msg


    def bank_loan(self, name: str) -> tuple[bool, str]:
        """向世界央行借一笔：现金即刻到账；**还清之前不能再借**（不叠加）。

        ★ 2026-09-22 授信改革（用户：「银行只能贷款当时 GDP×5 的金，不再要求设置回合，
        也不要求金额」→「**改成不能选贷款额和时间**」）⇒ **没有参数可选**，只有一种贷款：
        额度 = 当时 GDP × `BANK_LOAN_GDP_MULT`，期限 = `BANK_LOAN_TURNS`。
        额度只看借款当时的 GDP（上回合产出的 run-rate）——富国的信用上限自己会长，
        穷国想借满 1000 也借不到（这就是"按经济规模授信"的意思）。
        """
        if not self.bank_on():
            return False, "本局没开世界央行（mp_config.json 的 world_bank=true 才生效）"
        if name not in self.nations:
            return False, "借款方必须是现存国家"
        if name in self.bank["loans"]:
            ln = self.bank["loans"][name]
            return False, (f"你在第 {ln['taken_turn']} 回合借的那笔还没还清"
                           f"（到期应还 {ln['due']} 金、还剩 {ln['turns_left']} 回合）——"
                           "还清前不能再借")
        gdp = self.nation_gdp(name)
        credit = self.bank_credit(name)
        cap = (f"贷款额固定 = 你当前 GDP {gdp:.1f} 金/回合 × {BANK_LOAN_GDP_MULT} "
               f"= {credit} 金，期限固定 {BANK_LOAN_TURNS} 回合（都不能自选）")
        if credit <= 0:
            # 央行还没见过你的账：GDP 是"上一回合的产出"，开局没产出的国家就是 0 授信
            return False, (f"央行还没看到你的账：{cap}——GDP 记的是**上一回合的产出**（市价），"
                           "先去建设/扩张，产出过东西才谈得上授信")
        amount, turns = credit, BANK_LOAN_TURNS
        self.add_res(name, "黄金", amount)
        self.bank["loans"][name] = {"principal": amount, "due": amount, "turns_left": turns,
                                    "taken_turn": self.turn, "rate": self.bank_loan_rate()}
        self.log(f"🏦 {name} 向世界央行借款 {amount} 金（{turns} 回合后到期，"
                 f"利率 {self.bank_loan_rate():+.1%}；授信 {credit}）", phase="事件", nation=name)
        return True, (f"已到账 {amount} 金：{turns} 回合后到期，当前利率 "
                      f"{self.bank_loan_rate():+.1%}（= 储蓄 {self.bank_rate():+.1%} + "
                      f"{BANK_SPREAD:.0%}）；到期**强制扣款**，还清前不能再借"
                      f"（{cap}）")


    def bank_sell_report(self, frm: str, to: str, report_turn: int,
                         text: str) -> tuple[bool, str]:
        """[工具] 向世界央行买一份**别国已公布**的经济报表（`BUY_REPORT_COST` 金/次）。

        ★ 卖的是明细不是秘密：每期报表生成时全世界都收到过一行公告（GDP / 军费占 GDP /
        总资产），央行卖的是那张表的全文（投资、外贸、跨期增长…）。所以它跟 `spy` 不冲突——
        `spy` 偷的是**当下**的国库/储备/每块地的建设（100 金、3 回合后到手），
        这里买的是**定期公开的账**（20 金、当场到手）。

        `text` 由调用方（`玩家接口`）用同一套报表渲染器生成——引擎只认钱与库，不认排版。
        **买不到一律不收钱**（没开行 / 不是现存国家 / 买自己 / 国库不足）：扣款在全部校验之后。
        买到手进买家的 `econ_intel`（`query panel=spy` 可重看，与间谍情报同池、同配额）。
        """
        if not self.bank_on():
            return False, "本局没开世界央行（mp_config.json 的 world_bank=true 才生效）——没人在卖报表"
        if frm not in self.nations or to not in self.nations:
            return False, "买卖双方都必须是现存国家"
        if to == frm:
            return False, "买自己国家的报表不用花钱：report 工具直接看（免费）"
        if self.res(frm, "黄金") < BUY_REPORT_COST:
            return False, (f"国库不足：买报表需 {BUY_REPORT_COST} 金，"
                           f"你现 {self.res(frm, '黄金')}")
        self.add_res(frm, "黄金", -BUY_REPORT_COST)
        store = self.econ_intel.setdefault(frm, [])
        store.append({"from": to, "turn": self.turn,
                      "text": f"🏦【央行售出】{to} 第 {report_turn} 回合经济报表\n{text}"})
        del store[:-KEEP_SAVES]          # 与间谍情报同池、同配额（控体积）
        self.log(f"🏦 {frm} 花 {BUY_REPORT_COST} 金从世界央行买下 {to} 第 {report_turn} "
                 f"回合的经济报表（query panel=spy 可重看）", phase="事件", nation=frm)
        return True, (f"✅ 已买下 {to} 第 {report_turn} 回合的经济报表"
                      f"（-{BUY_REPORT_COST} 金，国库 {self.res(frm, '黄金')}）；"
                      f"这份表也进了你的情报库（query panel=spy）。全文如下：\n\n{text}")


    def _bank_settle(self) -> None:
        """每回合末的央行结算：储蓄结息 + 贷款计息/到期强制扣款（开关关着就空转）。"""
        if not self.bank_on():
            return
        r, lr = self.bank_rate(), self.bank_loan_rate()
        for n in self.alive():
            gold = self.res(n, "黄金")
            if r and gold > 0:
                d = int(gold * r)                     # 金是整数，截断取整
                if d < 0 and gold + d < 0:
                    d = -gold                         # ★ 储蓄**扣不到负**（用户口径）
                if d:
                    self.add_res(n, "黄金", d)
                    self.log(f"🏦 {n} 储蓄结息 {d:+d} 金（利率 {r:+.1%}，"
                             f"国库 {self.res(n, '黄金')}）", phase="事件", nation=n)
            ln = self.bank["loans"].get(n)
            if not ln:
                continue
            if lr:
                ln["due"] = max(0, int(round(ln["due"] * (1 + lr))))
            ln["turns_left"] -= 1
            if ln["turns_left"] <= 0:
                pay = ln["due"]
                self.add_res(n, "黄金", -pay)          # ★ 贷款**允许扣成负的**（用户口径）
                self.bank["loans"].pop(n, None)
                self.log(f"🏦 {n} 的央行贷款到期：强制扣款 {pay} 金"
                         f"（本金 {ln['principal']}、利率 {ln['rate']:+.1%}，"
                         f"国库 {self.res(n, '黄金')}）", phase="事件", nation=n)
            else:
                self.log(f"🏦 {n} 的央行贷款计息：应还 {ln['due']} 金、"
                         f"还剩 {ln['turns_left']} 回合（利率 {lr:+.1%}）",
                         phase="事件", nation=n)
