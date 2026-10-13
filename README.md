# 战国 · Zhanguo

战国是一款面向 LLM 决策的回合制大战略游戏。各国在同一世界中经营经济、组织军队、处理外交并争夺生存空间；人类可以观察、发出公告与来信，也可以研究模型在长局中的决策和失误。

当前 `main` 来自原 `正规化` 分支，采用模块化架构。旧版单体实现保存在 [`legacy`](https://github.com/northwindlight/zhanguo/tree/legacy)。[English](README.en.md)

## 真实对局资料

[独立研究目录](research/game-records/README.md) 收录实际游玩的完整存档和原始日志，供研究与参考：

| 对局 | 保存回合 | 使用模型 |
| --- | --- | --- |
| 八国终局，种子 20260921 | 258 | `qwen3.8flash`、DeepSeek |
| 八国快照，种子 20261009 | 158 | `qwen3.8flash` |

资料在 main 和 legacy 中均保留。原始文件逐字节保存，附模型来源说明、文件清单和 SHA-256。两局均来自旧版引擎，不能直接用当前 main 的存档加载器运行；研究旧局请对照 legacy 与相关历史提交。

## 当前架构

发行版定义游戏，host 装配运行，客户端通过统一玩家 API 行动，UI Mod 负责呈现。

- **引擎**：模块依赖、请求分发、发行版 Session、控制权代次、逐动作审计与完整周期存取。
- **发行版**：`native` 提供资源、建筑、市场、银行、领土、军队、外交、情报和国家生命周期；场景与登场规则归发行版。方格游戏可复用 `GridSession`，无地图游戏可直接使用通用 Session。
- **玩家客户端**：LLM 与 `v11plus` 规则 AI 使用同一绑定身份的 API，消费结构化事实和行动回执。
- **界面**：独立 UI Mod 编排提示、语言文案、终端面板和玩家可见的文字、SVG、PNG 地图。

默认 [mods.toml](mods.toml) 装配 native。可选 [experimental](mods/experimental/README.md) 是持续更新的实验性发行版，目前为 `0.3.0-beta.1`：

- 独立议长分两步审核国策，核对事实、情报和经济、外交、军事承诺；连续三回合未通过后罢免君主并重建记忆。
- 市场采用实际交付的有限库存，以及库存偏差反馈报价。
- 保存、修订和手动运行 Python 短脚本，直接操作标准 API；按计算量限制，失败保留已成功动作，每国每回合一次执行机会。

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

`--new` 会覆盖指定目标存档。`--turns` 表示运行到的周期号。保存只发生在完整结算边界，调用失败时保留最近完整存档。当前格式为 `zhanguo.session/2`；1.0 之前不承诺旧档兼容，不提供自动迁移，模块依赖不一致时拒绝混载。

## 八国剧本与观察者

```bash
python -m scenarios.eight_nations --seed 20260921
./start.sh --config mp_config.8国.20260921.json
```

剧本采用 24×24 地图、八国手摆开局与七雄共保周的初始安排。生成器遇到已有目标会拒绝覆盖，显式 `--force` 才覆盖；生成后按需要填写模型接入或改用 rule 客户端。

终端提供 `add 国名`、`send 国名 正文`、`say 正文`、`board`、`map 国名 [文件.svg或文件.png]`、`quit`。公告和来信可多行输入，以 `END` 结束；图形视觉可设置 `vision=true`，PNG 依赖见 [UI 说明](mods/ui/README.md)。终端入口检查发行版实际提供的管理能力。

## 开发与验证

- [Mod 开发指南](docs/Mod开发指南.md)：从可运行示例开始，增加内容、替换模块和组装独立游戏。
- [发行版 Session](docs/发行版Session.md)：场景、可选地图策略、管理能力、服务装配、审计与控制权。
- [玩家 API 与客户端](docs/客户端接线.md)：绑定身份、事实、请求和回执。
- [Native 模块](mods/native/README.md)、[UI Mod](mods/ui/README.md)、[experimental](mods/experimental/README.md)、[Python 沙盒](mods/experimental/SCRIPTING.md)。
- [模块化口径](模块化口径.md)与 [路线计划](PLAN.md)。

```bash
.venv/bin/python -m unittest discover -s tests -q
```

最近代码回归为 405 项离线测试通过，覆盖模块替换、合成对局、存取一致性、API 权限、议会、市场、沙盒与 UI。测试使用模拟模型后端；真实对局资料单独存放，不作为自动化测试输入。
