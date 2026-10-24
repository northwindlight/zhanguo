# RL 线：怎么跑、怎么改、怎么验

> 2026-10-22 重写。上一版有一层 `rl/engine.py` 门面（1648 行），把新引擎包成旧
> `world.*`，自己记回合号/地块表/军队表/建筑表，还自己复刻了野人合成、伤害摊分、
> 亡国除名、新兵、外交表决等**引擎规则**。⇒ 引擎改了规则，RL 侧不跟着改，
> **而且不报错**。那份门面**已删除**，本文档讲的是现在的形状。

---

## 1. 这条线是什么

一句话：**在引擎上训练一个会打仗的策略**。

- 引擎（`mods/native/*`）负责**规则**：战斗怎么打、地盘怎么算、谁亡国、
  兵能不能动、视野到哪、外交怎么表决。
- RL 线（`rl/`）负责**决策**：这一步谁动、动哪支军、打不打、打谁。
- 两者的接缝是**能力接口**（`[Capabilities]` / `[Interfaces]`），不是函数调用。

### 跑一遍

```bash
# 加载 RL 发行版（会逐个校验声明的方法确实存在）
.venv/bin/python -c "
from engine.runtime import Runtime
rt = Runtime.load('rl/release.toml')
rt.check_interfaces(rt.world_class()())
print('ok', len(rt.capabilities), '个能力')"

# 打一局（双方都由军事层驱动）
.venv/bin/python -c "
from rl.sandbox import Sandbox
sb = Sandbox(seed=7, size=8, n_nations=2).reset()
r = sb.rollout()
print('回合', r['turns'], '胜方', r['winner'])"

# 跑测试
.venv/bin/python -m unittest discover -s tests -q
```

---

## 2. 能力与接口：声明在哪、怎么校验

### 清单

| 文件 | 干什么 |
|---|---|
| `rl/release.toml` | **发行版清单**（不硬编码在代码里）：`../mods/experimental` + `mods/train` + `mods/scenario` |
| `rl/mods/train/mod.toml` | `rl:train` 的**能力声明**：`[Capabilities]` / `[Interfaces]` / `[State]` / `[Sandbox]` |
| `rl/mods/train/mod.py` | 提供 `rl:sandbox`（摆局）、`rl:truth`（训练真值）、`rl:odds`（战斗概率） |
| `rl/mods/scenario/mod.toml` | `scenario:rl-sandbox` 的声明 + `[Scenario]` 参数 |
| `rl/mods/scenario/mod.py` | **摆局的唯一出处**：十字开局、强制平原核心、扩地口径、开局兵 |

### 两条闸门

1. **加载期**：`runtime.check_interfaces(world)` 逐个核对 `[Interfaces]` 里声明的
   方法**真的存在**于能力对象上；缺一个 ⇒ `ModError`。
   *改一个方法名 ⇒ 加载就炸，而不是运行时静默算错。*
2. **调用期**：`Env.call` 跑在 `unit_context("rl:train")` 里，
   `runtime.object_call` 会检查"调用方声明过这个能力与方法吗"；
   没声明 ⇒ 当场 `ModError`。
   *RL 调错能力/调错方法 ⇒ 立刻炸。*

```bash
# 故意写错方法名 ⇒ 加载即报
sed -i 's/"native:army" = \["visible_units"/"native:army" = ["visible_unitsXXX"/' \
    rl/mods/train/mod.toml
.venv/bin/python -c "
from engine.runtime import Runtime
rt = Runtime.load('rl/release.toml')
rt.check_interfaces(rt.world_class()())"      # ⇒ ModError
git checkout rl/mods/train/mod.toml
```

### `rl/env.py`：唯一的接缝

`Env` 只做三件事：

- **转发** —— 把调用送到**声明过的**接口；
- **投影** —— 把引擎对象**现算**成 RL 要读的形状（`TileRow`），**不缓存**；
- **换词** —— RL 说中文短名（甲/乙），引擎说内容 ID（`rl:n0`），
  换词在 `rl/nations.py`（**纯函数**，按词表下标算，不是状态）。

它**不是**旧门面：不复刻引擎规则、不记影子状态、不把引擎的 `Unit` 重塑成 dict。

---

## 3. 口径：哪些归引擎，哪些归 RL

