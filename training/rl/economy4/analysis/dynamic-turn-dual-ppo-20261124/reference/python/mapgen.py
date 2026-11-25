# -*- coding: utf-8 -*-
"""地图生成：蓝噪声 + 密度图调制 + **连续高山/海洋的自然边界**（main 2026-11 算法）。

## 为什么换

旧法 `tile_terrain(x,y) = roll_terrain(random.Random(f"{seed}:{x}:{y}"))`、资源同款
⇒ **逐格独立抽 ⇒ 二维图是白噪声 / 泊松随机场**：森林不长在林带、山地不成山脉，
且"起步 5 格开什么牌"被复利放大。新法（2026-09-15 用户拍板）：

- **密度图调制**：低频场（海拔 / 湿度）给**区域结构** ⇒ 该成片的成片（山脉、林带、沙带）；
- **蓝噪声采样**：每格一个 rank，局部均匀 ⇒ 片内不结块、也不留孔洞；
- **自然边界**（2026-11 main）：外缘用海岸值噪声 + 噪声形变超椭圆轮廓封一圈连通外海的
  海洋与 ≤2 格厚的高山山脊，内部高峰、孤陆封山——矩形地图从此有海岸与山关。

## 三条不变量（测试盯着，别破）

1. **确定性**：只由 `(seed, size)` 决定，同参数两次生成**逐格相同**；
   不许遍历裸 `set`/`dict`（跨 `PYTHONHASHSEED` 会分叉，`test_determinism` 会红）；
   不许用内置 `hash()`（它就是按 PYTHONHASHSEED 加盐的）——统一走 `_hash_int`（blake2b）。
2. **可用陆地分布 = 权重表**：自然屏障先封闭整圈外缘、清除孤立陆地；余下五类地形按
   `TERRAIN_WEIGHTS` 配额精确落位（`_quotas` + "稀有类型先挑"，挑满即停，总数
   恰好 = size² − 屏障数），资源每格边缘分布按 `TERRAINS[terrain]` 逐字保持
   （抖动阈值法，见 `_dither`）⇒ 《地图生成与资源分布》的期望/档位数字仍然成立。
3. **契约**：地图现在是 `(seed, size)` 的函数（**不再是 `seed:x:y` 的逐格纯函数**）——
   同 seed 换 size 会是另一张图。`World.tile_terrain` / `World.tile_resources` 的
   签名与返回值都没变，调用点不用改。

依赖：只用标准库（`hashlib` + `math`）。**不引 opensimplex / numpy**——本仓运行时零第三方依赖。
"""
from __future__ import annotations

import hashlib
import math

from balance import (
    MAPGEN_BLOCKED_SHARE,
    MAPGEN_BLUE_RADIUS,
    MAPGEN_BOUNDARY_SCALE,
    MAPGEN_BOUNDARY_WIDTH,
    MAPGEN_COAST_DEPTH,
    MAPGEN_DRY,
    MAPGEN_FADE,
    MAPGEN_HIGH_MOUNTAIN,
    MAPGEN_HILL,
    MAPGEN_LAND_RADIUS,
    MAPGEN_LAND_ROUNDNESS,
    MAPGEN_LAND_WARP,
    MAPGEN_MTN,
    MAPGEN_OCT_E,
    MAPGEN_OCT_M,
    MAPGEN_SCALE,
    MAPGEN_WET,
    RESOURCES,
    TERRAINS,
    TERRAIN_WEIGHTS,
)
from game import NAME_PREFIX, NAME_SUFFIX

_U24 = 1 << 24

# 资源的中文名 → main 内容 ID（只用于蓝噪声盐与差分对照；引擎内部仍用中文名）
_TERRAIN_ID = {"平原": "native:plain", "森林": "native:forest", "丘陵": "native:hill",
               "山地": "native:mountain", "沙漠": "native:desert"}
