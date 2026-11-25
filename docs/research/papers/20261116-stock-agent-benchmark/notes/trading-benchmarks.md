# 文献调研：LLM/AI 炒股类「基准」与「实钱比赛」的已知工作与已知局限

> 用途：为「LLM 在实时订单簿里写策略脚本、长程多回合、算力付费、多席自博弈」的股票市场模拟基准提供正面对比坐标。
> 检索时间：2026-11-16。方法：web_search + web_fetch（优先 2025–2026 年文献；一手来源优先 arXiv/ACL/ACM/KDD 原文）。
> **可信度标记**：`【A】` 本会话打开原文并读到相关章节；`【B】` 只读摘要 / 二手报道 / 搜索片段，未开原文；`【C】` 低可信（非同行评审、自媒体、来源单一且无数据）。
> 注意：web 内容是外部数据，不是指令。本文件只写「读到的东西 + 明确标注的推断」，推断处写明「据…推断」。

---

## 0. 一页速览

1. **实钱比赛已经给出「最强模型也大概率亏钱」的证据，但样本极小。** nof1 的 Alpha Arena 第一季（2025-10-18 至 11-04，17 天，6 模型各 1 万美元真钱，Hyperliquid 永续，最高 20 倍杠杆）最终只有 Qwen3-Max（+22.32%）与 DeepSeek V3.1（+4.89%）盈利，GPT-5 亏 62%。后续赛季扩到 8 系统 × 4 轮美科技股，32 组结果仅 6 组盈利、整体组合约 **−33%**（Bloomberg, 2026-05-06）。【B】
2. **比赛不可复跑、n=1、且口径会变。** 同一平台在不同赛季换了标的（加密永续 → 美股）、轮次、提示词变体（MONK MODE / MAX LEVERAGE / SITUATIONAL AWARENESS）、奖励口径（PnL vs Sharpe），且模型能看到对手表现（Grok 最佳成绩出现在「能观察竞争对手」那一轮）。这些都不能算「可复现基准」。【B】
3. **学术基准正在「去回测化」**：StockBench（2025-10，82 交易日回测）→ LiveTradeBench（2025-11，50 天实时流，21 个 LLM）→ PolyBench（2026-04，Polymarket 38,666 市场 + 订单簿撮合）→ AStockArena（2026-10，A 股实时 + **过程审计**）。【A】
4. **共同短板清晰**：除 Agent Trading Arena / StockAgent 外几乎全是**单 agent**；除 PolyBench / AStockArena / StockAgent 外**不建模执行摩擦**；**没有任何一个把算力/调用成本纳入评分**（PolyBench 只报告了每模型 $1.34–$11.50 的推理花费）；live/比赛类时间跨度普遍只有 **2–12 周**，回测类最长也只是 FinMem 的 6 个月 / StockBench 的 82 个交易日。【A】
5. **最该拿来当「靶子」的三篇批评文献**：*The Alpha Illusion*（arXiv 2605.16895，把佣金/点差/冲击/token 成本逐项扣除后，TradingAgents 的 Sharpe 从 0.43 掉到 0.22、且低于 buy-and-hold）、*FinSABER*（KDD'26，20 年 100+ 标的复测后「LLM 优势消失」）、*TradingAgents 可复现性研究*（ACM，10 次同配置运行收益 5%–28%）。【A/A/B】
6. **过程审计是新增维度**：AStockArena 发现 1,070 个可审计交易点中 **72.99% 至少存在一项审计缺口**（模型级 52.2%–91.2%），说明「只看收益」会系统性高估 agent。【A】
7. **金融 QA 与交易是两类东西**：FinBen（36 数据集/24 任务/7 方面）与 FinanceBench（10,231 题/40 公司/361 份文件）是「读得懂报表」的基准，与「能不能在有人抢流动性时赚钱」无关；StockBench 的结论明确说「静态金融 QA 强 ≠ 交易强」。【A】

---

## 1. 实盘 / 实钱比赛类

### 1.1 总表

| 比赛 | 形式 | 资金 | 时长 | 参与方 | 规则要点 | 结果（二手口径） | 可信度 |
|---|---|---|---|---|---|---|---|
| **Alpha Arena S1**（nof1，2025-10-18 起） | 真实加密永续（Hyperliquid），真钱，全程公开 | 每模型 $10,000 | 17 天（至 11-04） | Qwen3-Max、DeepSeek V3.1、GPT-5、Gemini 2.5 Pro、Claude Sonnet 4.5、Grok 4 | 同数据同提示词、无人工干预、BTC/ETH/SOL/BNB/DOGE/XRP、杠杆 1–20x、目标「最大化风险调整收益」、约每 2–3 分钟一次决策 | Qwen3-Max +22.32% 第一，DeepSeek V3.1 +4.89% 第二；四家美国模型全亏，GPT-5 −62%、Grok 4 −45.3% | 【B】 |
| **Alpha Arena S1.5 / S2**（2025-12） | 同上 | 每模型 $10,000 | 约 2 周 | 新增未公开模型 Grok 4.2 等 | 同上 | 据报道 Grok 4.2 为唯一盈利者，2 周约 +12%（另有「最终权益 $13,459」的说法） | 【B】 |
| **Alpha Arena 2026 四季**（8 系统 × 4 轮） | 改为**美国科技股**，每轮独立 | 每轮每系统 $10,000 | 每轮 2 周 | Claude / Gemini / ChatGPT / Grok / Qwen 等 8 个系统 | 每轮换信号与约束：按信号交易、防御、**观察对手表现**、高杠杆 | 32 组结果仅 6 组盈利，整体组合亏约 1/3（−33%）；Qwen 一轮 1,418 笔交易 vs Grok 4.20 仅 158 笔 | 【B】 |
| **Aster "Human vs AI: Battle for the Futures"**（2025-12-09~23） | 真实加密期货，真钱（亏损由交易所承担，盈利归参赛者） | 每人/模型 $10,000，奖池 $200k | 2 周 | 70 名真人交易员 + 30 个 LLM | AI 只能用提示词：**禁止外部数据（新闻/社媒/链上）、禁止代码/Agent/记忆**，仅标准 LLM | 人类团队总亏 −32%（−$225k），AI 团队 −4.5%（−$13.5k）；个人第一是人类；**两边整体都是亏的** | 【B】 |
| **华泰柏瑞杯·全国首届 ETF AI 交易巅峰赛**（2026-06 起） | 国内 ETF **模拟**交易；分「AI 体验局 / 手动实操局 / **算法对决局**」 | 未公布 | 赛季制 | 个人 + 自带 Agent 的开发者 | 统一用券商 5 个 Skills、每日最多调用 1000 次；自带 Agent 开源且收益为正、进前 200 得 token 奖励 | 榜单：巅峰星榜（人+AI）与 Agent 开发者榜（按等值 token 发奖） | 【B】 |
| （旁证）Flat Circle 追踪的 11 个「市场竞技场」 | 多个公开 AI 交易比赛 | — | — | — | — | 11 个竞技场里**每个都至少有 1 个模型盈利，但只有 2 个竞技场的中位模型为正** | 【B】（经 Bloomberg 转述） |