### 归引擎（native）—— 一行都不许自己算

| 事项 | 走哪个能力 |
|---|---|
| 回合号 | `native:government.period()` |
| 地块归属 / 地形 | `native:territory.cell` / `terrain` / `owner` / `owned` |
| 视野 | `native:vision.positions` / `visible` |
| 军队、行动 | `native:army.move` / `attack` / `retreat` / `disband` / `reachable` |
| 征召额度与上限 | `native:army.recruit_quote` / `recruit` |
| 建筑 | `native:buildings.count` / `effects` / `has_role` / `seed` |
| 外交 | `native:diplomacy.submit` / `vote` / `war_between` / `allied` |
| 战斗概率 | `research:combat-probabilities.assess` |
| 伤害分摊 / 减伤 | `native:army.spread_damage` / `defense_pct` / `faction_power` |
| 野人 | `native:army.visible_units`（引擎**现场合成**） |
| 亡国 | `native:succession`（**登记**在 `enroll`、**裁决**在结算阶段） |

> ★ **亡国现在真的走引擎**（2026-10-23 修）：摆局时每个国家都被
> `native:succession.enroll` **登记**一次（登记发生在**市政厅落成之后** ——
> 没首都就登记 = 下一个结算点立刻判死）⇒ 结算时引擎自己判"国祚断绝"，
> 自己迁移站点、`diplomacy.eliminate`、`army.remove_nation`、`nations.extinguish`
> ⇒ **引擎状态与 RL 的判断不再分叉**（`Sandbox.alive` 只是**读**引擎的状态）。

### 归 RL（训练环境的先验，不是引擎规则）

| 事项 | 在哪 |
|---|---|
| 地图多大、几个国家 | `rl/sandbox.py`（`n_nations_for`） |
| 十字开局、核心强制平原、扩地口径 | `rl/mods/scenario/mod.py` |
| 补员节奏（每 5 回合、配额 = 1 + 厅数） | `rl/sandbox.py::resupply`（**额度问引擎**） |
| "原地不动"这个动作 | `rl/sandbox.py`（**引擎没有这个动作**，所以沙盒自己记 `held`） |
| 视野外的"撞墙试探格" | `rl/sandbox.py::legal` |
| 胜负口径（实体只剩一个） | `rl/sandbox.py::winning_entity` / `rl/evaluate.py::terminal` |
| 权重与奖励尺度 | `rl/scoring.py`（**单一出处**） |
| 击杀账本 | `rl/sandbox.py::KillLedger`（按 **`gid`** 记身份） |

### 三条口径裁定（2026-10-22）

1. **新兵本回合不能动** —— 走 native 的 `recruit`（征召 ⇒ `born_turn = 当前期`），
   引擎自己的 `Unit.can_act` 负责。沙盒**不再**有"造完就能动"的第三种方式
   （开局摆兵走 native 的 `seed` ＝ 初始化/导入，那是**另一条** native 路径）。
2. **无主格可以打** —— native 允许（引擎会在那儿**放一支真野人**，打它才能占格）。
   沙盒的"能不能打"一律由 `native:army.reachable(..., attack=True)` 回答。
   ★ 进攻候选 = `reachable(attack=True)` **减去自家/盟国**（那两格引擎会拒）。
     **野地也在里面** —— 因为引擎里 **`move` 不占地、`attack` 才占地**
     （`native:army.attack` 走 `_claim` 改归属）⇒ 不给野地的 `attack` 候选
     ⇒ **模型永远学不会主动占地**（2026-10-23 修，review P1-1）。
3. **平局时已亡国的一方按 −1（输）** —— 亡国就是输；两处口径不一致会让
   "早死比输划算"⇒ 模型会学出"主动送死"。

### 战斗概率的三条口径（2026-10-23 修）

1. **只转发引擎能力** —— `research:combat-probabilities.assess`。
   引擎能力要求"**提问方看得见目标格**"⇒ **迷雾里的野战推不出来**。
   ⇒ RL 侧**不编假分布**（旧口径写"一轮结束"，模型会把"不知道"读成"0 概率"），
     观测里**显式标"未知"**（`vocab.GRID_CB_UNKNOWN` / `vocab.A_CB_UNKNOWN`），
     其余概率列保持 0（**由"未知"列说明那是 0 概率而不是概率**）。
