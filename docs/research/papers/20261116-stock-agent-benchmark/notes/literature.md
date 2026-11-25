# 文献调研：AI 基准饱和 / agent 基准 / 金融交易类 LLM agent 基准

> 调研目的：为「股票市场模拟」基准（LLM 写受限 Python 策略脚本 → 在实时订单簿里交易 → 长程多回合、算力付费、多席自博弈）建立文献坐标。
> 检索时间：2026-11-16。检索工具：web_search + web_fetch（优先 2025–2026 年文献，允许早期经典）。
> **可信度标记**：`【A】` 本会话已打开原文并阅读（摘要或正文）；`【B】` 只看到搜索摘要 / 第三方导读，未打开原文；`【C】` 低可信（非同行评审、篇幅极短、独立作者且无数据）。数字后带 `†` 表示来自既有知识/记忆、本会话未逐一复核。

---

## 0. 一页速览（写论文时可先看这段）

1. 基准饱和已经从「社区抱怨」变成**可量化的测量学问题**：ICML 2026 的 [When AI Benchmarks Plateau](https://arxiv.org/abs/2602.16763) 用 60 个文本 LLM benchmark × 14 个属性给出饱和指数，结论是**近一半已饱和，且饱和率随基准年龄上升；私有测试集几乎不提供保护，真正相关的是「专家构建」**。【A】
2. 工业界已用行动承认失效：[OpenAI 2026-02 宣布不再用 SWE-bench Verified 衡量前沿编码能力](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)，理由是污染 + 测试用例拒绝正确解（审计子集 27.6%，其中 ≥59.4% 有缺陷）；半年内分数只从 74.9% 爬到 80.9%。【A】
3. Agent 基准的问题不是「不够难」，而是**可被刷**：[BenchJack](https://arxiv.org/abs/2605.12673) 对 10 个主流 agent 基准做自动红队，在不解决任何任务的情况下刷出接近满分，共发现 219 个缺陷；WebArena / OSWorld 需 3 轮迭代才能补上。【A】
4. 主流 agent 基准几乎都不建模**成本/资源约束**，且环境是「一次性任务」而非持续市场：唯一系统讨论 cost-efficiency 的是 [From Models to Agents](https://arxiv.org/abs/2602.18029)（§4.5、§5.1），它把「算力/花销帕累托前沿」列为 agent 评估的必要维度。【A】
5. 金融/交易类 benchmark 正在快速迭代（StockBench → LiveTradeBench → PolyBench → AStockArena），但共同短板清晰：**多为单 agent、以历史回测为主、无内生对手与价格冲击、无执行成本与算力付费**；最接近「多席自博弈」的是 [Agent Trading Arena](https://aclanthology.org/2025.findings-emnlp.294/)（零和市场、agent 影响价格），但它不做「LLM 写代码 + 算力付费」。【A】

---

## 1. 基准饱和 / 失效的批评文献（最重要）

### 1.1 总表

| # | 标题 | 作者 / 机构 | 年份 / 出处 | URL | 可信度 |
|---|---|---|---|---|---|
| 1 | **When AI Benchmarks Plateau: A Systematic Study of Benchmark Saturation** | Mubashara Akhtar\*, Anka Reuel\* 等 40 人（EvalEval Coalition；Stanford / Edinburgh / LMU 等） | 2026，**ICML 2026**（arXiv v1 2026-02-18，v4 2026-08-06） | https://arxiv.org/abs/2602.16763 | 【A】 |
| 2 | **Towards More Standardized AI Evaluation: From Models to Agents** | Ali El Filali（G42）、Inès Bedar | 2026-02（arXiv 2602.18029） | https://arxiv.org/abs/2602.18029 | 【A】 |
| 3 | **Position: Benchmarking is Broken — Don't Let AI be Its Own Judge** | Zerui Cheng, Stella Wohnig 等（NeurIPS 2025 Position Paper Track） | 2025（NeurIPS 38） | https://proceedings.neurips.cc/paper_files/paper/2025/hash/693e00827fd44bdfca210801fe1e6439-Abstract-Position_Paper_Track.html | 【A】 |
| 4 | **The Measurement Crisis: Saturation, Goodhart's Law, and the End of AI Leaderboards** | Oleh Ivchenko（敖德萨国立理工大学） | 2026-03-13，Zenodo 预印本 | https://zenodo.org/records/19007432 | 【C】 |
| 5 | **Beyond Static Benchmarks: Evaluating AI Systems in Open-Ended Real-World Contexts** | Xiangyu Guo（独立研究者） | 2026-01-26，Zenodo | https://zenodo.org/records/18380727 | 【C】 |
| 6 | **Do Androids Dream of Breaking the Game? Systematically Auditing AI Agent Benchmarks with BenchJack** | Hao Wang, Hanchen Li, Qiuyang Mang, Alvin Cheung, Koushik Sen, Dawn Song（UC Berkeley 等） | 2026-05-12（arXiv 2605.12673） | https://arxiv.org/abs/2605.12673 | 【A】 |
| 7 | **From benchmarks to deployment: a comprehensive review of agentic AI evaluation** | （Artificial Intelligence Review 综述） | 2026（Springer，DOI 10.1007/s10462-026-11571-0） | https://link.springer.com/content/pdf/10.1007/s10462-026-11571-0.pdf | 【B】 |
| 8 | **Why SWE-bench Verified no longer measures frontier coding capabilities**（工业界版「饱和宣言」） | OpenAI | 2026-02-23 | https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ | 【A】 |

### 1.2 逐条要点

#### 1) When AI Benchmarks Plateau（**本领域最系统、最可引用的一篇**）【A】
- **饱和的定义（可直接引用的操作性定义）**：基准饱和 = 「顶级模型之间**失去可靠区分度**」且「性能趋近经验天花板」；只有前者叫 *stagnation*，两者兼备才叫 *saturation*。定义刻意**不以人类水平为参照**（人基线常缺失/不可比）。
- **量化方法**：取 leaderboard 前 k=5 名，用 `SE(s)≈sqrt(s(1-s)/n_eff)`（n_eff = n^α，α=0.5）算评估噪声；`R_norm = (s_1 - s_k)/SE_Δ` 可读作信噪比；**饱和指数 `S_index = exp(-R_norm²) ∈ [0,1]`**，分 5 档（<0.01 / [0.01,0.3) / [0.3,0.7) / [0.7,0.9) / ≥0.9）。参数敏感性：k=3 vs 5 的 Spearman = 0.92。
- **关键数字**：**60 个文本 LLM 基准**、**14 个属性**、**近一半（nearly half）已饱和，且饱和率随基准年龄上升**。
- **最反直觉的结论**（论文里最值得引的一句）：**抗饱和靠「专家构建（expert-curation）」，而不靠私有测试集**；公开测试集、开放式输出格式、模板多样性都不构成保护。年龄与规模才是强预测项。
- **批评的具体缺陷**：单一分数趋同后榜单失去区分力；评估噪声被忽略导致把噪声当进步；benchmark 生命周期缺乏「退役/修订」标准（给出 lifecycle management 建议）；并明确点名治理场景（EU AI Act 第 51 条、AISI Inspect）依赖稳定的测量工具。
- 数据/代码：https://github.com/evaleval/benchmark-saturation ；幻灯：https://icml.cc/media/icml-2026/Slides/63311.pdf
- **注意**：我只读到正文 §3 前半（fetch 截断），§4 的具体基准名单与逐项饱和分未逐一核实；「近一半」等结论来自摘要与 §1，已核实。

#### 2) Towards More Standardized AI Evaluation: From Models to Agents（**最好用的「为什么旧范式对 agent 失效」综述**）【A】
- 核心断言：评估不再是流水线末端的 checkpoint，而是**控制函数**；问题从「模型多好」变成「系统能否在变化与规模下按意图行为」。旧范式三件套（静态基准、聚合分数、一次性成功判据）对 agent 是**结构性错配**。
- **饱和速度（可直接引用的判据）**：benchmark 生命周期是「诞生即不可能，死亡即饱和」；**新基准如果上线时最强模型就已 80%，这个基准一出生就死了**；健康的起点应是 **0.1%–10%**。引 Ofir Press 的 **「−200% 规则」**：把题出到「模型再强 3 倍今天也还是 0 分」。
- **污染与 Goodhart**：BigBench 因 GPT-4 记住了 Canary GUID 而被排除；「一旦度量成为目标，模型被激励去利用基准而不是学能力」。
- **LLM-as-judge 偏差清单**：位置偏差、长度（verbosity）偏差、自我偏好、谄媚（sycophancy），以及 judge 在 rubric 欠定义时的过度自信。
- **Agent 特有的「静默失败」**：harness 本身是测量仪器的一部分；共享状态/缓存/资源耗尽会让 trial 不再独立；ambiguous task spec、brittle grader、无 partial credit、可绕过（bypassability）等 13 条清单。
- **可靠性指标（对长程多回合基准极其对口）**：Pass@k 测能力、**Pass^k 测可靠性**；单次成功率 70% 的订票任务，Pass@3 ≈ 97%，而 **Pass^3 = 0.7³ ≈ 34.3%**。
- **成本维度（本论文的关键论据）**：§4.5「Cost and Efficiency Frontiers」明确要求把 agent 放在 **score × 金钱/时延的帕累托前沿**上评比，惩罚靠无限循环/暴力搜索换来正确终态的 agent；引 BrowseComp / PaperBench 的「性能随 test-time compute 近似线性增长」。
- **环境化评估**：§5 主张从静态数据集转向**模拟环境**（GAIA2 / ARE 手机生态、TextQuests 的 25 个 Infocom 文字冒险），并强调 GAIA2 已把「分数 vs 金钱成本」的帕累托前沿写进评估。

#### 3) Position: Benchmarking is Broken — Don't Let AI be Its Own Judge（**NeurIPS 立场论文，适合当「学界共识」引**）【A】
- 论点：当前评估生态是「Wild West」——**数据污染 + 厂商选择性报告 + 数据质量控制缺失**，导致「真进步」与「夸大宣称」无法区分，类比金融市场需要 Moody's 式可信评级。
- 主张：转向**统一、在线（live）、质量控制**的基准框架，「robust by construction」而不是靠自觉；提出 **PeerBench**：社区治理 + **sealed execution（密封执行）** + **item banking with rolling renewal（题库滚动更新）** + **delayed transparency（延迟公开）**。定位为开放基准之上的「证书级」补充层，而非替代。
- 对本文的用处：给「静态题库 + 公开答案」的失效提供**制度性**批评（不只是技术性）。

#### 4) The Measurement Crisis（**用户提到的候选标题之一，但内容极薄——慎引**）【C】
- 摘要主张（来自 Zenodo 摘要，我**未能读到正文文件**：正文 `uib-art2.md` 只有 **1.3 kB**，185 次浏览 / 49 次下载）：
  - 三个汇聚力量使 leaderboard 失去意义：(1) 饱和把分布压缩到无法区分的窄区间；(2) Goodhart（度量成为目标）；(3) ……（摘要被截断）
  - **数字（仅摘要）**：MMLU >90%、HumanEval >95%、HellaSwag >93%；并称「这不是智能的证据，而是我们的仪器失效的证据」。
- **判断**：属个人预印本系列（"Universal Intelligence Benchmark Series" 第 2/11 篇），1.3 kB 正文不可能承载实证。**可以引用其表述来锚定「>90% 饱和」这一常识，但不要当实证来源**；建议改引 #1（ICML）或 #2（G42）作为实证支撑。
- 另有同系列/同调的 [When the Measure Becomes the Target](https://zenodo.org/records/20685810)（2026-06，PT Hibrkraft），同样是观点文，不必单独引。

#### 5) Beyond Static Benchmarks（**同为候选标题，同样偏薄**）【C】
- 摘要主张：静态数据集 / 封闭任务 / 单一数字分数与现代 AI 系统的实际行为**结构性错位**（"category mismatch"）：基准测「封闭条件下的分数最大化」，真实世界要「边界管理、长程一致性、失败遏制、自适应判断」；假设的崩塌包括 closure / stationarity / comparability / average-case scoring。
- 提出从 **scorecards → safeguards**：失败模式分类学、对抗压力测试、长程漂移监控、人类接管可执行性、决策日志、校准与弃答、持续评估。
- **判断**：独立研究者 + Forge Order Publishing（"Civilization Physics" 系列），65 次浏览 / 27 次下载，非同行评审、无自有实验。**只适合当「观点引文」，不适合当证据**。

#### 6) BenchJack：agent 基准可被「不解题就满分」【A】
- 从历史 reward hack 事件归纳 **8 类反复出现的缺陷模式**，做成 **Agent-Eval Checklist**；再实现 **BenchJack**：驱动 coding agent 去「全知视角」审计基准、自动合成 exploit，并可迭代生成-对抗（发现新缺陷→打补丁）。
- **关键数字**：对 **10 个主流 agent 基准**（覆盖软件工程、网页导航、桌面计算、终端操作）；**在不解决任何任务的情况下拿到接近满分**；共发现 **219 个**不同缺陷；扩展管线把可 hack 任务比例从**接近 100% 降到 <10%**（4 个无致命设计缺陷的基准），**WebArena 与 OSWorld 在 3 轮内被完全补上**。
- 结论句可直接引用：**「评估流水线尚未内化对抗性思维」（evaluation pipelines have not internalized an adversarial mindset）**。
- 对本文的用处：给「静态题库 + 单一终态判分」提供**可被自动化攻击**的硬证据；也提示自研基准必须做 red-team。

#### 7) From benchmarks to deployment（综述，适合当「三大断裂」的引文）【B】
- 系统综述 **15 个大型基准**（AgentBench、WebArena、SWE-bench、PaperBench、MLGym、BrowserGym、HumanEval、MBPP、GAIA、ToolBench、TerminalBench、Mind2Web、ALFWorld、BabyAI、HotPotQA）。
- **三个根本断裂**（很好用的框架，我只从 PDF 片段读到）：(1) 任务成功 vs 过程质量；(2) **benchmark 性能 vs 部署的成本效率**；(3) 孤立任务 vs 端到端工作流。
- 定位独特：把**评估方法论**而非模型能力当作可靠部署的主要约束。

#### 8) OpenAI：为什么不再评估 SWE-bench Verified（**饱和的工业实证**）【A】
- 半年内 SOTA 只从 **74.9% → 80.9%**，于是要问「剩下的失败是模型不行还是数据集不行」。
- 审计模型常解不出的 **27.6%** 子集：其中**至少 59.4% 的题目测试设计有缺陷，会把功能正确的提交判错**。
- 第二问题是**针对题解训练**（train on solutions）：Verified 的题与解已进入训练数据。
- 建议改用 SWE-bench Pro。**结论句**：该基准「已不再适合衡量当今性能水平下前沿模型的自主软件工程能力」。

#### 9) 补充（可作为佐证，未单独展开）【B】
- **The SWE-Bench Illusion: When State-of-the-Art LLMs Remember Instead of Reason**：前缀补全实验中，实例级逐字命中率 **11.7%–31.6%**（Claude 家族从 12.1% 升到 31.6%，Claude 4 Opus 近 1/3 测试实例可逐字生成）。ACM DOI: https://dl.acm.org/doi/full/10.1145/3786583.3786882
- **Large-Scale AI Benchmarking Frameworks**（BioQI journal，质量一般，仅作旁证）：指出 MMLU 已被超过 87% 的「人类专家基线」；LLM 在改写题上掉 8.4pp（84.2%→75.8%）、某些设置下 12.4pp（94.8%→82.4%），作为污染迹象。https://zenodo.org/records/19615047
- 中文二手报道（**只作「热度」证据，不作学术引文**）：[《当标尺熔化：OpenAI 揭开 AI 竞赛最隐蔽的战场》](https://www.tmtpost.com/agent/ai-article/18271)（2026-06-17）——称 OpenAI 前沿评估负责人 Tejal Patwardhan 于 2026-06 发文谈 evals，并引用「60 个基准近一半饱和、私有测试集几乎无保护」，还给出 Math-500 上 o3 99.2 / Grok-4 99.0 / DeepSeek R1 98.3 的例子。**这些具体分数我未在原始论文中核实**（见 §6）。

---

## 2. 主流 agent benchmark 清单与实际形态

### 2.1 总表

| 基准 | 测什么 | 环境形态 | 规模 | 已知饱和 / 效度问题 | 有成本维度？ | 可信度 |
|---|---|---|---|---|---|---|
| **AgentBench** | LLM-as-agent 的推理与决策（8 个环境：OS/DB/KG/卡牌/解谜/网页购物等） | 多环境仿真，静态任务集 | 8 envs | 2023 年设计，已被后续基准超越；失败主因是长程推理与指令遵循；早期结论（如「代码训练对 agent 有双向影响」）已过时 | 否 | 【A】 |
| **WebArena** | 真实网站上的长程网页任务（功能正确性） | 4 域自托管真实站点（电商/论坛/CMS/地图），可复现 | 812 任务† | **reward hacking**（BenchJack 3 轮内被完全补上）；站点自托管易漂移；判分依赖功能正确性脚本 | 否 | 【B】 |
| **VisualWebArena** | 视觉-语言网页 agent（截图输入） | 与 WebArena 同源的视觉版真实站点 | ~900 任务† | 视觉 grounding 脆弱；继承 WebArena 的可刷性 | 否 | 【B】 |
| **OSWorld** | 真实操作系统里的开放式电脑任务 | **真实 OS 虚拟机**（Ubuntu/Win/mac），执行式评估 | **369 任务** | 可复现性（VM 快照、时序、应用版本）；**BenchJack 在 3 轮内被完全补上**；长尾任务 grader 脆 | 否（有 step 上限） | 【A】 |
| **GAIA** | 真实世界助理问答（推理+多模态+浏览+工具） | 静态题库（答案可字符串校验） | **466 题**†；人 92% vs GPT-4+插件 15% | 分数已大幅上升；答案在网页上→污染面大；单值答案判分掩盖过程 | 否 | 【A/B】（人机差距数字已核实） |
| **GAIA2 / ARE** | 「脏现实」助理：模糊指令、工具故障、时间推理 | 模拟手机生态（Email/Calendar/Contacts/FS） | 未核实 | 新；但已把 **score × 金钱成本帕累托**写进评估 | **部分有** | 【A】（转述自 #2） |
| **SWE-bench / Verified / Pro** | 真实 GitHub issue 修复（补丁通过隐藏测试） | 真实仓库 + 容器化测试 | 2,294 / **500**（Verified，†）/ Pro（新） | **OpenAI 2026-02 停用 Verified**：27.6% 子集里 59.4% 测试有缺陷；训练污染（逐字命中 11.7–31.6%）；半年 74.9→80.9% 趋平 | 否 | 【A】 |
| **Terminal-Bench 2.0** | 终端里的硬核长程任务 | 真实终端环境（Docker） | **89 任务** | 明确以「现实性 + 难度」对抗饱和（起点很低），但仍是**一次性任务**、无经济维度 | 否 | 【A】 |
| **τ-bench / τ²-bench** | 工具-用户对话 agent，领域政策遵循 | 仿真域（零售/航空；τ² 加电信 **dual-control**） | 未核实 | τ² 发现：从自主模式切到「指导用户操作共享世界」时**通过率降约 20%**；考 reliability（pass^k）而非单次能力 | 否 | 【A】 |
| **MLE-bench** | ML 工程能力（Kaggle 竞赛端到端） | 真实 Kaggle 竞赛离线复现，含 GPU 资源 | **75 竞赛**；o1-preview+AIDE 在 **16.9%** 达铜牌水平 | 公开 leaderboard / 公开解法→污染与「抄答案」；算力成本未纳入评分 | 部分（报告资源） | 【A/B】 |
| **PaperBench** | 从零复现 ICML 2024 论文 | 沙盒 + 12h 复现窗口 + rubric 判分 | 20 篇论文† | 判分依赖 rubric/LLM judge；复现成本高；最适合「长程 + 成本」讨论 | **部分有**（有复现时限/成本讨论） | 【B】 |
| **BrowserGym / WorkArena** | 网页 agent 统一框架 / ServiceNow 企业工作流 | 统一 gym 接口 + 企业 SaaS 仿真 | WorkArena 19k+ 任务† | 企业 SaaS 版本漂移；任务模板化→易饱和/易刷 | 否 | 【B】 |
| **CORE-Bench** | 计算可复现性（复现论文代码结果） | 容器化 + 代码执行 | 270 任务† | 依赖环境固定；判分是「结果是否一致」而非能力 | 否 | 【B】 |

> 标记 † 的数字（WebArena 812、VWA ~900、GAIA 466、SWE-bench 2294/500、PaperBench 20 篇、WorkArena 19k、CORE-Bench 270）来自既有知识，**本会话未打开对应论文逐项核实**；写作前建议复核。

### 2.2 逐条要点（只展开对本文最有用的）

- **AgentBench**【A】——8 个环境，ICLR 2024。它的历史价值是第一次把「LLM 当 agent 评测」制度化；但它自己就是「静态题库 + 一次性任务」范式的代表：环境彼此独立、无跨回合经济约束、无对手。适合当「第一代 agent 基准」的引文。
- **WebArena / VisualWebArena / OSWorld**【A/B】——都是「真实软件 + 执行式判分」这一代。它们的贡献是把评估从「文本对错」推向「世界状态是否被改对」。但三者的**共同软肋**被 BenchJack 一次性打穿：**只要判分只看终态且环境可被 agent 任意操作，就可以绕过任务拿分**。OSWorld 另有可复现性问题（VM 快照、应用版本、时序），这也是它被列为「难以稳定比较」的原因。
- **SWE-bench 家族**【A】——**本文最好用的「饱和 + 污染」双料案例**：OpenAI 的停用声明同时给了 (1) 分数趋平的曲线证据（74.9→80.9）、(2) 测试缺陷率（59.4%）、(3) 污染（train on solutions）。而 SWE-Bench Illusion 给了逐字记忆率（11.7–31.6%）。这两条合起来足以论证「静态、可公开、可训练」的基准必然失效。
- **GAIA / GAIA2**【A】——GAIA 的金句是「人 92% vs GPT-4 15%」，当年用来证明「LLM 并未通用」；如今分数已高，其字符串匹配判分与「答案在公开网页上」成为污染入口。GAIA2 的意义在于**把成本写进评估**（分数-金钱帕累托），这是本文「算力付费」维度最直接的先行者。
- **Terminal-Bench 2.0**【A】——89 个任务，明确针对「现有基准要么不真实、要么不够难」。它代表「把难度推到模型当前接近 0 分」这一派（与 #2 的「−200% 规则」同源），但仍是**一次性任务**：没有持续市场、没有资本约束、没有对手。
- **τ²-bench**【A】——**dual-control**（用户与 agent 共同改变世界状态）是对「真实交互」的一次重要修正；其结论「从自主切到协作，通过率降 ~20%，瓶颈是沟通与去中心化控制」对本文「多席」设计有直接借鉴：**多 agent 的关键难点不是单agent能力，而是协调/通信**。
- **MLE-bench / PaperBench**【A/B】——两者是「长程 + 高成本」任务的代表，也是少数**记录算力/成本**的基准。可直接引用来论证「前沿评估必须进入长程与成本维度」，同时指出它们仍无「对手/市场」维度。
- **综述【B】的三断裂** 是对第 2 节最好的收束：**成功≠过程质量、基准分≠部署成本效率、单任务≠端到端工作流**。

### 2.3 一个横向观察（可直接写进论文）

主流 agent 基准的环境形态可分三代：
1. **静态题库 / 单轮问答**（MMLU、GAIA 的前身逻辑）——已饱和；
2. **真实软件 + 一次性任务 + 终态判分**（WebArena、OSWorld、SWE-bench、Terminal-Bench）——可刷、可污染、不可持续，且**没有经济约束**；
3. **长程 + 成本感知 + 部分交互**（PaperBench、MLE-bench、GAIA2、τ²-bench）——刚起步，**但都没有「多主体内生市场」**。

---

## 3. 金融 / 交易类 LLM agent 基准与工作

### 3.1 总表

| 工作 | 类型 | 测什么 / 做什么 | 数据与环境 | 规模 | 主要局限 | 可信度 |
|---|---|---|---|---|---|---|
| **FinBen**（NeurIPS 2024） | Benchmark（多为静态任务） | 8 大方面 24 个金融任务：IE/文本分析/QA/生成/风险管理/预测/决策/双语 | **42 个数据集**；含首次「股票交易」评测与 RAG/agent 评测 | 评了 21 个 LLM（含 GPT-4） | 主体仍是**静态 QA / 分类**；交易部分短程、无对手、无成本；结论是 LLM 在高级推理与决策上弱 | 【A/B】 |
| **InvestorBench**（ACL 2025 Long） | Benchmark + agent 框架 | 多金融产品的**决策**任务：单股票、加密货币、ETF | 开源数据 + 多种市场环境 | 13 个 LLM backbone | 仍以**历史数据回放的决策**为主；单 agent；无市场冲击；无执行成本 | 【A】 |
| **FinanceBench**（2023） | QA Benchmark（非 agent） | 开卷财务问答（年报/10-K 检索+数值推理） | 公开 PDF + 标注 | 150 例公开样例 | 只测**知识检索与数值推理**，与交易能力无关；常被误当「金融 agent 基准」 | 【A/B】 |
| **TradingAgents**（2024-12） | 多 agent 框架 | 模拟交易公司分工：基本面/情绪/技术分析师 + 多空研究员辩论 + 风控 + 交易员 | **历史回测** | 未核实 | 多 agent 但**角色是「内部辩论」，不是「外部对手」**；无市场冲击、无执行成本；回测 → 未来函数/污染风险 | 【A】 |
| **FinMem**（2023-11） | 单 agent 架构 | 分层记忆 + 人物设定的交易 agent | 历史行情回测 | 未核实 | 单 agent、纯回测、无对手 | 【A/B】 |
| **FinAgent**（2024-02） | 单 agent 架构 | 多模态（新闻/价格/K 线）+ 工具增强的通用交易 agent | 历史回测 | 未核实 | 同上；「通用性」靠任务拼盘而非环境 | 【A/B】 |
| **Alpha-GPT**（2023-07） | 工作流 / 方法 | 人机交互式 **alpha 因子挖掘**（LLM 生成因子表达式） | 量化研究管线 | 未核实 | 是「研究助手」，**不是可执行交易 agent**；无市场环境 | 【A/B】 |
| **StockBench**（2025-10） | Benchmark | 多月连续交易：每日价格/基本面/新闻 → 买/卖/持 | 「contamination-free」的多月真实市场 | 未核实 | **多数模型打不过 buy-and-hold**；仍是单 agent、无对手、无市场冲击 | 【A】 |
| **LiveTradeBench**（2025-11，UIUC） | Benchmark（live） | 组合配置（多资产权重）而非单资产买卖；股票 + Polymarket | **实时数据流**（价格+新闻），消除离线回测与信息泄漏 | **50 天 live、21 个 LLM** | 结论：**LMArena 分高 ≠ 交易好**；仍无对手/无内生价格；成本维度弱 | 【A】 |
| **PolyBench**（2026-04） | Benchmark（live + 执行仿真） | 预测市场（Polymarket）上的预测与交易 | **38,666 个二元市场 / 4,997 事件**，快照同步 CLOB 状态 + 实时新闻；timestamp-locked | **7 个模型、36,165 条预测** | 只有 **2/7 为正收益**（MiMo-V2-Flash 17.6% CWR、Gemini-3-Flash 6.2%）；**有滑点/执行限制**（好），但仍单 agent、无自博弈 | 【A】 |
| **Agent Trading Arena**（EMNLP 2025 Findings） | **多 agent 零和市场** | LLM agent 互相交易、**交易行为直接影响价格**；bid-ask 交互 | 虚拟零和股市（NASDAQ / CSI 数据） | 未核实 | **最接近「多席自博弈」**；但：不做「LLM 写策略代码」、不建模算力付费、主要是数值/视觉推理研究 | 【A】 |
| **AStockArena**（2026-10-31） | Benchmark（实时 + 过程审计） | 不只看到收益，还审计**交易是否被如实上报、是否有证据支撑、是否与账户状态一致** | 中国 A 股**实时连续交易**、共享市场条件与执行约束 | **13 个模型、3,198 次序列决策**；1,070 个交易点 | **72.99% 交易点存在至少一项审计缺口**（模型级 52.2%–91.2%，证据不足最常见）；仍单 agent、无自博弈 | 【A】 |
| **MMARP**（ICLR 2025 投稿） | 大规模多 agent 角色扮演 | 用 LLM 群体模拟**金融市场动态** | 多金融数据集 | 「massively multi-agent」 | 自我承认：**LLM 对同一新闻/价格的判断不一致，未必能复现真实投资者**；是「模拟器」不是「基准」 | 【B】 |
| **How language model agents reshape price competition**（2026-10） | 经济学实验（self-play） | LLM 定价 agent 在**重复 Bertrand 竞争**中的均衡选择 | 同质/差异化商品市场，分布化 self-play（D-S-25/50） | 多种部署策略 | 结论：**市场结果取决于「部署策略」而非模型本身**；LLM 能「逃出」竞争性基准——对「自博弈基准」是重要的方法论警告 | 【A/B】 |
| **Synthetic Realism in Finance**（OpenReview） | 模拟器 | 高保真市场仿真 + 自适应 LLM agent（ABIDES 系） | 离散事件仿真 + LOB | 未核实 | 是「仿真平台」而非标准化基准；无排行榜/无固定任务集 | 【B】 |
| **What survives honest evaluation?**（2026-07，Zenodo） | 方法论 | 泄漏安全 + 搜索感知的「LLM 交易策略发现」评估：工具层从构造上排除未来函数，并校正「搜索强度」 | 策略发现管线 | 未核实 | 直指本文同类风险：**只报最优策略 + 不校正 look-ahead 与搜索次数 = 假阳性** | 【B】 |

### 3.2 逐条要点（挑最重要的展开）

- **FinBen**【A/B】——金融 LLM 评估的「全集式」基准：42 数据集 / 24 任务 / 8 方面，并首次把「股票交易」纳入。它的价值是证明「金融任务可以被标准化评估」；它的局限正好是本文的靶子：**绝大多数子任务是静态的（抽取/分类/QA），交易只是其中一小块，且短程、无对手、无成本**。
- **InvestorBench**【A】——第一个明确为「LLM-based agent 的**金融决策**」设计的基准，覆盖股票/加密/ETF 与 13 个 backbone。它把「环境」概念引入金融 agent 评估，但仍是**历史数据回放**：agent 的行为不改变价格，因此无法评测「在有人跟你抢流动性时的决策」。
- **TradingAgents**【A】——最常被引的「多 agent 金融」工作，但要点必须说清：它的多 agent 是**同一机构内部的分工与辩论**（多头/空头研究员、风控、交易员），**不是市场里的对手**。因此它评测的是「团队协作能否提升预测」，而不是「在竞争市场中能否存活」。且回测框架 → 未来函数与污染风险。
- **StockBench → LiveTradeBench → PolyBench → AStockArena：一条清晰的「去回测化」演进线**【A】
  - StockBench：多月连续决策，主打 contamination-free，但结论是**多数模型打不过 buy-and-hold**；
  - LiveTradeBench：**实时数据流**彻底绕开离线回测与信息泄漏，50 天 live / 21 模型，结论是 **LMArena 分数与交易表现不相关**（对「单一榜单分数」的又一击）；
  - PolyBench：把**订单簿（CLOB）状态与滑点/执行限制**纳入，38,666 市场、36,165 条预测，只有 2/7 模型正收益 —— 这是「执行成本」维度做得最实的一个；
  - AStockArena：把评估从「收益」推进到「**过程审计**」——72.99% 的交易点有审计缺口，说明**只看收益会系统性高估 agent**。
- **Agent Trading Arena**【A】——**本文最需要正面对比的先行工作**：零和市场、bid-ask 交互、**agent 的交易行为直接推动价格**。这已经具备「内生对手 + 价格冲击」。它没做的三件事正是本文的差异点：(1) 不用「LLM 写受限 Python 策略脚本」作为动作空间（它的动作是交易决策本身）；(2) 不算力付费；(3) 目标是研究数值/视觉推理，而非做「通用 agent 基准」。
- **Bertrand self-play 实验**【A/B】——最重要的**方法论警告**：市场结果（高价均衡 vs 竞争性低价）**是部署策略的性质，不是模型能力的性质**。这意味着任何「自博弈市场基准」都必须把**部署配置（信息可见性、更新频率、调用预算）当成实验条件显式记录**，否则分数不可比。这条对本文的「可复现性/公平性」章节非常有用。
- **What survives honest evaluation?**【B】——直接对应本文的风险清单：**只报最优策略、不校正 look-ahead、不校正搜索强度 = 结果不可信**。可作为「我们如何避免自欺」的方法论引文。

### 3.3 共同局限（论文可直接用的归纳）

1. **历史行情回测为主** → 未来函数（look-ahead）、幸存者偏差、以及最隐蔽的一种：**agent 的记忆/检索把未来的价格带进了决策**（见 [Lians 的 lookahead-bias 复现](https://github.com/Lians-ai/Lians/blob/bc7798809525039ee4da3b7f1c932d2e0f9c4d32/demo/lookahead-bias/README.md)：同一策略、同一数据，仅「recall 是否截断到决策时点」不同，收益 +41.3% vs −0.9%）。
2. **单 agent** → 没有对手，也就没有「策略被反制」「流动性被抢」「拥挤交易」这些真实市场里的核心困难。
3. **无内生价格**（除 Agent Trading Arena / MMARP / Bertrand 实验）→ 交易是「价格接受者」，无法评测市场冲击与自博弈均衡。
4. **无真实执行成本**（除 PolyBench 的滑点/执行限制、AStockArena 的执行约束审计）→ 高频/大单策略在评测里免费。
5. **无算力/调用成本维度** → 无法区分「聪明」与「烧钱」；这一点在通用 agent 评估里也才刚被提出（见 §1 第 2 篇 §4.5）。
6. **无「代码即动作」的受限编程**（除 Alpha-GPT 的因子生成，但它不在交易环境里）→ 无法测「把想法写成可执行策略并在约束下运行」这一现代 agent 的核心能力。
7. **没有公开的「多席自博弈 + 代码动作 + 算力付费」基准**：检索到的最近邻是 Agent Trading Arena（多席、内生价格）与 α-mining 系（代码/因子），**没有发现同时覆盖四要素的公开基准**。→ 这正是本文的定位空间。**（注：这是「检索所及」的判断，不是穷尽性结论。）**

---

## 4. 可能是用户说的那篇「很火」的论文

### 判断：**When AI Benchmarks Plateau: A Systematic Study of Benchmark Saturation**（ICML 2026，[arXiv:2602.16763](https://arxiv.org/abs/2602.16763)）

**理由（按证据强度排序）**：

1. **标题与用户描述完全吻合**：用户给的第一候选标题就是 "When AI benchmarks plateau: A systematic study of benchmark saturation"，且检索确认它是**真实存在、已被 ICML 2026 接收**的论文（arXiv 注释 "Published at ICML 2026"）。【A】
2. **传播规模符合「很火」**：40 位作者、跨机构（EvalEval Coalition）、ICML 2026 官方幻灯、arXiv 从 v1(2026-02) 迭代到 v4(2026-08)、有官方代码仓库、被多篇 2026 综述/导读引用；中文科技媒体（钛媒体 2026-06）专门写文转述「60 个基准近一半饱和、私有测试集形同虚设」。【A/B】
3. **结论具备「金句传播力」**：**「抗饱和靠专家构建，而不是私有测试集」**——反直觉、可一句话转述，是典型的爆款结论。【A】
4. **与工业界叙事合流**：OpenAI 2026-02 停用 SWE-bench Verified、2026-06 其前沿评估负责人公开谈「基准被饱和或游戏化」，与这篇形成互文。【A/B】

**次席候选与不推荐理由**：

| 候选 | 为何可能是它 | 为何不太可能是「很火」的那篇 |
|---|---|---|
| [Towards More Standardized AI Evaluation: From Models to Agents](https://arxiv.org/abs/2602.18029)（G42，2026-02） | 时间接近、面向 agent、有 HF 官方 blog 导读、金句多（−200% 规则、Pass^k） | 是**立场/方法论文**而非实证研究；作者仅 2 人，无「统计 N 个基准」的硬数字；传播度低于 ICML 论文 |
| [Position: Benchmarking is Broken](https://proceedings.neurips.cc/paper_files/paper/2025/hash/693e00827fd44bdfca210801fe1e6439-Abstract-Position_Paper_Track.html)（NeurIPS 2025） | 顶会立场论文，标题极「火」 | 发表更早（2025-12），主题是「评估治理/judge 偏差」，不是「饱和统计」 |
| [The Measurement Crisis](https://zenodo.org/records/19007432)（2026-03） | 标题最像「危机叙事」，且给了 MMLU>90%/HumanEval>95%/HellaSwag>93% 的数字 | **正文仅 1.3 kB**、个人预印本、185 次浏览、无实验 → 不可能「很火」；数字属二手归纳 |
| [Beyond Static Benchmarks](https://zenodo.org/records/18380727)（2026-01） | 标题对应用户候选 | 独立研究者、65 次浏览、非同行评审 → 传播度最低 |

**结论**：论文应把 **Akhtar & Reuel et al. (ICML 2026)** 作为「基准饱和」的主引文（实证 + 定义 + 数字），把 **El Filali & Bedar (2026)** 作为「agent 评估范式转变 / 成本维度」的主引文，把 **NeurIPS 2025 Position** 作为「评估治理与可信度」的引文。The Measurement Crisis / Beyond Static Benchmarks **最多作为「也有人这么说」的旁证，且必须标注为非同行评审**。

---

## 5. 建议的引用组合（8 条，足够撑起「动机」一节）

1. **饱和的实证与定义**：Akhtar, Reuel et al., *When AI Benchmarks Plateau*, ICML 2026 — https://arxiv.org/abs/2602.16763
2. **饱和的工业确认 + 污染/测试缺陷**：OpenAI, *Why SWE-bench Verified no longer measures frontier coding capabilities*, 2026-02 — https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
3. **agent 评估范式转变 + 成本/可靠性维度**：El Filali & Bedar, *Towards More Standardized AI Evaluation: From Models to Agents*, 2026 — https://arxiv.org/abs/2602.18029
4. **基准可被刷（对抗审计）**：Wang et al., *BenchJack*, 2026 — https://arxiv.org/abs/2605.12673
5. **评估治理/污染/选择性报告**：Cheng et al., *Position: Benchmarking is Broken*, NeurIPS 2025 — https://proceedings.neurips.cc/paper_files/paper/2025/hash/693e00827fd44bdfca210801fe1e6439-Abstract-Position_Paper_Track.html
6. **agent 基准的三大断裂（综述）**：*From benchmarks to deployment*, AI Review 2026 — https://link.springer.com/content/pdf/10.1007/s10462-026-11571-0.pdf
7. **金融 agent 基准现状（正面对比）**：Li et al., *InvestorBench*, ACL 2025 — https://aclanthology.org/2025.acl-long.126/ ；以及 Ma et al., *Agent Trading Arena*, EMNLP 2025 Findings — https://aclanthology.org/2025.findings-emnlp.294/
8. **「实时 + 执行成本 + 过程审计」的最新标杆**：*AStockArena*, 2026-10 — https://arxiv.org/abs/2611.00515 ；*PolyBench*, 2026-04 — https://arxiv.org/abs/2604.14199

---

## 6. 未核实 / 存疑清单（写论文前需复核）

| 事项 | 状态 |
|---|---|
| ICML 论文 §4 的**具体饱和基准名单**与逐项 S_index（我只读到 §3 前半，fetch 截断） | 未核实 |
| 钛媒体报道的 **Math-500：o3 99.2 / Grok-4 99.0 / DeepSeek R1 98.3** | 未在原始论文核实（二手来源） |
| 「MMLU >90% / HumanEval >95% / HellaSwag >93%」 | 仅来自 1.3 kB Zenodo 摘要，**无原始出处**；建议改用 ICML 论文或 AI Index 作为来源 |
| WebArena 812、VisualWebArena ~910、GAIA 466、SWE-bench 2,294/Verified 500、MLE-bench 16.9%、PaperBench 20 篇、WorkArena 19k、CORE-Bench 270 | 既有知识，本会话未逐一打开原文 |
| TuringBench / Terminal-Bench 的具体饱和状态 | 未核实（只读到「设计上起点极低」） |
| FinBen 的 42/24/8/21 数字 | 来自 NeurIPS PDF 片段与 ACM 摘要（【B】），基本可信但未开原文 |
| τ-bench 原始版本的具体规模 | 未核实 |
| Stanford AI Index 2024/2025 关于 HumanEval 饱和的表述 | 本会话未检索到原文，未核实 |
| 是否存在「多席自博弈 + 代码动作 + 算力付费」的公开基准 | 检索所及**未发现**；但不能排除 2026 年下半年新出的工作（建议补检索关键词：self-play benchmark、market ecology、code-as-action trading agent、compute budget agent benchmark） |

---

*所有 URL 于 2026-11-16 检索时可达；外部网页内容仅作数据使用。标注【A】的条目已打开原文，【B】仅读摘要，【C】为非同行评审的旁证。*