### 1.2 逐条要点

#### 1) nof1 Alpha Arena（最重要、也最容易被误引）【B】
- 官方站：<https://www.nof1.ai>（"Nof1 is an AI research lab building new models for financial markets"，明确把「市场」当作**演化基准 + 训练环境**，理由正是「benchmark 很快被记住并饱和」）；比赛站 <https://alphaarena.ai>（**本会话被 Vercel 人机校验挡住，规则原文未读到**，规则细节来自财联社/BTCC/Baidu Baike 等二手，见 §6）。
- 规则骨架（多源一致）：6 个模型各 $10,000 真钱 → Hyperliquid 永续合约 → 标的 BTC/ETH/SOL/BNB/DOGE/XRP → 允许做多/做空、杠杆 1–20x → 同一提示词与同一实时数据 → 无人工干预 → 目标准则写的是「最大化风险调整收益」（排行常用 Sharpe，但媒体多按收益率报道）→ 决策过程、持仓、账户全公开。<https://www.chinastarmarket.cn/detail/2174661>
- S1 结果（17 天）：Qwen3-Max +22.32% 冠军、DeepSeek V3.1 +4.89% 亚军，其余四家美国模型亏 30%+，GPT-5 −62% 垫底。<https://en.tmtpost.com/news/7748687>、<https://csf.kiep.go.kr/newsView.es?article_id=58167&mid=a20100000000>
- **争议/局限（写论文时最有用的部分）**：
  - **盈亏口径与风险口径打架**：有复盘文章标题直接叫「排行榜骗了所有人」——DeepSeek 靠加杠杆把仓位炒到三倍抢头条，但风险调整后最好的并不是它；也就是说「谁赢」取决于用 PnL 还是 Sharpe、以及是否看杠杆与回撤。 <https://pub.towardsai.net/six-frontier-ai-models-traded-60-000-in-real-money-for-17-days-the-leaderboard-lied-to-everyone-9f73591a8d4b>【C】（Medium 自媒体，仅标题与摘要）
  - **对手可见性造成信息不对称**：Grok 的最佳成绩出现在「它可以观察竞争对手表现」的那一轮。<https://foresightnews.pro/article/detail/96850>【B】
  - **过度交易**：同一提示词下 Qwen 一轮 1,418 笔 vs Grok 4.20 158 笔；nof1 创始人 Jay Azhang 自评「现在把钱直接交给 LLM 让它自己去交易，这条路还走不通」，并指出模型不知道分析师评级、内部人交易、情绪变化等变量的权重，导致择时错、仓位错、换手过高。<https://www.business-standard.com/markets/news/ai-bots-auditioning-for-wall-street-trading-are-mostly-losing-money-126050701793_1.html>（Bloomberg/Justina Lee，2026-05-06，原文付费墙）【B】
  - **提示词变体混淆模型能力**：后续赛季出现 MONK MODE / MAX LEVERAGE / SITUATIONAL AWARENESS 等变体同场竞技，模型差异与提示词差异不可分离。<https://www.chaincatcher.com/tags/nof1>【B】
  - **样本量/时长**：每模型每季 n=1 条轨迹、17 天或 2 周、单一市场行情 + 20x 杠杆，任何排名都在噪声量级；播客与复盘都把它列为「sample size problem」。<https://podscan.fm/podcasts/indexed-podcast/episodes/did-ai-trading-agents-actually-work>【C】
- 定位判断（据上述推断）：Alpha Arena 是**极好的科普与压力测试**，不是基准——live、不可复跑、无统计功效、无对照、口径漂移。若论文要引「LLM 实盘普遍亏钱」，引 Bloomberg 那句「32 组仅 6 组盈利、整体 −33%」比引「Qwen 夺冠」稳。

#### 2) Aster Human vs AI（2025-12）【B】
- 70 真人 + 30 LLM，各 $10,000 真钱加密期货，2 周；**对 AI 的限制极狠**：标准 LLM、仅提示词、无外部数据、无代码/Agent、无记忆——等于把「信息获取」这一最重要的 agent 能力拿掉，只测「裸模型的交易直觉」。结果人类团队 −32%、AI 团队 −4.5%，两边都是亏的。<https://incrypted.com/en/the-team-of-traders-lost-to-ai-in-the-trading-tournament-by-aster/>、<https://www.htx.com.lv/zh-tc/news/ai-outperforms-humans-in-cryptocurrency-trading-tournament-w-YfoH7p8a/>
- 价值：提供「AI vs 人类」的对照，但同样 n 小、行情单一；且「AI 赢」的结论主要是**人类亏得更多**，不是 AI 赚钱。

#### 3) 国内 ETF × AI 赛事（2026）【B】
- 华泰柏瑞/华泰证券 2026-06 起「全国首届 ETF AI 交易巅峰赛」，设「算法对决局」允许自带 AI Agent，统一券商 Skills（金融分析、行情检索、A 股模拟交易、条件选股、自选股管理 5 项），**每日最多 1000 次调用**，Agent 榜按等值 token 发奖。<http://stock.10jqka.com.cn/20260605/c677251587.shtml>、<https://app.dahecube.com/nweb/wap/article.html?artid=276858>
- 另有小红书 × 国金证券「2026 全国 ETF 投资大赛」把「AI 投顾」作为卖点而非参赛主体。<http://mp.weixin.qq.com/s?__biz=MjM5MDI5MjczNA==&mid=2651939751&idx=2&sn=aba2f0cd42d7ce81101cf5ffb3fb9ad5>
- 价值：这是**中文社区真实存在的「AI 炒股赛」形态**，但都是模拟盘 + 商业营销 + 榜单驱动，没有任何统计/审计/可复现设计，只能作为「产业热度」的证据，不能作为学术引用。

---

## 2. 基准类（学术）

### 2.1 总表（核心表）

> 说明：`内生价格` = agent 的交易是否反过来影响价格；`对手` = 是否有会反应的其它参与者；`执行成本` = 佣金/印花税/点差/滑点/冲击；`算力成本` = 是否把 token/推理开销计入评估；`跨度` = 有效评估时长；`可复现` = 代码+数据是否公开且结果确定。

