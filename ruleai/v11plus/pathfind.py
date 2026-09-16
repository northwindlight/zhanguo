# -*- coding: utf-8 -*-
"""寻路：**视野掩码 + 多回合代价场 + 本回合落点**（v11plus 的"寻路"一件）。

v10 的这一件是**单步贪心**：`min(邻居, key=切比雪夫距离)`。三个病：

  1. **完全不懂地形代价** —— 2026-09-15 地图换了之后，骑兵踏进森林/山地就吃满移动力，
     贪心却还在往那边走 ⇒ 卡住、来回抖；山地在它眼里和草原一样近；
  2. **从不问引擎的合法集** —— 明明引擎支持一回合走 2 格、明明有 `_reachable`，
     它每回合只挪一格；
  3. **没有"方向感"** —— 只看切比雪夫距离，不看"绕过这道山要几回合"。

本模块给两样东西（**分工是关键**）：

  · **本回合落点**用 `world._reachable`（引擎自己算的合法集：逐格代价、墙、预算全对）。
    这与 LLM 玩家的面板同源（`mp_ai.py:230` 的「本回合可及 N 格」、
    `mp_ai.py:259-273` 野地清单里的「可及」），所以**在玩家信息集之内**。
  · **多回合方向感**用自建的 `cost_field`：以目标为源、按移动力代价铺满全图
    （截断在 `max_cost` 内），视野内用真地形，**视野外一律按平原代价 1 估**。
    玩家手上没有这张图，所以它**只能**用看得见的信息算 —— 这是硬纪律，不是优化取舍。

★ 迷雾纪律（`expand_rule_v9.py` 记着四处历史越权，别再犯）：
  非视野格的**地形**一次都不许读（`world.tile_terrain` 对迷雾格是纯函数真值 = 偷看），
  一律按 `_UNSEEN_COST` 估；墙也只在视野内才认（视野外当可通行 —— 乐观假设，
  撞上了下回合重规划，代价是那一步白烧，比"隔着迷雾开图"划算得多）。

★ 无状态：本模块不存任何跨回合的东西（与 v10 一样每回合从世界状态重算）。
  想省算力就把 `cache` 传进来（调用方自己持有，随回合丢弃）——
  模块级缓存会在"同一回合里地形被改"（测试常干）时给出陈旧答案。
"""
from __future__ import annotations


from game import MOVE_COST, building_effect

_INF = 1 << 30
_UNSEEN_COST = 1        # 视野外一律按平原估（用户 2026-09-15 口径）


# ---------------------------------------------------------------- 几何
def chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    """棋盘距离（八邻一步 = 1）。**只用于排序粗估**，不用来判可达性——
    可达性一律问 `world._reachable`（那里才有地形代价与墙）。"""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


# ---------------------------------------------------------------- 视野
def vision_mask(world, name: str) -> frozenset:
    """`name` 这一回合**看得见**的全部格子（一次建好，别逐格问 `visible_to`）。

    口径与 `World.visible_to`（`mp.py:489`）**逐条对齐**：
      ① 自家 + 联盟成员的地块本身，以及它们的八邻；
      ② 自家/盟方的瞭望塔半径（`effects.vision_radius`，欧氏）圆内。

    ★ 为什么要自己铺一遍：`visible_to` 在答"看不见"时会**遍历全图 tiles**（找瞭望塔），
    逐格问就是 O(格数 × 地块数)。这里一趟 O(地块数) 建好，等价性由
    `tests/test_pathfind.py::TestVisionMask` 抽样对照 `visible_to` 钉住。
    """
    bloc = world.bloc_of(name)
    members = set(bloc["members"]) if bloc else set()
    out: set = set()
    towers: list = []
    for (x, y), t in world.tiles.items():
        o = t["owner"]
        if o != name and o not in members:
            continue
        out.add((x, y))
        for nb in world.neighbors(x, y):
            out.add(nb)
        if t["buildings"].get("瞭望塔"):
            towers.append((x, y))
    radius = building_effect("瞭望塔", "vision_radius")
    if radius and towers:
        r2 = radius * radius
        for tx, ty in sorted(towers):
            for x in range(max(0, tx - radius), min(world.size - 1, tx + radius) + 1):
                for y in range(max(0, ty - radius), min(world.size - 1, ty + radius) + 1):
                    if (tx - x) ** 2 + (ty - y) ** 2 <= r2:
                        out.add((x, y))
    return frozenset(out)


# ---------------------------------------------------------------- 代价场
def cell_cost(world, kind: str, cell: tuple[int, int], vision) -> int:
    """一格的地形代价（移动力点数）——**看不见就按平原估，绝不读真地形**。"""
    if cell not in vision:
        return _UNSEEN_COST
    return MOVE_COST.get(kind, {}).get(world.tile_terrain(*cell), 1)


