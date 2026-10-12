from __future__ import annotations
from ..core import *



class GovernmentLogic:
    def polity_rule(self, name: str, key: str, default):
        """取该国的政体修正项（数值全在 `balance.POLITY`；没有政体/没这一项 → `default`）。"""
        return self.runtime.parameter(self, name, key, default)


    def broadcast(self, text: str, phase: str = "事件") -> str:
        """**全世界都看得见**的公告（央行利率这类世界新闻用它）。

        与 `log` 的区别：`log` 按坐标做视野快照，**没有坐标的条目谁也看不到**；
        这里显式把 `seen` 写成全体现存国家，`events_for` 按快照放行。
        """
        entry = {"turn": self.turn, "phase": phase, "nation": None,
                 "x": None, "y": None, "text": text,
                 "seen": list(self.nations)}
        self.history.append(entry)
        return text


    def proclaim(self, text: str) -> str:
        """**国家行为**的世界公告：缔约 / 解除 / 宣战 / 议和 / 联盟成立与变更。

        ★ 口径（2026-09-19 用户：「**必须知情**」）：条约与战争是**公开行为**——第三方有权
        知道谁跟谁结盟、谁打谁、谁跟谁议和了（现实里这些本来就要公布，共同防御尤其如此：
        它本来就是冲着第三方设计的，第三方却被蒙在鼓里说不过去）。
        走 `broadcast`（`seen` = 全体现存国家，`phase="外交"`，进各国近讯的外交类），
        **不受视野过滤**——与央行利率公告同一条通道。

        与之相对，**商议过程**仍只给当事人（`log(nation=…)`）：联盟内部投票与计票、求和提议的
        来回、写信、情报。一句话：**「签了没有」公开，「怎么谈的」不公开。**
        """
        return self.broadcast(text, phase="外交")


    def add_usage(self, nation: str, stats: dict) -> None:
        """把一次调用的用量累计进 `token_usage[nation]`（**只为统计**，不参与结算）。

        口径与 `llm_provider` 的 stats 同键：`hit`/`miss` 是输入的两半（hit+miss=输入总量）、
        `out_tokens` 是输出、`reason_tokens` 是其中的思考。

        两条纪律（都是这个项目栽过的坑）：
        1. **只认 `nations` 里的国家**。键一旦让外部字符串长进来（模型名、临时 actor），
           存档契约断言就会在某个不相干的回合突然炸 —— 宁可丢掉这一笔记账。
        2. **估的与报的分开数**。`usage_reported` 为假时走的是本地估算，那部分必须能
           被分辨出来；否则汇总出来的"总用量"就是估数与真数的混合，拿它算钱会错得没边。
        """
        slot = self.token_usage.get(nation)
        if slot is None:
            return
        hit = int(stats.get("hit") or 0)
        miss = int(stats.get("miss") or 0)
        out = int(stats.get("out_tokens") or 0)
        slot["calls"] += 1
        slot["hit"] += hit
        slot["miss"] += miss
        slot["prompt"] += hit + miss
        slot["out"] += out
        slot["reason"] += int(stats.get("reason_tokens") or 0)
        if stats.get("usage_reported"):
            slot["real_calls"] += 1
        else:
            slot["est_calls"] += 1


    def usage_totals(self) -> dict:
        """把 `token_usage` 汇总成一行总账（放看海台用）。

        `estimated` 为真表示总数里掺了本地估算 —— 显示时要打 `≈`，别当真数看。
        """
        total = {"calls": 0, "prompt": 0, "hit": 0, "miss": 0, "out": 0,
                 "reason": 0, "est_calls": 0, "real_calls": 0}
        for slot in self.token_usage.values():
            for k in total:
                total[k] += int(slot.get(k) or 0)
        inp = total["hit"] + total["miss"]
        total["hit_rate"] = (total["hit"] / inp) if inp else None
        total["total"] = total["prompt"] + total["out"]
        total["estimated"] = bool(total["est_calls"])
        return total


    def log(self, text: str, phase: str = "事件", nation: str | None = None,
            x: int | None = None, y: int | None = None,
            parties: list[str] | None = None,
            lost_by: str | None = None) -> str:
        """写一条纪事。可见性见 `_stamp_seen`：
        `nation=` 只给那一家；坐标按视野快照；`parties=` **保证这些当事国看到**
        （对手方通知：被拒了、地被夺了——不广播，但对面必须知道）。

        `lost_by`：**地块易主时原属国**（`phase="领土"` 用）——给面板的
        【领土警报】按事实查（`nation`=得方 / `lost_by`=失方 / x,y=哪一格），
        **不解析文本**（文本是给人读的，改个措辞就把面板读崩了）。
        """
        entry = {
            "turn": self.turn, "phase": phase, "nation": nation,
            "x": x, "y": y, "text": text,
        }
        if lost_by is not None:
            entry["lost_by"] = lost_by
        self._stamp_seen(entry, parties)
        self.history.append(entry)
        return text


    def action(self, nation: str, tool: str, args: str, result: str, x=None, y=None):
        entry = {
            "turn": self.turn, "phase": "行动", "nation": nation,
            "x": x, "y": y,
            "text": f"{nation} ◇ {tool} {args} → {result}",
        }
        self._stamp_seen(entry)
        self.history.append(entry)


    def events_for(self, name: str, limit: int = 14) -> list[str]:
        """该国能看到的近期事件（自己相关，或发生在视野内）。信件走信箱，此处不重复。"""
        out = []
        for h in reversed(self.history):
            if h["phase"] == "信件":
                continue
            if h["nation"] == name:
                out.append(f"[第{h['turn']}回合] {h['text']}")
            elif "seen" in h:
                # ★ 有快照就**只认快照**（绝不退回现视野——否则夺地后旧战报会回溯显形）；
                #   无坐标的世界广播也走这条：broadcast 把 seen 写成全体现存国家。
                if name in h["seen"]:
                    out.append(f"[第{h['turn']}回合] {h['text']}")
            elif (h.get("x") is not None and h.get("y") is not None
                  and self.visible_to(name, h["x"], h["y"])):
                # ★ 按落盘时刻的视野快照过滤（_stamp_seen）；无快照的极少数条目退回现视野
                out.append(f"[第{h['turn']}回合] {h['text']}")
            if len(out) >= limit:
                break
        return list(reversed(out))


    def fresh_history(self) -> list[dict]:
        h = self.history[self.history_seen:]
        self.history_seen = len(self.history)
        return h


    def add_nation(self, name: str, polity: str = "", extra=None,
                   start: dict | None = None, summary=None) -> tuple[bool, str]:
        """看海中途加国：随机到距所有现有领地足够远的位置登场。polity 指定已加载的政体模块。
        extra=临时注入上下文(塞入正常 system_prompt，20回合后仅剩 summary 小结)；
        summary=20回合后常驻的小结(存进存档)；start=定制开局（如 {"骑":8,"黄金":2000,"补给":300}）。"""
        name = (name or "").strip()
        if not name:
            return False, "需要国名，如 add 匈奴 / add 秦"
        if name in self.nations:
            return False, f"国家 {name} 已存在"
        polity = (polity or "").strip()
        if polity and not self.runtime.find_policy(polity):
            return False, "所需政体模块未加载"
        margin = max(ARRIVE_MARGIN_MIN, self.size // ARRIVE_MARGIN_DIV)
        pos = None
        if self.tiles:
            for _ in range(ARRIVE_ATTEMPTS):
                x, y = self.rng.randrange(self.size), self.rng.randrange(self.size)
                if all(max(abs(x - px), abs(y - py)) >= margin for (px, py) in self.tiles):
                    pos = (x, y)
                    break
        else:
            pos = (self.size // 2, self.size // 2)
        if pos is None:
            pos = (self.rng.randrange(self.size), self.rng.randrange(self.size))
        self.nations[name] = Nation(name, None)
        self.order.append(name)
        self.mailbox[name] = []
        self.grid_short[name] = False
        self._assign_code(name)
        self._place_crosses({name: pos})
        if polity:
            self.apply_polity(name, polity, home=pos, start=start)
        if extra:
            self.extra_prompt[name] = {"text": str(extra), "until": self.turn + EXTRA_PROMPT_TURNS,
                                       "summary": str(summary or "")}
        desc = f"国家 {name} 登场（距各国至少 {margin} 格）"
        policy = self.runtime.actor_policy(self, name)
        if policy:
            desc = policy.appearance_description(self, name, start) + f"（距各国至少 {margin} 格）"
        self.log(desc, phase="事件", nation=name)
        return True, desc


    def cheat(self, name: str, **kw) -> tuple[bool, str]:
        """观海作弊补助：直接给某国加资源/骑兵（kw: 黄金/粮食/木头/矿石/石油/装备/补给/骑）。"""
        if name not in self.nations:
            return False, f"国家 {name} 不存在"
        r = self.nations[name].res
        parts = []
        home = self.own_tiles(name)[0] if self.own_tiles(name) else None
        for k, v in kw.items():
            v = int(v)
            if k == "骑" and v > 0 and home:
                for _ in range(v):
                    self.runtime.object_call(self,'army.units','spawn',name,'骑',*home)
                parts.append(f"骑+{v}")
            elif k in r:
                r[k] += v
                parts.append(f"{k}+{v}")
        if not parts:
            return False, "没补任何东西（可用 金/粮/木/矿/油/装/补/骑）"
        self.log(f"⚙ 观海补助 {name}：{'、'.join(parts)}", phase="事件")  # 观察者可见
        return True, f"已给 {name} 补助：{'、'.join(parts)}"


    def apply_polity(self, name, polity, home=None, start=None):
        if name not in self.nations: return
        return self.runtime.activate_actor(self, name, polity, home=home, start=start)


    def res(self, name: str, key: str) -> int:
        return self.nations[name].res.get(key, 0)


    def add_res(self, name: str, key: str, v: int):
        self.nations[name].res[key] += v


    def begin_turn(self) -> int:
        """开始下一回合：投递上一回合发出的信件。返回投递数。"""
        self.turn += 1
        self.phase = "行动"
        self.state_version += 1
        due = [m for m in self.mail_pending if m["arrive"] <= self.turn]
        self.mail_pending = [m for m in self.mail_pending if m["arrive"] > self.turn]
        for m in due:
            box = self.mailbox.get(m["to"])
            if box is not None:
                box.append({"from": m["from"], "text": m["text"], "turn": m["arrive"]})
        for m in due:
            self._delivery_status(m, "已送达" if m["to"] in self.nations else "失败",
                f"信件 {m['from']}→{m['to']}：" + ("已送达" if m["to"] in self.nations else "目标已亡国"))
            if m["to"] in self.nations:
                self.log(f"📮 {m['to']} 收到 {m['from']} 的信", phase="事件", nation=m["to"])
        # 在途馈赠：下回合到账；收方已亡国则退回
        due_g = [g for g in self.gift_pending if g["arrive"] <= self.turn]
        self.gift_pending = [g for g in self.gift_pending if g["arrive"] > self.turn]
        for g in due_g:
            if g["to"] not in self.nations:
                if g["from"] in self.nations:
                    self.add_res(g["from"], g["good"], g["n"])  # 退回
                    self._delivery_status(g, "已退回", f"馈赠目标已亡国；{g['good']}×{g['n']}已退回{g['from']}，发送费不退")
                else:
                    self._delivery_status(g, "失败", "馈赠双方皆亡国，物资无法交付或退回；任务失败")
                continue
            self.add_res(g["to"], g["good"], g["n"])
            self._delivery_status(g, "已送达", f"馈赠 {g['from']}→{g['to']} 已送达")
            self.log(f"🎁 {g['from']} 赠你的 {g['good']}×{g['n']} 到账", phase="事件", nation=g["to"])
        # 在途地图情报：下回合到账
        due_m = [m for m in self.map_pending if m["arrive"] <= self.turn]
        self.map_pending = [m for m in self.map_pending if m["arrive"] > self.turn]
        for m in due_m:
            if m["to"] not in self.nations:
                self._delivery_status(m, "失败", "地图收方已亡国，投递失败")
                continue
            self._delivery_status(m, "已送达", "地图已送达（发送时快照）")
            store = self.maps.setdefault(m["to"], [])
            store.append({"from": m["from"], "turn": m["arrive"], "text": m["text"]})
            del store[:-KEEP_MAPS]  # 只留最近 N 张图，控体积
            self.log(f"🗺 {m['to']} 收到 {m['from']} 的地图", phase="事件", nation=m["to"])
        # 间谍回报：3回合后盗回目标当前经济情报（含粗略军情数量）+ 地图；目标亡国则任务失败
        due_sp = [s for s in self.spy_pending if s["arrive"] <= self.turn]
        self.spy_pending = [s for s in self.spy_pending if s["arrive"] > self.turn]
        for s in due_sp:
            if s["from"] not in self.nations:
                self._delivery_status(s, "失败", "间谍派出国已亡国，任务失败")
                continue
            if s["to"] not in self.nations:
                self._delivery_status(s, "失败", "间谍目标已亡国，情报落空")
                self.log(f"🕵 {s['from']} 的间谍回报：目标 {s['to']} 已亡国，情报落空",
                         phase="事件", nation=s["from"])
                continue
            self._delivery_status(s, "已送达", "间谍已回报（到期回合采样）")
            store = self.econ_intel.setdefault(s["from"], [])
            store.append({"from": s["to"], "turn": s["arrive"], "text": self._econ_snapshot(s["to"])})
            del store[:-KEEP_SAVES]  # 只留最近 N 份，控体积
            # 间谍偷来的地图也进 intel（world.maps，与 share_map 同池，留最近 N 张）
            mstore = self.maps.setdefault(s["from"], [])
            mstore.append({"from": f"{s['to']}(间谍)", "turn": s["arrive"],
                           "text": self._map_snapshot(s["to"])})
            del mstore[:-KEEP_MAPS]
            self.log(f"🕵 {s['from']} 的间谍回报了 {s['to']} 的情报与地图",
                     phase="事件", nation=s["from"])
        return len(due)


    def save(self, path: str | Path) -> None:
        army_state = self.units.to_save()
        data = {
            "version": SAVE_VERSION,
            "mod_dependencies": self.runtime.dependency_manifest(),
            "mod_state": self.runtime.save_mod_state(self),
            "diplomacy_queue": self.diplomacy_queue,
            "command_results": self.command_results,
            "command_id": self.command_id,
            "approved_votes": self.approved_votes,
            "state_version": self.state_version,
            "phase": self.phase,
            "size": self.size, "seed": self.seed, "turn": self.turn,
            "rng_state": list(self.rng.getstate()),
            "nations": {n: nat.res for n, nat in self.nations.items()},
            "order": self.order,
            # ★ 按**插入序**（物化序）写，**不 sorted**：读回来之后 `self.tiles` 的遍历顺序
            #   才与"从没存过档"的进程一致。sorted 会让续跑进程按坐标序遍历，而
            #   `prod_value` 这类钱账是**逐格浮点累加**的 ⇒ 累加顺序一变就差 1 ULP
            #   （`test_resume_mid_game_equals_straight_run` 抓的就是这种病）。
            "tiles": {f"{x},{y}": t for (x, y), t in self.tiles.items()},
            "armies": army_state["armies"], "next_army_seq": army_state["next_army_seq"],
            "diplo_built": self.diplo_built,
            "nation_code": army_state["nation_code"],
            "guard_once": army_state["guard_once"],
            "wars": self.wars,
            "war_id": self._war_id,
            "truce": [[*sorted(pair), until] for pair, until in self.truce.items()],
            "pacts": self.pacts,
            "bank": self.bank,
            "blocs": self.blocs,
            "votes": self.votes,
            "vote_id": self._vote_id,

            "mail_pending": self.mail_pending,
            "mailbox": self.mailbox,
            "summaries": self.summaries,
            "summary_blocks": self.summary_blocks,
            "long_memory": self.long_memory,
            "turn_memory": self.turn_memory,
            "gift_pending": self.gift_pending,
            "map_pending": self.map_pending,
            "maps": self.maps,
            "spy_pending": self.spy_pending,
            "econ_intel": self.econ_intel,
            "plans": self.plans,
            "polity": self.polity,
            "extra_prompt": self.extra_prompt,
            "opening_guide": self.opening_guide,
            "peace_offers": self.peace_offers,
            "proposals": self.proposals,
            "offer_id": self._offer_id,
            "prices": self.prices,
            "equilibrium": self.equilibrium,
            "flow_in": self.flow_in,
            "flow_out": self.flow_out,
            "grid_short": self.grid_short,
            "gold_carry": self.gold_carry,
            "energy_report": self.energy_report,
            "econ_summary": self.econ_summary,
            "econ_reports": self.econ_reports,
            "ledger": self.ledger,
            "spend": self.spend,
            "gdp_run": self.gdp_run,
            "history": self.history,
            "history_seen": self.history_seen,
            # ★ 按 `USAGE_FIELDS` 补齐**每个国家**的格子（缺的补 0），而不是原样倒出
            #   `self.token_usage`：`gen=False` 的空壳世界、或建号后中途才补的国家，
            #   它可能只有一部分键甚至全空。load 侧是按国家重建的 ⇒ 不补齐就会出现
            #   "存的是 {}、读回来是 {秦:{...零...}}" 这种不对称，字节级往返立刻失败。
            "token_usage": {n: {k: int(self.token_usage.get(n, {}).get(k, 0) or 0)
                                for k in USAGE_FIELDS}
                            for n in self.nations},
        }
        # 契约断言：save 的键集合必须与 SAVE_KEYS 严丝合缝——忘了登记的新字段
        # 在第一次存档就炸（开发期），而不是变成静默丢档（运行时才发现=惨案）
        keys, want = set(data), set(SAVE_KEYS)
        if keys != want:
            raise RuntimeError(
                f"存档契约漂移：save 多出 {sorted(keys - want)}、少写 {sorted(want - keys)}"
                f"——新增状态字段请同步 SAVE_KEYS 清单与 load 搬运")
        # 原子写（tmp+rename）：turn_memory 使存档变大近一倍，避免写一半中断损坏档
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(copy.deepcopy(data), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


    @classmethod
    def load(cls, path: str | Path) -> "World":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        try:
            cls.runtime.check_save_dependencies(data.get("mod_dependencies"))
        except ValueError as exc:
            raise SaveFormatError(str(exc)) from exc
        ver = data.get("version")
        if ver is None:
            raise SaveFormatError(
                f"存档没有版本号（无版本号＝上古档，没法判断该按哪套语义读）：请用 --new 重开，"
                f"或删掉 {path}")
        if not (SAVE_MIN_VERSION <= int(ver) <= SAVE_VERSION):
            raise SaveFormatError(
                f"存档版本超出可读区间（档内 {ver}，本代码可读 {SAVE_MIN_VERSION}~{SAVE_VERSION}）："
                f"比代码新的档读不懂；比 {SAVE_MIN_VERSION} 老的档属于「删过字段」的那几版、"
                f"语义对不上。请用 --new 重开，或删掉 {path}")
        # ★ 追加式兼容：老档缺的**追加字段**按默认补齐（只加不删 ⇒ 老档永远开得起来）
        for k, dv in SAVE_DEFAULTS.items():
            if k not in data:
                data[k] = copy.deepcopy(dv)
        missing = [k for k in SAVE_KEYS if k not in data]
        if missing:
            raise SaveFormatError(
                f"存档缺字段：{missing}（这些不是已知的追加字段——文件被截断或改坏），请重开一局")
        w = cls(size=data["size"], seed=data["seed"], gen=False)   # 空壳：世界由存档整体还原（不预建默认三国，否则残出幽灵地块）
        w.turn = data["turn"]
        w.diplomacy_queue = data["diplomacy_queue"]
        try:
            pending_ids = [c['id'] for c in w.diplomacy_queue]
            results_ids = [c['id'] for c in data['command_results']]
            all_ids = pending_ids + results_ids
            if len(set(all_ids)) != len(all_ids) or any(type(i) is not int or i < 1 for i in all_ids):
                raise ValueError('请求编号重复或非法')
            if type(data['command_id']) is not int or data['command_id'] < max(all_ids, default=0):
                raise ValueError('请求计数器非法')
            for c in w.diplomacy_queue:
                if c['method'] not in cls.runtime.command_actors or c['status'] != '已提交':
                    raise ValueError('待执行动作非法')
                if c['params'].get(cls.runtime.command_actors[c['method']]) != c['actor']:
                    raise ValueError('动作发送方不一致')
                if any(g not in RES_KEYS or type(n) is not int or n < 0 for g, n in c['reserved'].items()):
                    raise ValueError('预留资源非法')
        except (ValueError, KeyError, TypeError) as exc:
            raise SaveFormatError(f'存档外交队列损坏，拒绝加载：{exc}') from exc
        w.command_results = data["command_results"]
        w.command_id = data["command_id"]
        w.approved_votes = data["approved_votes"]
        w.state_version = data["state_version"]
        w.phase = data["phase"]
        if w.phase not in ("行动", "已结算"):
            raise SaveFormatError("存档处于未完成或失败的结算阶段，拒绝加载")
        ver, internal, gauss = data["rng_state"]
        w.rng.setstate((ver, tuple(internal), gauss))
        # 以下全部按"版本已核对、键已核对齐"直读：只重建序列化丢掉的容器类型
        # （tuple/frozenset/set），不再有任何旧格式迁移分支。
        w.nations = {n: Nation(n, res) for n, res in data["nations"].items()}
        w.order = data["order"]
        w.mailbox = {n: data["mailbox"].get(n, []) for n in w.nations}
        # ★ 记忆 vs 在局状态的取舍**与 `_eliminate_if_dead` 逐条对齐**（两边不一致就会出现
        #   "存一次档亡国之君就失忆"这种只在重启后现形的 bug）：
        #   · **记忆**（summaries / summary_blocks / long_memory / polity）**含已亡国**——
        #     结算厅要请他们入场（2026-10-08 用户：「结算厅要结算所有国家」）。
        #   · **在局状态**（turn_memory 逐字缓冲区 / maps / econ_intel / plans / extra_prompt /
        #     mailbox）只留存活国——死了就不再行动，用不上，且 turn_memory 是存档里最大的一块。
        # summaries 条目恒为 {turn,text}（旧字符串格式随版本锁死一并退休）
        w.summaries = {n: [{"turn": int(it["turn"]), "text": str(it["text"])}
                           for it in lst] for n, lst in data["summaries"].items()}
        w.summary_blocks = {n: list(v) for n, v in data["summary_blocks"].items()}
        w.long_memory = {n: str(v) for n, v in data["long_memory"].items()}
        w.turn_memory = {n: list(v) for n, v in data["turn_memory"].items() if n in w.nations}
        w.gift_pending = data["gift_pending"]
        w.map_pending = data["map_pending"]
        w.maps = {n: list(v) for n, v in data["maps"].items() if n in w.nations}
        w.spy_pending = data["spy_pending"]
        w.econ_intel = {n: list(v) for n, v in data["econ_intel"].items() if n in w.nations}
        w.plans = {n: dict(v) for n, v in data["plans"].items() if n in w.nations}
        w.polity = dict(data["polity"])       # 身份：亡国之君的匈奴口吻也靠它（见上）
        w.extra_prompt = {n: dict(v) for n, v in data["extra_prompt"].items() if n in w.nations}
        w.opening_guide = bool(data["opening_guide"])
        try:
            w.runtime.object_call(w,'army.units','restore',data)
        except (ValueError, KeyError, TypeError) as exc:
            raise SaveFormatError(f'存档部队损坏，拒绝加载：{exc}') from exc
        w.diplo_built = {k: int(v) for k, v in data["diplo_built"].items() if k in w.nations}
        # 战争恒为冲突对象 {id,atk,def,followers,atk_followers,turn}（旧 [a,b] 边对已随版本退休）
        w.wars = []
        w._war_id = int(data["war_id"])
        for item in data["wars"]:
            w.wars.append({"id": int(item["id"]), "atk": item["atk"], "def": item["def"],
                           "followers": list(item.get("followers", [])),
                           "atk_followers": list(item.get("atk_followers", [])),
                           "turn": int(item["turn"])})
            w._war_id = max(w._war_id, int(item["id"]) + 1)
        # ★ 休战表 2026-10-11 起按**实体 id**存（与条约表同口径）；旧档写的是裸国名，
        #   读进来一律迁成 `国:名字`——不迁的话老档续局时**所有和约凭空消失**。
        w.truce = {_pair(_truce_key_id(ab[0]), _truce_key_id(ab[1])): int(ab[2])
                   for ab in data["truce"]}
        # 联盟：members 已过滤亡国，chief 权威
        w.blocs = []
        for b in data["blocs"]:
            members = [m for m in b["members"] if m in w.nations]
            if members:
                w.blocs.append({"name": str(b["name"]), "members": members,
                                "chief": b["chief"], "turn": int(b["turn"])})
        w.votes = [dict(v) for v in data["votes"]]
        w._vote_id = int(data["vote_id"])
        w.pacts = [dict(p) for p in data["pacts"]]
        # 世界央行：on/rate/loans 全在档里（开关也持久化——续局不该把央行开了又关）
        _bk = data["bank"]
        w.bank = {"on": bool(_bk.get("on")), "rate": float(_bk.get("rate", 0.0)),
                  "loans": {k: dict(v) for k, v in (_bk.get("loans") or {}).items()}}
        w.mail_pending = data["mail_pending"]
        w.peace_offers = data["peace_offers"]
        w.proposals = data["proposals"]
        w._offer_id = data["offer_id"]
        w.prices = {g: float(data["prices"][g]) for g in TRADEABLE}
        w.equilibrium = {g: float(data["equilibrium"][g]) for g in TRADEABLE}
        w.flow_in = {g: int(data["flow_in"][g]) for g in TRADEABLE}
        w.flow_out = {g: int(data["flow_out"][g]) for g in TRADEABLE}
        w.history = data["history"]
        w.history_seen = data["history_seen"]
        # 电网/结算摘要也持久化：否则续档后第一回合 all 面板电力 0、上回合结算丢失
        w.grid_short = {n: bool(v) for n, v in data["grid_short"].items() if n in w.nations}
        w.gold_carry = {n: float(v) for n, v in data["gold_carry"].items() if n in w.nations}
        w.energy_report = {n: tuple(v) for n, v in data["energy_report"].items() if n in w.nations}
        w.econ_summary = {n: s for n, s in data["econ_summary"].items() if n in w.nations}
        w.econ_reports = {n: list(v) for n, v in data["econ_reports"].items() if n in w.nations}
        # ★ ledger 按 LEDGER_FIELDS 归一：追加式政策下新加的账目字段在老档里不存在，
        #   而 `_ledger(n)[新键] += x` 是直接取键 ⇒ 不补就会 KeyError。缺的补 0，有的原样。
        w.ledger = {n: {**{k: float(v.get(k, 0.0) or 0.0) for k in LEDGER_FIELDS},
                        "_since": max(1, int(v["_since"]))}
                    for n, v in data["ledger"].items() if n in w.nations}
        w.spend = {n: {k: float(v[k]) for k in SPEND_FIELDS}
                   for n, v in data["spend"].items() if n in w.nations}
        # 每回合的 GDP run-rate（央行授信口径）。旧档（v4 之前）没有这个键 ⇒ 空表，
        # `nation_gdp` 会退回最新一期报表的 GDP——不许把"缺字段"读成"GDP=0、授信=0"。
        w.gdp_run = {n: float(v) for n, v in (data.get("gdp_run") or {}).items()
                     if n in w.nations}
        # 全期累计 token 用量：**统计字段，不参与结算**。按存档里的国家重建零值骨架，
        # 再把档内已有的数搬进来 —— 老档（没有这个键）也能正常开，只是从 0 起算。
        w.token_usage = {
            n: {k: int((data.get("token_usage") or {}).get(n, {}).get(k, 0) or 0)
                for k in USAGE_FIELDS}
            for n in w.nations
        }
        # 地块：存档即完整（recruited/built/buildings/pending/core 与全部建筑键都在），
        # 只做 "x,y" 字符串键 → (x,y) 元组键的还原，不再补字段
        for k, t in data["tiles"].items():
            x, y = map(int, k.split(","))
            w.tiles[(x, y)] = t
        w._ensure_guardians()
        try:
            cls.runtime.load_mod_state(w, data["mod_state"])
        except ValueError as exc:
            raise SaveFormatError(str(exc)) from exc
        return w


    def player_state(self, name, *, since_event=0):
        if name not in self.nations:
            raise ValueError("仅现存国家可查询玩家状态")
        armies = self.nation_armies(name)
        own = self.own_tiles(name)
        halls, construction, targets = [], [], []
        for x, y in own:
            tile = self.tiles[(x, y)]
            if tile["buildings"].get("市政厅"):
                halls.append({"x": x+1, "y": y+1, "count": tile["buildings"]["市政厅"],
                    "garrison": [a["id"] for a in armies if (a["x"], a["y"]) == (x, y)],
                    "under_attack": bool(self.battle_sides(x, y))})
            if any(tile.get("pending", {}).values()):
                construction.append({"x": x+1, "y": y+1, "buildings": copy.deepcopy(tile["pending"]),
                                     "effective": "本轮建筑落成阶段"})
        for (x, y), tile in self.tiles.items():
            if tile["owner"] != name and self.visible_to(name, x, y) and tile["buildings"].get("市政厅"):
                targets.append({"owner": tile["owner"], "x": x+1, "y": y+1,
                                "count": tile["buildings"]["市政厅"], "scope": "当前视野"})
        need, short, damage = self.supply_shortfall(name)
        reserved = {}
        for c in self.diplomacy_queue:
            if c["actor"] == name:
                for good, amount in c["reserved"].items():
                    reserved[good] = reserved.get(good, 0) + amount
        loan = copy.deepcopy(self.bank.get("loans", {}).get(name))
        if loan:
            projected = loan["due"]
            for _ in range(loan["turns_left"]):
                projected = max(0, int(round(projected * (1 + self.bank_loan_rate()))))
            loan.update(projected_due=projected, projection="利率保持当前值",
                        due_turn=self.turn + loan["turns_left"] - 1, due_phase="世界央行")
        votes = []
        for v in self.votes:
            if name in self._vote_stakeholders(v):
                item = {k: copy.deepcopy(v[k]) for k in ("id", "kind", "bloc", "proposer", "turn", "voting_turn")}
                # 盟外申请人只看时间和结果，不取得联盟商议票数。
                if self.bloc_of(name) is self.bloc_by_name(v["bloc"]):
                    item["votes"] = copy.deepcopy(v["votes"])
                item["deadline"] = "投票回合全部行动结束后的计票阶段"
                votes.append(item)
        changes = []
        for index, event in enumerate(self.history):
            if index < since_event:
                continue
            if event.get("nation") == name or name in event.get("seen", []):
                changes.append({"id": index+1, "source": "引擎事件", "turn": event["turn"],
                                "phase": event["phase"], "text": event["text"]})
        travel = [{"kind": kind, **copy.deepcopy(t)} for kind, tasks in
                  (("信件", self.mail_pending), ("馈赠", self.gift_pending),
                   ("地图", self.map_pending), ("间谍", self.spy_pending))
                  for t in tasks if t["from"] == name]
        pending = {"diplomacy": [copy.deepcopy(c) for c in self.diplomacy_queue if c["actor"] == name],
                   "votes": votes, "construction": construction, "travel": travel,
                   "proposals": [copy.deepcopy(p) for p in self.proposals
                       if name in self.entity_members(p.get('A', '')) or name in self.entity_members(p.get('B', ''))],
                   "peace_offers": [copy.deepcopy(p) for p in self.peace_offers
                       if name in (p.get('from'), p.get('to'))]}
        return {"source": "引擎当前事实", "turn": self.turn, "phase": self.phase,
                "state_version": self.state_version, "event_cursor": len(self.history),
                "current": {"resources_available": copy.deepcopy(self.nations[name].res),
                    "resources_reserved": reserved, "loan": loan, "townhalls": halls,
                    "supply": {"inventory": self.res(name, "补给"), "demand": need,
                               "shortfall_now": short, "starvation_now": damage,
                               "sampling": "当前库存与军队；尚未计入本轮生产"},
                    "armies": [{"id": a["id"], "x": a["x"]+1, "y": a["y"]+1,
                        "hp": a["hp"], "kind": unit_kind(a), "attack_base": unit_atk(a),
                        "engaged": bool(a.get("engaged")), "action_available": self.phase == '行动' and a.get("moved_turn") != self.turn
                            and a.get("born_turn") != self.turn and not a.get('engaged')
                            and not a.get('retreat_to'),
                        "tile_owner": self.owned_by(a['x'], a['y']),
                        "movement_budget": unit_speed(a),
                        "retreat_to": [v+1 for v in a["retreat_to"]] if a.get("retreat_to") else None}
                        for a in armies], "relations": self.rel_desc(name),
                    "grid_last_settlement": self.energy_report.get(name),
                    "visible_townhalls": targets},
                "changes": changes, "pending": pending,
                "results": [copy.deepcopy(c) for c in self.command_results if c["actor"] == name][-20:],
                "player_plan": {"source": "玩家计划与假设", **copy.deepcopy(self.plans.get(name, {}))},
                "history": {"source": "历史记录与玩家摘要，保留当时时间", "summaries": copy.deepcopy(self.summaries.get(name, []))[-3:]}}


    def decision_overview(self, name):
        s = self.player_state(name, since_event=len(self.history))
        cur, pending = s["current"], s["pending"]
        army = cur["armies"]
        halls = "、".join(f"({t['x']},{t['y']}){'空防' if not t['garrison'] else '驻防'}" for t in cur["townhalls"]) or "无已落成厅"
        supply = cur["supply"]
        loan = cur["loan"]
        debt = f"当前债额{loan['due']}，预计第{loan['due_turn']}轮末还{loan['projected_due']}（利率不变）" if loan else "无贷款"
        return (f"【引擎当前事实】第{s['turn']}回合 · {s['phase']} · 状态版本{s['state_version']}\n"
                f"国祚：{halls}；在建工程{len(pending['construction'])}项（在建厅不计国祚）\n"
                f"补给：当前库存{supply['inventory']}/军需{supply['demand']}，当前缺口{supply['shortfall_now']}；尚未计本轮生产\n"
                f"财政：可用金{cur['resources_available']['黄金']}，预留{cur['resources_reserved']}；{debt}\n"
                f"军事：{len(army)}支 / {sum(a['hp'] for a in army)}HP / 可行动{sum(a['action_available'] for a in army)}支 / 交战{sum(a['engaged'] for a in army)}支 / 待撤离{sum(a['retreat_to'] is not None for a in army)}支\n"
                f"有效关系：{cur['relations']}\n"
                f"【待处理】外交{len(pending['diplomacy'])}项，投票{len(pending['votes'])}项，在途{len(pending['travel'])}项；query panel=pending 查看编号与期限")
