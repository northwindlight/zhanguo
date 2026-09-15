# -*- coding: utf-8 -*-
"""规则 AI 注册表：**版本名 → 行为函数**（无 key 国家的代打 / RL 的老师）。

## 为什么不写死版本

`dummy_turn` 曾经 `from expand_rule_v9 import expand_rule_turn_v9` —— 版本号**长在代码结构里**：
换基线要改引擎、改文档、改测试；而"哪一版当基线"是**配置**，不是结构。
现在一律走名字解析，代码里不出现版本号：

    mp_config.json 顶层   "rule_ai": "v10"      # 所有无 key 的国家
    nations[] 里某一国    "rule_ai": "v9"       # 逐国覆盖（对照实验用）
    mp_config.json 不写   → DEFAULT_RULE_AI

命令行等价物：`--config` 里的这一项即可（没有单独的 CLI 开关，免得两处口径）。

## 加一个新版本

写 `expand_rule_vNN.py`（入口函数 `expand_rule_turn_vNN`，签名与既有一致：
`(world, name, rng=None, max_actions=40, on_action=None, on_result=None) -> list`），
然后在下面 `_SOURCES` 加一行。**不用改 dummy_turn / mp_run / README 的代码路径**
（README 的配置表里那一行是给人看的）。

## 历代表

| 版本 | 是什么 | 状态 |
|---|---|---|
| `v11` | v10 的**经济段原样复用** + 军事四件套换成独立模块（`pathfind`/`targeting`/`combat`/`formation`：地形代价寻路、先验估值、逐轮模拟评难度、先目标后分兵） | **可选**（配置写 `"rule_ai": "v11"`）；缺省仍是 v10 |
| `v10` | v9 + **抗抖**（引擎数值现读/现算）+ 不绕山地（只看打不打得赢） | **缺省**，也是 RL 线的 BC 老师基线 |
| `v9` | v8 + 视野门控（信息集 = 引擎给玩家的 `visible_to`） | 历代基线 |
| `v6` | `experiments/` 探针的**论文基线**（成绩出自它，别混用） | 历代基线 |
| `v5` / `v4` / `ai` | 更早的扩张流 | 历代基线 |

★ 换版本要**重量基线**：各版成绩不可混用（v8 之前有偷看、v10 改了山地与征召口径、
v11 改了补给口径与空目标的出兵下限）。
"""
from __future__ import annotations

import importlib
from typing import Callable

# 配置没写 `rule_ai` 时用它 —— 换基线**只改这一行**
DEFAULT_RULE_AI = "v10"

# 版本名 → (模块, 入口函数)。**懒加载**：只 import 真正用到的那一版
# （各版差异是策略差异，互相不依赖）。
_SOURCES: dict[str, tuple[str, str]] = {
    "v11": ("ruleai.v11", "expand_rule_turn_v11"),   # 分层实现，见 ruleai/
    "v10": ("expand_rule_v10", "expand_rule_turn_v10"),
    "v9": ("expand_rule_v9", "expand_rule_turn_v9"),
    "v6": ("expand_rule_v6", "expand_rule_turn_v6"),
    "v5": ("expand_rule_v5", "expand_rule_turn_v5"),
    "v4": ("expand_rule_v4", "expand_rule_turn_v4"),
    "ai": ("expand_rule_ai", "expand_rule_turn"),
}


def versions() -> tuple[str, ...]:
    """全部可用版本名（注册表里的顺序：新 → 旧）。"""
    return tuple(_SOURCES)


def resolve(name: str | None = None) -> tuple[str, Callable]:
    """把版本名解析成 `(版本名, 入口函数)`；`None`/空 → `DEFAULT_RULE_AI`。

    未知版本**当场报错并列出可用值**，不静默退回缺省 —— 配置写错了要立刻知道
    （静默退回 = 实验记录里的"v9"其实是 v10，这种账最难查）。
    """
    ver = str(name or "").strip().lower() or DEFAULT_RULE_AI   # 空白 = 没写 = 缺省
    if ver not in _SOURCES:
        raise ValueError(f"未知的规则 AI 版本：{name!r}（可用：{'/'.join(_SOURCES)}；"
                         f"缺省 {DEFAULT_RULE_AI}）")
    mod_name, fn_name = _SOURCES[ver]
    fn = getattr(importlib.import_module(mod_name), fn_name)
    return ver, fn