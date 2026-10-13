# 战斗概率推演：供规则 AI 研究

experimental 默认为 `client=rule` 启用。此能力通过发行版的规则 AI 工厂私有注入，不注册玩家工具；LLM、议长、Python 脚本拿不到概率接口。native 继续使用原简化估算与 v11plus 行为。

## 运行与 C 加速

```bash
# 在仓库根目录，使用运行游戏的 Python 编译。
.venv/bin/python -m mods.experimental.combat_research.build
```

需要 C 编译器和对应 Python 开发头文件；使用 `-O2 -ffp-contract=off` 编译本机 CPython 扩展。优先加速 DP 和骰子伤害枚举；扩展缺失、输入超出 C 表示范围或运行异常时回退 Python，结果的 `backend` / `fallback` 明确说明原因。本机构建的 `.so` 不提交、不作为跨平台分发文件。

运行时选择 `mods.experimental.toml`，玩家配置 `client=rule`；规则 AI 国策明确标注研究模式。无需为 LLM 增加提示词、协议字段或工具。

## 模型与边界

来源为 `origin/feat/rl-tactics`（提交 `5af3ceb`）的 `rl/combat_probs.py` / `rl/_combat_fast.c`。保留每方 1d6、同时出手、按存活输出签名合并伤害、记忆化状态图求概率的思路。模型是当前可见局面的静态战斗：把所选自军假设投入目标格，保留可见第三方、外交敌我、守方地形 / 城堡、撤退攻击惩罚及承伤覆盖。只读当前公开可见部队和可见目标，不查询隐藏敌军，不修改世界或随机数状态。

这次没有照搬旧伤害规则：普通伤害会把低血部队未吸收的余伤继续分配给存活部队；混合撤退覆盖按当前军队规则均摊后分别减伤。Python 调用当前军队模块的 `spread_damage`，C 实现与其对账。损血按实际 HP 损失计算，最多为初始血量；缓存键包含深度，避免在回合限额下复用不同剩余深度的结果。

不推演未来补给、回血、增援到达、撤退落位或后续外交变化；旧 rl-tactics 的旧 World 增援与撤退接口未接入。撤退中的单位会计入当前静态战斗，但不另提供撤退生还概率工具。研究结果不承诺真实战果，也不证明该策略优于 native。

## 结果与决策

私有能力 `research:combat-probabilities.assess(actor, ids, x, y)` 由工厂绑定 actor；客户端不能替换身份。未知目标返回 `known=false`，不包含概率。

已知目标返回 `research=true`、`known=true`、`winnable` 和 `probabilities`：

- `p_win` / `p_lose`：按势力，存活且敌军全灭 / 自军全灭且敌军仍存活的概率。
- `p_draw`：全场同归的概率；多方战斗可能存在多家无敌对幸存者。
- `rounds`：完成战斗的轮数概率质量。
- `expected_rounds_partial` / `expected_losses_partial`：预算内的部分期望；有未决质量时不是完整期望。
- `unresolved`：状态 / 回合预算截断的未决概率。胜负及轮数分布不按已解决质量重新归一化。
- `n_states`、`backend`、`fallback`：展开量及计算实现 / 回退原因。

规则 AI 用胜率下界达到阈值的最小兵数编组，并据此判断进攻。当前研究参数在 [mod.toml](mod.toml)：最多展开 4000 个状态、64 轮，胜率阈值 0.6。参数属于研究 Mod，不进入 host 的通用配置语义。

## 验证

`tests/test_combat_research.py` 包含余伤转移、损血上界、骰子穷举概率、预算截断、C/Python 完整结果一致、扩展回退、迷雾与世界 / RNG 不变、私有权限、规则 AI 实际循环与临时存档恢复。构建扩展后执行：

```bash
.venv/bin/python -m unittest tests.test_combat_research -v
```
