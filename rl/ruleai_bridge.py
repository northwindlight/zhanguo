# -*- coding: utf-8 -*-
"""**规则 AI 的"外部旋钮"** —— 视野（`HORIZON`）与编组状态。

## 为什么单独一个模块（2026-09-15）

两处口径 RL 侧必须能从外面拨，而**拨法在 v10 → v11 之间变了**，
两边的写法**长得一样但只有一个管用**：

  · v10 是**单文件** `ruleai/v10.py` ⇒ `mod.HORIZON = n` 改的就是经济层读的那个全局量。
  · v11/v12 是**包**，`fn.__module__` 是 `ruleai.v11.entry` ⇒ 给 `entry` 赋值只是
    在 entry 里造了个新变量，**经济层 `ruleai.v11.economy.HORIZON` 纹丝不动**。
    `ruleai/v11/__init__.py` 的模块说明**开头就写了这条警告**
    （「★ 别用 `mod.HORIZON = n` 那种写法 …… v10 当年就踩过'老师被设了窗口、
    自己还按缺省算'的坑」），并因此提供了 `set_horizon()`。

★ **2026-09-15 实测踩坑**：`rl/bc.py`（`get_teacher` / `set_horizon`）、
`rl/compare.py`（`run_rule`）、`experiments/probe_teacher_{versions,actions}.py`
四处都是 `mod.HORIZON = n` ⇒ **v11/v12 的视野恒为缺省 200**，
而 v10 真的被设成了「回合 + 20」。**后果是 v10 与 v11/v12 在长局上口径差 2.6 倍**
（500 回合那次：v10 按 520 规划、v11/v12 按 200 规划），
一度被误读成「v11/v12 在 500 回合塌到 v10 的 25%」。

## 这个模块的纪律：**设完必须自证**

写对了不难，难的是**下次别人再加一代时不会静默退回**。所以这里不"设完就算"，
而是设完**回读**：

  1. 包自己声明了 `set_horizon()` ⇒ 用它（版本自己的接口最懂自己的状态住哪）；
     否则退回"给存着 `HORIZON` 的最深那个模块赋值"。
  2. **回读 `getattr(顶层包, "HORIZON")`**（v11 的 `__getattr__` 是**读时转口**，
     拿到的是经济层当下的真值；快照 `from .economy import HORIZON` 骗不过它）。
     对不上就 `raise`，**不静默**。

  ⇒ 这条判据**能抓住上面那个 bug**：旧写法给 `entry` 赋值后，
    `ruleai.v11.HORIZON` 回读仍是 200 ⇒ 当场报错，而不是等到 500 回合的数出来才发现。

## 编组状态（`grouping._STATE`）

`ruleai/v11/grouping.py` 模块说明：「**RL 每局开始必须 `clear()`**，否则上一局的
编组会漏进新局」—— 而 RL 侧此前**一次都没调过**（`bc.py`/`compare.py`/探针里零命中）
⇒ v11/v12 的历史数还带"跨局编组泄漏"这个共犯；v10 没有这个模块，不受影响。
`clear_state()` 按同一套"找顶层包"的逻辑调它，没有就静默跳过（v10 正常无状态）。
"""
from __future__ import annotations

import sys


