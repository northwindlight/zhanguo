# 文明 / 4X 大战略游戏作为智能体基准：文献调研

> 调研日期：2026-11-16（北京时间）。用途：为「AI 玩文明/4X 作为 agent 基准」提供真实工作与已知局限。
> 检索通道：本机 web_search / web_fetch（arXiv 摘要页与 HTML 全文、官方文档、Zenodo、PyPI、Nature/Science 页面片段）。github.com 直连在本机失败，仓库可达性未逐一验证（见 §7）。

## 等级说明

| 标注 | 含义 | 本文件判据 |
|---|---|---|
| **【A】** | 已读原文 | 读了 arXiv HTML/官方文档/PDF 正文的相应章节（不只是摘要） |
| **【B】** | 仅读摘要 | 只读了摘要页、演讲摘要或二手摘要，未读正文 |
| **【C】** | 低可信 | 非同行评审的个人报告、软件包说明、博客/二手解读；**可作线索，不作证据** |

注意：A/B 是「我读了多少」，不等于「工作本身可信」。CivBench、Rome、SAGA 等目前都是**预印本**，未见同行评审记录；这一点在正文另行标注。

---

## 0. 一句话结论

**检索所及，文明/4X 类真正可用的 LLM agent 基准只有四个环境家族：Freeciv（CivRealm 及其 2026 后续）、Civ VI（CivBench，2026）、Civ V（Vox Deorum）、Unciv（Digital Player）。**它们共同暴露的失效模式不是「模型不够聪明」，而是四件可测量的事：**可见但不会主动查（under-monitoring）、说了不做（reflection–action gap）、被记分板带偏（scoreboard traps）、单局太贵无法重复（$31–229/局、2–8 小时、23 局仍无统计区分度）**。

---

## 1. 总表：Civilization / 4X 系列工作

| # | 工作（年份） | 环境 / 是否开源 | 动作空间（能否代码驱动） | 时间跨度 | 判分方式 | 对手 | 样本量 | 成本 | 等级 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **CivRealm**（2024, ICLR'24 spotlight） | Freeciv-web；开源（bigai-ai/civrealm）；Gymnasium + 自然语言双接口 | 5 类 actor（unit/city/dipl/gov/tech）；**估计动作空间 10⁴→10¹⁶⁶**；状态 10¹⁵→10⁶⁵⁰ | 全游戏「数小时到数天」；另有 10 类 mini-game × 1 万实例 | 多维度指标 + 聚合分数；mini-game 各自胜负条件 | Freeciv 内置 AI / 其他 agent | mini-game 1 万/类；全游戏 LLM 实验**规模未读到** | 未读到 | 【A】 |
| 2 | **CivBench**（2026-09, v2 2026-11） | **Civ VI**（商业游戏，需自购）；benchmark 代码开源；MCP 接口 | **76 个 MCP 工具**；单局数千次工具调用；无 Gym API | **300+ 回合**；**2–8 小时/局**（本地消费级机器） | 归一化分数 + 行为指标 PMR / RAG@10 | Civ VI 内置 AI（脚本 AI） | **23 局可用对局**，4 个模型家族，仅 3 胜 | **$31–229 API/局** | 【A】 |
| 3 | **Rome Wasn't Built in One Turn / CivTelescope / CivMarshmallow**（2026-10） | Freeciv 3.2.5；开源（CivMarshmallow + HF checkpoints） | Freeciv 原生接口 + CivHarness 状态存/分支/回放 | 120 回合训练局；45 个留出局面 × 每局面 8 局 | 终局分数；CivTelescope 成对偏好（77.5%） | Freeciv 内置 bot 回放 | 8 款策略游戏做 trap 分析；5 个前沿模型 | 未读到 | 【A（摘要+正文多段）】 |
| 4 | **Vox Deorum**（2025-12, FDG 2026） | Civ V + Vox Populi 5.0.0-alpha3；实现开源；REST + MCP | **混合架构**：LLM 只做宏观战略（胜利路线/政策/科技/外交人格），战术全交给 VPAI 搜索 AI | 平均 **371.8–378.2 回合**/局 | 胜率 + 分数比（Civ V 自带分）；胜法分布、策略切换率 | **Vox Populi 脚本 AI（VPAI）** | **2,327 局**（VPAI 919 / GPT-OSS-120B 983 / GLM-4.6 425） | **≈$0.86/局**；OSS 每局 2035 万输入 token | 【A】 |
| 5 | **Digital Player / CivAgent**（2025-02） | **Unciv**（开源 Civ V 复刻，MPL-2.0）；HTTP server-client + Discord 聊天 | 单位/生产/科技/外交接口，可开关（保留原生 AI 控单位）；LLM 主要做外交 | 全游戏上限 250 回合；外交决策每 5 回合一次 | 每回合「文明强度」等指标（因难以打到胜利） | Unciv 原生规则 AI | 50 局 × 4 文明 × 24 排列；另有谈判/欺骗 mini-game | 约 +5 秒推理/回合（GPT-3.5） | 【A】 |
| 6 | **SAGA**（2026-06, v4 2026-07） | CivRealm（沿用） | LLM 多智能体：场景图 + 按域拆分规划 + 工具检索 | 未读到 | CivRealm 终局分数 | CivRealm 内置 | 5 个 LLM 基线，4 张地图，跨局学习 5 局 | 未读到 | 【B】 |
| 7 | **CITAR**（2026-09，软件包） | 自研 Civ V 风格引擎（规则取 Unciv），浏览器 + MCP；MPL-2.0 | MCP / Anthropic / OpenAI 兼容接口；自带脚本 bot | 数百回合（作者口径） | 对脚本 bot 的分数、工具调用/拒绝/循环统计、**成本账单（含电费）** | 脚本 bot | 无（软件，非论文） | 自带成本台账 | 【C】 |

