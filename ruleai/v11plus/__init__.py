# -*- coding: utf-8 -*-
"""v11plus：**v11 的现役继承者** —— 经济层与军事层分离（设计底稿 `docs/v11编组模型.md`）。

    v11plus/
      __init__.py   你在这里：分层说明 + 入口转口
      entry.py      `expand_rule_turn_v11plus` = 经济层 + 军事层（四行胶水）
      ledger.py     两层共享的**动作账本**（唯一公共接口：world + Ledger + 额度）
      economy.py    经济层：钱/料/建造/征兵 —— **产兵权在这里**
      military.py   军事层：编组 → 判定式 → 寻路 → 出手
      grouping.py   军事层部件：编组状态机 + 全局求解器
      combat.py     军事层部件：难度判定式（直接调引擎伤害公式）
      pathfind.py   军事层部件：视野掩码 + 代价场 + 落点
      targeting.py  军事层部件：候选池

## 唯一要守的纪律：两层不许互相 import

经济层不知道编组怎么编，军事层不知道钱怎么花 —— 这样换掉其中一层（比如把经济换成
更聪明的预算分配）另一层一行都不用动。越界是**渐进**的：先在军事层里读一下国库
（"就一行"），再在编组里调一次 `recruit`（"顺手"），两次之后就分不开了。
`tests/test_ruleai_layers.py` 钉着这条。

两层共享的只有三样：`world`、`Ledger`、以及 `balance.py` 里的数值。

## 与 v10 的关系

v10 **一个字节没动**（它仍是缺省基线）；`DEFAULT_RULE_AI` 还是 `v10`。
要用这支得在配置里写 `"rule_ai": "v11plus"`。

## 与 v11 的关系（2026-09-16 定）

**`v11` 已冻结**（停在 `04a958d` 那一版：军事段带"每回合最多 4 场进攻"的闸、
寻路是逐场重建的 `cost_field`）—— 历史成绩继续归 v11，不再动它。
**`v11plus` = v11 整包副本 + 那次之后的全部修**，是本线的现役版本：

  · **引擎侧**（`mp.py`，2026-09-16 提交）：加 `troops` / `guardians` 两张派生表，
    寻路与战斗评估不再为"滤掉/找出野人"而扫全图（`_defs_at` 一项原占整局 16%）；
  · **寻路**：代价场换快版 `_dijkstra`（桶队列替堆）+ `enemy_cells` 只扫国家军队；
  · **军事**：删掉"每回合最多 4 场进攻"的闸（用户 2026-09-15：「v11 的操作限制删掉」）。

★ 实测（40x40、seed 0、单国 200 回合）：v11 22.8s → **v11plus 9.4s**；
  三国开战 100 回合 16.2s → **4.8s**，且**动作逐条相同**（`tests/test_pathfind.py`
  与 `tests/test_troops.py` 钉着等价性）。

## 给 RL 线的接口

- **入口**：`ruleai.v11plus.expand_rule_turn_v11plus`（签名与各代一致）。
- **没有视野旋钮**（用户 2026-09-15：「v10 起，不设默认视野，恒等于回合数加 20」）：
  规划窗口 = `world.max_turns + 20`，跑局的人把**本局总回合数**放进 world 即可。
  ★原先这里有 `set_horizon(n)` / `HORIZON` 转口；**已删** —— 那个接口本身就是坑：
  值住在 `economy.py`，从外面 `mod.HORIZON = n` 只会给入口模块造个新变量，
  经济层读不到（2026-09-15 实测：v11/v12 因此全程按 200 规划）。
  **能被设错的旋钮，不如没有旋钮。**
- **编组状态是模块内存**（`grouping._STATE`，见底稿 §七）：RL **每局开始必须
  `ruleai.v11plus.grouping.clear()`**，否则上一局的编组会漏进新局。
"""
from __future__ import annotations

from . import economy, grouping, military       # noqa: F401  转口：调用方按需取用
from .entry import expand_rule_turn_v11plus     # noqa: F401  注册表指向它

__all__ = ["expand_rule_turn_v11plus", "economy", "military", "grouping"]