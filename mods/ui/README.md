# 独立 UI Mod

`ui:standard` 是独立、无持久状态的 Mod。native 和 experimental 都在发行版 Members 中装配它；引擎只负责注册，不认识地图或 LLM 提示。UI 不访问 World、native 对象或领域内部接口，消费 Player API 已过滤的 JSON 观察和发行版独立提供的观察者 JSON 快照。

UI Mod 拥有 LLM 提示编排、工具描述展示、上下文统计与归档标题、观察者终端文案、文字/SVG/PNG 地图和语言目录。客户端拥有对话记忆、模型调用和工具执行；`ui/terminal.py` 负责终端输入输出与 ANSI 处理。`ui/` 是装配入口，实现在本目录；客户端使用宿主注入的 UI 对象。

## 配置

```json
{
  "language": "zh-CN",
  "vision": true,
  "map_max_px": 960,
  "map_estimated_tokens": 2048
}
```

这些配置可在顶层设置并由各国继承；单个国家可覆盖语言和视觉选项。`vision` 默认关闭，开启后给 LLM 发送图形地图。PNG 需要 `python -m pip install -r requirements-vision.txt`；缺少 Pillow 时发送文本地图。SVG 和文字地图不需要 Pillow。

终端：`map 秦` 显示文字地图，`map 秦 output/qin.png` 或 `map 秦 output/qin.svg` 导出图形地图。输出只含该国当时可见事实。命令中的文件路径有空格时用引号包住。

地图与 API 坐标均从 **0** 开始。地形 P/F/H/M/D、T 市政厅、C 城堡、国土颜色、军队数/总血量的读法随图片一起发送；军队外圈绿为自己、蓝为同盟、红为交战方、灰为中立。事实与精确 ID 留在状态 JSON 中。迷雾格为黑色，绘制边界与军队不查询隐藏格。

OpenAI 与 Anthropic 共用同一快照和 PNG，只在传输层转换图片块结构。历史保存 `zhanguo.ui.map/1` 事实快照、图例和文字回退，不保存图片/base64；续档会用同一快照重绘，不会用当前世界改写旧图。语言切换也不翻译或重写旧回放、推理签名及玩家正文。

native/experimental 新增 UI 成员后，整局存档的严格模块依赖清单随之改变。1.0 之前不承诺旧档兼容，不做兼容适配或自动迁移；依赖清单不一致即拒绝加载。

## 语言键

`locales/zh-CN.json` 与 `en-US.json` 按相同键提供文案。`language=en-US` 或 CLI `--language en-US` 切换新生成的界面文字。`ui_catalog` 可为 JSON 对象或 JSON 文件路径，覆盖指定键；自定义语言目录未提供的键回退中文。已有界面键的替换须保留占位参数，否则启动时拒绝。

例如：

```json
{
  "ui_catalog": {
    "console.sent": "已发送至 {target}",
    "tools.build.description": "提交建筑施工；是否完成以回执及后续状态为准。"
  }
}
```

工具描述可用 `tools.<动作名>.description`，参数说明可用 `tools.<动作名>.parameters.properties.<参数名>.description` 覆盖；不会改动作名、参数键、枚举或命名空间 ID。UI 编排与 experimental 的审核、交接提示已键化。领域规则、内容展示名、业务回执、手册正文仍由所属领域提供；后续完整语言替换应由对应模块提供键与语言文案，不能翻译 API 身份或随意机器翻译玩家正文。

## 替换 UI Mod

模块声明能力 `ui:presentation`，通过 `presentations()` 注册 `ui:create(cfg)`。工厂返回 UI 接口：`text`、`console.board(rows)`、`console.overview(snapshot)`、`llm(cfg)`、`observer_map(snapshot)`、`map_text(observation)`、`map_snapshot(observation)`、`map_image(observation)`、`map_svg(observation)`、`expand_message(message, vision=...)`。LLM 编排器提供 `system`、`state`、`map_context`、`actions`、`context_plan` 和键值 `text`。

发行版可将标准 UI 成员换成其他提供方。也可在宿主顶层设置 `ui_mods`，指向独立的加载清单（格式同根目录 `ui.toml`）或带 `mod.toml` 的 Mod 目录，覆盖发行版的 UI。独立 UI 装配只用于展示，不改世界模块与存档状态。领域模块不反向导入 UI Mod，客户端不导入 UI 实现类。

## 验证

```bash
.venv/bin/python -m unittest tests.test_ui_mod tests.test_parliament
```

离线验证模块替换、语言占位符、迷雾像素、确定性 PNG、SVG/PNG 导出、两种协议同图、缺少 Pillow 回退、事实快照续档、签名/回放保留，以及议长第一步不提前看到新版国策。