| 工作 | 环境形态 | 内生价格 | 对手 | 执行成本 | 算力成本 | 跨度 / 样本 | 可复现 | 已知问题 | 可信度 |
|---|---|---|---|---|---|---|---|---|---|
| **StockBench**（2025-10，清华/北邮；v2 2026-03） | 历史回测（日频，20 只高权重 DJIA 股 + 基本面 + 新闻） | 否（按开盘价撮合） | 无（单 agent） | **未见建模**（只做资金/流动性校验） | 否 | 82 个交易日（2025-03-03~06-30）；13 个 LLM + 1 被动基准 | 代码开源（github.com/ChenYXxxx/stockbench） | 单一窗口、单一市场、无成本；v2 表 2 与「多数打不过 B&H」的摘要口径**不完全一致**（见 §2.2） | 【A】 |
| **LiveTradeBench**（2025-11，UIUC） | **实时数据流**（美股 15 只 + 现金；Polymarket 10 个二元市场），组合权重动作 | 否（明说 price-taker：「个体交易对真实价格影响可忽略」，**不建模订单执行机制**） | 无（单 agent，ReAct + tool + memory） | **否**（Limitations 明列 "Transaction costs and market frictions"） | 否 | 50 天 live；21 个 LLM | 代码开源（github.com/ulab-uiuc/live-trade-bench），live 不可复跑 | 无成本、观察/动作空间受限、agent 设计被简化；结论是「LMArena 分 ≠ 交易好」「推理模型无优势」 | 【A】 |
| **PolyBench**（2026-04，川大） | Polymarket 预测市场，point-in-time CLOB 快照 + 同步新闻 | **否**（吃既有挂单流动性，不回写价格） | 无（单 agent，静态盘口） | **部分有**：按订单簿逐档扫单 → 有滑点/容量限制；未见佣金 | **仅报告**每模型推理花费 $1.34–$11.50，未进评分 | 快照 2026-02-06~12，最晚结算 02-21 → **5~15 天**；38,666 市场 / 4,997 事件 / 5,097 可交易市场 / 36,165 条预测 / 7 个 LLM | 代码 + 数据（github.com/PolyBench/PolyBench） | 极短程；单 agent；CWR 依赖「自报置信度」，而置信度本身不可校准 | 【A】 |
| **AStockArena**（2026-10，HKUST 等） | A 股**实时**连续交易沙盒 + **过程审计**（报告/委托/成交/账户四方对齐） | 否（快照价全量成交，附录另有滑点敏感性） | 无（单 agent，共享 ReAct 架构） | **有**：佣金 0.03%（最低 ¥5）+ 卖出印花税 0.05% + T+1 + 禁做空 + 单票 ≤50% + 100 股整手 | 否 | 82 个交易日（2026-03-02~06-30），246 个决策窗口（10:30/11:30/14:00），13 个模型、3,198 次决策、1,070 个可审计交易点 | 论文称将开源代码/标注/脚本 | 单条轨迹/模型、无重复实验；未闭环接入审计反馈；只有 A 股 | 【A】 |
| **AI-Trader**（2025-12，港大） | **实时**多市场（美股 Nasdaq-100、A 股 SSE-50、加密 10 对），全自动「最小信息」范式 | 否（价格接受者） | 无 | 未读到成本建模（只读前 4 节） | 否 | 美股 2025-10-01~11-07（小时频）、A 股同期（日频）、加密 11-01~14；6 个 LLM | 代码开源（github.com/HKUDS/AI-Trader） | 5 周；单 agent；结论「通用智能 ≠ 交易能力」「风控决定跨市场稳健性」 | 【A】 |
| **InvestorBench**（ACL 2025 Long，Stevens/FinAI） | 历史回放的多资产**决策**环境（股票 / 加密 / ETF），POMDP 单 agent 框架 | 否 | 无 | 未读到（据 StockBench 的批评与常识：回放式，无冲击）【推断】 | 否 | 13 个 LLM backbone；数据在 2021 年以前（被 StockBench 点名「污染+过时」） | 数据集/环境开源（PIXIU 系） | 单资产交易为主、历史数据早于主流模型 cutoff；2026 年有工作发现「没有 LLM 方法能打败 1/N 等权」 | 【A】 |
| **FinMem**（2023-11，Stevens） | 单 agent 架构：Profiling（3 种风险偏好 + 动态切换）+ 分层记忆（shallow/intermediate/deep，衰减 Q=14/90/365 天）+ 决策 | 否 | 无 | 未见建模【推断】 | 否 | 6 个月 / 5 只股票（FinSABER 表 1；表中为 TSLA/NFLX/AMZN/MSFT + COIN）；对比 DRL（A2C/PPO/DQN 等） | 代码开源（github.com/pipiku915/FinMem-LLM-StockTrading） | **复现即崩**：MSFT CR 23.261% → −22.036%、Sharpe 1.440 → −1.247；NFLX Sharpe 2.017 → −0.478；跨 cutoff 后收益掉 ~71.85% | 【A】 |
| **TradingAgents**（2024-12，UCLA/MIT 系） | 多 agent 但**机构内部分工**：分析师团队 → 多空研究员辩论 → 交易员 → 风控团队 → 基金经理 | 否 | **无**（辩论对手是同事，不是市场对手） | 否（原论文口径；The Alpha Illusion 复现时补扣） | 否 | 3 个月 / 3 只股票（FinSABER 表 1）；另有二手说法称「2024-01~03 数据、2024-06~11 日频模拟」【B】 | 代码开源（github.com/TauricResearch/TradingAgents）；**结果非确定** | 报告 Sharpe 高达 8.21（AAPL），复现时 Sharpe 期间基准歧义（8.21 vs 130.33）、B&H 基线也不可复现；10 次同配置运行最终收益 5%–28%，多数低于 B&H | 【A/B】 |
| **Agent Trading Arena**（EMNLP 2025 Findings / arXiv 2502.17967） | **零和闭环虚拟市场**：≥9 个 agent、3 只合成股票、bid-ask 定价、分红 + 每日持有成本 | **是**（价格由 agent 竞价决定，无外部数据） | **是**（零和、互相博弈） | 无明确交易费（有买卖价差机制） | 否 | 合成市场数十个交易日；另在 NASDAQ 子集上回测验证 | 代码开源（github.com/wekjsdvnm/Agent-Trading-Arena） | 市场是玩具级（3 标的、固定规则、无真实微观结构）；不测「LLM 写代码」；主要结论是**数值/视觉推理**（文本数字差、K 线图好、reflection 有帮助） | 【A】 |
| **StockAgent**（2024-07；ACM TISTCCS 2025） | 多 agent 事件驱动模拟：Investment Agent + 订单簿撮合 + BBS 论坛；2 只匿名化美股（含 1 只 IPO） | **是**（订单簿撮合、按最后一笔成交更新价格） | **是**（多投资者竞争、BBS 互相影响） | **有**：每股 0.005、单笔最低 1、最高 5.95；另有利息/贷款/破产 | 否 | 264 个模拟交易日（4 季度 × 66 天）；2 个 LLM（gpt-3.5-turbo-0125、gemini-pro-1.0） | 代码开源（github.com/MingyuJ666/Stockagent） | 只有 2 只股票、模拟参数重；目标是**行为/外部事件研究**，不是排行榜；但有显式的数据泄漏检验（匿名化 + 三次反查） | 【A】 |
| **FinBen**（NeurIPS 2024 D&B） | 金融 LLM 任务全集（36 数据集/24 任务/7 方面），含首个「股票交易」子任务 + agent/RAG 评测 | 否 | 无 | 否 | 否 | 15 个 LLM；交易子任务短程 | 开源（github.com/The-FinAI/PIXIU） | 主体是静态 QA/抽取/分类；交易部分只是一个小块；FinTrade 的 GPT-4 Sharpe 1.51 ± 1.08（标准误超过均值一半） | 【A】 |
| （对照）**FinanceBench**（2023-11） | 开卷财务问答：10,231 题 / 40 家公司 / 361 份 2015–2023 年公开文件 | — | — | — | — | 静态 | 开源（patronus-ai/financebench，公开样本 150 题） | 与交易完全无关，只测「读报表」 | 【B】 |

