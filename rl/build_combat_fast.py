# -*- coding: utf-8 -*-
"""★ 编译 **RL 线自带**的战斗 DP 加速扩展（可选；编不出来就整条回落纯 Python）。

    python3 rl/build_combat_fast.py

★ **为什么自带一份**（而不是直接用主线 `mods/experimental/combat_research/` 那份）：
  主线那份是**从本分支的 `rl/_combat_fast.c` 移植过去的**，但它**还带着旧的整数摊分**
  ⇒ 多方局与引擎**差 1 点血** ⇒ 本分支不能直接用它。
  而本分支的纪律是**引擎文件与 main 逐字一致** ⇒ 修好的这份放在 `rl/` 下，
  `mods/` 一个字都不动（主线继续用它自己那份，行为不变）。
  详见 `rl/_combat_fast.c` 的文件头与 `rl/PLAN.md` §0.7。

★ **编译选项 `-ffp-contract=off` 不能少**：aarch64 默认会把 `a*b+c` 合成 FMA
  （一次舍入）⇒ 与 Python 的"先乘后除、两次舍入"**位型不同** ⇒ 观测特征悄悄变了。
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sysconfig
from pathlib import Path


def build() -> Path:
    root = Path(__file__).resolve().parent
    output = root / ("_combat_fast" + sysconfig.get_config_var("EXT_SUFFIX"))
    command = shlex.split(os.environ.get("CC", sysconfig.get_config_var("CC") or "cc"))
    command += ["-O2", "-ffp-contract=off", "-fPIC", "-shared",
                "-I" + sysconfig.get_path("include"),
                str(root / "_combat_fast.c"), "-o", str(output)]
    subprocess.run(command, check=True)
    return output


if __name__ == "__main__":
    print(build())
