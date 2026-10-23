"""RL 线的**声明式 Mod 包**。

★ 这里面没有"把引擎包成旧 `world.*`"的门面（那种东西已按 review §8 全部删除）。
  每个 mod 都是引擎的原生 `GameMod`：能力写进 `mod.toml`，对象由 `objects(world)` 提供，
  规则只通过**声明过的**接口调用。
"""