def enemy_cells(world, name: str) -> frozenset:
    """**有与我交战的他国驻军**的格（一趟 O(国家军队数)，一回合算一次）。

    ★ 为什么要预先算：引擎 `_mv_wall` 里那一条是 `any(... for d in world.armies)` ——
      而野人守卫把**全图**铺满了（40×40 就有 ~1500 支），所以逐格问一次就是 O(1500)。
      代价场每回合要问几十万次，这一项一度占了 79% 的运行时。
      口径与引擎逐字一致：只看**他国**且**与我交战**的（野人驻军不是墙 —— 行军不打野人）。

    ★ 2026-09-16：改成扫 `world.troops`（**非野人名单**，引擎里那张惰性自失效的表）。
      `"野人"` 那一条本就写着 `d["owner"] != "野人"`，所以**逐条等价**；而 1500 支野人
      从此不再被走过 —— 实测（40x40、第 60 回合、1583 支军队）本函数 128μs → 0.7μs，
      而一回合要问 ~9 张场。名单本身的维护见 `mp.World.troops` 的文档。
    """
    return frozenset((d["x"], d["y"]) for d in world.troops
                     if d["owner"] != name and world.war_between(name, d["owner"]))


def is_wall(world, name: str, cell: tuple[int, int], vision, blocked=None) -> bool:
    """这一步**走不进去**吗（与引擎 `_mv_wall` 同口径，但只在视野内才认）。

    视野内：他国/中立领土是墙（要进占只能 atk）；野地上有与我交战的他国驻军也是墙
    （野人驻军**不是**墙 —— 行军不打野人）。视野外：一律当可通行。

    `blocked` = `enemy_cells(...)` 的结果，**由调用方算一次传进来**（见那个函数的注释：
    现算一次的成本是 O(军队数)，而本函数每回合要被问几十万次）。
    """
    if cell not in vision:
        return False
    x, y = cell
    owner = world.owned_by(x, y)
    if owner is not None and owner != name and not world.allied_between(name, owner):
        return True
    if blocked is None:
        blocked = enemy_cells(world, name)
    return cell in blocked


_ADJ: dict[int, tuple[tuple[int, ...], ...]] = {}


def _adj_of(size: int) -> tuple[tuple[int, ...], ...]:
    """每格的邻居表（**整数下标** = `x * size + y`）：只与地图边长有关，建一次全家共用。

    与 `World.neighbors` **同序**（`dx` 外层、`dy` 内层）。为什么要有它：旧内层每弹一格
    都现建一个邻居列表，实测那一项（80 万次调用）占掉整体运行时的 1/6；而这张表是
    **纯几何**的 —— 与局面无关，所以它不存在"缓存陈旧"这个问题（与下面那两张表不同）。
    """
    got = _ADJ.get(size)
    if got is None:
        rows = []
        for x in range(size):
            for y in range(size):
                rows.append(tuple(
                    x2 * size + y2
                    for x2 in (x - 1, x, x + 1) for y2 in (y - 1, y, y + 1)
                    if (x2 != x or y2 != y) and 0 <= x2 < size and 0 <= y2 < size))
        got = tuple(rows)
        _ADJ[size] = got
    return got