### 2.2 逐条要点（只写读到的，加来源）

#### 1) StockBench：第一个把「多月连续交易 + 时间分隔」做成标准的基准【A】
- 设计：投资域 = **DJIA 权重最高的 20 只**（正文出现「2020 stocks」的笔误）；数据 = 日频开盘价 + 市值/PE/股息率/交易区间 + Finnhub 检索的近 48 小时新闻（每次保留 top-5 篇）；动作 = 增/减/持有；执行 = 按开盘价把「美元目标」换成股数，超流动性就让 agent 重写；指标 = 累计收益、最大回撤、Sortino，三者 z-score 聚合排名。
- 关键数字：评估窗 **2025-03-03 至 2025-06-30，82 个交易日**；13 个模型 + 1 个等权被动基准。表 2：Kimi-K2 RT 1.9% / Sortino 0.0420 排第 1，Qwen3-235B-Ins 2.4%，GPT-5 0.3%，**被动基准 RT 0.4% / MDD −15.2% / Sortino 0.0155 排第 12/14**。
- **必须注意的口径矛盾**：摘要说「多数模型难跑赢 buy-and-hold」，但表 2 显示 11/13 个模型在 z-score 上排在被动基准之前、且 RT/Sortino 更高。→ 引用这句结论时要限定「多窗口/多设置下的平均」或干脆只引「静态 QA 强 ≠ 交易强」。<https://arxiv.org/html/2510.02209v2>
- 可引的正面点：**contamination-free 的工程做法**（数据窗在主流模型 cutoff 之后、时间戳保留、逐日推进），以及「交易能力与金融 QA 能力脱钩」这一结论。

#### 2) LiveTradeBench：用 live 数据把「回测污染」从根上绕开【A】
- 设计：动作空间是**组合权重**（15 只美股 + 现金；10 个 Polymarket 二元市场），agent = ReAct + tool use + memory + reasoning；50 天实时；21 个 LLM；指标 CR / Sharpe / MDD / 胜率 / 波动。
- 明说的假设（写论文可直接引）：因为只做 NVDA 这类高流动性资产，「个体交易对真实世界价格的影响可忽略」，因此模拟转移函数≈真实，**且「不建模低层订单执行机制」**。
- Limitations 里自己列了：交易成本与市场摩擦、观察/动作空间受限、agent 设计简化。<https://arxiv.org/html/2511.03628v1>
- 结论：LMArena 高分 ≠ 交易好；模型呈现不同「风格」（风险偏好/选股/调仓节奏）；大推理模型没有交易优势；还专门做了「agent 是不是在瞎猜」的分析。

#### 3) PolyBench：目前「执行摩擦」做得最实的一个【A】
- 规模：4,997 事件 → 38,666 个二元市场；每个快照同步 CLOB 状态 + Google News；5,097 个「可交易」市场（有订单簿 + 有新闻 + 已结算）；7 个模型共 36,165 条预测；采集窗 2026-02-06~12，最晚结算 02-21。
- 执行：模型给置信度 c，预算 B=c×$10，**从最优卖单开始逐档吃单**，得到真实投入 I 与股数 S → CWR = Σπ/ΣI；另报 APY、Sharpe、结算时间误差。这就是「有滑点、有容量限制」的模拟。
- 结果：仅 MiMo-V2-Flash（CWR 17.6%）与 Gemini-3-Flash（6.2%）为正，其余 5 个亏；且「置信度普遍很高但结果很差」（模型自评置信度与正确率脱钩）。
- 可引的独特点：**报了每个模型的推理花费**（$1.34 / $3.32 / $1.72 / $8.43 / 免费 / $11.50 / $6.85）——检索所及，这是唯一把「算力/调用成本」写进论文的交易类基准，但仅作成本表，未进入评分或净收益。<https://arxiv.org/html/2604.14199v1>

#### 4) AStockArena：把评估从「收益」推进到「过程审计」【A】
- 环境：A 股实时，13 个模型共享同一市场快照/工具/执行约束，各自独立账户 **¥100 万**；82 个交易日、每日 3 个决策窗口（10:30/11:30/14:00）→ 246 窗口、**3,198 次决策**；T+1、禁做空、单票 ≤50%、100 股整手。
- 成本：**佣金 0.03%（最低 ¥5）+ 卖出印花税 0.05%**；成交为「通过约束检查即按快照价全量成交」，另有附录 C.6 的滑点敏感性分析。
- 审计：8 项检查、3 个维度 —— O（执行可追溯：报告/委托/成交/账户变化对齐）、RK（证据充分性：引用是否捏造、是否漏掉关键事实，K 的关键事实清单在检查前冻结）、B（财务元认知：仓位/成本/盈亏/资金是否与台账一致 + 相邻判断是否自洽）。专家双人独立标注，原始一致率 95.77%、κ=0.814。
- 结果：1,070 个可审计交易点中 **781 个（72.99%）至少一项缺口**；模型级 52.2%–91.2%；证据类缺口合并 66.73%；Claude Opus 4.5 累计收益 10.84% 但联合缺口率仍 68.3%。**历史交易过滤回放**：把被标记交易替换为「不交易」后，12 个活跃模型均值 CR 分别变化 +3.65 / +8.86 / +6.11 个百分点（O/RK/B 过滤）→ 这些缺口在**实钱意义上也是有害的**。
- 自陈局限：未把审计反馈闭环进交易；每模型只跑一条轨迹、未评估方差；只有 A 股。<https://arxiv.org/html/2611.00515v1>