2. **增援按观测方算** —— 「增援胜率」那一列回答的是"**我**的增援到齐后我赢多少"
   ⇒ 问引擎时用**观测方**的身份（引擎能力要求"提问者自己的部队"），
   且**自家部队不按视野过滤**（我自己的军队在哪儿我当然知道 ——
   旧口径按视野过滤 ⇒ 预备队在野地就数不着 ⇒ 那一列恒等于基础胜率）。
3. **轮数分档用真边界** —— `Odds.round_bins` 的每一档是 `sum(p_rounds[0..边])`
   （旧口径把整个分布累加进每一档 ⇒ 无论边界是几都返回同一个值）。

### 评测口径的三条（2026-10-23 下午修）

`rl/eval_fixed.py` 是这条线**唯一的裁判**（"有没有学到策略"就问它）⇒
它自己的口径错一点，**所有结论都跟着错**：

1. **采样臂按策略概率抽** —— 旧口径是 `rng.permutation(len(p))`（**均匀随机**）
   ⇒ 把策略从"强烈偏好 hold"换成"强烈偏好 attack"，同种子的行动**一模一样**
   ⇒ `--sample`（用户定的**唯一判据**）**测不出任何东西**。
   ⇒ 现在按 `p` 抽，被引擎拒了就在**剩下的里按剩下的概率重抽**。
2. **评估要带记忆** —— 旧口径每步都走 `net(batch)` ⇒ 潜槽**反复用初值**
   （= 记忆等于没带，而且**不报错**）⇒ 记忆臂的评测**量不到记忆的收益**。
   ⇒ 现在走 `forward_state` + **逐国**的 `mem_in`/`mem_out`（与训练同一条链）。
3. **评测池不许混随机网** —— 旧口径按 `--pool` 建满网，**只灌存档里有的那几份**
   ⇒ 缺的槽**保持随机初始化** ⇒ 一份 2 份网子的存档按默认 5 份加载，
   **3 份随机网直接进了评测池** ⇒ 分数里掺着没训过的策略。
   ⇒ 现在：存档有几份就用几份，**并且把这件事说出来**。

### 续训口径的两条（2026-10-23 下午修）

1. **Adam 的动量要存也要取** —— 旧口径只存权重 ⇒ 续训时优化器**根本不存在**
   ⇒ 下一步就**新建一个 Adam** ⇒ `m`/`v` 归零 ⇒ **周期重启反复丢动量**
   （Adam 的头几步偏差修正占主导 ⇒ 等于每轮都用"刚起步的 Adam"跑几十步）。
   ⇒ 现在 `opts` 一起存/取（**命令行的 `--lr` 仍然说话**）。
2. **"学过但还没冻结"的快照要随存档走** —— 旧口径里它们的权重**只在本进程内存里**
   ⇒ 重启就**静默回到旧指纹**（`--league-snapshot-every 3` + `--restart-after 5`
   这种组合必踩）⇒ 现在 `league_hot` 一起存，续训时灌回池子。

### 第三轮复审的四条口径（2026-10-23 傍晚修）

1. **动作失败后必须重取观测与候选**（`rl/eval_fixed.py`）—— 引擎的"**盲推**"
   （打看不见的目标）**失败时也会消耗行动** ⇒ `_auto_advance` 把回合**交给下一位玩家**
   ⇒ 剩下那几个候选**是上一位玩家的** ⇒ 继续执行就等于**替下一位玩家落子**
   （实测：甲前向一次、乙前向零次，评估器却替乙执行了一个 hold）。
   ⇒ 现在：**换人、换回合或本方军队状态变化，这批候选立即作废**，重新观测并前向。
   判据用 `Sandbox.decision_key()`，从引擎现读本方状态，不读取敌方隐藏信息。
   只有状态未变的拒收才继续尝试剩余候选。即使盲推后仍是同一玩家，也须重新决策。
   ★ 配套：`hold` 拒收不存在、阵亡、交战中、已消耗行动、已 hold 或位置过期的军队。
