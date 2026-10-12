from __future__ import annotations
from ..core import *



class BuildingsLogic:
    @staticmethod
    def _eff(t: dict) -> dict:
        """有效建筑数 = 已建成 + 在建（用于占位/上限判断）。"""
        b = t["buildings"]
        p = t.get("pending", {})
        return {k: b.get(k, 0) + p.get(k, 0) for k in b}


    def build(self, name: str, x: int, y: int, building: str) -> tuple[bool, str]:
        """下单建造。造价即扣，但建筑先进入『在建』，回合结算时才落地，下回合起生效。"""
        t = self.tiles.get((x, y))
        if name not in self.nations:
            return False, f"国家 {name} 不存在"
        if t is None or t["owner"] != name:
            return False, "只能在自己的地块上建造"
        if building not in BUILDINGS:
            return False, f"未知建筑：{building}"
        info = BUILDINGS[building]
        b = t["buildings"]
        eff = self._eff(t)
        if t["built_this_turn"]:
            return False, "该地块本回合已下过单（每地块每回合限建 1 座）"
        if sum(eff.values()) >= MAX_SLOTS:
            return False, f"建筑位已满（{MAX_SLOTS} 格，含在建）"
        cr = info["cap_resource"]
        if cr is not None:
            have = t["resources"][cr]
            if have <= 0:
                return False, f"本地无{cr}，无法建{building}"
            if eff[building] >= have:
                return False, f"{building} 已达上限：本地 {cr}={have}"
        if info["kind"] == "castle" and eff[building] >= info["max_level"]:
            return False, f"{building} 已达上限 L{info['max_level']}"
        # 特殊约束（市政厅等）：需该地块已用建筑位达标（含在建）/ 每地块限座
        used_eff = sum(eff.values())
        if info.get("min_slots") and used_eff < info["min_slots"]:
            return False, f"{building} 需该地块已用建筑位 ≥{info['min_slots']}（现 {used_eff}），先建满再盖"
        if info.get("limit") and eff[building] >= info["limit"]:
            return False, f"{building} 已达上限（每地块 {info['limit']} 座）"
        if info.get("limit_nation") and self.diplo_built.get(name, 0) >= info["limit_nation"]:
            return False, f"{building} 自建全国限 {info['limit_nation']} 座（已自建过；再要只能从别国手里抢）"
        lv = eff[building]
        cost = info["cost"][lv] if info["kind"] == "castle" else info["cost"]
        label = f"城堡L{lv+1}" if info["kind"] == "castle" else building
        bp = TERRAIN_STATS[t["terrain"]]["build_penalty"]   # 地形施工惩罚（只上浮金价，木材不变）
        if bp:
            cost = cost * (100 + bp) // 100
        disc = ""
        if t["buildings"].get("工程院") and building != "工程院":
            _disc = building_effect("工程院", "build_discount")   # 只认**已落成**的（本格有它）
            cost = cost * (100 - _disc) // 100
            disc = f"，工程院-{_disc}%"
        cost = cost * self.runtime.parameter(self, name, "build_cost_pct", 100) // 100
        wood = info["wood"]
        if self.res(name, "黄金") < cost:
            return False, f"黄金不足：{label} 需 {cost}，国库 {self.res(name,'黄金')}"
        if self.res(name, "木头") < wood:
            return False, f"木材不足：{label} 需 {wood}，储备 {self.res(name,'木头')}"
        self.add_res(name, "黄金", -cost)
        self.add_res(name, "木头", -wood)
        self.flow_out["木头"] += wood   # 世界流量：大兴土木推高木价
        led = self._ledger(name)        # 经济报表：投资=实付金 + 木×当时市价（含地形/工程院/匈奴修正后的真价）
        led["invest_gold"] += cost
        led["invest_wood_value"] += self._mval("木头", wood)
        self._spend(name)["build"] += cost + self._mval("木头", wood)   # 总消费：建造
        t["pending"][building] += 1  # 在建，回合末才落地
        t["built_this_turn"] = 1
        if building == "外交中心":
            self.diplo_built[name] = self.diplo_built.get(name, 0) + 1  # 自建名额记账（抢来的不占）
        tile_name = t["name"]
        note = f"，{t['terrain']}施工+{bp}%" if bp else ""
        return True, f"动工 {label}（@{tile_name}{note}{disc}，本回合在建、下回合生效），-{cost}金 -{wood}木"


    def _stage_gather(self, ctx):
        runrate_from = self._ledger_runrate_mark()
        # 0) 刷新每地块每回合配额
        for t in self.tiles.values():
            t["recruited_this_turn"] = 0
            t["built_this_turn"] = 0
            t["militia_recruited_this_turn"] = 0

        # 1) 采集
        prod = {n: {k: 0 for k in ("粮食", "木头", "矿石", "石油", "装备", "补给")} for n in self.alive()}
        plants: dict[str, list] = {n: [] for n in self.alive()}
        factories: dict[str, list] = {n: [] for n in self.alive()}
        maint = {n: 0 for n in self.alive()}
        gold_in = {n: 0 for n in self.alive()}
        for (x, y), t in self.tiles.items():
            owner = t["owner"]
            if owner not in self.nations:
                continue
            b = t["buildings"]
            for bname, cnt in b.items():
                if cnt == 0:
                    continue
                info = BUILDINGS[bname]
                kind = info["kind"]
                if kind == "extract":
                    # 采集类：无投入，按 outputs 产出入储备。
                    # （军屯曾走这条分支屯田产粮，2026-09-18 起不产——它现在是纯民兵编制，
                    #   见 balance.py；这条分支只剩采集建筑。）
                    for g, amt in info["outputs"].items():
                        self.add_res(owner, g, amt * cnt)
                        prod[owner][g] += amt * cnt
                        self.flow_in[g] += amt * cnt   # 世界流量：产出
                        self._ledger(owner)["prod_value"] += self._mval(g, amt * cnt)
                elif kind == "gold":
                    gain = info["outputs"].get("黄金", 0) * cnt * MARKET["黄金"]
                    self.add_res(owner, "黄金", gain)
                    gold_in[owner] += gain
                    self._ledger(owner)["gold_in"] += gain
                elif kind == "energy":
                    plants[owner].append((bname, info, cnt))
                elif kind == "factory":
                    factories[owner].append((bname, info, cnt))
                    maint[owner] += cnt
                elif kind in ("barracks", "townhall"):
                    maint[owner] += cnt

        ctx["runrate_from"] = runrate_from
        ctx["prod"] = prod
        ctx["plants"] = plants
        ctx["factories"] = factories
        ctx["maint"] = maint
        ctx["gold_in"] = gold_in


    def _stage_industry(self, ctx):
        prod = ctx["prod"]
        plants = ctx["plants"]
        factories = ctx["factories"]
        maint = ctx["maint"]
        gold_in = ctx["gold_in"]
        # 2) 电网 + 工厂
        for n in self.alive():
            et = 0
            for bname, info, cnt in plants[n]:
                fuel = info["fuel"]
                batches = cnt
                for f, need in fuel.items():
                    batches = min(batches, self.res(n, f) // need)
                for f, need in fuel.items():
                    self.add_res(n, f, -need * batches)
                    self.flow_out[f] += need * batches   # 世界流量：能源厂烧燃料
                    self._ledger(n)["fuel_value"] += self._mval(f, need * batches)
                et += batches * info["energy_out"]
            short = et < maint[n]
            self.grid_short[n] = short
            self.energy_report[n] = (et, maint[n], short)
            if not short:
                for bname, info, cnt in factories[n]:
                    batches = cnt
                    for f, need in info["inputs"].items():
                        batches = min(batches, self.res(n, f) // need)
                    if batches == 0:
                        continue
                    for f, need in info["inputs"].items():
                        self.add_res(n, f, -need * batches)
                        self.flow_out[f] += need * batches   # 世界流量：工厂投料
                        self._ledger(n)["mid_value"] += self._mval(f, need * batches)
                    for g, amt in info["outputs"].items():
                        self.add_res(n, g, amt * batches)
                        prod[n][g] += amt * batches
                        self.flow_in[g] += amt * batches    # 世界流量：工厂产出
                        self._ledger(n)["prod_value"] += self._mval(g, amt * batches)
                # 市政厅：每座 = effects.gold_base + 该地块已占建筑位(不含自身)×effects.gold_per_slot 金；电网不足即停摆
                for (hx, hy), ht in self.tiles.items():
                    if ht["owner"] != n:
                        continue
                    h = ht["buildings"].get("市政厅", 0)
                    if h:
                        others = sum(ht["buildings"].values()) - h
                        hall_gain = (building_effect("市政厅", "gold_base")
                                     + others * building_effect("市政厅", "gold_per_slot")) * h
                        self.add_res(n, "黄金", hall_gain)
                        gold_in[n] += hall_gain
                        self._ledger(n)["gold_in"] += hall_gain

        ctx["prod"] = prod
        ctx["gold_in"] = gold_in


    def _stage_construction(self, ctx):
        # 6.5) 在建建筑落地（施工 1 回合）：本回合结算不产出，落地后从下回合开始生效
        for t in self.tiles.values():
            p = t.get("pending") or {}
            for k, c in p.items():
                if c:
                    t["buildings"][k] += c
            t["pending"] = {k: 0 for k in BUILDINGS}


    def industry_upkeep(self, name: str) -> dict[str, int]:
        """该国工业**每回合的额定投料** `{资源: 单位数}`——**只看不扣**（供面板做断料预警）。

        · **能源厂**：`fuel` × 座数（烧 木头 或 石油）；
        · **工厂**（补给厂 / 装备厂）：`inputs` × 座数。
        兵营与市政厅只吃电、不吃料，故不在内；城镇/城堡等也不吃料。

        ★ 口径是**额定需求**，不是"实际会扣掉多少"：实际扣料会被仓库存量截断
          （`resolve_turn` 里那两个 `batches = min(cnt, res//need)`），而这张表要回答的
          恰恰是"仓里够不够"——拿被截断后的结果做预警是循环论证（永远不报警）。
        ★ 与 `supply_shortfall` 同类：**派生量、不进存档**，随 `tiles` 现读现算。
        ★ 使用方（`玩家接口._res_line`）负责说清一件事：**电网停摆时工厂根本不投料**
          （`resolve_turn` 只在 `not short` 时才跑工厂），那时的实际投料只剩电厂的燃料。
        """
        need: dict[str, int] = {}
        for t in self.tiles.values():
            if t["owner"] != name:
                continue
            for bn, cnt in t["buildings"].items():
                if not cnt:
                    continue
                info = BUILDINGS.get(bn)
                if info is None:
                    continue
                for per_map in (info.get("fuel"), info.get("inputs")):
                    for f, per in (per_map or {}).items():
                        need[f] = need.get(f, 0) + per * cnt
        return need
