# -*- coding: utf-8 -*-
"""观测 → **token 窗口**。P2：只出 token 张量 + mask，不接任何网络。

设计见 `rl/TOKEN_DESIGN.md`。这一层的职责边界很窄，别越界：

- **它不做特征工程。** 地块通道、全局向量、候选特征全部来自 `env._obs()` ——
  那套东西已经被 `test_branch_behavior_parity` 盯着，别在这里造第二份。
  tokenizer 只做一件事：**把已经算好的观测，重新组织成「一组 token」。**
- **它不出模型。** 每个组的原始特征宽度由本文件定死；投影到 `d_model` 是 P4 的事。
- **它必须是纯函数**：同一个 `(env, obs)` 调两次，逐位相等（`tests/test_tokenize.py`）。

窗口布局（预算 512，实际用 321）：见 `GROUPS` 与 `CAP`。

    组   上限   现在
    g     1     1       全局：库存/价格/回合/电力/领地/建筑/军队 + 外交预留 8
                        ★累计消费那三栏**停供**（置零，宽度保留）——见 tokenize() 里的注释
    m    64     9~64    地图 patch（8×8 网格，逐通道块内均值）
    a   192     0~n     军队（自家在前，视野内的他国/野人在后）
    n     8     0       势力（**外交预留**，现在永远 mask 掉）
    e    32     0       事件（**外交预留**）
    r     8     0       记忆（每回合池化 2 个 × 近 4 回合；P4 才填）
    k    16     0       关键节点（现在不设）

★**两处对 `TOKEN_DESIGN.md` §1 的有意偏离**，都是为了「宽度固定」：

1. **M 组不用自适应 patch 边长 `p ∈ {2,4,8,16}`，改成固定 8×8 = 64 格、块边长自适应。**
   原因：`C×p×p` 拍平后**宽度随 p 变**，同一次训练里不同帧的 token 宽度就不一样，
   没法拼批（和现在网格要 padding 到批内最大是同一个病），而且 P4 得为每个 p 备一个
   `Linear`。改成「块内逐通道取均值」后宽度恒为 `C+3`，与地图/帝国大小无关，
   分辨率等价（都是"最多 64 格铺满外接框"）。
   ★**代价：块内布局丢了**（2026-09-12 审阅指出）。块边长随外接框自适应 ——
   小帝国时块是 1×1（无损），**帝国越大丢得越多**（外接框 40×40 时一块 5×5＝25 格取均值，
   哪一格有资源、哪一格有敌人全平掉了）。而候选只带自己的落点，没有"往块里看"的能力。
   若 BC 匹配率不够，两个便宜的补法（都不用改形状）：
   ① 每通道再带一个 **max**（`[mean, max]`，宽度 C→2C，token 数不变）；
   ② 加大 `M_SIDE`（更多更小的块，但 M 组上限 64 是硬预算）。
2. **A 组特征宽度 12（见 `F_A`），不用 `TOKEN_DESIGN` 里那条 13 维的写法** ——
   "情报年龄"那一维现在恒为 0：`World` **没有"这块地什么时候探明的"记录**
   （`mp.py` 里只有事件日志 `history`，没有逐格探明时间戳）。
   与其填一个假的 0 当特征，不如先不设——等补了逐格探明时间再加。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from game import unit_kind, unit_max_hp

from rl.env import Obs, ZhanguoEnv
from rl.vocab import NATION_SLOTS, POS_SCALE, TOKEN_BUDGET

# 兵种 one-hot 的顺序：取**引擎的** UNIT_TYPES，不自己写死 —— 写死的那份
# 一旦引擎加了兵种就会静默错位（`rl/vocab.py` 的 `UNIT` 是给候选子项用的冻结表，
# 两者含义不同，别混）。
from game import UNIT_TYPES as UNIT_KINDS3

# --------------------------------------------------------------------------
# 窗口布局
# --------------------------------------------------------------------------
GROUPS = ("g", "m", "a", "n", "e", "r", "k")

CAP = {"g": 1, "m": 64, "a": 192, "n": NATION_SLOTS, "e": 32, "r": 8, "k": 16}

M_SIDE = 8                      # M 组固定 8×8 = 64 格
E_MAX, R_MAX, K_MAX = CAP["e"], CAP["r"], CAP["k"]

# 每组的**原始**特征宽度（投影到 d_model 是 P4 的事）。都是模块级常量，
# 不许在运行时按局面算——宽度一变，ckpt 全废。
F_DIPLO_RESERVED = 8            # G 组里给外交留的位（关系/盟/待议…）
F_A = 3 + 3 + 1 + 2 + 1 + 1 + 1     # 归属3 兵种3 hp 坐标2 交战 已动 情报年龄
F_N = 16                        # 势力（外交预留）
F_E = 12                        # 事件（外交预留）
F_R = 16                        # 记忆
F_K = 8                         # 关键节点

OWNER_SELF, OWNER_FOE, OWNER_BARB = 0, 1, 2


# --------------------------------------------------------------------------
@dataclass
class Window:
    """一组 token + 各自的 mask。`feats[g]` 形状 `(n_g, F_g)`，`mask[g]` 是 `(n_g,)`。

    mask=False 的位置**特征全 0**（本文件的约定，`flat()` 和测试都依赖它）。
    """
    feats: dict[str, np.ndarray]
    mask: dict[str, np.ndarray]
    meta: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.feats[g].shape[0] for g in GROUPS)

    @property
    def live(self) -> int:
        return sum(int(self.mask[g].sum()) for g in GROUPS)

    def flat(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """拼成一整个序列：`(tokens [T,F_max], group_id [T], mask [T])`。

        宽度按 `F_max` 右补 0 —— 各组的原始宽度不同，这一步只是为了让 P3
        能先把窗口池化起来（`窗口 → 池化 → 原有点积头`）。
        **P4 不该用这个**：它应该按 `group_id` 走各自的编码器，别把 12 维的军队
        和 56 维的全局补到同一个宽度再当同质 token 用。
        """
        fmax = max(self.feats[g].shape[1] for g in GROUPS)
        toks, gid, msk = [], [], []
        for gi, g in enumerate(GROUPS):
            f = self.feats[g]
            pad = np.zeros((f.shape[0], fmax - f.shape[1]), np.float32)
            toks.append(np.concatenate([f, pad], axis=1) if pad.shape[1] else f)
            gid.append(np.full(f.shape[0], gi, np.int64))
            msk.append(self.mask[g])
        return (np.concatenate(toks, 0).astype(np.float32),
                np.concatenate(gid), np.concatenate(msk))


def _zeros(n: int, f: int) -> np.ndarray:
    return np.zeros((n, f), np.float32)


def _patch_group(grid: np.ndarray, vis_ch: int, x0: int, y0: int,
                 anchor: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, dict]:
    """地图 → ≤64 个 patch token。

    `grid` 是 `_obs()` 给的外接框网格 `[C,H,W]`，**已经按视野门控过**（视野外通道为 0），
    所以这里不需要再调 `visible_to`。

    每个 patch 的特征 = 块内逐通道均值 `C` 维 + 块中心的相对家偏移 `2` 维
    + 块内可见占比 `1` 维。
    """
    c, h, w = grid.shape
    ph = max(1, math.ceil(h / M_SIDE))          # 块高（自适应）
    pw = max(1, math.ceil(w / M_SIDE))
    ni = math.ceil(h / ph)
    nj = math.ceil(w / pw)
    n = ni * nj

    out = _zeros(CAP["m"], c + 3)
    msk = np.zeros(CAP["m"], bool)
    ax, ay = anchor
    vis = grid[vis_ch]
    for i in range(ni):
        for j in range(nj):
            k = i * nj + j
            bx, by = i * ph, j * pw
            blk = grid[:, bx:bx + ph, by:by + pw]
            out[k, :c] = blk.mean(axis=(1, 2))
            # 块中心的**相对家**偏移。绝对坐标永远不进特征：
            # 智能体不知道地图多大、也从未探索过边界（用户 2026-09-11 口径）。
            out[k, c] = (x0 + bx + (ph - 1) / 2.0 - ax) / POS_SCALE
            out[k, c + 1] = (y0 + by + (pw - 1) / 2.0 - ay) / POS_SCALE
            out[k, c + 2] = float(vis[bx:bx + ph, by:by + pw].mean())
            msk[k] = True
    return out, msk, {"patch_hw": (ph, pw), "patch_grid": (ni, nj)}


def _army_group(world, agent: str, anchor: tuple[int, int], turn: int,
                own: list[dict]) -> tuple[np.ndarray, np.ndarray, dict]:
    """军队 token：**自家在前**（顺序必须与 `env._refresh_armies` 一致），
    视野内的他国/野人在后。

    为什么自家必须在前且同序：候选动作的 `army_idx` 索引的就是那个顺序
    （`env._cand_pack` 用 `env.army_index`）。P4 的候选 cross-attention 要拿
    `army_idx` 直接指到 A 组的 token 上，顺序错了就指错兵。
    """
    rows: list[np.ndarray] = []
    ax, ay = anchor
    own_ids = set()
    for a in own:
        own_ids.add(a["id"])
        rows.append(_army_row(a, OWNER_SELF, ax, ay, turn))
    # 视野内的他国与野人（按 id 排序，保证确定性）
    foes = [a for a in world.armies
            if a["hp"] > 0 and a["id"] not in own_ids
            and world.visible_to(agent, a["x"], a["y"])]
    for a in sorted(foes, key=lambda z: z["id"]):
        owner = OWNER_BARB if a["owner"] == "野人" else OWNER_FOE
        rows.append(_army_row(a, owner, ax, ay, turn))

    n_own = len(own)
    out = _zeros(CAP["a"], F_A)
    msk = np.zeros(CAP["a"], bool)
    n = min(len(rows), CAP["a"])
    for i in range(n):
        out[i] = rows[i]
        msk[i] = True
    return out, msk, {"n_own_armies": n_own,
                      "n_armies_truncated": max(0, len(rows) - CAP["a"])}


def _army_row(a: dict, owner: int, ax: int, ay: int, turn: int) -> np.ndarray:
    r = np.zeros(F_A, np.float32)
    r[owner] = 1.0                                   # 归属 3
    k = unit_kind(a)
    for j, uk in enumerate(UNIT_KINDS3):
        r[3 + j] = 1.0 if k == uk else 0.0            # 兵种 3
    r[6] = a["hp"] / max(1, unit_max_hp(a))           # 血量
    r[7] = (a["x"] - ax) / POS_SCALE                  # 相对家的偏移
    r[8] = (a["y"] - ay) / POS_SCALE
    r[9] = 1.0 if a.get("engaged") else 0.0
    r[10] = 1.0 if a.get("moved_turn") == turn else 0.0
    # r[11] 情报年龄 —— 恒 0。`World` 没有「这块地何时探明」的记录，
    # 所以只有**当前可见**的军队才进得来，"几回合前见过"表达不了。
    # 详见模块 docstring 的偏离说明 2。
    return r


def tokenize(env: ZhanguoEnv, obs: Obs, *, mem: np.ndarray | None = None
             ) -> Window:
    """`(env, obs)` → `Window`。**纯函数**：不改 env/world，同输入同输出。

    `mem`：R 组的原始特征 `(R_MAX, F_R)`（每回合池化 2 个 × 近 4 回合）。
    记忆的**产生**是 P4 的事（要维护跨回合缓冲），这里只负责把槽位摆好：
    传了就用，没传就全 mask 掉。
    """
    if getattr(env, "_bbox", None) is None:
        raise RuntimeError("请先调 env._obs() —— tokenize 要用它裁出来的外接框原点")
    x0, y0, _x1, _y1 = env._bbox
    anchor = env.anchor
    ch_names = env.obs_channels()
    vis_ch = ch_names.index("visible")

    # 自家军队：**自己按 id 排序，不调 `env._refresh_armies()`** —— 那会写
    # `env.army_index`，纯函数就没了。排序口径与它一致（都按 `a["id"]`），
    # 并用候选表长度对账，一旦哪天口径改了会在这里**响亮地**炸掉，而不是悄悄错位。
    world, me = env.world, env.agent
    own = sorted(world.nation_armies(me), key=lambda a: a["id"])
    assert len(own) == obs.cand["army_feats"].shape[0], (
        f"A 组军队数与候选的 army_feats 对不上：{len(own)} vs "
        f"{obs.cand['army_feats'].shape[0]} —— 候选的 army_idx 会指错 token")
    # ★只查数量不够，**顺序**才是 `army_idx` 指的准不准的关键。两边都按 `a["id"]`
    #   排序，所以逐位相等的检查在这里是免费的：`env.army_ids` 由 `_obs()` 里的
    #   `_refresh_armies()` 写下（读它不写它，纯函数性不受影响）。
    #   数量对、顺序错，是最难查的一种 —— 候选会稳定地指向**另一支**军队。
    _eids = getattr(env, "army_ids", None)
    if _eids is not None:
        assert list(_eids) == [a["id"] for a in own], (
            f"A 组军队顺序与 env.army_ids 不一致：{_eids} vs {[a['id'] for a in own]}")
    del _eids
    assert len(UNIT_KINDS3) == 3, f"UNIT_TYPES 不是 3 个：{UNIT_KINDS3}"

    m_feat, m_msk, m_meta = _patch_group(obs.grid, vis_ch, x0, y0, anchor)
    a_feat, a_msk, a_meta = _army_group(world, me, anchor, world.turn, own)

    # ★G 组**停供累计消费**（2026-09-12 用户口径）。理由两条：
    #   ① 它是 reward（`spend_total`）的原函数，喂进观测等于把成绩单放进状态；
    #   ② LLM 玩家在 **main 的面板上看不到它**（`main:mp_ai.py` 的 Observer 面板是
    #      「国库/储备/国土/军队/关系/通信」），而契约第 2 条要求"每个 token 组都必须
    #      能在 main 的面板里找到对应项" —— 这三栏是唯一找不到的。
    #   **形状保留**（宽度照旧），只是不再提供信号：那几维的权重自然变成死权重，
    #   旧 ckpt 不废、将来要加回来也不用改形状。
    #   取维**按名字不按下标**：写死 19/20/21 正是本仓库反复出事的那个模式。
    g_raw = np.asarray(obs.glob, np.float32).copy()
    for _i, _nm in enumerate(env.glob_channels()):
        if _nm.startswith("spend:"):
            g_raw[_i] = 0.0
    g = np.concatenate([g_raw, np.zeros(F_DIPLO_RESERVED, np.float32)])
    feats = {
        "g": g[None, :].astype(np.float32),
        "m": m_feat,
        "a": a_feat,
        "n": _zeros(CAP["n"], F_N),       # 外交预留
        "e": _zeros(CAP["e"], F_E),       # 外交预留
        "r": (np.asarray(mem, np.float32) if mem is not None
              else _zeros(CAP["r"], F_R)),
        "k": _zeros(CAP["k"], F_K),
    }
    mask = {
        "g": np.ones(1, bool),
        "m": m_msk, "a": a_msk,
        "n": np.zeros(CAP["n"], bool),    # 外交预留：现在永远不亮
        "e": np.zeros(CAP["e"], bool),
        "r": (np.ones(CAP["r"], bool) if mem is not None
              else np.zeros(CAP["r"], bool)),
        "k": np.zeros(CAP["k"], bool),
    }
    meta = {"bbox": (x0, y0), "anchor": anchor, "agent": me,
            "turn": world.turn, **m_meta, **a_meta}
    return Window(feats=feats, mask=mask, meta=meta)


def assert_budget(win: Window, budget: int = TOKEN_BUDGET) -> None:
    """总 token 数不许超预算。**按上限算，不按实际用量** ——
    上限才是显存的约束（mask 掉的位置照样占张量）。"""
    hard = sum(CAP[g] for g in GROUPS)
    assert hard <= budget, f"窗口上限 {hard} 超预算 {budget}"
    assert win.total <= hard, f"实际 token 数 {win.total} 超过上限 {hard}"
