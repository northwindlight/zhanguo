"""地图算法的声明式数值。地形与资源一律使用内容 ID，不再维护中文映射副本。"""
RESOURCES = ["native:ore", "native:gold", "native:farmland", "native:oil", "native:wood"]

TERRAINS = {
    "native:plain": {  # 沃土：耕地/木头为主，几乎无矿无油
        "native:ore": [80, 16, 3, 1, 0, 0],
        "native:gold": [96, 4, 0],
        "native:farmland": [6, 26, 32, 21, 11, 4],
        "native:oil": [92, 7, 1, 0],
        # 2026-10-16：扩大木料覆盖，只将零档权重移至1木，已有高档权重不变。
        "native:wood": [20, 47, 21, 9, 3, 0],
    },
    "native:forest": {  # 木头宝地，兼有耕地
        # 以下为2026-09-26历史说明；当前版本按Mod依赖严格校验，不迁移旧图。
        # ★ 2026-09-26 用户「森林资源生成逻辑改一下必有 1 木」：木头档位第一档由 5% 归零，
        #   那 5 个百分点挪进 x1（`[5,15,…]` → `[0,20,…]`，总和仍 100）。
        #   动机：**森林是木头的招牌地形**（期望 2.55，全地形最高），却有 5% 的森林一格木头都没有
        #   ——"走到林子里没木头"是反直觉的陷阱，也让"森林=木料来源"这条心智模型漏气。
        #   改后森林木头期望 2.55 → **2.60**，全图每格期望 1.3720 → **1.3845**（+0.9%）。
        # ⚠ 这一条改的是**地图本身**（`MapGen(seed, size)` 的产物）：资源本来就与地形
        #     **同源同图**（同一支纯函数一次算全图），所以同一版本内不存在任何接缝。
        #     唯一的接缝来自**换代码**——同一个 `(seed, size)` 在新旧代码下是两张不同的图，
        #     而老档里**已物化**的格子是字面量存着的（可能留着 0 木的森林）。
        #     影响面：只有"旧代码写的档 + 新代码续玩"才看得到，差别也就是那 1 格木头、
        #     地形不变 ⇒ **不必 bump SAVE_VERSION**（那会把旧档一并作废，代价远大于收益）。
        #     现存三个档都不会碰到：新生成的 24 图档已重生成、184 终局档与 5 国档都已结算归档。
        "native:ore": [74, 19, 6, 1, 0, 0],
        "native:gold": [95, 5, 0],
        "native:farmland": [28, 30, 26, 11, 4, 1],
        "native:oil": [95, 4, 1, 0],
        "native:wood": [0, 20, 29, 28, 17, 6],
    },
    "native:hill": {  # 全能有，样样不拔尖，略出黄金
        "native:ore": [18, 28, 25, 17, 8, 4],
        "native:gold": [88, 11, 1],
        "native:farmland": [32, 30, 24, 10, 3, 1],
        "native:oil": [80, 15, 4, 1],
        "native:wood": [15, 38, 25, 14, 6, 2],
    },
    "native:mountain": {  # 矿石/黄金富集，粮木贫
        "native:ore": [4, 18, 29, 26, 16, 7],
        "native:gold": [76, 20, 4],
        "native:farmland": [72, 20, 6, 2, 0, 0],
        "native:oil": [74, 20, 5, 1],
        "native:wood": [45, 41, 11, 3, 0, 0],
    },
    "native:desert": {  # 石油宝地，其余皆贫
        "native:ore": [68, 21, 8, 3, 0, 0],
        "native:gold": [89, 10, 1],
        "native:farmland": [90, 7, 2, 1, 0, 0],
        "native:oil": [12, 26, 28, 26, 8],
        # 2026-10-16：沙漠仍以缺木为主，有木比例由30%收回至10%，仅供后续新档。
        "native:wood": [90, 9, 1, 0, 0, 0],
    },
}

TERRAIN_WEIGHTS = {"native:plain": 30, "native:forest": 25, "native:hill": 20, "native:mountain": 15, "native:desert": 10}

# Natural barriers carry no exploitable resource capacity. The five existing
# terrain weights describe usable land, after the boundary mask is removed.
for _terrain in ('native:high-mountain', 'native:ocean'):
    TERRAINS[_terrain] = {resource: [100] for resource in RESOURCES}

MAPGEN_HIGH_MOUNTAIN = 0.86
MAPGEN_BOUNDARY_SCALE = 0.07
MAPGEN_BOUNDARY_WIDTH = 0.055
MAPGEN_COAST_DEPTH = 0.10
MAPGEN_BLOCKED_SHARE = 0.20

MAPGEN_SCALE = 0.22

MAPGEN_OCT_E = 4

MAPGEN_OCT_M = 3

MAPGEN_MTN = 0.80

MAPGEN_HILL = 0.52

MAPGEN_DRY = 0.32

MAPGEN_WET = 0.68

MAPGEN_FADE = 0.12

MAPGEN_BLUE_RADIUS = 2

MAPGEN_SHIFT_MIX = 7
VALUES = {k:v for k,v in globals().copy().items() if k.isupper()}