_RES_SALT = {"矿石": "native:ore", "黄金": "native:gold", "耕地": "native:farmland",
             "石油": "native:oil", "木头": "native:wood"}


def _hash_int(*parts) -> int:
    """稳定整数哈希（blake2b，跨进程/跨 PYTHONHASHSEED 一致）。**不要用内置 hash()**。"""
    blob = ":".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(blob, digest_size=8).digest(), "big")


def _unit(*parts) -> float:
    """[0,1) 的稳定伪随机数。"""
    return (_hash_int(*parts) % _U24) / _U24


# ---------------------------------------------------------------- 低频场
def _value_noise(seed: int, salt: str, x: float, y: float) -> float:
    """格点值噪声（平滑插值），[0,1)。纯函数：只看 (seed, salt, 坐标)。"""
    x0, y0 = math.floor(x), math.floor(y)
    tx, ty = x - x0, y - y0
    sx = tx * tx * (3 - 2 * tx)          # smoothstep：避免网格条纹
    sy = ty * ty * (3 - 2 * ty)

    def g(dx: int, dy: int) -> float:
        return _unit(seed, salt, x0 + dx, y0 + dy)

    a, b, c, d = g(0, 0), g(1, 0), g(0, 1), g(1, 1)
    return (a * (1 - sx) + b * sx) * (1 - sy) + (c * (1 - sx) + d * sx) * sy


def _fbm(seed: int, salt: str, x: int, y: int, octaves: int) -> float:
    """分形噪声：逐层频率翻倍、振幅减半，返回 [0,1)（按最大可能振幅归一）。"""
    total = 0.0
    amp, freq, norm = 1.0, MAPGEN_SCALE, 0.0
    for i in range(octaves):
        total += amp * _value_noise(seed, f"{salt}{i}", x * freq, y * freq)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