2. **热快照恢复要"先钉住、再加载"**（`rl/league.py`）—— 旧口径先 `net_of(mid)`
   （这一步**自己会触发逐出**）、后 `mark_hot` ⇒ 缓存上限一紧（`net_cap=1`），
   **刚灌进去的那份当场被逐出** ⇒ 下次再取就是**盘上的旧权重** ⇒ "恢复成功"是**假的**
   （实测：恢复 3 份、缓存上限 1 ⇒ 报告 3 份成功，另外两份再读时是旧权重）。
   ⇒ 恢复与正常训练均**先保护热成员，再加载**。训练后尚未到冻结轮时，
   更新对象留在缓存中，权重和 Adam 随主档保存。
3. **热快照的优化器状态也要跟存档走**（`rl/league.py`）—— `opts` 只覆盖**主网络**，
   `league_hot` 仍只有权重 ⇒ 归档间隔 > 1 时，**这批快照的续训等于每轮重开 Adam**
   （主网络那份有动量、这批没有 ⇒ 两边口径还不一样）。
   ⇒ 现在 `league_hot` 的形状是 `{mid: {"weights": …, "opt": …}}`
   （**旧形状仍认** ⇒ 盘上已有的旧存档不作废）。
4. **联盟胜利按"建局时的队伍"结算**（`rl/sandbox.py` / `rl/evaluate.py`）——
   引擎的 `Diplomacy.eliminate`（亡国）会把死者从联盟名单里删掉，**人不够两个就把联盟
   整个解散** ⇒ 事后**查不到死者原来属于谁**（活下来那位的标签也变了）
   ⇒ "**我战死但我的队赢了**"被算成"我输了"
   （实测：乙、丁同队 ⇒ 乙先亡、丁最终获胜 ⇒ 两人拿到 −1 / +1）
   ⇒ 这把"**有利于团队、但本国先亡**"的策略标成失败。
   ⇒ 现在：**队伍在建局时定下，之后不变**；最后剩下的那个队伍赢，
   **它的全部成员（活着的、亡国的）都算赢**（`reward` 与 `outcome_for` 统一口径）。
   训练的 `_outcome_for` 直接转发 `Sandbox.outcome_for`；预算截断时，队友仍存活的
   亡国成员结果未定，保留价值自举，不提前写入失败奖励。
   ★ 队伍身份**跟着结盟走**（`set_alliance` 时刷新）—— 因为引擎那条
     "**正在战争不能建盟**"逼着调用方**先 `war=False` 建局、再结盟** ⇒ 这条路
     是**正路**（只记在 `reset()` 里 ⇒ 那种局里甲乙还是"各自为政"的旧标签
     ⇒ 联盟明明赢了，`winner()` 却是 `None`）。
   ★ **平局**仍按 2026-10-22 的裁定：活着 ⇒ 0，**已亡国 ⇒ −1**（亡国就是输）。

---

## 4. 怎么验

### 五个新测试文件（结构闸门，不依赖某一局的数值）

| 文件 | 钉什么 |
|---|---|
| `tests/test_rl_sandbox.py` | 能力声明能加载；**方法名写错 ⇒ 加载即报**；合法动作来自引擎的可达集；门面已删；决策层不许吞异常 |
| `tests/test_rl_obs_engine_truth.py` | 观测的归属/视野/防御%/野人**逐项等于引擎真值** |
| `tests/test_rl_train_truncation.py` | 截断**不产生伪终局**；终局口径单一；亡国方在平局按 −1 |
| `tests/test_rl_stats_weighting.py` | 权重单一出处；击杀账本按 `gid` 记（不同国的同号军不撞车） |
| `tests/test_rl_battle_odds.py` | 战斗概率**逐位等于**引擎能力；看不见的目标不编数；RL 侧不再有 DP 实现 |
| `tests/test_rl_review_fixes.py` | **复审十条**（2026-10-23 上午）：野地进攻候选 / 亡国落引擎 / 迷雾战斗不崩 / 地形类型边界 / 增援按观测方 / 推不出来标"未知" / 老师的未知守军 / 轮数分档 / MCTS 可导入 / `with_reinf=False` 不崩 |
| `tests/test_rl_review2_fixes.py` | **复审十一条**（2026-10-23 下午）：采样臂按策略概率 / 亡国后战争能恢复 / 评估带记忆 / 迷雾不漏地形城防 / 续训恢复 Adam / 热快照随存档走 / 学习者轮转不饿死人 / 截断自举用新价值 / 评测池不混随机网 / 跨臂先手生效 / 对手网同设备 |
| `tests/test_rl_review3_fixes.py` | **复审四条**（2026-10-23 傍晚）：失败后不替下一位玩家落子 / 热快照恢复不被缓存逐出 / 热快照带 Adam 状态 / 2v2 按联盟口径结算 |
| `tests/test_rl_review4_fixes.py` | 训练截断的联盟奖励 / 真实训练入口与磁盘紧缓存 / 权重和 Adam 续训一致 / 同一玩家盲推失败后重新决策 / hold 校验 |

