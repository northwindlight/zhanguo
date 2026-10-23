# -*- coding: utf-8 -*-
"""★ **可选依赖的探针** —— 让"没装 torch"的机器**跳过**而不是**报错**。

    为什么需要
    ─────────
    主线的 CI（`.github/workflows/ci.yml`）跑的是**全量** `unittest discover -s tests`，
    依赖只装了 `requirements-vision.txt`（**没有 torch** —— torch 有 450MB+，
    对主线回归是纯负担）。
    ⇒ RL 线那些**要 torch**的测试（模型/训练/联赛）在 CI 上必须**优雅跳过**，
    否则整个 CI 挂掉 —— 而它们**本来就该在装了 torch 的机器上跑**
    （RL 线的开发机就是）。

    ★ **口径**：`skip` 不是"放过"—— 装了 torch 的机器上**照跑**，
    跳过的机器上**明说**"没装 torch"（不是静默）。

    用法：
        from tests.rl_deps import torch  # 或 has_torch()
        @unittest.skipUnless(has_torch(), "需要 torch（RL 线依赖）")
        class TestX(unittest.TestCase): ...
"""
from __future__ import annotations

try:                                     # pragma: no cover - 环境探测
    import torch                          # noqa: F401
    HAS_TORCH = True
    TORCH_ERROR = ""
except Exception as exc:                 # pragma: no cover - 环境探测
    torch = None                          # type: ignore[assignment]
    HAS_TORCH = False
    TORCH_ERROR = str(exc)


def has_torch() -> bool:
    """本机装了 torch 吗（装了才跑 RL 的模型/训练测试）。"""
    return HAS_TORCH


def torch_or_skip() -> str:
    """没装 torch 时给 `unittest.skip` 用的理由。"""
    return f"需要 torch（RL 线依赖）；本机没装：{TORCH_ERROR or 'ModuleNotFoundError'}"


# ================================================================ 造局助手
# ★ 2026-10-22：旧的测试直接在门面的**地块表**上改归属、改建筑
#   （`w.tiles()[cell]["owner"] = ...`）—— 那正是 review §8 说的"影子状态"：
#   改的是**RL 自己那张表**，**引擎不知道** ⇒ 断言的东西与引擎的真值能分叉。
#   ⇒ 这里给一组**只走引擎公开能力**的助手，测试改用它们。
def give_site(env, cell, owner: str | None) -> None:
    """把 `cell` 那一格的**站点**转给 `owner`（连带那里的建筑一起过去）。

    ★ 走 native 的 `territory.transfer`（**公开**能力）。
    ★ "某国的厅没了"在引擎里就是"**那个站点归别人了**"——
      建筑是**站点上的实例**，站点一转，厅自然不在那家了 ✔
      （native 的 `native:succession` 判亡国用的也是"厅还在不在"）。
    """
    site = env.tile(*cell).tile_id
    if not site:
        raise ValueError(f"{cell} 没有站点（未物化）⇒ 转不了")
    env.call("native:territory", "transfer", site,
             env.id_of(owner) if owner else None)


def set_owner(env, cell, owner: str | None) -> None:
    """把 `cell` 的归属改成 `owner`（`None` = 无主）。

    ★ 无主 = **把站点转成无主**（`territory.transfer(site, None)`）——
      引擎里"无主的已物化格"与"未物化的格"都**不产野人**
      （native 的 `visible_units`：物化过的格不合成野人）✔
    """
    x, y = int(cell[0]), int(cell[1])
    site = env.tile(x, y).tile_id
    if not site:
        # ★ 格还没物化 ⇒ 引擎**建格时**才能定归属（`territory.create`）。
        #   （旧门面有自记的表，格子"本来就在"，随便改 ⇒ 引擎那边根本不知道。）
        env.call("native:territory", "create", f"rl:cell-{x}-{y}",
                 env.id_of(owner) if owner else None, x, y)
        return
    env.call("native:territory", "transfer", site, env.id_of(owner) if owner else None)