def _owning_pkg(fn):
    """`fn` 所在**那一代的包**（`ruleai.v11.entry` → `ruleai.v11`）；单文件版返回 `None`。

    判据是"这个模块有 `__path__`"（真包），不是"名字里有点"——
    `ruleai.v10` 名字里有点但它**是模块不是包**，所以 v10 返回 `None`（正是我们要的）。

    往上爬时**优先停在带 `set_horizon`/`grouping` 的那一层**（那是这一代的公开接口层），
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
        if hasattr(cand, "set_horizon") or hasattr(cand, "grouping"):
            return cand                        # ★这一代的接口层，认它
    return found


def _horizon_sites(scope_name):
    """`scope_name` **那一代之内**所有真实存着 `HORIZON` 的模块（按名字深度降序）。

    ★只看 `vars(m)`，**不触发 `__getattr__`** —— 包上的"读时转口"不是存储点，
    而 `entry.py` 里 `from .economy import HORIZON` 那种**快照**是，会出现在这里。
    所以列表里可能有假权威（快照），**不能盲取第一个**，详见 `set_horizon`。
    ★作用域必须**卡在一代以内**：早先按顶层 `ruleai` 扫，v11 的候选里混进了
    `ruleai.v10` —— 跨代串味，取"最深"时会取到别的代。
    """
    if not scope_name:
        return []
    out = []
    for name, m in list(sys.modules.items()):
        if m is None or not (name == scope_name or name.startswith(scope_name + ".")):
            continue
        if "HORIZON" in vars(m):
            out.append((name.count("."), name, m))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def horizon_of(which_or_fn) -> int | None:
    """**回读**该代当下生效的视野（权威副本）。读不到返回 `None`。

    权威判据：包版本读 `getattr(pkg, "HORIZON")`（`__getattr__` 读时转口到经济层），
    单文件版本读它自己的模块全局。★**别读 `entry.HORIZON`** —— 那是 `import` 那一刻的
    快照，包设了新视野它也不变（`set_horizon` 的自证就是靠这条区分真假）。
    """
    import rule_ai
    fn = which_or_fn
    if isinstance(which_or_fn, str):
        _name, fn = rule_ai.resolve(which_or_fn)
    mod = sys.modules.get(getattr(fn, "__module__", "") or "")
    pkg = _owning_pkg(fn)
    if pkg is not None and hasattr(pkg, "HORIZON"):
        return pkg.HORIZON
    scope_name = (pkg or mod).__name__ if (pkg or mod) is not None else ""
    sites = _horizon_sites(scope_name)
    return sites[0][2].HORIZON if sites else None


def set_horizon(which_or_fn, turns: int) -> int | None:
    """把规则 AI 的评估视野设成 `turns`，**设完回读自证**，返回实际生效的值。

    `which_or_fn` 收版本名（`"v11"`）或入口函数（`rule_ai.resolve()` 的第二个返回值）。

    ★两种"没设上"要分开（2026-09-15）：
      · **这一代根本没有视野旋钮**（v4/v5/v6/ai —— v6 还是 BC 的缺省老师）
        ⇒ 返回 `None`，**无操作**，与旧写法（`hasattr` 不成立就跳过）行为一致，
        不能炸掉 `--teacher v6` 这条能跑的路。
      · **有旋钮却没设上**（写错了存储点）⇒ `raise RuntimeError`。
        宁可当场炸，也不要一个"以为设了"的长局 —— 这正是 500 回合那次被坑的方式。
    """
    import rule_ai
    fn = which_or_fn
    if isinstance(which_or_fn, str):
        _name, fn = rule_ai.resolve(which_or_fn)
    target = int(turns)

    mod = sys.modules.get(getattr(fn, "__module__", "") or "")
    pkg = _owning_pkg(fn)
    scope = (pkg or mod)
    scope_name = scope.__name__ if scope is not None else ""

    sites = _horizon_sites(scope_name)
    has_pkg_api = pkg is not None and hasattr(pkg, "set_horizon")
    if not sites and not has_pkg_api:
        return None                    # ★没有这个旋钮：无操作，不是错误

    # 1. 包自己的接口优先
    if has_pkg_api:
        pkg.set_horizon(target)
    else:                               # 2. 退回：给最深那个存储点赋值（v10 走这条）
        sites[0][2].HORIZON = target

    # 3. ★自证：回读**权威**副本
    got = horizon_of(fn)
    if got != target:
        raise RuntimeError(
            f"视野没设上：{getattr(fn, '__module__', fn)} 目标 {target}，回读 {got}"
            f"（pkg_api={has_pkg_api}）——★别用 `mod.HORIZON = n` 那种写法，"
            f"包版本的权威副本在更深的模块里（见本文件说明）")
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
