from __future__ import annotations
from ..core import *



class TerritoryLogic:
    def __init__(self, size=80, seed=None, *, nations=None, starts=None, res=None, gen=True, max_turns=200):
        self.size = size
        self.seed = seed if seed is not None else random.randrange(1 << 31)
        self.rng = random.Random(self.seed)
        self.max_turns = int(max_turns)
        self.runtime.initialize_state(self)
        if gen:
                    names = list(nations or ["秦", "楚", "齐"])
                    for nm in names:
                        self.nations[nm] = Nation(nm, (res or {}).get(nm))
                        self.order.append(nm)
                        self.mailbox[nm] = []
                        self.grid_short[nm] = False
                        self._assign_code(nm)
                    if starts:
                        self._place_crosses(starts)
                    else:
                        self._place_ring(names)
                    self._ensure_guardians()


    def alive(self) -> list[str]:
        return [n for n in self.order if n in self.nations]


    def _check(self, x: int, y: int) -> None:
        if not (0 <= x < self.size and 0 <= y < self.size):
            raise IndexError(f"坐标越界：({x+1},{y+1}) 超出地图（1..{self.size}）")


    def tile_terrain(self, x: int, y: int) -> str:
        """这一格的地形：**已物化的地块以它自己的 `terrain` 为准**（那是权威值——
        占地后写进地块、也写进存档），未物化的才现问 `mapgen`。

        （移动代价按地形算之后，这条从"纯函数"变成"以地块为准"很关键：
        测试里就地改地形、以及将来任何地形改造，都必须让可达性看得见。）
        """
        # ★ **2026-09-24 优化**：`mapgen` 是 `@property`（惰性建图），而 `MapGen._terrain`
        #   本来就是一张建好的二维数组 ⇒ 这里**直接缓存那张数组**，不再逐次过 property。
        #   cProfile 实测（两局）：`mapgen` 的 getter 被调 **456,844 次**，是 `_reachable`
        #   之后的第二大热点。语义不变（同一张数组、同一个惰性时机 —— 第一次问才建图）。
        t = self.tiles.get((x, y))
        if t is not None:
            return t["terrain"]
        g = self._terr_grid
        if g is None:
            g = self._terr_grid = self.mapgen._terrain
        return g[y][x]


    @property
    def mapgen(self):
        """整张图的生成器（`mapgen.MapGen`，**惰性**：第一次问地形/资源才建）。

        2026-09-15 起地图不再是逐格纯函数，而是**由 `(seed, size)` 决定的一整张图**
        （蓝噪声 + 密度图调制，见 `mapgen.py`）：逐格独立抽是泊松随机场，局部既结块
        又有孔洞，且"起步 5 格开什么牌"会被复利放大。对外接口没变
        （`tile_terrain` / `tile_resources` 签名与返回值照旧），调用点不用改。

        ★ **惰性**是有意的：读档（`gen=False`）连图都不用生成——存档里已物化的地块
        自带 `terrain`/`resources` 字面量，只有新占的格子才会问到这里。
        """
        if self._mapgen is None:
            from .mapgen import MapGen
            self._mapgen = MapGen(self.seed, self.size)
        return self._mapgen


    def tile_resources(self, x: int, y: int) -> dict[str, int]:
        """该地块的矿藏/耕地布局（建采集建筑要看的就是它）——**静态属性**，
        跟谁先占、占之前打过几仗毫无关系（见 `mapgen`）。

        历史两坑（都已修，别再踩）：曾用**世界共享 RNG** `roll_resources(self.rng, …)`
        ⇒ 同一格摇出什么取决于轮到它时 RNG 走了多远（战斗掷骰也在那条流上），
        「种子可复现」形同虚设；命名同理（每建一格都消耗、撞名重试使消耗个数不定）。
        现在 `self.rng` 只剩战斗与开局布点在用。
        """
        return self.mapgen.resources(x, y)


    def ter_char(self, x: int, y: int) -> str:
        return TERRAIN_CHARS[self.tile_terrain(x, y)]


    def visible_buildings(self, name: str, x: int, y: int) -> dict:
        """name 在 (x,y) 上**看得见**的建筑（不含在建）——全作唯一口径，面板一律走这里。

        · **自家的地**：全部建筑（自己的工地当然清楚；在建不在此列，看 `pending`）。
        · **视野内的他国地**：**只有城堡与市政厅** —— 两样都是"从外面就看得出"的公共建筑：
          要塞（2026-09-19 用户：「不知道城堡很吃亏」）与**市政厅（国祚，2026-09-21）**。
          后者必须公开，否则"拔光市政厅即亡国"这条规则根本无从瞄准（看不见就打不着）。
          兵营/工厂/农田仍是内政底细，那是 `spy` / 换图才买得到的东西。
        · **视野内的无主故土**（前朝废墟，`owner is None` 且已物化）：**全部建筑** ——
          那条纪律护的是"**别国的**内政底细"，这里没有国（亡国才留下这种格），
          也就无机密可言。**它是唯一能产出 `owner is None` 的物化格的来源**
          （见 `_eliminate_if_dead`），所以这条分支不会误伤任何"正常的无主野地"
          （那些格根本没物化）。
        · **视野外**：空 dict（与 `visible_to` 同一条纪律）。

        （公开档位：2026-09-19 只放行城堡；2026-09-21 加市政厅（国祚）与无主故土全开。
        其余建筑与地块资源仍旧未探明。）
        """
        t = self.tiles.get((x, y))
        if not t:
            return {}
        if t["owner"] == name:
            return {b: n for b, n in t["buildings"].items() if n}
        if not self.visible_to(name, x, y):
            return {}
        if t["owner"] is None:
            return {b: n for b, n in t["buildings"].items() if n}
        return {b: t["buildings"][b] for b in ("城堡", "市政厅") if t["buildings"].get(b)}


    def owned_by(self, x: int, y: int) -> str | None:
        t = self.tiles.get((x, y))
        return t["owner"] if t else None


    def neighbors(self, x: int, y: int) -> list[tuple[int, int]]:
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.size and 0 <= ny < self.size:
                    out.append((nx, ny))
        return out


    def own_tiles(self, name: str) -> list[tuple[int, int]]:
        return sorted((x, y) for (x, y), t in self.tiles.items() if t["owner"] == name)


    def frontier_of(self, name: str) -> set[tuple[int, int]]:
        fr = set()
        for (x, y), t in self.tiles.items():
            if t["owner"] != name:
                continue
            for nx, ny in self.neighbors(x, y):
                if (nx, ny) not in self.tiles:
                    fr.add((nx, ny))
        return fr


    def nation_building_count(self, name: str, building: str, *, include_pending: bool = False) -> int:
        """name 全部地块上某建筑的已落成座数（include_pending=True 含在建）。"""
        n = 0
        for t in self.tiles.values():
            if t["owner"] != name:
                continue
            n += t["buildings"].get(building, 0)
            if include_pending:
                n += (t.get("pending") or {}).get(building, 0) or 0
        return n


    def visible_to(self, name: str, x: int, y: int) -> bool:
        """name 是否看得见 (x,y)：它本身或相邻格（含对角）有自家的地。
        联盟共享视野：盟友的地块视同己方（自己+盟友地盘各带相邻一圈）。"""
        cand = [(x, y)] + self.neighbors(x, y)
        bloc = self.bloc_of(name)
        for cx, cy in cand:
            o = self.owned_by(cx, cy)
            if o == name:
                return True
            if bloc is not None and o in bloc["members"]:
                return True
        # 瞭望塔：己方/盟方任一瞭望塔半径（`effects.vision_radius`）圆（欧氏）内也可见（事件视野）
        # 半径**每次调用取一次**（效果是数据，可能被整体抖动）；这条路径调用极频繁，
        # 别把它塞进内层循环。
        _tower_r = building_effect("瞭望塔", "vision_radius")
        _tower_r2 = _tower_r * _tower_r
        for (tx, ty), t in self.tiles.items():
            if not t["buildings"].get("瞭望塔"):
                continue
            o = t["owner"]
            if o != name and not (bloc is not None and o in bloc["members"]):
                continue
            if (tx - x) ** 2 + (ty - y) ** 2 <= _tower_r2:
                return True
        return False


    def _stamp_seen(self, entry: dict, parties: list[str] | None = None) -> None:
        """纪事落盘时刻抓一份"谁看得见这里"的快照。events_for 按快照过滤，
        而不是按查询时刻的视野——否则你后来夺下的地上发生的**旧** Retreat/战报
        会事后凭空显形（回溯补发情报）。

        `parties`：**必须看到这条**的当事国（把结果告诉对面）。有坐标时是**并进**
        视野快照，没坐标时它们就是全部可见者。这条通道是用户 2026-09-20 报的两个洞
        的正解：求和/缔约**被拒**此前只有拒绝方自己看得见（对面一直等下去），
        夺地只写攻方视角（失主**四周已无自家地**时连"我丢了哪块"都收不到）。
        ★ 口径：**"怎么谈的"不广播**，但**当事人必须知道结果**——两者不冲突
        （广播是给第三方的，parties 是给桌上那两方的）。
        """
        x, y = entry.get("x"), entry.get("y")
        # ★ **坐标成对才算坐标**（2026-09-23 修）：只看 `x is not None` 的话，
        #   半个坐标（有 x 没 y）会一路带到 `visible_to` → `neighbors` → `None + int` 崩掉。
        #   缺一个 = 没有坐标（按无坐标处理），后面 events_for 也只认成对的（同口径）。
        has_coords = x is not None and y is not None and bool(self.nations)
        if not has_coords and not parties:
            return
        seen = [n for n in self.nations if self.visible_to(n, x, y)] if has_coords else []
        for p in (parties or ()):
            if p in self.nations and p not in seen:
                seen.append(p)
        entry["seen"] = seen


    def _new_tile(self, x: int, y: int, owner: str) -> dict:
        terrain = self.tile_terrain(x, y)
        used = {t["name"] for t in self.tiles.values() if t.get("name")}
        return {
            "owner": owner,
            "terrain": terrain,
            "resources": self.tile_resources(x, y),
            "buildings": {b: 0 for b in BUILDINGS},
            "pending": {b: 0 for b in BUILDINGS},  # 在建（下回合才生效）
            # ★地块命名**不能用世界共享 RNG**（2026-09-11 修）。
            #   `roll_tile_name` 撞名会重试，所以每建一格消耗的随机数**个数不定**；
            #   而 `self.rng` 同时是战斗掷骰（`_die` → `self.rng.randint(1,6)`）的流 ——
            #   于是**战斗结果取决于建地的顺序和数量**，同一 seed 两局对不上。
            #   这和 `tile_resources` 那段注释里已经修过的病是同一个（当时只修了资源，
            #   命名漏了）。改成与地形/资源同一套做法：**纯函数 of (seed, x, y)**。
            #   名字本身仍可能因撞名而不同（`used` 依赖建地顺序），但那是纯装饰；
            #   关键是**战斗的随机流从此与命名无关**，A/B 对比才干净。
            "name": roll_tile_name(random.Random(f"{self.seed}:{x}:{y}:name"), used),
            "core": owner,  # 核心领土：首任 owner；每次战争结束按参与者实占重算
            "recruited_this_turn": 0,
            "built_this_turn": 0,
        }


    def _place_crosses(self, starts: dict[str, tuple[int, int]]):
        """十字开局：中心 + 上下左右。★ **中心（=本国核心）必然是平原、且自带一座市政厅**——
        用户 2026-09-21 口径「开局默认有个市政厅，在国家核心」＋「中心格必然是平原」；
        它是**国祚**（见 `has_townhall` / `_eliminate_if_dead`）。白送，不花钱、也不受
        `min_slots≥6` 那道门槛约束（那是"自己再盖一座"的门槛）。

        ★ 地形改成平原**只写这一格的 `terrain`**：地块一旦物化，它自己的 `terrain`
        就是权威（`tile_terrain` 先查已物化的格）⇒ 地图/移动代价/防御/建造惩罚
        全都跟着这一处变，不必到处打补丁。理由：核心是国祚所在地，开局就顶着一座
        山地/森林的施工惩罚说不过去（且山地地形防御会把首都变成免费要塞）。"""
        for nm, (cx, cy) in starts.items():
            for dx, dy in CROSS:
                x, y = cx + dx, cy + dy
                if 0 <= x < self.size and 0 <= y < self.size and (x, y) not in self.tiles:
                    self._drop_guardians(x, y)   # 全图野人已预置：新占的格子上的守卫撤走
                    t = self._new_tile(x, y, nm)
                    if dx == 0 and dy == 0:
                        t["terrain"] = "平原"
                        # 资源是**按地形权重**抖出来的 ⇒ 改了地形就得跟着重算，
                        # 否则留下一格"平原产石油"的怪物（见 `MapGen.resources_as`）
                        t["resources"] = self.mapgen.resources_as(x, y, "平原")
                        t["buildings"]["市政厅"] = 1
                    self.tiles[(x, y)] = t
            # 兜底：中心格跑到界外时（畸形 starts）也得有国祚——否则这国一出场就是死的
            if not self.has_townhall(nm):
                self._seed_townhall(nm)


    def _place_ring(self, names: list[str]):
        """各国环状开局（3 国=三角）。"""
        n = len(names)
        c = self.size / 2
        r = self.size * START_RING_RATIO   # 环半径（占地图边长比例，见 balance）
        pts = {}
        for i, nm in enumerate(names):
            ang = -math.pi / 2 + i * 2 * math.pi / n
            pts[nm] = (int(round(c + r * math.cos(ang))), int(round(c + r * math.sin(ang))))
        self._place_crosses(pts)


    def _seed_townhall(self, name: str) -> str | None:
        """白送一座**已落成**的市政厅，落在"国家核心"。两处用法都叫"给它一条国祚"——
        开局（`_place_crosses`）与**旧档迁移**（`load`）。

        「核心」＝**自家最早物化的那块地**：`_place_crosses` 先建中心、后建四臂，而
        `tiles` 按插入序保存/还原（见 `save`），所以这个次序跨存档稳定。
        ★ 别拿 `t["core"]` 判：那是"首任归属"（战后按实占重算），**开局每一格都等于 owner**，
        认不出首都在哪——拿它挑只会挑到坐标最小的那块。"""
        owned = set(self.own_tiles(name))
        if not owned:
            return None
        pick = next(p for p in self.tiles if p in owned)
        self.tiles[pick]["buildings"]["市政厅"] += 1
        return self.tiles[pick]["name"]


    def _stage_claims(self, ctx):
        war_lines = ctx["war_lines"]
        # 4.95) 弃城即陷：本回合发生过战斗的格子，守军全撤走/覆灭后格上只剩唯一一方
        # （且与格主交战）→ 直接改旗。兑现 rules 既定的「守军弃城即陷」，无需再补一刀 atk。
        # ★ sorted：_conquer 会改归属、可触发亡国（改 wars/truce/blocs），
        #   裸 set 迭代序受 hash 随机化 → 同 seed 换进程改旗顺序不同 → 战局分叉。
        for bx, by in sorted({(x, y) for x, y, _ in war_lines}):
            owner = self.owned_by(bx, by)
            if owner is None or owner not in self.nations:
                continue
            holders: dict[str, list[dict]] = {}
            for a in self.armies:
                if (a["x"], a["y"]) == (bx, by):
                    holders.setdefault(a["owner"], []).append(a)
            if owner in holders:
                continue  # 格主守军还在 → 不动
            # 守军尽撤 → 围攻方按索取顺序接管（第一个 atk 者优先；多个互相交战者仍算混战）
            foes = {g: fs for g, fs in holders.items() if g != "野人" and self.war_between(g, owner)}
            g = self._claim_winner(foes)
            if g is None:
                continue
            ok, msg = self._conquer(bx, by, g, "守军尽撤")
            if ok:
                self.log(f"⚔ 守军尽撤 @({bx + 1},{by + 1}){self.ter_char(bx, by)}，{msg}",
                         phase="战报", x=bx, y=by)


    def _stage_deaths(self, ctx):
        # 4.96) ★ 亡国判定（**回合末统一**，2026-10-09 用户拍板：「当全球强制和平的结算插入
        #   是回合末还是回合中」→ **回合末**）。
        #   · 放在 `_withdraw_illegal` **之前**：亡国会清掉滞留军的 `engaged` 并把余土变无主，
        #     紧接着第 5 步就把它们按兵种速度遣返——与旧时序一致（旧版是战斗结算当场亡国、
        #     然后第 5 步遣返），变的只是**判定时刻**：从"命令阶段当场"挪到"回合末统一"。
        #   · 战果照旧当场可见：地已经易主、日志已经出（`_conquer` 里那几条），
        #     亡国只是不再**半路**改写同回合剩余命令所面对的世界。
        self._settle_deaths()