```bash
.venv/bin/python -m unittest \
  tests.test_rl_sandbox tests.test_rl_obs_engine_truth tests.test_rl_train_truncation \
  tests.test_rl_stats_weighting tests.test_rl_battle_odds \
  tests.test_rl_review_fixes tests.test_rl_review2_fixes tests.test_rl_review3_fixes \
  tests.test_rl_review4_fixes -q
```

### CI 口径（`python -m unittest discover -s tests -q`）

★ CI **不装 torch**（只装 `requirements-vision.txt`）⇒ 训练类的 RL 测试在 CI 上
  **整份跳过**（`has_torch` 守卫）⇒ CI 的时间预算由**非训练**的 RL 测试决定。
★ 两条线都测过了：
  · **CI 口径（无 torch）**：**1117 项，OK，34 skipped，2m47s**（预算 20 分钟）✔；
  · **本地口径（有 torch）**：训练类用例在本地**要跑真训练**（CI 上它们整份跳过）
    ⇒ 慢很多；用例规模已按"够用就缩"调过（`test_rl_adam_persist` 8×8/6 回合、
    `test_rl_memory` / `test_rl_batch_rollout` / `test_rl_rollout_budget` /
    `test_rl_slot_norm` 的局面与步数都缩了）。
    ⚠ 仍有一条**没解决**：本地全量（带 torch）跑不完 —— 训练类用例要**建 1M 参数的模型**
      并在 CPU 上跑几十步前向（模型大小是**用户口径**：≥1m 参数，不能缩）
      ⇒ **本地全量回归要么装 GPU，要么按文件分批跑**。CI 不受影响。

★ **CI 的其余步骤也过**：`scripts/api_doc.py --check`、`docs/site/build.py`、
  `mp_run.py --check-config`（两份配置）、`tests.test_examples_tour`。

⇒ **建局的开销**是两个大头，都按 native 的批量口径治了：
  · **宣战**：native 的宣战要"提交 → 结算（生成表决）→ 代投 → 再结算"⇒
    **一对一次结算** ⇒ N 国两两宣战 = `2×N(N-1)/2` 次结算（**平方级**）。
    改成**批量宣战**（全提交 → 一次结算 → 一次代投 → 一次结算）⇒ **恒定 2 次**。
  · **兵营**：native 的额度是"每期每兵营 1 支"，而 RL 的补员配额是 `1+厅 = 2` 支/期
    ⇒ **2 座就够**（原来摆 8 座 ⇒ 每局白跑 6 次能力调用 × N 国）。

### 一次真实训练（2026-10-24 实测）

★ 这台机器（**1 物理核 / 3 GB 内存 / 无 GPU**）上跑通的一次**真训练**（不是冒烟）：

```bash
# 3 国 / 8×8 / 每局 60 回合上限 / 每 iter 2 局 / 共 8 iter
# 联赛快照每 2 iter 归档一次 / 每 4 iter 重启进程（续训）
PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 \
.venv/bin/python -u -m rl.train --threads 1 --device cpu \
  --size 8 --nations 3 --t-max 60 --episodes 2 --iters 8 --pool 4 \
  --league-snapshot-every 2 --restart-after 4 --league-cache 16 \
  --max-steps 4000 --ckpt-every 2 \
  --out rl/runs/base3.pt --league-db rl/runs/league_base3.db
```

* **跑完 8 个 iter**（EXIT=0），**16 局**：3 局分出胜负、13 局打满 60 回合判平。
* **每局每个玩家都拿到恰好一次结果**（对账行 `非零奖励6/段6` ⇒ 结算没漏人、没多给）。
* **定时重启**（iter 4 后）续训成功：横幅是「**灌回 4/4 份权重 + 4 份优化器状态**」
  ⇒ 热快照的**权重与 Adam 动量**都跨重启活着（存档里每份热快照的 `exp_avg` 非零）。