def _dijkstra(world, name: str, gl: list, kind: str, vision, max_cost: int) -> dict:
    """代价场的**快实现**：与 `cost_field` 原先那版（元组 + 字典 + `World.neighbors`）
    **逐值等价**，只是把内层降到"整数下标 + 定长数组"。

    三处提速，都不改语义：

      · **邻居表按边长预建**（`_adj_of`）—— 纯几何，与局面无关；
      · **`best` 用定长数组、堆里存 `(代价, 下标)`** —— 下标 `x * size + y` 单调于
        `(x, y)`，所以出堆顺序与旧写的 `(代价, x, y)` **同序**，逐值不变；
      · **地形代价与"墙"在本场够得到的方框内现算成两张表** —— 每跨一步至少花 1 点移动力，
        所以代价 ≤ `max_cost` 的格必然落在切比雪夫距离 `max_cost` 之内（方框外够不到）。
        现算 ⇒ 与旧写法同源、**不存在缓存陈旧**；而每格只问引擎一次（旧写法每格问 8 次）。
    ★ 2026-09-16：堆换成**桶队列**（Dial）—— 步代价只可能是 1/2 的小整数，于是"按距离
      分桶 + 从小到大扫一遍"就够了，省掉 68 万次 push/67 万次 pop 的堆操作（实测这一项
      占整局 ~8%）。**逐值等价**：Dijkstra 的取值与松弛顺序无关，桶只是把"下一个该弹谁"
      从堆里换成数组下标；只有代价为 0 的边才会往**当前**桶里追加，`while b` 那个内层
      循环正好兜住（本局地形最小代价是 1，这条是防将来加"道路 0 代价"）。
    """
    size = world.size
    n = size * size
    adj = _adj_of(size)

    xs = [g[0] for g in gl]
    ys = [g[1] for g in gl]
    lo_x, hi_x = max(0, min(xs) - max_cost), min(size - 1, max(xs) + max_cost)
    lo_y, hi_y = max(0, min(ys) - max_cost), min(size - 1, max(ys) + max_cost)

    move = MOVE_COST.get(kind, {})
    by_owner = world.owned_by
    allied = world.allied_between
    blocked = enemy_cells(world, name)      # ★ 一趟算好，别在几十万次松弛里各扫一遍全图
    wall_of = bytearray(n)                  # 视野内"走不进去"的格（与 `is_wall` 同口径）
    uniform = not move or all(v == 1 for v in move.values())
    cost_of = None if uniform else [_UNSEEN_COST] * n
    for x in range(lo_x, hi_x + 1):
        base = x * size
        for y in range(lo_y, hi_y + 1):
            if (x, y) not in vision:
                continue                    # 视野外：地形一次都不读、墙一律不认
            i = base + y
            if cost_of is not None:
                cost_of[i] = move.get(world.tile_terrain(x, y), 1)
            owner = by_owner(x, y)
            if owner is not None and owner != name and not allied(name, owner):
                wall_of[i] = 1
            elif (x, y) in blocked:
                wall_of[i] = 1

    # ★ 2026-09-16：**每步都只花 1 点**的兵种（步/民，以及兵种表里没有的名字 ——
    #   那时 `move.get(terrain, 1)` 一律回 1）走**纯 BFS**：代价 = 步数，
    #   连地形表都不用建（省一遍 `tile_terrain`）。逐值等价，见 `_bfs_ball` 的说明。
    if uniform:
        best = _bfs_ball(gl, size, adj, wall_of, max_cost, n)
        absent = 255
    else:
        best = _bucket_ball(gl, size, adj, cost_of, wall_of, max_cost, n)
        absent = _INF

    out: dict = {}
    for x in range(lo_x, hi_x + 1):
        base = x * size
        for y in range(lo_y, hi_y + 1):
            v = best[base + y]
            if v != absent:
                out[(x, y)] = v
    return out


def _bfs_ball(gl: list, size: int, adj, wall_of: bytearray, max_cost: int,
              n: int) -> bytearray:
    """**每步只花 1 点移动力**时的代价场：逐层 BFS，返回"距离表"（255 = 够不到）。

    ★ 为什么可以走这条快路：`MOVE_COST[kind]` 全为 1 ⇒ 代价就是**步数**，
      BFS 的"层号"与 Dijkstra 的距离**逐值相同**（八邻等权，先到即最短）；
      于是不需要地形表、桶队列与 `max(...)`，每个格只进队一次。
    ★ 与 Dijkstra 一样：源格照记（哪怕它是墙）、墙格不许进、只铺到 `max_cost` 层。
    """
    best = bytearray([255]) * n
    level: list = []
    for gx, gy in gl:
        i = gx * size + gy
        if best[i] == 255:                  # 源格（同格重复的 goal 只算一次）
            best[i] = 0
            level.append(i)
    d = 0
    while level and d < max_cost:
        d += 1
        nxt: list = []
        for i in level:
            for j in adj[i]:
                if best[j] == 255 and not wall_of[j]:
                    best[j] = d
                    nxt.append(j)
        level = nxt
    return best


def _bucket_ball(gl: list, size: int, adj, cost_of: list, wall_of: bytearray,
                 max_cost: int, n: int) -> list:
    """步代价 1/2 的代价场：**桶队列**（Dial）版的 Dijkstra，逐值等价（见 `_dijkstra`）。"""
    best = [_INF] * n
    buckets: list = [[] for _ in range(max_cost + 1)]
    for gx, gy in gl:
        i = gx * size + gy
        if best[i] == _INF:                 # 源格（同格重复的 goal 只算一次）
            best[i] = 0
            buckets[0].append(i)
    for d in range(max_cost + 1):
        b = buckets[d]
        while b:                            # ← 0 代价的边会往当前桶追加，故用 while 排空
            i = b.pop()
            if best[i] != d:                # 陈旧条目（这一格后来被更近的路改过）
                continue
            here = cost_of[i]
            for j in adj[i]:
                if wall_of[j]:
                    continue                # 走不进去 ⇒ 更不能穿过（与引擎逐格判定一致）
                cj = cost_of[j]
                ncost = d + (here if here > cj else cj)
                if ncost <= max_cost and ncost < best[j]:
                    best[j] = ncost
                    buckets[ncost].append(j)
    return best


