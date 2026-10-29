# Windows 便携体验包

[v0.7.0 预发布页](https://github.com/northwindlight/zhanguo/releases/tag/v0.7.0) · [Windows x64 下载](https://github.com/northwindlight/zhanguo/releases/download/v0.7.0/zhanguo-0.7.0-windows-x64.zip) · [SHA-256 校验值](https://github.com/northwindlight/zhanguo/releases/download/v0.7.0/SHA256SUMS)

上一正式版为 [v0.5.1](https://github.com/northwindlight/zhanguo/releases/tag/v0.5.1)。新版本默认先供试玩，确认满意后再转为正式 Release。

用于让朋友直接体验地图、界面与演出，并给音乐、美术提建议。
目标为 Windows 10/11、x64。完整解压后双击 `Zhanguo.exe`，无需安装 Godot、Python 或 .NET SDK。
主菜单可选玩家模式或看海模式。玩家先定制本国和地图，再设置 AI 对手；看海可选 v13 规则 AI 八国局，点开始和继续即可离线观看。

v0.7.0完成两版计划的第二批：正式预付生产队列、持续鼠标军令与选中光环、发行版开关、旧档副本升级、简易地图／空位筛选、银行与经济报表邮箱。运行提交`c940304`通过[1075项与Windows九组验收](https://github.com/northwindlight/zhanguo/actions/runs/40818268137)，由同一次CI公开三附件；[发布说明](releases/v0.7.0.md)、[收尾验收](reviews/银行邮箱与规则收尾-20261029.md)。仍为单机一位真人，保持原18阶段结算。

生产队列与持续军令改变了旧档依赖。已知旧档在存档列表中选择“升级为新副本”，原档与历史保留；未知依赖仍拒绝，旧版包继续保留。

ZIP 中有：

| 内容 | 用途 |
| --- | --- |
| `Zhanguo.exe`、`Zhanguo.pck`、`data_…` | 导出的游戏、素材和自带 .NET 运行库 |
| `runtime/python`、`game` | 官方嵌入式 Python 与运行所需的源码、规则、翻译 |
| `userdata`（首次使用后创建） | 本机存档、菜单配置与 API 设置 |
| `开始体验.txt` | 操作、暂停/快进、镜头与启动说明 |
| `给音乐和美术的说明.md`、`music` | 场景需求、音乐交付和画面反馈入口 |
| `美术预览/index.html`、`美术预览/素材`、`美术预览/补绘SVG` | 图录、合成预览、SVG母版、PNG、帧表 |
| `美术预览/地图SVG` | 地形、城堡、指南、银行与邮箱图标母版 |
| `演出参考.mp4` | 32秒实际战斗演出，方便讨论节奏 |
| `portable.json`、`bundle-manifest.json`、`LICENSES` | 构建提交、文件哈希与各自的来源/许可 |

`userdata` 里的 API 配置属于使用者自己；转发给朋友请用原始 ZIP。
原始包从跟踪文件的白名单取运行源码，不复制已有存档、本机配置、缓存或开发环境。
`music` 中新放的文件暂不自动播放，本版尚无正式音乐/音效；朋友可以先做小样，收到后再接入。

2026-10-26 的单国收支、街道、集市与新字体开发包已通过[Windows 实际窗口验收](https://github.com/northwindlight/zhanguo/actions/runs/40268362667)，在该次 Actions 的 `Zhanguo-Windows-x64` 工件中下载。它采用运行提交 `9cfdaa7`，尚未替换已发布的 v0.5.1；[本轮截图与检查范围](reviews/GUI单国收支与街道演出-20261026.md)。

2026-10-26 的真人单机开发包已通过[Windows 真人窗口验收](https://github.com/northwindlight/zhanguo/actions/runs/40317006180)，运行代码 `19fec89`，在该次 Actions 的 `Zhanguo-Windows-x64` 工件下载。新建对局为一个国家选“真人”、填写昵称，即可从底部入口办理建设、军令、市场、外交与情报；[截图与验收](reviews/真人GUI第二步-20261026.md)。这批功能属于 main 开发包，v0.5.1 原发布包保持原样。

2026-10-27 的图标操作开发包已通过[Windows 实际窗口验收](https://github.com/northwindlight/zhanguo/actions/runs/40442608814)，运行提交 `1f83661`，现作为 [v0.6.0 Pre-release](https://github.com/northwindlight/zhanguo/releases/tag/v0.6.0) 发布，Windows 附件与验收工件字节相同。
主菜单现在明确区分玩家／看海；玩家先定制本国与地图，再设置 AI 对手。点本国地块使用建设／征兵图标格，短悬停看费用与要求、长悬停看作用和预计收益；缺条件变暗，每格本期施工次数独立显示。
独立城堡、林场、四种厂区及资源 SVG 附在 `美术预览/建设征兵SVG`；[截图与验收](reviews/真人GUI图标与交互-20261027.md)。

2026-10-27 的市场与驻军打磨版发布为 [v0.6.1 Pre-release](https://github.com/northwindlight/zhanguo/releases/tag/v0.6.1)：市场增加十档买卖按钮和自动交易，施工可半额撤销，完工后可零返还拆除；驻军与军令移到地块面板顶部，野人待战／交锋使用同一编队与血量。左上角常驻显示燃料约束下的电力预计供电／需求，以及上期实际电网状态。
运行提交 `b2c51b7`；[Windows 便携包与实际窗口验收](https://github.com/northwindlight/zhanguo/actions/runs/40477510475)、[本轮截图与范围](reviews/市场与建筑拆除及驻军-20261027.md)。本版请新开局，旧档继续用原发布包。

本轮 [v0.6.2 Pre-release](https://github.com/northwindlight/zhanguo/releases/tag/v0.6.2) 增加右侧地块／军队总览和筛选、资源悬浮产消明细，并修复军屯耕地占用、数字框需回车、市场期望库存与真人倍速切换。买卖、建设和征兵可在动画期间连续排队，支持取消尚未提交的指令；每笔按执行时条件核算。
运行代码 `3f57e07`；[Windows构建与实际窗口](https://github.com/northwindlight/zhanguo/actions/runs/40494984758)，[本轮截图与范围](reviews/军队总览与地块筛选-20261027.md)。可读v0.6.1存档，保留旧档既有建筑，不自动拆除；更早版本原有严格检查保持。

本轮v0.6.3修复军令误拦截、已知逐格路线和瞭望塔限建，接通CI自动预发布；[实现范围与验收](reviews/GUI军令规则与自动预发布-20261028.md)。

## 发布顺序

完成一批可试玩版本后，默认先发布 GitHub **Pre-release**。CI、对应运行代码的 Windows 实际窗口及包校验通过后，上传 Windows ZIP、源码 ZIP 和 `SHA256SUMS`，核对远端附件大小与摘要，再公开预发布。

用户试玩确认后，将同一条 GitHub Release 取消 Pre-release 标记、更新标题并设为正式最新版，同步 README 下载说明；仅转换发布状态时保留原标签、运行包和校验值。需要改代码则另发新版本的预发布，并重新构建和验证。

构建与上传材料保存在仓库忽略目录 `archive/releases/<版本>/`，不再把交付包放到主目录。主目录中的旧交付副本清理后，通过发布页下载。

## 构建与验证

自动发布已接入`ci.yml`：main上根目录`VERSION`对应尚未发布的标签时，离线回归、文档、C#与Windows实际窗口全部通过后直接发Pre-release。Windows附件来自同次成功构建，源码绑定同一提交，上传摘要核对完成再公开。重跑同提交可续传草稿，已公开附件不覆盖；后续完成版本递增`VERSION`并添加`docs/releases/v<版本>.md`。规则源码也会触发Windows构建；仅文档改动且无待发版本时不发包。

[Windows便携包工作流](../.github/workflows/windows-portable.yml)在 Windows 构建机上执行。
它使用官方 Godot 4.7.2 .NET 的导出模板、.NET 8 SDK和 Python 3.13.17嵌入式发行版；
下载的工具均锁定版本和 SHA-256。SDK与编辑器仅用于构建，不要求接收者安装。

GitHub Actions 的 **Windows 便携包** 运行完成后，`Zhanguo-Windows-x64` 产物内有游戏 ZIP 和校验文件。
另一份 `Windows-portable-verification` 保存包在 Windows 上的检查报告与窗口截图。

本地 Windows 构建机也可以运行：

```powershell
python scripts/package_windows.py --output archive/windows-build-new --cache archive/windows-tools
$zip = (Get-ChildItem archive/windows-build-new/*.zip).FullName
./scripts/verify_windows_package.ps1 -Package $zip -Output archive/windows-check-new
```

输出目录必须为新目录。打包脚本只带运行所需的项目内容，自带解释器只从自己的标准库和 `game` 导入。
导出应用从 exe 所在目录解析环境，忽略源码开发环境的根目录、解释器和默认对局配置。
测试把 ZIP 解压至中文及空格路径，从无关工作目录启动，并让系统 Python/.NET 环境不可用：

1. 导出的 exe 用随包 Python 建世界、完成一期、保存并退出。
2. 打开真实窗口检查主菜单、配置表单与 API 档案布局。
3. 用 ANGLE 在构建机绘制默认活地图，逐批确认演出并推进到三期，核对像素素材、字体、抽屉、Esc 自动暂停/保存排队/恢复和保存退出。

4. 在正常 v13 世界生成的样例中，检查市场收付款、生产、补给、进攻箭头、实际送达信件和三档缩放的飞鸟。
5. 用四种逻辑窗口尺寸、两档账目字号与 1.25／1.5 倍内容渲染缩放验证 12 组排版；此项不等同于修改 Windows 系统 DPI。
6. 正常真人开局实际购买、写信、建设征兵、移动及攻击、买图，运行三期，检查迷雾、静态历史、四尺寸、宣战警告、排队保存与放弃后退出。
7. 在12×12合成局验证A灭B后的历史国色失效，保持未知新归属与读档一致。
8. 实际切换原生／实验版、关闭三个实验功能，检查四尺寸；已知旧存档升级为新副本并读回，保留原文件、历史、路线和脚本；校验缺少议会模型的开局提示与游戏内实际规则摘要。旧夹具是显式测试输入，不作为包内运行依赖。

窗口检查使用构建机的图形后端，不能代替朋友电脑上 Vulkan 或实际显卡的性能测试。

官方资料：[Godot Windows 导出](https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_windows.html)、
[C# 运行与构建要求](https://docs.godotengine.org/en/stable/tutorials/scripting/c_sharp/c_sharp_basics.html)、
[Python 嵌入式发行版](https://docs.python.org/3.13/using/windows.html#the-embeddable-package)。
