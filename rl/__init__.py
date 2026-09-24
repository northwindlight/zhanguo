# -*- coding: utf-8 -*-
"""战国 RL 战术线（分支 `feat/rl-tactics`）。

    计划全文见 `rl/PLAN.md`。旧线（"端到端替代 LLM"那条）封存在 `feat/rl`
    @ `5260224`，tag `rl-archive-2026-09-24`；**本线不继承它的任何 ckpt**（基座不同）。

    sandbox.py   8×8「攻取国祚」沙盒 —— 规则归沙盒，**军事动作嫁接 v11plus 军事层**
    （后续：net.py / mcts.py / arena.py / ppo.py / train.py，见 PLAN §6）
"""