def cost_field(world, name: str, goals, kind: str, vision, *, max_cost: int,
               cache: dict | None = None) -> dict:
    """以 `goals` 为源的**代价场**：`{格子: 走到最近那个目标还要几点移动力}`。

    一致代价搜索（Dijkstra），步代价 = `max(出发格, 目标格)` —— 与引擎 `_reachable`
    同一把尺子（对称），所以**反向**从目标铺出来的数，就是"我还差多少"。
    只铺到 `max_cost` 为止（不然每回合要为每个目标 Dijkstra 全图）。

    `cache`：调用方持有的字典（键 = `(goals, kind, max_cost)`），同一回合里重复问同一个
    目标就直接命中。**模块自己不存缓存** —— 见文件头。

    ★ 2026-09-15：内层换成 `_dijkstra`（快版，逐值等价）。**为什么**：v11 在 40x40、
      200 回合上比 v10 慢 5.9x（22.9s vs 3.9s），而它的动作数**更少** —— 慢的不是动作多，
      是这里。后期每回合真算 ~40 张场、每张 330~475 格，连同 `neighbors`/`cell_cost`
      占掉整体时间的六成。
    ★ **前提到 `goals` 全在地图内** —— `targeting.candidates` 只产出地图内的格；
      万一越界会被丢掉（旧写法把它当"代价 0 的源"记下来，那是个没有调用方的死分支）。
    """
    key = (tuple(sorted(goals)), kind, max_cost)
    if cache is not None and key in cache:
        return cache[key]
    gl = [g for g in sorted(goals)
          if 0 <= g[0] < world.size and 0 <= g[1] < world.size]
    best = _dijkstra(world, name, gl, kind, vision, max_cost) if gl else {}
    if cache is not None:
        cache[key] = best
    return best


# ---------------------------------------------------------------- 本回合怎么走
def marchable(world, name: str, army: dict, target: tuple[int, int], *, reach=None) -> bool:
    """这一军**本回合够不够得着** `target`（atk 口径：终点可以是敌国格/驻军格）。

    这就是"原子攻击"的前置条件——引擎 `attack` 里任何一支到不了就**整通调用全废**，
    所以出手前必须逐支问清楚（`reach` 由调用方缓存后传进来，引擎那个不带缓存）。
    """
    if (army["x"], army["y"]) == target:
        return True
    if reach is None:
        reach = world._reachable(name, army, for_attack=True)
    return target in reach


def best_step(world, name: str, army: dict, field: dict, *, reach=None,
              goal: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """本回合往目标走的**那一格**；没有值得走的一步 → `None`。

    候选 = `world._reachable(name, army)`（引擎的合法集：地形代价、墙、预算全对，
    与玩家面板同源）。在这堆**走得进去**的格里挑"走完之后离目标最近"的那一格：

        选 `field` 值最小的格；平手比 (本回合花的代价, 坐标) —— 全确定。

    ★ **不进步就不动**：若最好的一格并没有比原地更近（`field[原地]`），返回 `None`。
      这条是防抖的关键（v10 的病就是"每回合都挪一格、方向随机"）：
      只认**严格更近**的格子，就永远不存在"走回头路"。

    ★★ **场够不到我时按直线距离走**（`goal` 给了才启用）：代价场是**截断**的
      （`V11_FIELD_MAX_COST`），比它更远的军队在可达格里**一个场值都没有** ——
      没有这条兜底，它就**原地站到天荒地老**（实测：领土少 38 格、停滞军队·回合
      从 55 涨到 358，全是这一条造成的）。兜底只按切比雪夫距离走、且同样要求
      **严格更近**，所以它只是"先朝目标挪，等走进场的半径再交给地形代价"。
      代价：这一段路上它看不见地形代价（可能先踏进一块森林）—— 可接受，
      因为下一回合就重规划，而"站着不动"是更坏的错。
    """
    cur = (army["x"], army["y"])
    if reach is None:
        reach = world._reachable(name, army)
    here_rem = field.get(cur)
    pick = None
    for cell in sorted(reach.keys()):          # 排序遍历 ⇒ 与 hash 序无关
        if cell == cur:
            continue
        rem = field.get(cell)
        if rem is None:
            continue
        if here_rem is not None and rem >= here_rem:
            continue                            # 不比原地更近 ⇒ 不值得走
        key = (rem, reach[cell], cell)
        if pick is None or key < pick[0]:
            pick = (key, cell)
    if pick is not None:
        return pick[1]
    if goal is None or here_rem is not None:
        return None                             # 原地已在场里 ⇒ 是真到了，别乱走
    here_d = chebyshev(cur, goal)
    for cell in sorted(reach.keys()):
        if cell == cur:
            continue
        d = chebyshev(cell, goal)
        if d >= here_d:
            continue                            # 兜底也只认严格更近
        key = (d, reach[cell], cell)
        if pick is None or key < pick[0]:
            pick = (key, cell)
    return pick[1] if pick is not None else None