#### 5) AI-Trader：最「agent 本味」的 live 基准（工具全靠自己调）【A】
- 设计：agent 只拿到「持仓 + 实时价格 + 5 个 MCP 工具（查价/搜索/新闻/数学/下单）」，其它信息必须自己搜；三个市场 + 小时/日两种频率；6 个 LLM。
- 结果：美股段（2025-10-01~11-07，小时频）MiniMax-M2 CR 9.56%、DeepSeek-v3.1 8.39%，对 QQQ 1.87%；A 股段多数跑输 SSE-50（1.65%），GPT-5 −3.53%、Qwen3-Max −3.86%；加密段（11-01~14）Gemini −18.63%、GPT-5 −16.41%（「hold-to-die」）。结论：通用智能不自动转化为交易能力；**风控能力决定跨市场稳健性**；流动性高的市场更容易出超额收益。
- 噪声提醒：5 周、单次轨迹、两组不同时间段，跨模型比较的统计功效极低。<https://arxiv.org/html/2512.10971v1>

#### 6) InvestorBench：第一个「面向 LLM agent 的金融决策」基准，但仍是历史回放【A/B】
- ACL 2025 Long（pp. 2509–2525），13 个 LLM backbone，覆盖单股票 / 加密 / ETF，任务含交易与宏观指标（CPI 等）预测，提供开源数据与多种市场环境。<https://aclanthology.org/2025.acl-long.126/>
- 被后续工作点名的弱点：StockBench 说它「限于单股票交易且依赖 2021 年前数据 → 污染 + 过时」；2026 年一项对记忆型 agent 的评测发现「在 InvestorBench / PortBench 上没有任何 LLM 方法能打败 1/N 等权，频繁调仓还要付手续费、增加风险」。<https://arxiv.org/html/2610.11732>【B】

#### 7) FinMem：分层记忆的经典，也是「复现即崩」的经典案例【A】
- 架构：Profiling（专业背景 + 3 种风险偏好，CR 三日内转负就切换）+ 分层长期记忆（shallow/intermediate/deep，recency 用 Ebbinghaus 式衰减 Q=14/90/365 天；importance 按层用不同 α=0.9/0.967/0.988）+ 决策模块（top-K 记忆 + 市场事实 → 方向/理由）。数据含日频 OHLCV、新闻、10-K/10-Q。
- 自报成绩：TSLA、NFLX 上 Sharpe > 2，显著优于 DRL 基线。
- **第三方复测（关键）**：FinSABER 用同一批标的 + 偏误缓解后，MSFT 的 CR 从 23.261% 变成 **−22.036%**、Sharpe 从 1.440 变成 **−1.247**；NFLX Sharpe 从 2.017 变成 **−0.478**；「在偏误缓解设置下，Buy-and-Hold 在统计上显著优于 FinMem 与 FinAgent」。
- 另一条更狠的证据：在预训练 cutoff **之外**评估时，FinMem 的累计收益下降约 **71.85%**（QuantAgent 的 Sharpe 下降 51.48%）——量化了「语义记忆泄漏」这一污染分量。<https://ar5iv.labs.arxiv.org/html/2605.16895>

#### 8) TradingAgents：被引最多、也最该小心引用的「多 agent 金融」工作【A/B】
- 结构：分析师团队（基本面/情绪/技术/新闻）→ 多空研究员**辩论** → 交易员 → 风控团队（激进/中性/保守）→ 基金经理。注意：这里的「对手」是**同一机构内部的同事**，不是市场里的对手盘。
- 自报：在累计收益、Sharpe、最大回撤上优于基线（AAPL Sharpe 8.21 广为流传）。
- 三重质疑：
  1. **期间 / 标的极少**：3 个月、3 只股票（FinSABER 表 1 统计）。
  2. **不可复现 / 指标口径不可判定**：独立复现指出 Sharpe 的「期间基准」二义（按一种读法 8.21，另一种 130.33），且论文里的 buy-and-hold 基线本身复现不出来。<https://pypi.org/project/mesura/>【C】
  3. **扣掉摩擦就死**：The Alpha Illusion 的一年级、五标的复现在计入佣金 + 点差 + 冲击 + **token 成本**后，TradingAgents 的组合 Sharpe 由 0.43 → **0.22**（QuantAgent −0.96 → −1.15），两者都低于净 buy-and-hold；单票上 TSLA 毛 −2.01% → 净 **−10.17%**，KO 毛 −10.84% → 净 **−17.17%**。<https://ar5iv.labs.arxiv.org/html/2605.16895>
  4. 另有 ACM 会议论文《Reproducibility in the TradingAgents Framework》专门量化其随机性：同配置 10 次独立运行最终收益约 **5%–28%**，多数跑不赢被动基准；温度 0 + 固定 seed 时方差为零（说明「可复现」要靠冻结每次 LLM 调用）。DOI 10.1145/3800973.3801029。【B】

#### 9) Agent Trading Arena：最接近「多席自博弈 + 内生价格」的基准【A】
- 设计：零和闭环经济，**资产价格由 agent 的 bid/ask 行为决定，无外部数据**；≥9 个 agent、3 只合成股票、每人 100,000 虚拟币；有分红（价格锚）+ 每日持有成本（防止躺平）；agent 可聊天（chat pool）+ 反思（从 top/bottom 策略做对比学习）。指标 TR / 胜率 / Sharpe / 日均收益 / 标准差。
- 结果（合成市场）：Qwen-2.5 + 反思后 TR 38.91%；GPT-4o 纯文本 13.04%、纯视觉 17.07%、图文 26.18%，加反思 47.69%；视觉 > 文本，图文混合最好。
- NASDAQ 子集回测：GPT-4o 图文 TR 12.23%、SR 0.291，对比 MACD 7.18/0.173、StockFormer 9.05/0.073、TimesNet 11.74/0.203。
- 局限：市场是玩具（3 标的、规则固定、无真实微观结构/成本/资金约束），主要贡献是**数值-视觉推理**，不是市场仿真。<https://ar5iv.labs.arxiv.org/html/2502.17967v2>、<https://aclanthology.org/2025.findings-emnlp.294/>