* **吞吐**：9651 样本 / 55 分钟 ⇒ **≈ 1.05 万样本/小时**（1 核 CPU；`update` 占 90%）。
* **评测（采样臂，6 个固定种子，两臂共用同一组局面）**：
  | 臂 | 胜场（甲/乙/丙） | 平局 | 策略集中度 `ent/logK` |
  |---|---|---|---|
  | 训练臂（存档权重） | 0 / 0 / 0 | 6 | **0.988** |
  | 未训练基线 init#0 | 0 / 0 / 0 | 6 | 1.000 |
  | 未训练基线 init#1 | 0 / 0 / 0 | 6 | 1.000 |

  ⇒ **集中度**：训练臂 0.988 **低于**两个基线（**都恰好 1.000** —— 随机初始化的
    logits 几乎相同 ⇒ 分布就是均匀的）⇒ **策略确实被推离了均匀分布**（信号在噪声带外）。
  ⇒ **胜场**：两臂**一模一样**（6 局全打满 60 回合判平）⇒ **落在噪声带内** ——
    这个样本量下**还看不出"学会没学会"**（局局平局 ⇒ 没有胜负信号可分）。
* ★ **为什么要 `--t-max 60`**（仓库缺省是 500）：`t_max=20` 时**每局都打满、局局平局**
  ⇒ **一个胜负信号都没有**（16 局 0 胜）⇒ PPO 没有梯度；提到 60 才有 3/16 的局分出胜负。
  ★ **算术**（为什么本机跑不出效果）：PPO 在 140 万参数上要有可见动静，通常得
  **10^5～10^6 样本** ⇒ 按本机 1.05 万/小时算是 **10～100 小时**；再说仓库推荐档
  （12–20 图、`t_max=500`）**每样本贵 10 倍上下** ⇒ **天到周**。
  ⇒ 要真看到学习效果，**得用 GPU**（`--device cuda`：`update` 占 90% 且全是 matmul
  ⇒ 上卡收益最大，采集那半边是 Python 沙盒、上卡几乎没用）。
* ★ 顺带在跑训练时**逮到两个真 bug**（都已修 + 补闸门）：
  ① `rl/train.py::_outcome_for` **漏传 `teams`** ⇒ 截断局里"本国已亡、队伍还在"的玩家
     被提前判成输（结算侧 `None`、训练侧 `−1` ⇒ 两处口径分叉）；
  ② `rl/eval_fixed.py` 的 `ent/logK` **除零崩**（候选只剩 1 个时 `logK=0`；
     这行从 2026-09-25 起没设防，训练侧早有守卫、评测侧没有）。

### 手工闸门

```bash
# ① 门面确实不在了
ls rl/engine.py 2>&1 | grep -q "No such" && echo "门面已删 ✔"

# ② RL 侧不再有战斗 DP 的实现（会和引擎漂移）
ls rl/combat_probs.py rl/_combat_fast.c 2>&1 | grep -q "No such" && echo "战斗 DP 已删 ✔"

# ③ 决策层没有吞异常的写法
grep -rn "except.*:$" -A1 rl/*.py rl/teacher/*.py | grep -w pass && echo "有吞异常 ✘" || echo "无吞异常 ✔"

# ④ 旧文档已删
ls rl/PLAN.md rl/REPAIR-2026-10-22.md 2>&1 | grep -q "No such" && echo "旧文档已删 ✔"
```

### 已知缺口（2026-10-23 三轮复审后）

