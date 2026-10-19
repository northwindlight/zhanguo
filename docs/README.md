# 文档索引

运行入口见[项目 README](../README.md)。现行接口与生成规则列在本页前面；设计稿、阶段复核和历史资料分别归类。旧报告记录的是当时的版本，不能用其中的待办覆盖当前实现或后续定案。

## 当前使用与开发

- **[Mod 开发文档站](https://northwindlight.github.io/zhanguo/)**：按开发流程组织，带可搜索的 API 参考（API 参考页由 `scripts/api_doc.py` 从源码生成）。本地预览：`python3 docs/site/build.py --serve`。
- [游戏说明书](../mods/native/docs/游戏说明书.md)：手写维护的规则与口径；与四本讲义同在 `mods/native/docs/`，不参与运行时装配。
- [Mod 开发指南](Mod开发指南.md)：示例、模块声明与开发接口。
- [发行版 Session 与剧本胜利钩子](发行版Session.md)：宿主、服务、剧本挂载及存取契约。
- [配置层](配置层.md)、[客户端接线](客户端接线.md)：配置提供者与绑定玩家 API。
- [native](../mods/native/README.md)、[experimental](../mods/experimental/README.md)、[UI](../mods/ui/README.md)：发行版和界面入口。
- [presentation](../presentation/)：中立的呈现原语（语言目录、终端渲染、回执、简报、地图），UI Mod 与模型客户端共用。

## 设计与规划

- [模型面向层与发行版解耦](design/模型面向层与发行版解耦.md)：模型客户端与发行版耦合的六处根因、契约正典与改造说明。
- [模块化口径](design/模块化口径.md)、[对象化口径](design/对象化口径.md)、[正规化口径定稿](design/正规化口径定稿.md)：设计要求与历史裁决；最新行为仍须结合当前说明书及后续明确修订。
- [路线计划](plans/PLAN.md)、[后续改造大纲](plans/后续改造大纲.md)：现行路线与保留的阶段计划。
- [GUI 选型与计划](design/GUI选型与计划.md)、[地图生成与资源分布](design/地图生成与资源分布.md)。
- [v11 编组模型](design/v11编组模型.md)：规则 AI 的编组设计记录。

## 实现复核

- [八国局第23期上下文超限恢复（2026-10-19）](reviews/八国局上下文超限恢复-20261019.md)：保守窗口、旧回放压缩与截断摘要检查。
- [消费指标误注入与周国策偏差事故（2026-10-19）](reviews/消费指标误注入与周国策偏差事故-20261019.md)：公开评测提示的引入、真实国策偏差、财政报告缺口及定点记忆修复。
- [API/tool 修复验收（2026-10-18）](reviews/API-tool修复验收-20261018.md)：七项行为缺口与 API 声明、query 参数、领域预演及固定声明收口。

- [UI 退步复核与修复验收（2026-10-16）](reviews/UI退步复核-20261016.md)：对照旧版注释，记录 14 项信息错误、遗漏与阅读能力退步及逐项修复验收。
- [错误实现总表](错误实现.md)：保留原入口及审计证据。
- [0.2.3 修复验收](reviews/错误实现-修复验收.md)、[结盟意愿差异复核](reviews/结盟意愿差异复核.md)。
- [toolcall 对照](reviews/toolcall对照.md)、[正规化语义审查](reviews/正规化语义审查.md)、[说明书核对](reviews/正规化说明书核对.md)、[阶段 A 记录](plans/阶段A改造记录.md)：对应阶段的历史工程记录。
- [审计实验](../experiments/implementation_audit/README.md)：合成基准和离线续算材料。

## 研究与历史资料

- [消费总量评测口径论文](research/消费总量评测指标论证.md)：消费与投资、资本积累、有效需求、充分市场下的消费价值证明、内生价格的跨局比较与探针识别边界；[实现与复现附录](research/消费指标实现与复现.md)、[消费指标实验](../experiments/consumption_metric/README.md)。2026-10-15 的[静态核对快照](research/消费指标实现核对.json)保留其各自口径与源码指纹。
- [公开真实对局资料](../research/game-records/README.md)：历史原始资料，独立于源码发行包和自动测试。
- [历史匈奴教义](archive/strategy/匈奴教义.md)：旧版人类侧策略资料；当前政体入口见 [huns](../mods/native/polities/huns/README.md)。
- [旧领域声明](archive/legacy-domain-declarations/)：保留作历史对照。
- [旧规则和讲义](archive/legacy-rules/README.md)：退役说明书、四篇旧手册和原领域规则片段。

本机对局、预览与配置按 `.gitignore` 管理；生成图片归本机 `archive/previews/`，不会混入源码发行包。