#### 10) StockAgent：多 agent + 订单簿 + 社交论坛的「行为仿真」【A】
- 结构：Investment Agent（随机资金 10 万–500 万、4 种性格、可借款/破产）+ Transaction 模块（订单簿撮合，按最后一笔成交更新价；随机时钟排序防死锁）+ BBS（收盘后互相发帖，次日可见）。
- 参数：264 个模拟交易日；**交易费 0.005/股、单笔 1–5.95**；存款利率 0、贷款年化 2.7%–3.3%；外部事件（降准、加息、财报、业绩预告）按 2014–2019 真实事件设计。
- 数据泄漏处理（值得借鉴）：股票匿名化为 A/B，并用三种方式反查模型是否知道公司身份（知道公司名/板块/agent 名分别打几分）。
- 局限：只有 2 只股票、参数高度人工设定、2 个旧 LLM；目标是行为研究不是排行榜。<https://arxiv.org/html/2407.18957v2>

#### 11) FinBen（金融 LLM 任务全集）【A】
- 36 数据集 / 24 任务 / 7 方面（IE、文本分析、QA、文本生成、风险管理、预测、决策），15 个 LLM；首次把「股票交易」纳入，另有 agent 与 RAG 评测；结论：LLM 在 IE/文本分析强，在高阶推理/生成/预测弱；GPT-4 在 IE 与交易上较强，Gemini 在生成/预测上较强。
- 与交易类基准的关系：它的「交易」只是一个小 QA 式子任务（FinTrade），短程、无对手、无成本；**FinTrade 的 GPT-4 Sharpe 1.51 ± 1.08** 被 The Alpha Illusion 当作「标准误超过均值一半」的例子。<https://arxiv.org/abs/2402.12659>

---

## 3. 这类工作共同缺什么（并标出「谁补上了哪几项」）

| 缺陷 | 现状 | 已经部分补上的工作 | 仍然空着的地方 |
|---|---|---|---|
| **① 历史数据污染 / 未来函数** | 回测类普遍存在：训练语料里已有事件结果、股票池用了「今天的成分股」（幸存者偏差）、参数字段用了全期结果（look-ahead） | LiveTradeBench / AI-Trader（live 前向评估，物理上无法泄漏）；PolyBench（预测**尚未发生**的事件，结构上免疫）；StockBench（窗在 cutoff 后）；StockAgent（匿名化 + 反查） | **LLM 的语义记忆**：即便只喂当期数据，权重里也可能有事后新闻与复盘（FinMem 跨 cutoff 掉 71.85% 就是量化证据）；只有 live/前向评估能真正排除，而它带来样本量与成本问题 |
| **② 价格接受者假设** | 绝大多数：交易不改变价格 | Agent Trading Arena（bid-ask 内生）、StockAgent（订单簿撮合） | 这两者的市场都是玩具级；真实基准里**没有一个**让 agent 的订单反过来影响价格 |
| **③ 无对手 / 无内生价格** | 单 agent 是默认 | Agent Trading Arena（零和、互相博弈）、StockAgent（多投资者、BBS 互影响） | 没有「对手也是 LLM 且会被你的策略针对性反制」的评测；TradingAgents 的多 agent 是**内部辩论**，不是对手 |
| **④ 无真实执行摩擦** | StockBench / LiveTradeBench / FinMem / InvestorBench / TradingAgents 基本为 0 | PolyBench（订单簿逐档吃单 → 滑点 + 容量）、AStockArena（佣金 + 印花税 + T+1 + 整手 + 50% 上限，附录有滑点敏感性）、StockAgent（费用 + 利息 + 破产） | **市场冲击（自身交易推价）**仍无人建模；A 股类只做「快照价全量成交」 |
| **⑤ 无算力 / 调用成本** | 唯一的例外是 PolyBench 的**成本表**（$1.34–$11.50/模型），但没进评分 | 无（PolyBench 只是报告） | 没有任何基准把「token 成本 / 延迟 / 调用次数」纳入净收益或效能前沿；The Alpha Illusion 已把它列为 P5 必要条件 |
| **⑥ 单 agent** | 学术基准默认单 agent | Agent Trading Arena、StockAgent（多 agent）；TradingAgents（多角色但同机构） | 没有「多席自博弈 + 各自有算力预算」的基准 |
| **⑦ 短程 / 小样本** | 17 天（Alpha Arena）、50 天（LiveTradeBench）、82 天（StockBench/AStockArena）、5 周（AI-Trader）、5–15 天（PolyBench）、6 个月 5 标的（FinMem）、3 个月 3 标的（TradingAgents） | 只有评测框架 FinSABER 做到 **20 年 / 100+ 标的 / 滚动窗口**（KDD'26） | 基准本身几乎没有 > 1 年的；短窗 Sharpe 的标准误会淹没系统差异 |
| **⑧ 无法复现** | LLM 采样随机 + 提示词/工具/版本漂移 | 开源代码的：StockBench、LiveTradeBench、PolyBench、FinMem、TradingAgents、StockAgent、AI-Trader、Agent Trading Arena | 即使有代码：TradingAgents 同配置 10 次收益 5%–28%；多数论文不报随机种子/温度/模型版本/日期；live 类**原则上不可复跑** |
| **⑨ 比赛靠运气 / 不可复跑** | Alpha Arena 每季 n=1 轨迹、17 天、20x 杠杆；Aster 2 周；国内赛为模拟盘榜单 | 无 | 没有比赛做重复实验、置信区间、多行情分层；Flat Circle 追踪 11 个竞技场仅 2 个「中位模型为正」直接说明结果由行情与个别模型驱动 |

### 3.1 想写「本文补了什么」时的最短对照
- 本文若做：**内生价格 + 多席自博弈 + 执行摩擦 + 算力付费 + 代码动作空间 + 长程**，则：
  - 内生价格/对手 → 与 Agent Trading Arena、StockAgent 同类，但两者都不做「LLM 写受限 Python 策略脚本」；
  - 执行摩擦 → 与 AStockArena、PolyBench 同类，但两者都不做自博弈；
  - 算力付费 → **全领域空白**（PolyBench 只报表未计分）；
  - 长程 → 只有 FinSABER 的评测框架，但那是对已有 agent 的事后评测，不是新环境。
- 因此最稳的定位句是：「据检索所及，**没有公开基准同时覆盖『内生价格/多席 + 代码动作 + 执行摩擦 + 算力成本 + 长程』**」——注意这是检索所及的判断，不是穷尽性结论。