- **旧 RL 测试套件（`tests/test_rl_*.py`）还在迁移中**。
  造局助手已备齐（`tests/rl_deps.py`：`make_world` / `set_owner` / `set_hall` /
  `make_site` / `new_army` / `disband_at` / `give_site` / `unmaterialized_cells` /
  `is_materialized`）—— **全部只走引擎公开能力**，不再改任何影子表。
  已迁完（全绿，**这些就是范本**）：`test_rl_turn_clock`、`test_rl_evaluate`、
  `test_rl_backbone`、`test_rl_alliance`、`test_rl_auto_advance`、
  `test_rl_sandbox_clone`、`test_rl_important_places`。
  ⇒ 另有 **40 个 RL 测试文件全绿**、**2 个还红**（`combat_obs` / `league_versions`；
    后者还**超时**——它跑**真训练**，引擎真跑比门面慢一个量级）。
  ★ `combat_obs` 剩下的是**引擎里不存在的局面**：
    "**残血但还在交战**"（引擎的血量只由**战斗**改 ⇒ 一受伤那场仗就结束了）
    与"**野战格必须看不见**"（native 的 `attack` 在**守军还活着**时**不立刻占格**，
    但对手一死就占 ⇒ 得先保证守军活着）。
  ★ **两份测试随 RL 侧 DP 一起删了**（不是"跑了没跑"的问题，是**归属变了**）：
    · `tests/test_combat_probs.py` —— "RL 的 DP 必须等于引擎的 DP"；
      现在 RL **只转发**引擎能力 ⇒ 这条**结构上**成立，由
      `tests/test_rl_battle_odds.py`（逐位对拍）钉住。
    · `tests/test_rl_dp_identity.py` —— RL 侧 DP 的**逐位冻结基准**；
      DP 现在住在 `mods/experimental/combat_research/`，它自己的基准在主线的
      `tests/test_combat_research.py`（9 项，全绿）⇒ 基准跟着 DP 一起搬走了。
  ★ 剩下的卡点**不是改名**，是**引擎表达不了旧门面的某些局面**：
    · "**有地没厅**" —— 厅是**站点上的实例**，站点转走地也走 ⇒ 引擎里不存在；
    · "**把军挪到某格**" —— 位置是引擎里的事实，只能**重建**（两局对比），不能改 dict；
    · "**假野人驻守**" —— 引擎的野人是**现场合成**的，"未物化的可见格"才是"有野人"。
  ⇒ 这些断言要按引擎**可达的状态**重写（不是机械迁移能解决的）。
  ⇒ 已迁的三个文件就是按这个口径改的，可当范本。
- **比旧门面慢**：现在每一回合都走**真引擎**（旧门面是自己在 Python 里算规则），
  实测整局约 **0.17 s/回合**（8×8 两国 31 回合 ≈ 5 s）。大图多国的局会明显变慢。
- **战斗概率的两块仍由 RL 侧算**：**增援**够不够得着（寻路，属 RL 决策）
  与**撤退生还率**（引擎能力不含）—— 引擎那部分（胜负、轮数、掉血）**只转发**，
  `tests/test_rl_battle_odds.py` 逐位对拍钉住。
  ★ 增援这一块现在只算**观测方自己**的预备队（"我到齐了能赢多少"）；
    **敌军的增援是未知量**（看不见就别点名）⇒ 不猜。
- ★★ **迷雾里的野战"推不出来"**（2026-10-23）：引擎的推演能力要求"提问方
  **看得见**目标格"⇒ 双方都看不见时**没有概率**。RL 侧**不自己算**
  （那是复刻引擎的 DP）⇒ 观测里**显式标"未知"**
  （`vocab.GRID_CB_UNKNOWN` / `vocab.A_CB_UNKNOWN`）。
  ⇒ **代价**：大地图上"接触战"的概率通道多半是"未知"（8×8 的交战格可见率
  27%~66%，16×16 只有 0%~7%）⇒ 想让它有内容，只能等引擎那边给出
  **不要求视野**的推演口（那是主线的能力，不是 RL 线该自己造的）。
- ★★ **观测宽度变了**（2026-10-23）：网格 **27 → 28** 列（多了"推不出来"标记）、
  军队 token 尾部 **14 → 15** 列 ⇒ **旧 ckpt 一律作废**（宽度对不上）。
- ★★ **迷雾里的地形/城防不再泄漏**（2026-10-23 修）：网格的 `GRID_DEFENSE` /
  `GRID_MOVE` 与**候选的地形段**，对**看不见的格**一律走"**按平原估**"
  （与 `rl/teacher/pathfind.py` 的 `_UNSEEN_COST` 同一条口径）⇒
  只改**不可见格**的地形，**合法动作、网格、候选输入三者全都不变**
  （`tests/test_rl_review2_fixes.py::TestFogLeak` 钉住）。
  ★ 这条原来是**真泄漏**：模型能靠"不可见格的地形"偷看，而且**合法动作一模一样**
    ⇒ 训练存在一条利用隐藏信息的通道。
