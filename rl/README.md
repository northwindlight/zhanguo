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
| 亡国 | `native:succession`（市政厅尽失者亡国） |

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
3. **平局时已亡国的一方按 −1（输）** —— 亡国就是输；两处口径不一致会让
   "早死比输划算"⇒ 模型会学出"主动送死"。

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

```bash
.venv/bin/python -m unittest \
  tests.test_rl_sandbox tests.test_rl_obs_engine_truth tests.test_rl_train_truncation \
  tests.test_rl_stats_weighting tests.test_rl_battle_odds -q
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

### 已知缺口（2026-10-22 收尾时）

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