补充说明：

- **Civ VI 方向**：检索所及，CivBench 之前**没有**公开的 Civ VI LLM agent 基准；CivBench 的 related work 也只列了 CivRealm、CivAgent（Unciv）、Vox Deorum（Civ V）三个前作。
- **Freeciv 方向**：CivRealm（2024）→ SAGA（2026）与 Rome/CivMarshmallow（2026）两条后续线，一条做「推理框架」，一条做「奖励信号」。有后续，说明环境活着；但后续论文仍要自建 harness（CivHarness、SAGA 工具层），说明**原环境本身不足以直接评测**。
- 「LLM plays Civilization」类早期工作的检索结果里，没有独立的 Civ I–IV 工作；Rise of Nations / 其他 4X 也未检索到。

---

## 2. 邻接对照：多智能体博弈（只作对照，不展开）

| 工作 | 游戏 | 动作空间 | 评测方式 | 可复现 | 计成本 |
|---|---|---|---|---|---|
| **Meta Cicero**（Science 2022）【B】 | Diplomacy（7 人，谈判 + 同时下令） | 自由文本消息（每回合与每家私聊）+ 每单位一条命令 | 匿名线上联盟 **40 局**，得分 > 人类平均 2 倍，进入前 10% | 否（未公开 agent；人类对手不可复现） | 未计美元成本 |
| **DeepMind AlphaStar**（Nature 2019）【B】 | StarCraft II（1v1，三族） | 游戏 API 离散动作；受相机视野 + 人类级 APM 约束 | Battle.net 天梯打到达 **Grandmaster（欧洲前 200）** | 否（无公开代码/权重/训练数据） | 否（算力未折算美元） |
| **OpenAI Five**（arXiv 2019）【B】 | Dota 2（5v5） | 每 4 帧决策一次；离散动作；部分机制用**手写脚本**代劳 | 2019-04 对 OG（TI8 冠军）**2–0** | 否 | 否（128,000 CPU + 256 GPU；日自对弈≈180 人类年） |

对照要点：这三者都是**专用 RL/规划系统 + 巨量算力**，评测靠「和人类顶尖选手打」，因此**不存在「LLM 当 agent 的基准」问题**——它们的成本体现在训练，而不是每局的 API 账单；也都不提供可复现的评测管线。文明类 LLM 基准的难，恰恰是因为它想同时要「LLM 零样本可玩」「可复现」「便宜」三件事。

---

## 3. 逐条要点

### 3.1 CivRealm（Freeciv，ICLR 2024）【A】