def set_hall(env, cell, n: int) -> None:
    """把 `cell` 上的**市政厅**设成 `n` 座（0 = 没有，1 = 一座）。

    ★ 走引擎的公开能力：
      · `n >= 1` → `buildings.seed(site, 'native:town-hall', count=n)`
        （`MAX_PER_SITE=1` ⇒ 最多一座）；
      · `n == 0` → **把站点转成无主**（`territory.transfer(site, None)`）——
        厅是**站点上的实例**，站点转走，厅跟着走。
    ★★ **诚实说明**：引擎里"**有地没厅**"这个状态**不可表达** ——
      厅在站点上，站点转走 ⇒ 地也走；地还在 ⇒ 厅就在。
      （旧门面有自记的建筑表，能表达这个状态 ⇒ 那是"引擎里不存在的局面"。）
      所以断言"有地没厅"的测试要按引擎**可达的状态**重写。
    """
    if n <= 0:
        give_site(env, cell, None)
        return
    env.call("native:buildings", "seed", env.tile(*cell).tile_id, "native:town-hall", count=int(n))


def make_site(env, cell, owner: str | None, terrain: str = "native:plain") -> None:
    """把 `cell` **物化**成一块地并归 `owner`（旧测试的 `w.tiles()[c] = {...}`）。

    ★ 走 native 的 `territory.create`（**公开**能力）。
    ★ 站点 id 用 `rl:t-{x}-{y}`（合法内容 ID，与引擎的 `territory.create` 一致）。
    """
    x, y = int(cell[0]), int(cell[1])
    env.call("native:territory", "create", f"rl:t-{x}-{y}",
             env.id_of(owner) if owner else None, x, y, terrain=terrain)


def make_world(size: int = 8, seed: int = 0, nations=("甲", "乙"), starts=None):
    """造一个**按引擎口径**摆好的沙盒世界（取代旧门面的 `SandboxWorld`）。

    ★ 旧 `rl/engine.py` 的 `SandboxWorld` **已删除**（review §8：门面是"垫在算法层
      下面的假旧引擎"）。⇒ 测试要造世界，就走**剧本能力**（`rl:scenario`），
      拿回一个**能力客户端**（`rl/env.py` 的 `Env`）。
    ★ 造局之后要改归属/建筑，用上面那三个助手（**只走引擎公开能力**）。
    """
    from rl.env import Env
    env = Env.load()
    env.setup(size=size, seed=seed, nations=list(nations), territory_target=0,
              armies=0, kind="native:infantry", random_starts=starts is None,
              starts=starts)
    return env


def new_army(env, owner: str, cell=(0, 0), kind: str = "native:infantry", count: int = 1):
    """在 `cell` 造一支军 → `(gid, seq)`（**公开**能力；旧门面的 `_new_army` 已删）。

    ★ 走 native 的 `military.seed`（初始化/导入 ⇒ **本回合可以动**）。
      回合内征召是**另一条** native 路径（`recruit` ⇒ 本回合不能动），
      别用这个函数做补员。
    ★ 返回 `(gid, seq)`：与旧门面的 `_new_army` 同形，
      但 `gid` 是**全局唯一**的（`seq` 是本国序号，跨国会撞）。
    """
    units = env.call("native:army", "seed", env.id_of(owner), kind,
                     int(cell[0]), int(cell[1]), count=int(count))
    unit = units[0]
    return unit.gid, unit.id


def disband_at(env, owner: str, cell) -> None:
    """把 `owner` 在 `cell` 上的军**真删**（旧门面是直接从列表里剔掉）。"""
    for unit in env.armies():
        if unit.owner == env.id_of(owner) and (unit.x, unit.y) == tuple(cell) and unit.alive:
            env.call("native:army", "disband", unit.owner, unit.id)


def is_materialized(env, cell) -> bool:
    """这一格**物化**了吗（引擎的 `territory` 里有没有它）？

    ★ 引擎的野人是**现场合成**的：条件是"**没物化** + 没驻过军 + 可见"
      ⇒ "有野人驻守" = **没物化**；"失地" = **物化了但无主**。
      （旧门面有自记的野人表，能造出"物化过、又清掉野人"这种状态；
        引擎里那是不存在的局面。）
    """
    return bool(env.tile(int(cell[0]), int(cell[1])).tile_id)


def unmaterialized_cells(env) -> list:
    """所有**没物化**的格（引擎会在可见的那些上现场合成野人）。"""
    size = env.size()
    return [(x, y) for x in range(size) for y in range(size)
            if not is_materialized(env, (x, y))]