## 中文看海控制台

启动时显示命令帮助和存档/日志位置。每回合开始、结算后显示世界市场、各国七类库存、国土及各兵种军队数量、上期经济报告、国策、条约、战争和近期通信；模型每次调用后立即显示具体行动与实际结果：建造显示“动工农场，−20金 −15木”，交易显示买卖数量、实际金额及价格变化，国土每格一行，规则按章节显示；不再逐字段展开原始 toolcall。军队行动根据公开回执区分立即占领、交战和待撤退，外交提交仍显示等待结算。新事件随后显示。长回执在终端自动截断（最多约 900 字/14 行），日志同时保留完整中文展示和原始回执。发言进入滚动区，思考默认收起，完整正文保存在日志，收起时不逐次打印提示；`thoughts` 查看最近思考，`thoughts on/off` 切换后续展开。底部状态区保留上下文计划、等待模型及重试通知。看海使用国家展示名、中文动作和状态；玩家和模型正文保留原文。

`map` 显示观察者全图，`map world.txt/world.svg/world.png` 导出全图；`map 秦 [文件]` 使用秦的过滤视野。每次结算自动更新日志同名的 `.map.txt`。地图渲染由 UI Mod 完成，发行版的 `observer_snapshot()` 仅提供脱离世界对象的事实，全图不会进入玩家工具或模型上下文。

控制台支持旧版公告、寄信别名及多行 `END`；`rate 5` 调整储蓄利率为 5%，`rate` 查看；`cheat 秦 金100 粮20 骑2` 为观察者补助；`add 国名 [政体ID]` 手动登场，新国家复用现有客户端接入设置，新增身份随存档续局。上述管理行为由发行版提供，不开放给玩家。

LLM 行动期间，命令在每次模型调用结束后处理；`quit` 完成当前整回合、保存后退出，`Ctrl-C` 中断并保留最近完整存档。`pause` 在完整回合保存后暂停，`resume` 继续。正在等待模型响应时，输入暂时排队。长命令水平滚动，完整输入仍会提交；终端缩放后按新宽度重画状态区。

## 精简模型表示

地块操作直接用 `x/y`，例如 `{"kind":"farm","x":3,"y":11}`；军队行动使用整数 `aid/ids`。模型状态和工具回执将站点路径表示为 `position:[x,y]`，库存表示为 `stocks:{资源键值:数量}`。军队公共事实移除内部全局编号、自动名称及交战排序编号；行动所需的整数编号、位置、血量和行动状态仍保留。

内容参数默认使用注册表中唯一的短键值，如 `farm`、`food`、`infantry`；多个模块定义同名内容时，使用完整命名空间键值。引擎工具用 `content_parameters=("kind",)` 或 `("resource",)` 声明需要解析的字段，普通正文不会当作内容键值解析。完整键值仍可直接调用。UI 使用发行版注册表提供的键值映射显示状态，不改写原始事实、审计、历史正文或地图快照。

建筑信息无参数调用 `building_economics {}` 一次返回全部类型的完整报告，不接受地块参数。`building_catalog {}` 提供不依赖市价的各级基础成本、配方、能源、效果及门槛。真实施工须指定坐标；`build {kind,x,y}` 直接校验、扣款并返回施工回执。玩家查询仅 `tile {x,y}` 接受坐标，其余查询按全国或整类返回。默认 LLM UI 在稳定上下文中提供公开规则、常备建筑/兵种/地形资料，以及常耗缺口、能源装机上限、军队行动状态、边境敌军和外交待办；每批工具后仅刷新变化的事实；历史小结只展示最近两条，完整日志和客户端历史保留。日志保存完整中文行动、原始参数及原始回执；终端截断只影响显示。当前优先完善中文叙述，新增叙述模板的完整国际化另行补齐。

公开指南由发行版的 `handbooks` 目录声明：`player_rules(topic="基础指南")` 可随时查全文，经济学手册等标题别名同样可用。默认提示开局前20期包含基础指南，经济手册常备，参战时包含战争手册，战略手册按需查；experimental 在共享正文后追加实验参考，议长读取同一来源。目录和正文放入稳定提示，不每次复制到状态增量。

LLM 客户端另提供 `memory_search(query,limit)`，检索本国历史行动、发言与回执，排除思考。压缩上下文不清除检索源，历史随客户端状态保存；这个工具不进入世界领域 API。`countries`、`threats`、`battle` 是不带坐标的整合查询，交战面板在盲战中只展示本国。行情展示买/卖单价，军队显示逐支数量，报表缺省最近一期，外交面板显示可操作提议和投票编号。
