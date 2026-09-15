# -*- coding: utf-8 -*-
"""v11：**经济层与军事层分离**的规则 AI（设计底稿 `docs/v11编组模型.md`）。

    v11/
      __init__.py   你在这里：分层说明 + 入口转口
      entry.py      `expand_rule_turn_v11` = 经济层 + 军事层（四行胶水）
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

v10 **一个字节没动**（它仍是缺省基线、也是 RL 线的 BC 老师）；`DEFAULT_RULE_AI`
还是 `v10`。要用 v11 得在配置里写 `"rule_ai": "v11"`。

## 给 RL 线的接口

- **入口**：`ruleai.v11.expand_rule_turn_v11`（签名与各代一致）。
- **`set_horizon(n)`**：改经济层的评估窗口。★ 别用 `mod.HORIZON = n` 那种写法 ——
  那个值住在 `economy.py` 里，直接给本模块赋值**不会**传到经济层（v10 当年就踩过
  "老师被设了窗口、自己还按缺省算"的坑）。所以这里给的是函数。
- **编组状态是模块内存**（`grouping._STATE`，见底稿 §七）：RL **每局开始必须
  `ruleai.v11.grouping.clear()`**，否则上一局的编组会漏进新局。
"""
from __future__ import annotations

from . import economy, grouping, military       # noqa: F401  转口：调用方按需取用
from .entry import expand_rule_turn_v11         # noqa: F401  注册表指向它

__all__ = ["expand_rule_turn_v11", "set_horizon", "HORIZON", "economy", "military",
           "grouping"]


def set_horizon(turns: int) -> None:
    """设经济层的评估窗口（RL 的 `set_horizon` 口径；见模块说明）。"""
    economy.HORIZON = int(turns)


def __getattr__(name: str):                     # noqa: D105
    """`HORIZON` **读时转口**到经济层：拿到的是当下的值，不是 import 那一刻的快照。"""
    if name == "HORIZON":
        return economy.HORIZON
    raise AttributeError(name)