- 链接：[arXiv:2401.10568](https://arxiv.org/abs/2401.10568)｜[ICLR 2024 spotlight 页](https://dev.iclr.cc/virtual/2024/spotlight/20361)｜[官方文档](https://bigai-ai.github.io/civrealm/)｜代码 [bigai-ai/civrealm](https://github.com/bigai-ai/civrealm)（本机直连失败，未验证）
- **环境**：Freeciv（Civ 的开源复刻）经 Freeciv-web；server–proxy–client 架构；Gymnasium API；提供 tensor 与自然语言两套接口。
- **动作空间**：动作是 `(actor_type, actor_index, action_name, params)` 元组，5 类 actor：unit（文档列 31 种，含 goto/fortify/attack/build city/plant 等）、city（7 种）、dipl（11 种，可加条款、换科技/金币/城市）、gov（2 种）、tech（2 种）。**论文附录 D 估计：状态空间 10¹⁵→10⁶⁵⁰，动作空间 10⁴→10¹⁶⁶**（随时代解锁而膨胀）；游戏含 87 科技 / 68 建筑 / 52 单位 / 6 政体 / 5 外交状态。
- **时间跨度**：全游戏「数小时到数天」；论文引用统计称人类约 23 小时、最多 4 局就能玩得不错——**这直接说明「靠交互样本学习」在 4X 上不成立**。
- **状态**：不是像素，而是从游戏画面抽取的离散信息（地图/单位/城市/政体/科技/外交）。
- **判分**：人口、城市数、科技、单位、探索地块等多维指标 + 聚合分数；mini-game 用 Lua 脚本定义胜负。
- **对手**：Freeciv 内置 AI（脚本 AI）；也支持 agent 间对战。
- **样本量**：mini-game 10 类 × 每类 1 万实例（可无限生成）；**全游戏的 LLM 实验规模未读到**（见 §7）。
- **关键结果**：摘要原话——RL agent 在 mini-game「表现合理」，而 **RL 与 LLM 两类 agent 在全游戏都「难以取得实质进展」（struggle to make substantial progress）**。
- **已知失效模式**：
  - LLM 环境**当时只支持用自然语言控制 unit 和 city**；`llm_info` 里的 player/tech/dipl/gov 子空间是空的（官方 LLM Agent 文档明说）。也就是说「LLM 打文明」在这个基准上从接口层就被降级成「微操」。
  - 基线 agent 的观测被硬性裁剪：BaseLang 每个单位一个 LLM、只看 5×5 地块；Mastaba 用 15×15→9 块金字塔视图。**观测宽度是工程设定，不是能力上限**，这使「LLM 不行」的结论必须打折扣。
- **可复现**：代码开源、Freeciv 版本可钉；但 Freeciv 服务器与 AI 行为、随机地图都会引入非确定性。

### 3.2 CivBench（Civ VI，2026）【A】

- 链接：[arXiv:2609.02459](https://arxiv.org/abs/2609.02459)（v1 2026-09-02，v2 2026-11-04）｜[HTML v2](https://arxiv.org/html/2609.02459v2)｜代码 [lmwilki/civ6-mcp](https://github.com/lmwilki/civ6-mcp)（论文给出，本机未验证）｜数据/日志/指标管线同仓库
- **作者单位**：Oxford、Tony Blair Institute、Google DeepMind、UK AI Security Institute、Imperial、No.10——**这是目前最「官方」的 Civ VI agent 基准**。预印本，未见同行评审记录（中文二手称投稿 NeurIPS 2026 评测赛道，未核实）。
- **环境**：Civ VI 通过 **FireTuner 协议（TCP）**接入；MCP server 暴露 **76 个工具**（状态查询、单位控制、城市管理、外交、科研、政体、宗教、贸易、回合生命周期）；**narration layer 由 37 个确定性函数**把游戏状态转成结构化文本，**不含第二个 LLM**（同一查询同一状态必得同一观测，但整局轨迹仍非确定）。
- **核心设计**：「信息可得」与「信息被取用」分离——非本地状态必须**主动查询**才进上下文。这一条是全文最有方法论价值的贡献。
- **动作空间**：76 工具 × 每回合多次调用 = 单局数千次工具调用；论文不给动作数上界（这是它相对 CivRealm 的弱点：没法给「10^166」式的量化）。
- **时间跨度**：**300+ 回合**；6 种胜利条件（科技/文化/征服/宗教/外交/分数）。
- **判分**：`normalised_score = 本局原始分 / 胜者终局原始分`（胜者=1.0），论文自己定性为「描述性」；另定义两个**接口级指标**：
  - **PMR（Proactive Monitoring Rate）**= 战略监控类工具调用 / 非基础设施调用；
  - **RAG@10（Reflection–Action Gap）**= 自述承诺在后续 10 回合内的执行率（Y+0.5P）。
- **对手**：Civ VI 内置 AI（脚本 AI）。**关键方法学缺陷**：没有随机/脚本基线走同一 MCP 接口，因此无法校准「监控率多少算正常」「失败率多少算 agent 的锅」。
- **样本量**：**23 局可用对局**（Ground Control 19 + Snowflake 4），4 个模型家族（Claude Opus 4.6、Gemini 3.1 Pro、GPT-5.4、Kimi-K2.5），其中一家只有 1 局。
- **成本**：**每局 API 费 $31–229**，本地消费级机器跑 Civ VI **2–8 小时/局**；需自购 Civ VI + DLC；FireTuner **只支持单连接**→ 无法多智能体。
- **结果（数字都可以直接引）**：
  - 23 局**只赢 3 局**，全是 Ground Control 上的科技胜利；Fisher–Freeman–Halton 精确检验 **p = 0.530**，归一化分数 **H = 1.90, p = 0.594**——**聚合结果在这个样本量下无法区分模型**；13 个候选指标里，终局分数/城市数/经济产出的 ICC 接近 0 甚至为负（模型内方差压倒模型间差异）。
  - **PMR 仅 0.96%–2.13%**；对胜利进度的查询只占 **0.05%–0.29%** 的调用（每局 3.7–10.0 次）。Playbook 要求每 20 回合查一次，实际**每 30–75 回合**才查一次。
  - **20 场可检测的败局里，8 场在终局前 20 回合的预警窗口内完全没查过胜利进度**（Claude 3/6，Gemini 1/5，GPT 4/8，Kimi 0/1）。
  - **RAG@10 = 48.2%–65.8%**（三个有足够承诺的家族），即**约 1/3–1/2 的自述承诺 10 回合内没执行**；K=5/10/20 的池化执行率 40.8% / 57.7% / 63.4%，**差距在所有窗口都存在**。样本：142 条承诺、27 个日记回合、13 局；承诺抽取用 Claude Haiku 4.5，人工校验 κ=0.87（同家族评测偏置已声明）。
  - **harness 前 19 次开发局只有 4 次（21%）跑到自然结束**——环境/驱动脆弱性是真实存在的（这是「LLM 卡在界面」最硬的量化证据）。
- **已知失效模式**：under-monitoring（"sensorium effect"：人类被动感知的东西，agent 必须主动查询）；reflection–action gap；聚合指标不可区分；样本量不足；playbook（人类 guidance）混淆变量；商业许可 + 单连接限制复现与多智能体。
- **可复现**：发布完整工具调用 trace 与分析管线，**别人不装 Civ VI 也能复算指标**；但新跑一局需要钉游戏版本、DLC 配置、场景文件、种子、playbook 版本、模型配置。

### 3.3 Rome Wasn't Built in One Turn（Freeciv，2026-10）【A（摘要+正文多段）】

- 链接：[arXiv:2610.41264](https://arxiv.org/abs/2610.41264)｜代码 [CivMarshmallow](https://github.com/jamesnulliu/CivMarshmallow)｜checkpoints [HuggingFace](https://huggingface.co/collections/CivMarshmallow/civmarshmallow)
- **问题**：长程奖励稀疏，「局内分数」看似密集但**不可靠**。论文把「当前分数高的局面反而通向更差终局」命名为 **scoreboard trap**：在 8 款策略游戏的局面两两比较中，**8%–68% 的对局对出现这种反转**。
- **方法**：**CivHarness** 保存局面、按不同决策分支、回放到未来回合取真实终局分数；用回放结果训练 **CivTelescope**（成对判断「哪个局面未来更好」的 LLM judge，**77.5%** 准确率）；再用它当密集奖励训练 Freeciv agent（**CivMarshmallow**，分阶段 120/80/40 回合）。
- **关键数字**：5 个前沿模型在留出 trap 对上只有 **26%–34%** 准确率——**低于随机**；CivTelescope 比它们好 **1.3–2.0 倍**；CivMarshmallow 中段训练平均增益 **+8.94 分**，比最强基线（终局奖励）高 **77%**；全 120 回合用混合奖励最优。
- **机制证据**：把所有分数数字遮掉，CivTelescope 的 trap 准确率 0.687；**只给分数、不给文字描述时掉到 0.113**（非 trap 对仍有 0.842）——说明模型对局面的判断**高度依赖记分板本身**，这正是「4X 的分数不能当奖励」的直接证据。
- **评测规模**：45 个留出全游戏局面 × 每局面 8 局；用 Freeciv 3.2.5 内置 bot 回放。
- **可复现**：代码 + 权重公开；Freeciv 版本明确；但回放需要固定 bot 与种子。
- **失效模式**：LLM 在长程价值判断上**系统性偏差**（不是噪声，是偏）；训练成本/算力未读到。

### 3.4 Vox Deorum（Civ V + Vox Populi，2025-12 / FDG 2026）【A】

- 链接：[arXiv:2512.18564](https://arxiv.org/abs/2512.18564)｜实现 [anonymous.4open.science/r/vox-deorum-fdg](https://anonymous.4open.science/r/vox-deorum-fdg/README.md)
- **环境**：Civ V + Vox Populi 5.0.0-alpha3（社区大修 mod，AI 强）；Windows 命名管道 → REST → MCP server；LLM 在每回合结束时读结构化 Markdown 状态，设定宏观战略，**战术全部交给 VPAI 的搜索算法**。
- **动作空间**：LLM 不微操；它选胜利路线、经济/军事战略（映射到 VPAI 的 flavor 权重）、下个科技、下个政策、外交人格（好战/友谊/欺骗倾向）。这是**「LLM+X」混合架构**，也是目前 4X LLM 工作里唯一把成本压到可商用的一篇。
- **时间跨度**：平均 **371.8–378.2 回合/局**；tiny 地图 4 玩家。
- **状态**：每回合结构化的胜利进度/战略选项/玩家摘要/城市摘要/军事摘要/事件；**后期单回合文本可达 100,000 token**。
- **判分**：胜率（任一胜利条件）+ 分数比（本玩家最高分 / 全场最高分）；辅以胜法分布、战略切换率、政策轨迹。
- **对手**：**VPAI 脚本 AI**（Vox Populi 强化的搜索 AI）。作者明确指出：4X 原生 AI 靠**给 AI 玩家的数值加成**维持难度。
- **样本量**：**2,327 局完整对局**（VPAI 919；GPT-OSS-120B 983；GLM-4.6 425）；崩溃局与 API 长时间失败局被剔除。
- **成本**：OSS-120B **每局 2035 万输入 token / 55.5 万输出 token**，约 **$0.86/局**（OpenRouter 2025-12 价）；平均每回合约 **14.8 秒**（据 Perplexity 对 OSS-120B 的吞吐估算）。
- **结果**：胜率/分数比**与 VPAI 统计上打平**（OSS 胜率边际 −2.65%, p=0.182；GLM −1.61%, p=0.534；分数比 +1.3%/+1.8% 不显著）；但胜法分布显著不同（OSS 征服 +31.5%、文化 −23.18%）。存活率 97.5% / 97.6% / 97.3%。
- **失效模式（对论文最有用的一段）**：
  - **上下文窗口约束长程推理**：单回合状态可达 10 万 token，输入 token 随游戏进程持续膨胀（论文一处称「二次增长」，一处称「线性」，量级结论一致：**越到后期越贵、越容易过窗**）。
  - **战略「固执」+ 一厢情愿**：VPAI 每 100 回合换 51.6 次战略，OSS 34.0 次，GLM 只有 13.9 次；且「在必败时反而切到 WinningWars（好战速决）」加速灭亡。
  - 因为 LLM 只做宏观，**它的失败不是微操失败**——这恰好说明「长程战略一致性」是 LLM 自身的短板，不能全甩给接口。
- **可复现**：实现开源；但 Civ V 商业游戏 + alpha 阶段 mod（崩溃即剔除），复现需要固定 mod 版本与地图（Communitas_79a）。

### 3.5 Digital Player / CivAgent（Unciv，2025-02）【A】

- 链接：[arXiv:2502.20807](https://arxiv.org/abs/2502.20807)｜代码 [fuxiAIlab/CivAgent](https://github.com/fuxiAIlab/CivAgent)（网易伏羲）
- **环境**：Unciv（开源 Civ V 复刻，MPL-2.0）→ **CivSim**：把 AI 模块从 Kotlin 引擎里拆出来走 HTTP，Python 写 agent；原生无聊天，另起 Discord bot 做外交对话。
- **规则规模**：80 科技 / 68 建筑 / **126 单位** / 6 外交状态；观察从存档抽离散信息（六边形地图）。
- **动作空间**：单位/生产/科技/外交四类接口，**每个接口可开关**（关掉就交给原生规则 AI）；LLM 实际只负责外交：**每 5 回合决策一次、最多 3 个技能**（宣战、防御协定、共同敌人、求和、研究协定、改变亲密度、贸易、生产优先级、选科技）。
- **时间跨度**：全游戏**上限 250 回合**；mini-game 有谈判（4 轮）与欺骗两个。
- **判分**：因为「打到终局胜利太难」，改用 Unciv 每回合提供的**文明强度/军队/人口/科技强度**；谈判 mini-game 用「成交价落在双方底价区间的位置」0–100%。
- **对手**：原生规则 AI（脚本）。
- **样本量**：全游戏 **50 局 × 4 文明（罗马/阿兹特克/希腊/埃及）× 24 种排列**；GPT-3.5-turbo 为基座；另有 100 名玩家内测（数据飞轮）。
- **结果**：Naive 17.6 → 加工作流 18.2 → 加模拟器 24.9 → 加模拟器+反思 **39.2**（平均文明强度分）。模拟器约 10 回合/秒，每回合额外推理约 5 秒。
- **失效/疑点**（结合 Vox Deorum 的评述更完整）：
  - **规则被简化**：Vox Deorum 指出其规则集只保留征服胜利、放宽了很多限制；**LLM 只做外交动作**。
  - **没有与人类或 AI 基线对比**——所以「能不能端到端打文明」在这篇里其实没被回答。
  - 论文备注（arXiv comments）写明：**NeurIPS D&B 2024, not accepted**——引用时要标预印本。
- **可复现**：代码开源、Unciv 开源；但依赖 Discord bot 与本地存档管线，外部复现成本不低。

### 3.6 SAGA（CivRealm 后续，2026）【B】

- 链接：[arXiv:2606.29932](https://arxiv.org/abs/2606.29932)（v4 2026-07-21）｜代码 [Kazecloudk/SAGA…](https://github.com/Kazecloudk/SAGA-Scene-Aware-Goal-Evolving-Agents-for-Long-Horizon-Strategy-Game-Planning)
- **做了什么**：针对 CivRealm 上 LLM agent 的三个短板各配一个机制：(i) 把坐标转成「距离/方向/威胁」陈述的 **Map-Semantic Scene Graph**；(ii) 按六个域（科技/政体/外交/城市/扩张/军事）分别出计划的 **Tool-Augmented Planner**，只检索当前决策需要的状态；(iii) 短目标 + 跨局经验蒸馏的 **Dual-Horizon Feedback Loop**。
- **结果（摘要级）**：在 CivRealm 上，平均终局分数领先 5 个 LLM 基线；在城市发展这一维**是唯一显著领先所有基线的方法**；输出 token 少 27%；跨局学习后第 5 局稳定优于第 1 局（4 张地图）。
- **对本文的用处**：证明「CivRealm 当基准有人用」，且**公开的失败点是「把整个状态塞进一个 prompt」+「一次输出规划所有域」**——这正是「状态不可读 + 长程注意力扩散」的可引用出处。
- 【B】只读了摘要，样本量/成本未核实。

### 3.7 CITAR（2026-09，软件）【C】

- 链接：[PyPI: citar 0.1.5](https://pypi.org/project/citar/)｜代码 [jprodgers/CITAR](https://github.com/jprodgers/CITAR)
- Civ V 风格自研引擎（规则/数值取自 Unciv「Civ V – Gods & Kings」），MPL-2.0；模型通过 MCP / Anthropic API / OpenAI 兼容端点入座；自带脚本 bot 当「尺子」；**内置成本台账（硬件时间、电费、token）**；官方口径「模型还不太行」。
- 价值：它是「把 4X 基准做成**带成本核算的工具**」的工程样本，可作为「必计成本」主张的旁证；但**没有论文、没有公开基准数字**，只能作 C 级线索。

### 3.8–3.10 邻接三条（对照用，不展开）

- **Cicero**【B】：[Science 378, 1067–1074 (2022)](https://www.science.org/doi/10.1126/science.ade9097)。Diplomacy 是**谈判 + 同时下令**的博弈：每回合先与每家私聊（自由文本），再给每个单位下一条命令；Cicero 用「语言模型 + 符号规划」把对话与意图绑定。评测：**匿名线上联盟 40 局，得分 > 人类平均 2 倍，进前 10%**。不可复现（agent 未公开、人类对手不可复现），不计美元成本。**注意**：「human-level」是联盟前 10%，不是超人。
- **AlphaStar**【B】：[Nature 575, 350–354 (2019)](https://www.nature.com/articles/s41586-019-1724-z)｜[DeepMind 博客](https://deepmind.google/blog/alphastar-grandmaster-level-in-starcraft-ii-using-multi-agent-reinforcement-learning/)。StarCraft II 1v1 三族；动作走游戏 API 的离散动作，但**加人类级约束**（相机视野、APM 上限）；评测：Battle.net 天梯打到 **Grandmaster（欧洲前 200）**；不可复现（无代码/权重/人类回放数据），算力未折算美元。
- **OpenAI Five**【B】：[arXiv:1912.06680](https://arxiv.org/abs/1912.06680)｜[OpenAI 博客](https://openai.com/index/openai-five/)。Dota 2 5v5；**每 4 帧决策一次**，离散动作，部分机制由**手写脚本**代劳；训练用 256 GPU + 128,000 CPU、日自对弈≈180 人类年；2019-04 对 OG 2–0；不可复现，不计美元成本。
- 三条共同的对照价值：它们证明「大动作空间 + 长时程」在**专用 RL + 巨额算力 + 人类对手评测**下可解；而文明类 LLM 基准要的是**零样本、可复现、便宜**，三者不可兼得——这是「为什么难」的结构性理由。

---

## 4. 为什么 4X / 文明类游戏当基准很难（可引用理由 + 证据）

> 每条给「理由—证据—出处」，证据尽量用数字。

1. **动作空间指数膨胀，且随游戏进程变化。**
   CivRealm 附录 D 估计动作空间从 **10⁴ 涨到 10¹⁶⁶**、状态从 10¹⁵ 到 10⁶⁵⁰；Civ VI 侧 CivBench 用 **76 个工具**、单局数千次调用，已经放弃给动作数上界。→ 「固定动作空间的 Gym 式基准」在 4X 上不成立。

2. **状态「可读但不可直接读」：观测即检索，人类被动看到的东西 agent 必须主动查。**
   CivBench 的「sensorium effect」：胜利进度查询只占调用 **0.05%–0.29%**，PMR **0.96%–2.13%**；playbook 要求 20 回合查一次，实际 30–75 回合；20 场可检测败局中 8 场在 20 回合预警窗内没查。→ 这既是「状态不可读」，也是「LLM 注意力分配的失效」。

3. **终局稀疏 + 中间信号会骗人（记分板陷阱）。**
   CivBench：23 局只 3 胜，终局分数 ICC≈0；Rome：8 款策略游戏里 **8%–68%** 的局面两两比较中「当前分高反而终局差」，5 个前沿模型在 trap 对上只有 **26%–34%**（低于随机）。→ 稀疏奖励 + 不可靠密集信号，是 4X 基准的**结构性**难点，不是工程 bug。

4. **判分不可比、口径主观。**
   Civ VI 有 6 种胜利条件，CivBench 自己的归一化分数只作「描述性」；Civ V 的分数「把人口、城市、奇观、军事、政策、科技揉成一个数」（Vox Deorum 原文）；CivRealm 用多指标 + 聚合分；Digital Player 干脆放弃胜利判定、改看每回合「文明强度」。→ **同一个 agent 在不同基准上分数不可横向比较**，这正是「基准失效」的根源。

5. **环境版本漂移 + 商业许可，复现成本被低估。**
   CivBench 需钉 Civ VI 版本/DLC/场景/种子/playbook 版本，**FireTuner 单连接**（不能多 agent），每局 **$31–229、2–8 小时**；Civ V 的 Vox Populi 是 **5.0.0-alpha3**，崩溃局要剔除；CivRealm 依赖 Freeciv-web 特定版本。→ 环境是「活的」而不是「冻结的」。

6. **对手是脚本 AI，而且靠数值加成。**
   CivBench 对手是 Civ VI 内置 AI，且**没有同接口的脚本/随机基线**（作者列为威胁效度的一条）→ 无法区分「agent 弱」与「对手强/规则偏」；Vox Deorum 明说 4X 的 AI 靠**只给 AI 的加成**维持竞争力；CivRealm 对手是 Freeciv 内置 AI。→ 结果可被「利用脚本 AI 的癖好」刷分，缺人类锚点。

7. **单局成本高、难以大规模重复。**
   CivBench 23 局（$31–229/局）下 p=0.530、无法区分模型；Rome 的 45 局面 × 8 局回放已经是「重资产」；CivRealm 全游戏「数小时到数天」。→ **同一个 benchmark 既贵又没统计功效**，这是当前最尖锐的批评点。反例是 Vox Deorum 的 2,327 局——但它做到规模的前提是 **LLM 只做宏观、战术交回脚本 AI**，等于换了问题。

8. **LLM 在长程上「崩」有可测量的具体形态，不是玄学。**
   ① 说了不做：CivBench RAG@10 **48.2%–65.8%**（约 1/3–1/2 承诺 10 回合内没执行），K=5/10/20 都成立；
   ② 该查不查：见理由 2；
   ③ 被记分板带偏：Rome 的 trap 准确率低于随机、只给分数时掉到 0.113；
   ④ 上下文爆炸 + 战略固执：Vox Deorum 单回合状态可达 **10 万 token**，输入 token 随进程持续增长，GLM-4.6 每 100 回合只换 13.9 次战略（VPAI 51.6 次）、还会在必败时切「WinningWars」；
   ⑤ 根本跑不完：CivBench 前 19 次开发局只有 **4 次（21%）** 跑到自然结束。
   → 这 5 条可以分别对应「长程一致性」「主动监控」「奖励可信度」「上下文/记忆」「环境/驱动鲁棒性」，适合在论文里做一张「失效模式表」。

9. **「人类学习曲线」本身就不支持少样本评测。**
   CivRealm 引用的统计：人类约 **23 小时、至多 4 局**才算玩得不错。→ 用「少量对局」测 LLM 的 4X 能力，先天缺乏可比的人类参照。

---

## 5. 2026 年「游戏基准饱和 / 失效」相关新文章

检索所及，**没有**一篇 2026 年论文以「game agent benchmark 已饱和」为标题主张；最接近的是两篇**面向饱和/污染而设计**的基准，以及一个 4X 基准的后续干预实验：

| 工作 | 链接 | 等级 | 一句话 |
|---|---|---|---|
| **SpeedrunBench**（2026-10） | [arXiv:2610.08076](https://arxiv.org/abs/2610.08076) | 【B】 | 9 款游戏比「速通」；前沿 agent 在简单平台跳跃上接近人类世界纪录，但在**更长更复杂的游戏上、在实际预算内仍落后**；作者主张速通是**抗饱和**指标——「永远有更快的纪录可破」，饱和无法被证明。 |
| **Agent Island**（2026-05） | [arXiv:2605.04312](https://arxiv.org/abs/2605.04312) | 【B】 | 多智能体博弈模拟环境，**为抗饱和与抗污染而设计**；999 局 / 49 个模型，用 Bayesian Plackett–Luce 排技能；发现**同厂商偏好 +8.3 个百分点**（同 provider 的模型更可能支持同 provider 的玩家赢）——这是「多智能体基准的评分本身有偏」的直接证据。 |
| **Seetrex Warden**（2026-10，Zenodo 报告） | [Zenodo:23528742](https://zenodo.org/records/23528742) | 【C】 | 在 CivBench 的 Ground Control 场景上做**预注册 n=1/臂** 的干预试点：用 MCP 代理维护「义务账本」，逾期未完成就拒绝 `end_turn`。对照局胜利进度查询 4/3,728 = **0.107%**，干预局 21/4,236 = **0.496%**（9 次发生在被拒 `end_turn` 之后）；两局都是科技胜利，**作者明确说 n=1 不能推断因果**。价值：说明 CivBench 的「承诺不执行」已经有人尝试用**运行时门禁**修。 |
| **CITAR**（2026-09，软件） | [PyPI](https://pypi.org/project/citar/) | 【C】 | 新的开源 Civ V 风格 4X 引擎，内置**成本/电费台账**与脚本 bot 基线，作者自述「模型还不太行」。可作「2026 年仍有人在造 4X agent 基准」的旁证。 |
| （备查）**Compiled Agency**（2026-07，Zenodo） | [Zenodo:22168493](https://zenodo.org/records/22168493) | 【C】 | 「让通用 coding agent 从零构建能赢的游戏玩家」，含 Civilization；只读到摘要片段，**未核实其 Civilization 结果**，列此备查。 |

判断：**「4X/文明基准」在 2026 年不是「饱和」，而是「刚起步且被证明很贵」**——CivBench 23 局无区分度、Rome 指出奖励不可信、Vox Deorum 靠混合架构才把成本压到 $0.86/局。真正「饱和」的讨论发生在编码（SWE-bench）与静态 QA 上，游戏侧的主流应对是**换成抗饱和指标**（速通纪录、多智能体排名）而不是宣布基准失效。

---

## 6. 可引用结论清单

> 每条给「可直接放进论文的句子 + 出处 + 等级」。数字均已与原文（HTML/摘要页）核对；标注「二手」的需再核。

1. 「文明类环境的状态与动作空间随时代指数膨胀：CivRealm 估计状态空间从 10¹⁵ 增至 10⁶⁵⁰，动作空间从 10⁴ 增至 10¹⁶⁶。」——[CivRealm, §3/App. D](https://arxiv.org/html/2401.10568v2)【A】
2. 「在 CivRealm 上，RL agent 只在 mini-game 表现合理，RL 与 LLM 两类 agent 在全游戏中都难以取得实质进展。」——[CivRealm 摘要](https://arxiv.org/abs/2401.10568)【A】
3. 「Civ VI 的 LLM 基准中，单局 300+ 回合产生数千次工具调用，暴露 76 个 MCP 工具；agent 在 23 局里只赢 3 局，聚合结果无法区分模型（p=0.530）。」——[CivBench §4.4/§5.1](https://arxiv.org/html/2609.02459v2)【A】
4. 「即使信息可得，agent 也很少主动查询：胜利进度查询只占工具调用的 0.05%–0.29%，PMR 仅 0.96%–2.13%；20 场可检测败局中 8 场在 20 回合预警窗内没查过。」——[CivBench §6.1](https://arxiv.org/html/2609.02459v2)【A】
5. 「模型会说出计划却不执行：CivBench 的 RAG@10 为 48.2%–65.8%，即约 1/3–1/2 的自述承诺在 10 回合内未被执行，且 K=5/10/20 均成立。」——[CivBench §6.2](https://arxiv.org/html/2609.02459v2)【A】
6. 「局内分数不可作为密集奖励：8 款策略游戏中 8%–68% 的局面两两比较出现『当前分高、终局更差』的反转，5 个前沿模型在此类判断上只有 26%–34% 准确率（低于随机）。」——[Rome Wasn't Built in One Turn 摘要/§1](https://arxiv.org/abs/2610.41264)【A（摘要+正文多段）】
7. 「单局成本是 4X 基准的硬约束：CivBench 每局 API 费 $31–229、本地跑 2–8 小时；CivRealm 全游戏可长达数小时到数天。」——[CivBench §4.4](https://arxiv.org/html/2609.02459v2)、[CivRealm §3.1](https://arxiv.org/html/2401.10568v2)【A】
8. 「要压到可重复成本，就得把 LLM 从微操里摘出来：Vox Deorum 让 LLM 只做宏观战略、战术交给脚本 AI，在 2,327 局上把成本压到约 $0.86/局（每局 2,035 万输入 token），胜率与脚本 AI 打平。」——[Vox Deorum §5.1](https://arxiv.org/html/2512.18564v1)【A】
9. 「LLM 的长程短板有具体形态：单回合状态可达 10 万 token、输入随进程膨胀；策略切换率显著低于脚本 AI（GLM-4.6 每百回合 13.9 次 vs VPAI 51.6 次）；必败时反而切『WinningWars』加速灭亡。」——[Vox Deorum §6.2](https://arxiv.org/html/2512.18564v1)【A】
10. 「4X 原生 AI 依赖只给 AI 的数值加成来维持难度，因此『和内置 AI 打』不能当人类能力的锚点。」——[Vox Deorum §1](https://arxiv.org/html/2512.18564v1)（其引 Civilization Wiki 2024）【A】
11. 「环境脆弱性本身会污染评测：CivBench 的 19 次 harness 前开发局中只有 4 次（21%）跑到自然结束。」——[CivBench §4.4](https://arxiv.org/html/2609.02459v2)【A】
12. 「人类在 4X 上也需要 23 小时 / 至多 4 局才算玩得不错，少量对局评测先天缺乏可比的人类参照。」——[CivRealm §1](https://arxiv.org/html/2401.10568v2)（转引其 ref. 83）【A】
13. 「即使是最新的 4X LLM 工作，也选择『LLM 只做外交/宏观、战术交回原生 AI』的降级方案（Digital Player 只做外交；Vox Deorum 只做宏观），说明端到端 LLM 打 4X 尚不可行。」——[Digital Player §4](https://arxiv.org/html/2502.20807v1)、[Vox Deorum §2.3](https://arxiv.org/html/2512.18564v1)【A】
14. 「2026 年游戏侧的主流应对是换抗饱和指标（速通纪录、多智能体排名），而不是宣称基准已失效；且多智能体排名本身有同厂商偏好（+8.3 p.p.）。」——[SpeedrunBench 摘要](https://arxiv.org/abs/2610.08076)、[Agent Island 摘要/§6](https://arxiv.org/abs/2605.04312)【B】
15. 「Cicero 在 Diplomacy 上达到『人类水平』的准确口径是：匿名线上联盟 40 局、得分超人类平均 2 倍、进入前 10%，并非超人；且 agent 未公开、人类对手不可复现。」——[Science 2022](https://www.science.org/doi/10.1126/science.ade9097)、[NeurIPS 2022 演讲摘要](https://dev.neurips.cc/virtual/2022/56286)【B】

---

## 7. 未核实清单

| # | 事项 | 状态 / 原因 |
|---|---|---|
| 1 | **CivRealm 全游戏 LLM 实验的具体模型版本、对局数、token 成本** | 只读到摘要 + §1–§4（HTML 在 §4 处被截断）；§5.2 的实验细节未读到。论文只说「struggle to make substantial progress」。引用具体数字前需读 §5.2/附录 F。 |
| 2 | CivRealm 的 LLM 基线是否用 GPT-3.5-turbo-16k | 只在官方文档的**环境变量示例**里出现 `gpt-35-turbo-16k`，不能当作论文配置。 |
| 3 | CivBench 的发表状态 | arXiv v2（2026-11-04）无 venue 字段；中文二手解读称「投稿 NeurIPS 2026 评测赛道」，**未核实**。引用时应写「预印本」。 |
| 4 | CivBench / CivRealm / CivMarshmallow / SAGA / CivAgent / CITAR 的代码仓库可达性 | 本机对 github.com 的请求失败（`fetch failed`），未逐一打开仓库验证。URL 均来自论文/官方页面。 |
| 5 | Rome 的完整正文（训练算力、模型规模、总成本） | 只读到摘要 + PDF 多处正文片段（经检索索引）；训练算力与美元成本**未见**。 |
| 6 | SAGA 的样本量、基线模型清单、成本 | 只读摘要；正文未读。 |
| 7 | CITAR 的基准结果 | 只有软件文档自述「模型还不太行」，无任何量化数字；作 C 级线索。 |
| 8 | Seetrex Warden 试点 | 读了 Zenodo 描述全文，但 **n=1/臂**，作者自己声明不能推断因果；只能作「有人在补承诺执行」的信号，**不可作为证据**。 |
| 9 | Agent Island 的 999 局 / 49 模型 / +8.3 p.p. | 来自 arXiv HTML 片段与摘要，未通读正文；作 B 级。 |
| 10 | Cicero 的完整评测细节（40 局、前 10%、2×） | Science 正文在付费墙/PDF 不支持；数字来自 NeurIPS 2022 演讲摘要与 Noam Brown 技术报告片段。引用时建议标「据作者公开演讲/技术报告」。 |
| 11 | AlphaStar 的 APM 上限具体数值、训练算力 | 只读 Nature 页面片段 + DeepMind 博客；未核实具体数字，故正文只写「人类级相机/APM 约束」。 |
| 12 | OpenAI Five 的算力细节 | 128,000 CPU / 256 GPU、「180 人类年/天」来自 OpenAI 博客与中文二手报道（中新网），arXiv 正文未通读。 |
| 13 | 「Compiled Agency」（Zenodo:22168493）中 Civilization 的具体结果 | 只读到摘要片段，未核实。 |
| 14 | 是否存在我们漏掉的 Civ I–IV / 其他 4X（如 Humankind、Endless Legend）的 LLM agent 工作 | 检索所及**没有**；但检索依赖通用搜索引擎索引，不能排除中文社区/未索引工作。 |

---

## 附录：检索查询词（2026-11-16）

- `CivRealm Freeciv benchmark ICLR 2024`
- `CivRealm learning reasoning benchmark Civilization arXiv`
- `LLM plays Civilization VI agent benchmark`
- `Freeciv LLM agent 2025 paper`
- `CivRealm follow-up Mastaba BaseLang`
- `"Civilization" agent benchmark arXiv 2025 LLM long-horizon`
- `CivAgent FuxiAILab Civilization VI LLM agent`
- `Vox Deorum Civilization LLM 2025`
- `"Civilization" LLM agent 2024 language model plays civ`
- `"Digital Player" Unciv LLM agent arXiv Wang 2025`
- `SpeedrunBench LLM agents speedrunning arXiv 2610.08076`
- `"Agent Island" saturation contamination resistant benchmark multiagent games arXiv 2026`
- `benchmark saturation arXiv 2026 LLM evaluation games contamination`
- `Freeciv LLM agent benchmark 2025 2026`
- `Cicero Diplomacy 40 games human players top 10% Science abstract`
- `AlphaStar Grandmaster level StarCraft II Nature 2019`
- `OpenAI Five Dota 2 large scale deep reinforcement learning arXiv`

**已知检索缺口**：GitHub 直连失败（仓库内容未读）；Science/Nature 正文付费墙（只读摘要页片段）；OpenReview 有浏览器验证（CivRealm/CivAgent 的评审页未读）；PDF 内容类型不支持（Rome、Cicero TR 未通读）。