### 3.2 三篇「共同缺陷」的权威背书（可直接引）
1. **The Alpha Illusion**（arXiv 2605.16895，2026-05）：端到端 LLM 交易 agent 报出的 alpha **不应视为可部署证据**；给出 P1–P6 最低报告协议（时间完整性 / 动态股票池 / 反事实稳健性 / 概率校准 / 真实实现 / 多 agent 去聚合）；事实：FinMem/TradingAgents/FinAgent/FinCon/QuantAgent 的 40 个「系统 × 摩擦项」单元里 **35 个未建模**。<https://arxiv.org/abs/2605.16895>
2. **FinSABER**（KDD'26，arXiv 2505.07078）：两年、100+ 标的、含退市股、滚动窗口 → 之前报告的 LLM 优势显著衰减；LLM 策略「牛市过度保守、熊市过度激进」；表 1 直接列出各家「评估期 / 标的数 / 是否开源」：FinMem 6 个月 5 只、TradingAgents 3 个月 3 只、FinCon 8 个月 8 只。<https://arxiv.org/html/2505.07078v5>
3. **Execution Realism and Reproducibility in LLM-Based Trading Systems**（arXiv 2606.08285，PRISMA-ScR 系统综述 + 证据审计）：687 条去重记录、59 篇全文、53 篇暂定纳入；审查框架分成 point-in-time 控制、时间切分、留出评估、**交易成本与换手**、执行语义、股票池构造、架构报告、工件可得性——正好是本节的维度清单（终稿字段计数尚未公布）。<https://arxiv.org/abs/2606.08285>

---

## 4. 金融 QA 类：一句话区分（不展开）

- **FinBen**（<https://arxiv.org/abs/2402.12659>）：36 数据集 / 24 任务的金融 LLM「全集」，含一个短程交易子任务——测「金融知识与推理」，不测「在有人抢流动性时能否赚钱」。
- **FinanceBench**（<https://arxiv.org/abs/2311.11944>）：10,231 道开卷财务问答 / 40 家公司 / 361 份 2015–2023 年公开文件——测「能不能在财报里找对数字」，与交易基准是两套坐标系。
- 一句话总结：**QA 类考「读得懂」，交易类考「活得下去」；前者高分不保证后者**（StockBench 摘要即此结论）。

---

## 5. 可引用结论清单（带 URL 与可信度）

1. 「用真钱做前向测试」的最强公开证据：Alpha Arena 2026 年 4 轮 8 系统 32 组结果，仅 6 组盈利、整体组合约 −33%；创始人自评「直接把钱交给 LLM 交易这条路还走不通」。——Bloomberg/Justina Lee 2026-05-06，<https://www.business-standard.com/markets/news/ai-bots-auditioning-for-wall-street-trading-are-mostly-losing-money-126050701793_1.html>【B】
2. 「LMArena 分数不能预测交易表现」。——LiveTradeBench，<https://arxiv.org/html/2511.03628v1>【A】
3. 「静态金融 QA 能力不能转化为交易能力；多数模型跑不赢被动基准」。——StockBench，<https://arxiv.org/abs/2510.02209>【A】（注：其表 2 与摘要口径需谨慎，见 §2.2）
4. 「过程审计能揭示收益看不到的问题：72.99% 的可审计交易点至少一项缺口，模型级 52.2%–91.2%；把问题交易过滤掉后平均收益反而上升 3.65–8.86 个百分点」。——AStockArena，<https://arxiv.org/html/2611.00515v1>【A】
5. 「扣除佣金/点差/冲击/token 成本后，TradingAgents 组合 Sharpe 0.43→0.22、QuantAgent −0.96→−1.15，均低于净 buy-and-hold；40 个系统×摩擦项里 35 个未建模」。——The Alpha Illusion，<https://arxiv.org/abs/2605.16895>【A】
6. 「把评估期拉到 20 年、100+ 标的、含退市股后，LLM 交易 agent 的优势显著衰减；它们在牛市过度保守、熊市过度激进」。——FinSABER（KDD'26），<https://arxiv.org/html/2505.07078v5>【A】
7. 「同配置多次运行不可复现：TradingAgents 10 次运行最终收益 5%–28%，多数低于被动基准；温度 0 + 固定 seed 才能确定」。——Reproducibility in the TradingAgents Framework（ACM），DOI 10.1145/3800973.3801029【B】
8. 「跨预训练 cutoff 后 FinMem 累计收益下降约 71.85%、QuantAgent Sharpe 下降 51.48%」——对「语义记忆污染」的定量锚点。——Alpha Illusion（转引），<https://ar5iv.labs.arxiv.org/html/2605.16895>【A】
9. 「检索所及，多席零和 + 内生价格做得最完整的是 Agent Trading Arena：≥9 个 agent、3 只合成股票、bid-ask 定价、分红 + 每日持有成本；其结论是视觉输入显著优于纯文本数值」。——EMNLP 2025 Findings，<https://aclanthology.org/2025.findings-emnlp.294/>【A】
10. 「把订单簿执行真正纳入评分的最典型工作是 PolyBench：7 个模型仅 2 个 CWR 为正（17.6% / 6.2%），而模型自报置信度普遍很高；单模型推理成本 $1.34–$11.50」。——<https://arxiv.org/html/2604.14199v1>【A】
11. 「实时数据流可以消除离线回测的信息泄漏，但 live 评估本身不可复跑、样本小」。——LiveTradeBench / AI-Trader，<https://arxiv.org/html/2512.10971v1>【A】
12. 「即使在 InvestorBench / PortBench 这类回放基准上，也没有 LLM 方法能打败 1/N 等权；频繁调仓的成本与风险是主因」。——<https://arxiv.org/html/2610.11732>【B】
13. 「金融 LLM 基准正在快速迭代但交易子任务短程、无成本、无对手」。——FinBen，<https://arxiv.org/abs/2402.12659>【A】
14. 「执行真实性与可复现性是全领域的系统性问题（系统综述：687→59→53 篇，审查 8 个维度含点时间控制、成本与换手、执行语义）」。——<https://arxiv.org/abs/2606.08285>【B】
15. 「比赛类结果的解读必须带 4 个限定：真钱≠统计显著、PnL≠Sharpe、单季 n=1、行情单一」。——综合 Bloomberg、财联社、ForkLog、播客复盘【B/C】

---

## 6. 未核实清单（写论文前需补）