- ★★ **亡国之后的"停战期"是真代价**（2026-10-23 修）：引擎的亡国处理
  （`Diplomacy.eliminate`）会**清空全部战争**并给幸存者两两**停战**
  （native 的 `FallTruce = 10` 期）⇒ RL 的动作空间里**没有"宣战"**，
  所以**沙盒替幸存者把宣战走一遍**（停战期内**不**宣 ⇒ 不绕过引擎规则）。
  ⇒ **代价**：灭国之后有 **10 期的停战**（那段时间谁也打不了谁，只能扩地），
    再加宣战本身要走的"提交 → 结算 → 代投 → 再结算"（**2 个期号**）。
  ⇒ 这是**引擎的规则**（"亡国 ⇒ 战争结束 ⇒ 冷静一段"），不是 RL 侧能绕开的。
- ★ **本机跑全量 RL 回归要按文件分批**（2026-10-23 傍晚实测）：带 torch 的用例里有几个
  跑**真训练/真 rollout** ⇒ 51 个 RL 测试文件一次跑完要 **≈ 20 分钟**
  （实测：49 个文件 **539 项 / 984 s**；两个最慢的另计 219 s）⇒ 本地要分批跑，
  CI（不装 torch）整份跳过 ⇒ CI 侧不受影响。
  ⇒ **本机没有 GPU** ⇒ `--device cuda` 那条路**只做过静态核对**（P2-11）。
  ★★ 顺带说明：正因为 `test_rl_rollout_budget` 以前**从没跑到过终点**（180 s 超时
  被排除在批量回归外），里面一条**旧断言**一直没被人看见 ——
  "截断边界只该有**一处**"是**单人局**的口径；多玩家局里 `done` 是**逐玩家**标的
  （每个玩家自己最后一步才是他的边界，见 `rl/train.py::_mem_windows`）
  ⇒ 断言已按现行口径改对（**逐段**核对），实现在这条上**没错**。
- ★★ **联盟口径靠"调用方把队伍表传进去"**（2026-10-23 傍晚）：`evaluate.terminal(env, me)`
  **不给 `teams`** 时会**退回旧口径**（亡国的队友算输）⇒ **新调用点必须传 `sb.team_of`**
  （`rl/sandbox.py` / `rl/mcts.py` 都传了）⇒ 这条**容易再犯**，别漏。
  ★★ **2026-10-24 补**（跑训练时发现）：`rl/train.py::_outcome_for` 原来**自己抄了一份**
    沙盒的截断分支、却**漏了 `teams`** ⇒ **被步数预算截断**的那一局里，"本国已亡、
    队伍还在"的玩家被提前判成**输**（结算侧说 `None`、训练侧说 `−1` ⇒ **两处口径分叉**）
    ⇒ 现在训练侧**真转发** `Sandbox.outcome_for`（文档里写着"转发"，代码以前是抄的），
    口径**只有一处**。
- ★ **热快照的动量只随 `league_hot` 走**（2026-10-23 傍晚）：**已被冻结**的成员
  （`kind == "snap"`，不再学）本来就没有"下一轮要接着用的动量"⇒ 不受影响；
  **在训的那批**（`mark_hot`）现在权重与 Adam 状态**一起存、一起取**。

### 目录

```
rl/
  release.toml          发行版清单
  env.py                能力客户端（唯一接缝：转发 + 现算投影 + 换词）
  sandbox.py            一局攻取国祚（RL 侧状态：回合序、待办、记忆账本）
  nations.py            短名 ⇄ 内容 ID（纯函数）
  odds.py               战斗概率（**只转发**引擎能力）
  encode.py             观测编码
  evaluate.py           评估 / 终局口径
  scoring.py            权重与奖励尺度（单一出处）
  military.py           军事层（对照线）
  teacher/              老师（v11plus）
  tasks.py              任务夹具（**只走公开能力**）
  mods/
    train/              rl:sandbox / rl:truth / rl:odds
    scenario/           rl:scenario（摆局的唯一出处）
```
