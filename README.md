# 战国 · Zhanguo

战国是一款面向 LLM 决策的回合制大战略游戏。各国在同一世界中经营经济、组织军队、处理外交并争夺生存空间；人类可以观察、发出公告与来信，也可以研究模型在长局中的决策和失误。

当前发行版本 **0.4.1**，详见 [更新记录](CHANGELOG.md)。API 与 tool 分层，面板与对外关系事实收口，建筑/资源按角色声明，说明书与四本讲义在 `mods/native/docs/` 手写维护。模型工具由发行版 tool 层与客户端按权限提供。`main` 来自原 `正规化` 分支，采用模块化架构。旧版单体实现保存在 [`legacy`](https://github.com/northwindlight/zhanguo/tree/legacy)。[English](README.en.md)

本版修复 API/tool 入口不一致、国策到期审核和外交收尾，收口 API 声明、查询参数与领域预演，见 [更新记录](CHANGELOG.md)。

## 真实对局资料

[研究文章与长任务野榜](research/papers/README.md)：DeepSeek、Qwen、MiniMax与Gemini在大战略游戏中的行为观察和主观评分，明确区分实测、假说与不对称历史样本。

[独立研究目录](research/game-records/README.md) 收录实际游玩的完整存档和原始日志，供研究与参考：

| 对局 | 保存回合 | 使用模型 |
| --- | --- | --- |
| 八国终局，种子 20260921 | 258 | `qwen3.8flash`、DeepSeek |
| 八国快照，种子 20261009 | 158 | `qwen3.8flash` |

资料在 main 和 legacy 中均保留。原始文件逐字节保存，附模型来源说明、文件清单和 SHA-256。两局均来自旧版引擎，不能直接用当前 main 的存档加载器运行；研究旧局请对照 legacy 与相关历史提交。

## 当前架构

Godot 原生桌面上帝视角已开始实现：运行 `python3 desktop.py` 启动独立离线八国演示，
地图铺满窗口，支持细化地貌、缩放/拖动/点选、国界与军队，国力/市场/纪事通过抽屉打开，支持完整回合暂停保存。
依赖与范围见 [桌面说明](mods/ui/desktop/README.md)，目前仍是第一版演示骨架。
第一阶段正按两周到一个月分批推进；`python3 desktop.py --sample terrain` 打开独立 3×3 地貌比例草样，
进度与验收范围见 [GUI 第一阶段](docs/plans/GUI第一阶段.md)。

发行版定义游戏，host 装配运行，客户端通过统一玩家 API 行动；模型视图由客户端组装，UI Mod 提供呈现原语。

- **引擎**：模块依赖、请求分发、发行版 Session、控制权代次、逐动作审计与完整周期存取；不导入 UI，呈现层由 host 注入 Session。
- **配置**：组件声明字段语义，发行版组合，CLI 和程序调用共用准备入口；见 [配置层契约](docs/配置层.md)。
- **发行版**：`native` 提供资源、建筑、市场、银行、领土、军队、外交、情报和国家生命周期；场景与登场规则归发行版。方格游戏可复用 `GridSession`，无地图游戏可直接使用通用 Session。
- **玩家客户端**：LLM 与 `v11plus` 规则 AI 使用同一绑定身份的 API，消费结构化事实和行动回执。
- **界面**：独立 UI Mod 装配语言文案、终端面板和玩家可见的文字、SVG、PNG 地图；渲染原语在 `presentation/`，模型视图在 `clients/model_view.py`。

默认 [mods.toml](mods.toml) 装配 native。可选 [experimental](mods/experimental/README.md) 是持续更新的实验性发行版，目前为 `0.15.0`：

- 独立议长分两步审核国策，核对事实、必要评估与承诺；未解决事项跨审核追踪，原始证据按需分页，输入逐次受预算限制。审慎审核同时要求推进游戏目标，允许有依据的冒险；连续三回合未通过后罢免君主并重建记忆。
- 市场采用实际交付的有限库存，以及库存偏差反馈报价。
- 保存、修订和手动运行 Python 短脚本，直接操作标准 API；按计算量限制，失败保留已成功动作，每国每回合一次执行机会。
- 为规则 AI 启用[研究用战斗概率推演](mods/experimental/combat_research/README.md)，可选 C 加速；概率能力不注册为 LLM 工具。

另有 [stock](mods/stock/README.md) 发行版：模拟炒股。LLM 玩家只能通过 Python 脚本下单，
买卖不作为工具暴露；每回合分开盘窗口与回合末统一工作窗口，每 5 个交易日休市 2 天；
回合开局能看到上回合脚本的打印输出与报错，以及自己的持仓与财富。

## 开始新局

```bash
cp mp_config.example.json mp_config.json
# 修改国家、客户端和接入参数，再启动。
./start.sh --config mp_config.json --new
```

Windows 可使用 `start.ps1`。启动脚本建立本地虚拟环境并安装依赖；也可在已准备好的环境中运行 `.venv/bin/python mp_run.py`。

每国显式配置 `id`、`name` 和 `client`。`client=rule` 使用确定性的 v11plus；默认 native 可完全离线运行。`client=llm` 需填写模型和接入参数，支持 OpenAI 兼容与 Anthropic 两种协议。凭据保留在本地配置中，不写入整局存档。

启用 experimental 时，复制 [实验配置模板](mp_config.experimental.example.json) 为自己的配置，顶层指定 `"mods": "mods.experimental.toml"`，并为议长设置模型接入。各局使用独立 save 与 journal 路径。

```bash
./start.sh --config mp_config.json          # 续局
./start.sh --config mp_config.json --turns 10
```

`--new` 会覆盖指定目标存档。`--turns` 表示运行到的周期号。保存只发生在完整结算边界，调用失败时保留最近完整存档。当前格式为 `zhanguo.session/4`；1.0 之前不承诺旧档兼容，不提供自动迁移，模块依赖不一致时拒绝混载。

终局成绩单：`python settlement.py --save 专用存档.json --table`；生成报告用 `--no-chat --out 报告.md`，结算厅另需外部模型配置。保存时同目录的 `<存档>.audit/` 留存动作与领域历史，备份整局时一并复制。坐标统一从 **0** 开始。

## 八国剧本与观察者

```bash
python -m scenarios.eight_nations --seed 20260921
./start.sh --config mp_config.8国.20260921.json
```

剧本采用 24×24 地图、八国手摆开局与七雄共保周的初始安排。生成器遇到已有目标会拒绝覆盖，显式 `--force` 才覆盖；生成后按需要填写模型接入或改用 rule 客户端。

八国是发行版挂载的轻量钩子 Mod，配置选择 `"scenario": "eight_nations"`。周任盟主时盟友自动成为「周天下附庸」，保留正常盟友权力、强制上交实际生产的10%；普通附庸上交15%、外交由宗主控制，只能由宗主释放或发动独立战争脱离。向周 `submit_vassalage` 主动称臣，回合末可自动创建周天下、首次扣周100金并由周任盟主；已有联盟按入盟表决、不再收费。周及其特殊附庸覆盖全部存续国家且至少5国（含周），完整回合结算即触发仅周获胜，其他成员臣属保全（未胜未败，不列入主胜或次胜）；兼并至唯一国家为终极胜利。外交请求在全体行动结束后结算，宣战最后处理。详见 [剧本 Mod 与钩子](docs/发行版Session.md)。朝贡按每国每期各类资源的全国实际总产出乘贡率，小数按附庸、宗主及资源跨期累计，累计额向下取整扣取，余数继续结转。

看海控制台提供中文全景国力、市场、经济报告、国策、外交和逐次调用播报。`help` 查看命令；`map` 看世界全图，`map 国名 [文件.txt/.svg/.png]` 看单国视野；`add 国名 [政体]`、`send 国名 正文`、`say 正文`、`rate 5`、`cheat 国名 金100 粮20 骑2` 提供观察者管理。公告和来信支持多行 `END` 和 `@文件`。命令在模型调用间处理，`quit` 完成整回合并保存后退出。每回合自动更新日志同名的 `.map.txt`，详见 [UI 说明](mods/ui/README.md)。

模型优先使用短内容键值，重名时指定命名空间；地块动作使用 `x/y`，军队使用整数 `aid/ids`。experimental 的 Python 工具提供用法示例和批量处理提示；失败消耗脚本执行额度，普通国家行动仍可继续。

## 开发与验证

全部资料见[文档索引](docs/README.md)。

**Mod 开发文档站**：<https://northwindlight.github.io/zhanguo/> ——按开发流程组织，带可搜索的 API 参考
（API 参考页由 `scripts/api_doc.py` 从源码生成，与代码同步；教程页手写，在 `docs/site/content/`）。
本地预览：`python3 docs/site/build.py --serve`。

一键启动（默认跑 native 发行版）：

```bash
./start.sh --check        # 配置体检：逐国核对模型接入，缺什么说清楚
./start.sh                # 续局（无档则新开）
./start.sh --offline      # 零依赖的 minigame 开发模式，不需要任何 API key
```

配置模板见 `mp_config.example.json`（密钥只写进 `mp_config.json`，该文件不入库）。
配置与启动的完整说明见文档站的[配置与启动](https://northwindlight.github.io/zhanguo/开始/03-配置与启动.html)页。

- [Mod 开发指南](docs/Mod开发指南.md)：从可运行示例开始，增加内容、替换模块和组装独立游戏。
- [发行版 Session](docs/发行版Session.md)：场景、可选地图策略、管理能力、服务装配、审计与控制权。
- [玩家 API 与客户端](docs/客户端接线.md)：绑定身份、事实、请求和回执。
- [GUI 选型与计划](docs/design/GUI选型与计划.md)：跨平台桌面、2D/3D 地图与动画素材选型，以及看海优先的接线计划。
- [Native 模块](mods/native/README.md)、[UI Mod](mods/ui/README.md)、[experimental](mods/experimental/README.md)、[Python 沙盒](mods/experimental/SCRIPTING.md)。
- [模块化口径](docs/design/模块化口径.md)与 [路线计划](docs/plans/PLAN.md)。

```bash
.venv/bin/python -m unittest discover -s tests -q
```

离线回归覆盖模块替换、合成对局、存取一致性、API 权限、议会、市场、沙盒与 UI。测试使用模拟模型后端；真实对局资料单独存放，不作为自动化测试输入。各批次验收结果见更新记录与复核报告。

## 代码入口

根目录 Python 入口为 `host.py`（装配发行版 Session）、`mp_run.py`（CLI）与 `settlement.py`（终局结算）。上下文与模型接入在 `clients/`（模型视图 `clients/model_view.py`），规则客户端在 `ruleai/`，终端传输在 `ui/terminal.py`，呈现原语在 `presentation/`，UI Mod 装配在 `mods/ui/`，地图生成在 `mods/native/map_generation/`。旧转口、旧领域实现和退役实验使用 legacy 分支及对应历史提交查阅。

[游戏说明书](mods/native/docs/游戏说明书.md) 与四本策略讲义在 `mods/native/docs/` 手写维护，不由代码生成，也不参与运行时装配。

Release 的源码 ZIP 不打包真实对局资料；研究资料从仓库的 [独立研究目录](https://github.com/northwindlight/zhanguo/tree/main/research/game-records) 获取。