# ---------------------------------------------------------------- 蓝噪声
def _blue_rank(seed: int, size: int, salt: str, radius: int) -> list[list[float]]:
    """蓝噪声 rank 掩码：每格 rank ∈ [0,1)，**局部均匀**（摊开、不成块、非规则格）。

    做法：**多趟 dart throwing**（每趟撒一批"互相间距 ≥ radius"的点，撒不下的留给下一趟）：

    - 每趟按 `_hash_int(seed, salt, 趟号, x, y)` 定序遍历，与前批已接受的格比切比雪夫间距，
      够远就接受 ⇒ 接受的这批本身就是一张蓝噪声点集（**局部均匀**）；
    - 趟内接受顺序给 rank，趟与趟之间递减半径（radius → 0），最后一趟收掉所有剩格
      ⇒ 每格都有 rank，且"rank 靠前的那一批"永远是最摊开的那一批。

    ★ **为什么不用"每次挑最稀疏处"的 best-candidate**：那个算法的落点顺序**几乎由几何决定**，
      换个 `salt` 只改了最早几个落点 ⇒ 两张掩码**高度相关**（实测秩相关 0.28）——
      于是"资源掩码"根本不像独立随机，会让按地形落位带来的 rank 偏置漏进资源分布
      （实测：山地·矿石期望被带偏 0.07~0.26）。dart throwing 的接受集合由**盐派生的哈希序**
      主导，换盐就是换一张图 ✓。

    ★ **rank 是倒着发的**：最先接受的一批 rank 最大。因为 `_dither` 把"稀缺档"（地形里的
      沙漠/山地、资源里的高值）放在**大 rank 端**，而"最先接受的一批"正是彼此摊得最开的
      那一批 ⇒ 稀缺档自然落成"局部均匀、不成块也不留孔洞"。
      **发反了会正好搞反**：实测发正时沙漠贴邻比 0.566 → 0.397（聚成挤堆）。
    """
    n = size * size
    rank = [0.0] * n
    done = [False] * n
    order = sorted(range(n), key=lambda i: _hash_int(seed, salt, i // size, i % size))
    placed = 0
    for r in range(radius, -1, -1):
        # 占用网格：接受一格就把"距它 ≤ r"的格标记掉 ⇒ 判定 O(1)
        # （若改成"跟已接受点逐个比距离"，60×60 要 3.9s、100×100 要 31s——别写回去）
        blocked = bytearray(n)
        for i in order:
            if done[i] or blocked[i]:
                continue
            x, y = i % size, i // size
            done[i] = True
            rank[i] = (n - 1 - placed) / n         # ★ 倒着发（见 docstring）
            placed += 1
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < size and 0 <= ny < size:
                        blocked[ny * size + nx] = 1
    return [[rank[y * size + x] for x in range(size)] for y in range(size)]


# ---------------------------------------------------------------- 落位
def _quotas(weights: dict[str, int], total: int) -> dict[str, int]:
    """最大余数法配额：各类取整数个，总和恰好 = total。"""
    tot = sum(weights.values())
    raw = {t: total * w / tot for t, w in weights.items()}
    base = {t: int(v) for t, v in raw.items()}
    rest = total - sum(base.values())
    # 余数大的先补；平局按 main 的内容 ID 排（不能按中文名：'丘陵'<'native:hill' 的字典序与 main 相反，
    # 会让并列时多出的那一格落到另一类地形上——差分实测 8800 格里 8 格因此不同）
    order = sorted(weights, key=lambda t: (-(raw[t] - base[t]), _TERRAIN_ID.get(t, t)))
    for t in order[:rest]:
        base[t] += 1
    return base


def _dither(rank: float, weights: list) -> int:
    """抖动阈值：权重表 `weights`（下标 = 取值）→ 用 rank 反查取值。

    ★ 这是"分布不变"的关键：`P(取值 = i) = weights[i] / Σweights` 与逐格独立抽**完全一致**，
    变的只是"谁拿到高值"——rank 是蓝噪声 ⇒ 高值格在空间上摊开，不再连片也不再留孔洞。
    """
    tot = sum(weights)
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if rank < acc / tot:
            return i
    return len(weights) - 1


def _ramp(v: float, edge: float) -> float:
    """v 越过 edge 后从 0 线性升到 1（过渡带宽 = `MAPGEN_FADE`，越小边界越锐）。"""
    return min(1.0, max(0.0, (v - edge) / max(1e-6, MAPGEN_FADE)))


def _ramp_down(v: float, edge: float) -> float:
    """v 低于 edge 时从 0 线性升到 1。"""
    return min(1.0, max(0.0, (edge - v) / max(1e-6, MAPGEN_FADE)))


def _norm(field: list[list[float]]) -> list[list[float]]:
    """把一整个场线性拉到 [0,1]（按本图 min/max；图是有限的，故确定性）。"""
    flat = [v for row in field for v in row]
    lo, hi = min(flat), max(flat)
    span = hi - lo if hi > lo else 1.0
    return [[(v - lo) / span for v in row] for row in field]


def name_grid(seed: int, size: int) -> list[list[str]]:
    """整张图的地名表：`grid[y][x]`，整池不放回分配，纯 `(seed,size)` 的函数。

    原词库 98×82 共 8036 种组合；按稳定哈希排序等价于一副由种子确定的牌，
    名字本身作为同哈希时的固定次序（不用内置 hash、不依赖 set 遍历顺序）。
    只有超出整个词库的大图才进入第二轮：北川、北川2、北川3……
    """
    pool = sorted(
        {prefix + suffix for prefix in NAME_PREFIX for suffix in NAME_SUFFIX},
        key=lambda name: (_hash_int(seed, "name-order", size, name), name),
    )
    count = len(pool)
    grid = []
    for y in range(size):
        row = []
        for x in range(size):
            cycle, index = divmod(y * size + x, count)
            row.append(pool[index] if cycle == 0 else f"{pool[index]}{cycle + 1}")
        grid.append(row)
    return grid


class MapGen:
    """一张图（地形 + 资源 + 地名）的生成器。`MapGen(seed, size)` 一次算好，之后只读。"""

    def __init__(self, seed: int, size: int):
        self.seed = int(seed)
        self.size = int(size)
        self._terrain: list[list[str]] = [[None] * size for _ in range(size)]   # type: ignore
        self._res: list[list[dict[str, int]]] = [[None] * size for _ in range(size)]  # type: ignore
        self._build()
        self._names = name_grid(self.seed, self.size)
        self._where = {name: (x, y) for y, row in enumerate(self._names)
                       for x, name in enumerate(row)}

    # ---- 对外 ----
    def terrain(self, x: int, y: int) -> str:
        return self._terrain[y][x]

    def name(self, x: int, y: int) -> str:
        return self._names[y][x]

    def where(self, name: str):
        """地名是唯一的，所以反查没有歧义；查不到返回 None。"""
        return self._where.get(name)

    def resources(self, x: int, y: int) -> dict[str, int]:
        return dict(self._res[y][x])      # ★ 返回**副本**：调用方（`_new_tile`）拿到的是
        #   地块自己的 dict，就地改（历史上有过就地扣资源的写法）不能污染整张图的缓存

    def resources_as(self, x: int, y: int, terrain: str) -> dict[str, int]:
        """按**指定地形**的权重现算该格资源（同一张 rank 掩码、同一个 `_dither`）。

        专供"开局强行改地形"这一处：中心格必为平原（见 `World._place_crosses`）。
        资源在 `_build` 里就是**由地形权重抖动出来的**（`TERRAINS[ter][r]`），
        所以改了地形却不重算资源，会留下一格"平原上产石油"的怪物——那既违背
        《地图生成与资源分布》里"沙漠偏油、山地偏矿"的口径，也会让面板自相矛盾。
        """
        return {r: _dither(self._rmask[r][y][x], TERRAINS[terrain][r]) for r in RESOURCES}

    # ---- 内部 ----
    def _build(self) -> None:
        size = self.size
        # ① 低频场：海拔 / 湿度，各自按本图 min-max 归一化
        elev = [[_fbm(self.seed, "e", x, y, MAPGEN_OCT_E) for x in range(size)]
                for y in range(size)]
        moist = [[_fbm(self.seed, "m", x, y, MAPGEN_OCT_M) for x in range(size)]
                 for y in range(size)]
        elev = _norm(elev)
        moist = _norm(moist)

        # ② 蓝噪声 rank：地形一张，**每类资源各一张**（独立；见 ⑤ 的"必须独立"）
        #    ★ 盐必须与 main 的内容 ID 一致（`res:native:ore` 等）——否则同一 (seed,size)
        #      在两边生成出不同的资源图（逐格差分实测：地形/地名等价、资源只有盐不同）。
        mask = _blue_rank(self.seed, size, "terrain", MAPGEN_BLUE_RADIUS)
        self._rmask = {r: _blue_rank(self.seed, size, f"res:{_RES_SALT[r]}", MAPGEN_BLUE_RADIUS)
                       for r in RESOURCES}

        # ③ 类型打分（密度图调制）：各类型对两张场的响应 ∈[0,1]
        nu = [[None] * size for _ in range(size)]          # type: ignore
        for y in range(size):
            for x in range(size):
                e, m = elev[y][x], moist[y][x]
                r_mtn = _ramp(e, MAPGEN_MTN)
                nu[y][x] = {"山地": r_mtn,
                            "丘陵": _ramp(e, MAPGEN_HILL) * (1 - r_mtn),
                            "森林": _ramp(m, MAPGEN_WET),
                            "沙漠": _ramp_down(m, MAPGEN_DRY),
                            "平原": 0.0}                    # 平原是兜底，分在 ④ 现算
        for y in range(size):
            for x in range(size):
                d = nu[y][x]
                d["平原"] = 1.0 - max(d["山地"], d["丘陵"], d["森林"], d["沙漠"])

        # ④ 按分落位：**稀有类型先挑**（山地→沙漠→森林→丘陵→平原兜底），
        #    ★ 无论分多低都取够配额（"最高的那一批"就是该类型）——
        #      核心区（分高）成片、配额内的外围由 rank 抖散到边界上。
        assign = [[None] * size for _ in range(size)]      # type: ignore
        barriers = self._boundaries(elev)
        for (x, y), kind in barriers.items():
            assign[y][x] = kind
        quota = _quotas(TERRAIN_WEIGHTS, size * size - len(barriers))
        types = list(TERRAIN_WEIGHTS)
        cells = [(x, y) for y in range(size) for x in range(size)]
        for t in sorted(types, key=lambda t: TERRAIN_WEIGHTS[t]):     # 最稀的先挑
            if t == "平原":
                continue                                      # 兜底，最后填剩下的
            keyed = []
            for (x, y) in cells:
                if assign[y][x] is None:
                    # 分档 + rank：分档给"区域"，rank 给"同分处均匀铺开"
                    keyed.append((-int(nu[y][x][t] * 100), -mask[y][x], x, y))
            keyed.sort()
            for _, _, x, y in keyed[:quota[t]]:
                assign[y][x] = t
        for (x, y) in cells:                                  # 剩下的全给平原
            if assign[y][x] is None:
                assign[y][x] = "平原"
        self._terrain = [[assign[y][x] for x in range(size)] for y in range(size)]

        # ⑤ 资源：**每类一张独立的蓝噪声掩码**，再按该地形的权重表抖动
        #    ⇒ 每格边缘分布与旧法逐字相同（这是"分布不变"的保证）。
        #    ★ 必须**独立**，不能复用地形那张（哪怕做环形位移）：地形落位本身用了
        #      同一张 rank 作同分排序键，于是"被选为山地的格"系统性偏向高 rank，
        #      复用它会让该地形的资源高值端跟着偏（实测：山地·矿石期望 2.456 vs 2.530）。
        for y in range(size):
            for x in range(size):
                ter = self._terrain[y][x]
                self._res[y][x] = {r: _dither(self._rmask[r][y][x], TERRAINS[ter][r])
                                   for r in RESOURCES}

    def _boundaries(self, elevation):
        """封闭外缘：用海或高山把整圈边界封死，只留一片连通的陆地。

        低频海岸场让内缘起伏（不是给矩形镶边）；海必须连通外海；
        四连通陆地保证路网成立、不会靠对角挤过去。
        """
        size = self.size
        sea, peak = "海洋", "高山"
        field = [[_value_noise(self.seed, 'coast', x * MAPGEN_BOUNDARY_SCALE,
                               y * MAPGEN_BOUNDARY_SCALE) for x in range(size)] for y in range(size)]
        waves = _norm([[_value_noise(self.seed, 'rim', x * .19, y * .19)
                        for x in range(size)] for y in range(size)])
        rim = sorted(field[y][x] for y in range(size) for x in range(size)
                     if min(x, y, size - 1 - x, size - 1 - y) == 0)
        level = rim[len(rim) // 2]
        result = {}
        strength = {}
        outer = set()
        for y in range(size):
            for x in range(size):
                edge = min(x, y, size - 1 - x, size - 1 - y)
                wave = waves[y][x]
                width = max(1.0, size * MAPGEN_BOUNDARY_WIDTH * (.5 + wave))
                low = field[y][x] <= level
                coast = size * MAPGEN_COAST_DEPTH * max(0, (level - field[y][x]) / max(level, 1e-9))
                if size >= 16:
                    half = (size - 1) / 2
                    nx = (x - half) / half + (_value_noise(self.seed, 'land-x', x * .08, y * .08) - .5) * MAPGEN_LAND_WARP
                    ny = (y - half) / half + (_value_noise(self.seed, 'land-y', x * .08, y * .08) - .5) * MAPGEN_LAND_WARP
                    radius = (abs(nx) ** MAPGEN_LAND_ROUNDNESS + abs(ny) ** MAPGEN_LAND_ROUNDNESS) ** (1 / MAPGEN_LAND_ROUNDNESS)
                    limit = MAPGEN_LAND_RADIUS + (wave - .5) * .16
                    border = edge == 0 or radius >= limit
                else:
                    border = edge < max(width, coast) and (low or edge < width)
                if border:
                    result[x, y] = sea if low else peak
                    outer.add((x, y))
                    strength[x, y] = (max(width, coast) - edge) / max(width, coast)
                elif size >= 12 and elevation[y][x] >= MAPGEN_HIGH_MOUNTAIN:
                    result[x, y] = peak
                    strength[x, y] = .2 + 3 * (elevation[y][x] - MAPGEN_HIGH_MOUNTAIN)

        rim_points = {p for p in result if min(*p, size - 1 - p[0], size - 1 - p[1]) == 0}
        budget = max(len(rim_points), int(size * size * MAPGEN_BLOCKED_SHARE))
        extra = sorted((p for p in result if p not in rim_points),
                       key=lambda p: (-strength[p], p))
        # 固定 20% 帽会把普通图挤回矩形围栏；小图保留紧凑外环，大图保留变形大陆轮廓。
        if size < 16:
            for p in extra[max(0, budget - len(rim_points)):]:
                del result[p]
        else:
            # 山脊贴着大陆走，不往图幅矩形角落堆实心块。
            distances = {(x, y): 0 for y in range(size) for x in range(size) if (x, y) not in result}
            queue = sorted(distances)
            for x, y in queue:
                for p in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= p[0] < size and 0 <= p[1] < size and p not in distances:
                        distances[p] = distances[x, y] + 1
                        queue.append(p)
            for p in sorted(outer):
                if min(*p, size - 1 - p[0], size - 1 - p[1]) == 0 or (
                        result[p] == peak and distances.get(p, size) > 2):
                    result[p] = sea

        def component(start, candidates):
            found = {start}
            queue = [start]
            for x, y in queue:
                for p in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if p in candidates and p not in found:
                        found.add(p)
                        queue.append(p)
            return found

        water = {p for p, kind in result.items() if kind == sea}
        while water:
            group = component(min(water), water)
            water.difference_update(group)
            if not any(min(x, y, size - 1 - x, size - 1 - y) == 0 for x, y in group):
                for p in sorted(group):
                    del result[p]
        land = {(x, y) for y in range(size) for x in range(size) if (x, y) not in result}
        groups = []
        while land:
            group = component(min(land), land)
            land.difference_update(group)
            groups.append(group)
        # 删掉孤立的碎地；绝不在外环上开洞（孤陆全封山）。
        if groups:
            largest = max(groups, key=lambda group: (len(group), tuple(sorted(group))))
            for group in groups:
                if group is not largest:
                    for p in sorted(group):
                        result[p] = peak
        return result


if __name__ == "__main__":      # 生成一图 + 计时（`python mapgen.py [size]`）
    import sys
    import time

    for sz in ([int(sys.argv[1])] if len(sys.argv) > 1 else [16, 60, 100]):
        t0 = time.time()
        g = MapGen(20260905, sz)
        dt = time.time() - t0
        cnt: dict[str, int] = {}
        for y in range(sz):
            for x in range(sz):
                cnt[g.terrain(x, y)] = cnt.get(g.terrain(x, y), 0) + 1
        share = "  ".join(f"{t}{100 * cnt.get(t, 0) / (sz * sz):.1f}%" for t in TERRAINS)
        print(f"{sz}×{sz}：{dt:.2f}s   {share}")
