# -*- coding: utf-8 -*-
"""**规则 AI 的"外部旋钮"** —— 模块级常量的设置（`MIL_SHARE` 这类）与编组状态。

## 这个模块存在的原因（2026-09-15）

规则 AI 的策略常量住在**各自的模块**里，而"住在哪"随重构会变：

  · v10 是**单文件** `ruleai/v10.py` ⇒ `mod.HORIZON = n` 改的就是它读的那个全局量。
  · v11/v12 是**包**，`fn.__module__` 是 `ruleai.v11.entry` ⇒ 给 `entry` 赋值只是
    在 entry 里造了个新变量，**经济层 `ruleai.v11.economy.HORIZON` 纹丝不动**。

★**2026-09-15 实测踩坑**：`rl/bc.py`（`get_teacher` / `set_horizon`）、`rl/compare.py`、
两个探针共四处都写 `mod.HORIZON = n` ⇒ **v11/v12 的视野恒为缺省 200**，
而 v10 真的被设成了「回合 + 20」。后果是两边在长局上口径差 2.6 倍
（500 回合那次：v10 按 520 规划、v11/v12 按 200 规划），一度被误读成
「v11/v12 在 500 回合塌到 v10 的 25%」。

⇒ **同日晚些时候用户直接把这个旋钮删了**（「v10 起，不设默认视野，恒等于回合数加 20」）：
视野现在是 `world.max_turns + 20`，**没有可设错的接口**。本模块因此不再管视野。
**能被设错的旋钮，不如没有旋钮** —— 这是这一天的总结论。

## `set_knob`：留下来的通用旋钮

`MIL_SHARE` 这类常量还是得能从外面拨（要扫它）。纪律与当初 `HORIZON` 一样：

  1. 找**那一代之内**真实存着这个名字的**最深模块**（权威副本）去设；
  2. **回读自证**，对不上就 `raise`。

## 编组状态（`grouping._STATE`）

`ruleai/v11/grouping.py` 模块说明：「**RL 每局开始必须 `clear()`**，否则上一局的
编组会漏进新局」—— 而 RL 侧此前**一次都没调过**（`bc.py`/`compare.py`/探针里零命中）
⇒ v11/v12 的历史数还带"跨局编组泄漏"这个共犯；v10 没有这个模块，不受影响。
"""
from __future__ import annotations

import sys


def _owning_pkg(fn):
    """`fn` 所在**那一代的包**（`ruleai.v11.entry` → `ruleai.v11`）；单文件版返回 `None`。

    判据是"这个模块有 `__path__`"（真包），不是"名字里有点"——
    `ruleai.v10` 名字里有点但它**是模块不是包**，所以 v10 返回 `None`（正是我们要的）。

    往上爬时**优先停在带 `grouping` 的那一层**（那是这一代的公开接口层），
    找不到就停在最深的包 —— 免得将来多套一层子包时认错人。
    """
    mod = sys.modules.get(getattr(fn, "__module__", "") or "")
    if mod is None:
        return None
    parts = mod.__name__.split(".")
    found = None
    while len(parts) > 1:
        parts.pop()
        if len(parts) < 2:
            break                              # ★爬到顶层 `ruleai` 就停：它不是"这一代"
        cand = sys.modules.get(".".join(parts))
        if cand is None or not hasattr(cand, "__path__"):
            continue
        if found is None:
            found = cand                       # 最深的包（兜底）
        if hasattr(cand, "grouping"):
            return cand                        # ★这一代的接口层，认它
    return found


def _knob_sites(scope_name: str, knob: str):
    """`scope_name` **那一代之内**所有真实存着 `knob` 的模块（按名字深度降序）。

    ★只看 `vars(m)`，**不触发 `__getattr__`**。★作用域必须**卡在一代以内**：
    早先按顶层 `ruleai` 扫时，v11 的候选里混进了 `ruleai.v10` —— 跨代串味，
    取"最深"会取到别的代。
    """
    if not scope_name:
        return []
    out = []
    for name, m in list(sys.modules.items()):
        if m is None or not (name == scope_name or name.startswith(scope_name + ".")):
            continue
        if knob in vars(m):
            out.append((name.count("."), name, m))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def set_knob(which_or_fn, name: str, value, *, required: bool = True):
    """设该代**模块级旋钮**（`MIL_SHARE` 这类），**回读自证**，返回生效值。

    `which_or_fn` 收版本名（`"v11"`）或入口函数（`rule_ai.resolve()` 的第二个返回值）。
    ★`required=False` 时"这代没这个旋钮"返回 `None` 而不是报错（老几代没有的常量）。

    ★为什么不直接用 `ruleai.v11.economy.MIL_SHARE = x`：**下次重构搬了家就静默失效**
      —— `HORIZON` 那次就是这么栽的。名字 → 位置的解析只该有一处，就是这里。
    """
    import rule_ai
    fn = which_or_fn
    if isinstance(which_or_fn, str):
        _name, fn = rule_ai.resolve(which_or_fn)
    mod = sys.modules.get(getattr(fn, "__module__", "") or "")
    pkg = _owning_pkg(fn)
    scope = pkg or mod
    scope_name = scope.__name__ if scope is not None else ""

    sites = _knob_sites(scope_name, name)
    if not sites:
        if required:
            raise RuntimeError(f"{scope_name} 里找不到旋钮 {name}，无法设置")
        return None
    sites[0][2].__dict__[name] = value
    got = sites[0][2].__dict__[name]
    if got != value:
        raise RuntimeError(f"旋钮 {name} 没设上：目标 {value!r}，回读 {got!r}")
    return got


def clear_state(which_or_fn) -> bool:
    """清空该代规则 AI 的**模块内存状态**（v11+ 的编组）。没状态就返回 `False`。

    ★**每局开始必须调**（`ruleai/v11/grouping.py` 模块说明）：漏了的话上一局的
    `军 id → 目标格` 会漏进新局 —— 结果仍确定可复现，但**不是"一口气跑"的那个结果**。
    """
    import rule_ai
    fn = which_or_fn
    if isinstance(which_or_fn, str):
        _name, fn = rule_ai.resolve(which_or_fn)
    pkg = _owning_pkg(fn)
    if pkg is None:
        return False
    g = getattr(pkg, "grouping", None)
    if g is None or not hasattr(g, "clear"):
        return False
    g.clear()
    return True