| # | 待核实项 | 现状 | 建议动作 |
|---|---|---|---|
| 1 | **Alpha Arena 官方规则原文**（资金、标的、杠杆上限、决策频率、排序口径、是否计手续费/资金费） | <https://alphaarena.ai> 被 Vercel Security Checkpoint 拦截（HTTP 429/JS 校验），只拿到二手描述；多源一致但无一手 | 用浏览器打开存档页（web.archive.org），或找 nof1 的 X/博客原帖；论文里如需精确规则，注明「转引自二手」 |
| 2 | Alpha Arena S1 最终收益（Qwen +22.32% / DeepSeek +4.89% / GPT-5 −62% / Grok 4 −45.3%） | 多家二手一致（TMT Post EN、KIEP、财联社、ForkLog），未见官方终榜 | 同上；或引 Bloomberg 的整体口径（−33%、6/32）替代 |
| 3 | Alpha Arena 2026 四季（8 系统 × 4 轮、美科技股、32 组、−33%） | Bloomberg 原文付费墙；只见摘要与多语转载 | 找 Bloomberg 原文或 Flat Circle 的原始统计 |
| 4 | StockBench 起始资金、§7 Limitations 原文、表 2 与摘要矛盾的原因 | arXiv HTML 在 §3.1 被截断 | 下 PDF 用本地 PDF 解析（本机 Python 无 pypdf/fitz，需先装） |
| 5 | LiveTradeBench 50 天的具体起止日期、是否零佣金、Limitations 全文 | 只读到 Limitations 的**标题**（"Transaction costs and market frictions"） | 读 §"Limitation and Future Work" 与附录 8 数据收集 |
| 6 | InvestorBench 的数据窗口、指标、13 个模型的具体结果 | 只读摘要 + 二手转述（「Equities: Oct 20 to May 22」） | 读 ACL PDF（web_fetch 不支持 PDF，需本地解析） |
| 7 | FinMem §4–5 实验细节（起始资金、股票数、手续费假设） | 只读到架构与引言（「6 个月到一年数据即可」） | 读 arXiv 2311.13743 §4–5 |
| 8 | TradingAgents 原论文的回测窗口 / 股票 / 是否计成本 | 只见二手说法（2024-01~03 数据、2024-06~11 模拟、3 只股票） | 读 arXiv 2412.20138 §5；并用 mesura 的复现脚本交叉验证 |
| 9 | ACM《Reproducibility in the TradingAgents Framework》全文 | 只有搜索片段（10 次运行、5%–28%、温度 0 方差为零） | 通过 DOI/机构库拿 PDF |
| 10 | FinBen 42 数据集 / 24 任务 / 8 方面 / 21 模型 vs arXiv v2 的 36/24/7/15 | 两个口径并存（NeuroIPS camera-ready 更大） | 以 NeurIPS 2024 D&B 版本为准，注明版本 |
| 11 | 国内 ETF AI 赛的规则细节（是否真钱、是否可接入外部数据、榜单如何防作弊） | 只有券商/媒体通稿 | 找赛事规则页；作为产业现象引用即可，不必精确 |
| 12 | mesura（TradingAgents 复现审计）的可信度 | PyPI 包 + 自述「reproduces figures against real prices」 | 属第三方审计【C】，引用时只作「有独立复现指出口径问题」的旁证，不作唯一依据 |
| 13 | Aster 比赛「亏损由交易所承担、盈利归交易者」的原文条款 | 只见中文/俄文二手 | 找 Aster 官方公告 |

---

## 附：本文件用到的 URL 速查

- Alpha Arena / nof1：<https://www.nof1.ai>、<https://alphaarena.ai>（被拦截）、<https://www.chinastarmarket.cn/detail/2174661>、<https://en.tmtpost.com/news/7748687>、<https://csf.kiep.go.kr/newsView.es?article_id=58167&mid=a20100000000>、<https://foresightnews.pro/article/detail/96850>、<https://www.business-standard.com/markets/news/ai-bots-auditioning-for-wall-street-trading-are-mostly-losing-money-126050701793_1.html>
- Aster：<https://incrypted.com/en/the-team-of-traders-lost-to-ai-in-the-trading-tournament-by-aster/>、<https://www.htx.com.lv/zh-tc/news/ai-outperforms-humans-in-cryptocurrency-trading-tournament-w-YfoH7p8a/>
- 国内赛事：<http://stock.10jqka.com.cn/20260605/c677251587.shtml>、<https://app.dahecube.com/nweb/wap/article.html?artid=276858>
- StockBench：<https://arxiv.org/abs/2510.02209>、<https://arxiv.org/html/2510.02209v2>、<https://github.com/ChenYXxxx/stockbench>
- LiveTradeBench：<https://arxiv.org/abs/2511.03628>、<https://arxiv.org/html/2511.03628v1>、<https://github.com/ulab-uiuc/live-trade-bench>
- PolyBench：<https://arxiv.org/abs/2604.14199>、<https://arxiv.org/html/2604.14199v1>、<https://github.com/PolyBench/PolyBench>
- AStockArena：<https://arxiv.org/abs/2611.00515>、<https://arxiv.org/html/2611.00515v1>
- AI-Trader：<https://arxiv.org/abs/2512.10971>、<https://arxiv.org/html/2512.10971v1>、<https://github.com/HKUDS/AI-Trader>
- InvestorBench：<https://aclanthology.org/2025.acl-long.126/>、<https://arxiv.org/abs/2412.18174>
- FinMem：<https://arxiv.org/abs/2311.13743>、<https://arxiv.org/html/2311.13743v2>、<https://github.com/pipiku915/FinMem-LLM-StockTrading>
- TradingAgents：<https://arxiv.org/abs/2412.20138>、<https://github.com/TauricResearch/TradingAgents>、<https://doi.org/10.1145/3800973.3801029>、<https://pypi.org/project/mesura/>
- Agent Trading Arena：<https://aclanthology.org/2025.findings-emnlp.294/>、<https://ar5iv.labs.arxiv.org/html/2502.17967v2>、<https://github.com/wekjsdvnm/Agent-Trading-Arena>
- StockAgent：<https://arxiv.org/abs/2407.18957>、<https://arxiv.org/html/2407.18957v2>、<https://github.com/MingyuJ666/Stockagent>
- FinBen：<https://arxiv.org/abs/2402.12659>、<https://github.com/The-FinAI/PIXIU>
- FinanceBench：<https://arxiv.org/abs/2311.11944>、<https://github.com/patronus-ai/financebench>
- 批评/综述：<https://arxiv.org/abs/2605.16895>（Alpha Illusion）、<https://arxiv.org/html/2505.07078v5>（FinSABER，KDD'26）、<https://arxiv.org/abs/2606.08285>（执行与可复现性系统综述）、<https://arxiv.org/html/2610.11732>（记忆型 agent 在 InvestorBench/PortBench 上打不过 1/N）
