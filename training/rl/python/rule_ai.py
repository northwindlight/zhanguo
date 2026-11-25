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

写 `ruleai/vNN.py`（入口函数 `expand_rule_turn_vNN`，签名与既有一致：
`(world, name, rng=None, max_actions=40, on_action=None, on_result=None) -> list`），
然后在下面 `_SOURCES` 加一行。**不用改 dummy_turn / mp_run / README 的代码路径**
（README 的配置表里那一行是给人看的）。

★ 家谱与纪律写在 `ruleai/__init__.py`（一代一个文件、互不 import、
  `v11` 与 `v11plus` 是仅有的两个带子目录的——它们分经济层与军事层）。

## 历代表

| 版本 | 是什么 | 状态 |
|---|---|---|
| `v12` | **v11 + 拿掉 4 处 `int()` 截断**（`int(1.60)=1` 把木价看低 37% ⇒ 建筑预算算错 ⇒ 不排楼）：按 **v11** 迭代，**不含** v11plus 那批提速修 | 已注册（**不是缺省**，也不是现役——现役仍是 `v11plus`） |
| `v11plus` | **v11 的现役继承者**：v11 整包 + 2026-09-16 那批修（引擎两张派生表、寻路换桶队列、删掉每回合 4 场进攻的闸）—— 且**动作逐条不变** | **现役**（配置写 `"rule_ai": "v11plus"`）；缺省仍是 v10 |
| `v11` | **经济层与军事层分离**：经济段照抄 v10，军事段换成**全局编组**（每支军认领一个目标、人数由判定式算、只在目标消失时重编） | **已冻结**（停在 `04a958d`，历史成绩仍归它；现役见 `v11plus`） |
| `v10` | v9 + **抗抖**（引擎数值现读/现算）+ 不绕山地（只看打不打得赢） | **缺省**，也是 RL 线的 BC 老师基线 |
| `v9` | v8 + 视野门控（信息集 = 引擎给玩家的 `visible_to`） | 历代基线 |
| `v8` | v7 的重写：**一张账 + 串行判定**（四套互不知道的账合成一套） | 历代基线 |
| `v7` | ⚠️ **反面教材，勿用**（同一批资源上叠四套账，兵营永远攒不到）—— 自带 best_build，仅供对照 | 耻辱柱 |
| `v6` | `experiments/` 探针的**论文基线**（成绩出自它，别混用） | 历代基线 |
| `v5` / `v4` / `ai` | 更早的扩张流 | 历代基线 |

★ `v1`/`v2`/`v3` 已遗失（从未入库或只在旧历史里）—— 家谱从 v4 起。

★ 换版本要**重量基线**：各版成绩不可混用（v8 之前有偷看、v10 改了山地与征召口径、
v11 改了补给口径与空目标的出兵下限）。
★ `v11plus` 与 `v11` **行为相同、只是快**（40x40 单国 200 回合 22.8s → 9.4s，
  三国开战 100 回合 16.2s → 4.8s；动作序列逐条相同，见 `tests/test_pathfind.py`）——
  所以历史那批 v11 的成绩可以直接当 v11plus 的基线，不用重量。
"""
from __future__ import annotations

import importlib
from typing import Callable

# 配置没写 `rule_ai` 时用它 —— 换基线**只改这一行**
DEFAULT_RULE_AI = "v10"

# ★动作额度**无上限**（用户 2026-09-15：「看海口径的动作上限是谁设的，给我全删了」）。
#   规则 AI 的签名一直带 `max_actions`（各代入口缺省 40），而**看海那一路**
#   （`mp_run` → `dummy_turn`）给的缺省是 **12** —— 于是看海时每国每回合只能发 12 个动作，
#   超了直接截断。实测（`experiments/probe_teacher_actions.py`，4 图 × 200 回合）：
#   **v10 有 8.6% 的回合被截顶**（69/800），v11 为 0.9%、v12 为 0.6%
#   （v11 的 `V11_MIL_RESERVE` 就是为治这个才把经济段与军事段分开的）。
#
#   ★为什么给一个大数、而不是把 `max_actions` 从签名里删掉：
#     那个形参在**每一代**的入口里都有（`ruleai/v4…v12` + `ai_legacy`），
#     删它要动 10 个文件，**其中 v10 是 BC 老师，按纪律一个字节都不许动**
#     （改它 = 作废全部历史标签）。⇒ 在调用侧把缺省放开，各代的语义一个字不改。
#     `10 ** 9` 也是 `feat/rl` 那条线一直用的写法（那边的 `rl/bc.py`、`rl/compare.py` 等）。
#
#   ★配置里**仍可**显式写 `"max_actions": N` 卡住（`mp_run` 读 `ncfg`）；
#     这里删掉的是"没配就只给 12 个"那个缺省上限。
UNLIMITED_ACTIONS = 10 ** 9

# 版本名 → (模块, 入口函数)。**懒加载**：只 import 真正用到的那一版
# （各版差异是策略差异，互相不依赖）。
_SOURCES: dict[str, tuple[str, str]] = {
    "v12": ("ruleai.v12", "expand_rule_turn_v12"),   # ★v11 + 拿掉 4 处 int() 截断（见 ruleai/v12/）
    "v11plus": ("ruleai.v11plus", "expand_rule_turn_v11plus"),   # ★现役：v11 + 2026-09-16 那批修
    "v11": ("ruleai.v11", "expand_rule_turn_v11"),   # 冻结版（经济层 + 军事层），见 ruleai/v11/
    "v10": ("ruleai.v10", "expand_rule_turn_v10"),
    "v9": ("ruleai.v9", "expand_rule_turn_v9"),
    "v8": ("ruleai.v8", "expand_rule_turn_v8"),
    "v7": ("ruleai.v7", "expand_rule_turn_v7"),
    "v6": ("ruleai.v6", "expand_rule_turn_v6"),
    "v5": ("ruleai.v5", "expand_rule_turn_v5"),
    "v4": ("ruleai.v4", "expand_rule_turn_v4"),
    "ai": ("ruleai.ai_legacy", "expand_rule_turn"),
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