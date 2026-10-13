# zhanguo 战国

战国 0.2.0：模块化发行版 native 4.1.0，统一玩家 API v1。引擎管理模块与请求分发，native 提供领域规则，LLM 和规则 AI 通过同一个玩家接口行动。规则 AI 仅维护 v11plus，旧版本全部退役。

## 开局与续局

1.0 之前不承诺旧档兼容，不提供兼容适配或自动迁移；整局按发行版依赖清单严格校验。

```bash
cp mp_config.example.json mp_config.json
# 修改各国 id、client 和 LLM 接入参数；client=rule 可完全离线运行。
./start.sh --config mp_config.json --new
./start.sh --config mp_config.json
```

`--new` 明确重开并覆盖指定存档；新旧局使用不同 save 与 journal 路径。运行参数另有 `--save`、`--turns`、`--small-ctx`、`--ctx-window`。`--turns` 指运行到的周期号。模型失败会停止，保留最近完整结算存档。

国家配置建议显式填写 `id`（稳定身份）、`name`（显示名）、`client`（llm 或 rule）。默认规则版本为 v11plus，配置退休版本会拒绝启动。政体使用模块作用域，例如 `polity=huns`；支持 initial、scheduled、manual 登场。续局所需 API key 来自外部配置，不存入整局存档。

旧 World 构造器与旧整局存档已退役，旧存档应在对应历史提交续局；新客户端不构造兼容 World。银行配置由发行版模块拥有，旧 world_bank 与 opening_guide 开关不再使用。

终端命令：`add 国名`、`send 国名 正文`、`say 正文`、`board`、`map 国名`、`quit`。say 或 send 国名后可输入多行，以 END 结束；正文也可用 @文件路径。加入的国家需预先在配置声明。

## 八国剧本

```bash
python -m scenarios.eight_nations --seed 20260921
./start.sh --config mp_config.8国.20260921.json
```

生成器默认使用示例接入配置，显式 `--template 路径` 才使用其他配置；已有目标需 --force 才覆盖。八国采用 24×24 手摆位置，七雄共保周，常驻目标文案随档保存，观察者手动结束对局。生成的示例 LLM 配置需填入接入参数；离线局把 client 改为 rule。

## 架构与验证

可选[experimental 实验性发行版](mods/experimental/README.md)提供两步国策审核、事实与承诺校核、连续三回合未通过后的君主罢免和记忆重建，并提供有限库存与库存反馈定价市场、按计算量限制的 Python 沙盒。使用独立配置 `mp_config.experimental.example.json`；默认发行版仍为 native。

- [发行版 Session 与 API 暴露](docs/发行版Session.md)：开局、登场和管理能力归发行版，统一提交、审计与控制权机制。
- [统一玩家接口和客户端接线](docs/客户端接线.md)：方法、回执、权限、状态和迁移边界。
- [Mod 开发指南](docs/Mod开发指南.md)：按任务分条的配方——加资源、加阶段、加兵种、换实现、提供客户端、组装发行版、一个最小可玩游戏；每条配可运行示例与验收测试。
- [独立 UI Mod](mods/ui/README.md)：LLM 界面编排、语言键和图形地图；native 与 experimental 均装配。
- [Native 领域模块](mods/native/README.md)：18 个领域模块和独立 UI Mod、56 个模块工具与结算挂点。
- [模块化口径](模块化口径.md)：领域所有权与依赖方向。

```bash
.venv/bin/python -m unittest discover -s tests
```

离线回归覆盖引擎、领域、玩家 API、客户端、八国剧本与临时 CLI 整局，使用模拟后端。DeepSeek 两种协议已真实连通；用户已确认 Claude 完成联网、续档和墙钟验证。旧单体测试与历史策略基准保留作档案，不继续维护。新版 LLM 使用结构化状态；独立 [UI Mod](mods/ui/README.md) 编排提示、语言键和玩家可见地图，设置 `vision=true` 可发送 PNG，终端可导出 PNG/SVG。
