# 独立 UI Mod

`ui:standard` 是独立、无持久状态的 Mod。native 和 experimental 都在发行版 Members 中装配它；引擎只负责注册，不认识地图或 LLM 提示。UI 不访问 World、native 对象或领域内部接口，只消费 Player API 已过滤的 JSON 观察。

UI Mod 拥有 LLM 提示编排、工具描述展示、上下文统计与归档标题、观察者终端文案、文字/SVG/PNG 地图和语言目录。客户端拥有对话记忆、模型调用和工具执行；`console.py` 保留终端输入输出与 ANSI 处理。`ui/` 是装配入口，实现在本目录；`clients/view.py` 仅保留调用入口。

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

模块声明能力 `ui:presentation`，通过 `presentations()` 注册 `ui:create(cfg)`。工厂返回 UI 接口：`text`、`console.board(rows)`、`llm(cfg)`、`map_text(observation)`、`map_snapshot(observation)`、`map_image(observation)`、`map_svg(observation)`、`expand_message(message, vision=...)`。LLM 编排器提供 `system`、`state`、`map_context`、`actions`、`context_plan` 和键值 `text`。

发行版可将标准 UI 成员换成其他提供方。也可在宿主顶层设置 `ui_mods`，指向独立的加载清单（格式同根目录 `ui.toml`）或带 `mod.toml` 的 Mod 目录，覆盖发行版的 UI。独立 UI 装配只用于展示，不改世界模块与存档状态。领域模块不反向导入 UI Mod，客户端不导入 UI 实现类。

## 验证

```bash
.venv/bin/python -m unittest tests.test_ui_mod tests.test_parliament
```

离线验证模块替换、语言占位符、迷雾像素、确定性 PNG、SVG/PNG 导出、两种协议同图、缺少 Pillow 回退、事实快照续档、签名/回放保留，以及议长第一步不提前看到新版国策。
