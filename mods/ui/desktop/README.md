# 战国 · 观天下

Godot 4.7.2 .NET / C# 原生桌面，默认 Compatibility 渲染器，可通过启动参数使用 Vulkan。
上帝视角观察者专用，Python 继续持有世界、规则、AI 和存档。

![逐国横幅与聚落小人](../../../docs/design/gui-live-actor-20261024.png)

默认 `python desktop.py` 已接入与地貌样板共用的连续山体、林地、农田、海岸和雪峰绘制。
输入来自当前八国演示局的真实观察者快照，含种子、地形、资源和建筑；不使用固定3×3地图替代。
地图占满整个窗口，列国、山河详情、市集、纪事为覆盖地图的抽屉，默认全部收起。
打开抽屉不会改变地图尺寸或镜头；播放、单回合与保存按钮悬浮在右上角。
外缘曲线、真实指针操作与缓存检查见[本批记录](../../../docs/reviews/GUI全窗地图与抽屉-20261023.md)。

现已接入逐动作演出：按实际行动顺序显示“秦 · 第1回合”等横幅、自动聚焦，逐项播放采购、
建设、征召和调动军令。行军放在回合末演出，沿合法路线分队行进、抵达和接敌；
随后展示生产、军需与每场战斗的交锋、伤亡和易帜，全部播完再进入下一期。
右上角提供0.5× / 1× / 2× / 4× / 16×五档速度，默认慢速1×；“跟随”可关闭自动聚焦。
档位只作用于规则 AI 和回合结算；LLM 行动、思考与发言自动恢复1×，按钮显示“LLM 1×”，
切回规则 AI 或结算后恢复原档位。
居民在聚落附近走动，施工有地基、脚手架和工人；野地显示潜在守军，军队以密集小队表现，
脚下按兵种标注真实规则军队数（如“步×2”“骑兵×2”），上方显示国名、爱心血条与
合计 HP / 上限、盾牌与实际防御减伤；插图中的小人数量只表现队形。
防御取真实地形及本方堡垒，进攻方不借用敌方地形防御；沙漠的负防御也会如实显示。
远景铺满领土国色，放大后国色淡化，露出地貌。
[32秒实际窗口录像](../../../docs/design/gui-live-battle-tour-20261024.mp4)与[演出验收](../../../docs/reviews/GUI逐动作与聚落军团-20261024.md)。

2026-10-24 视觉增强批：水面由着色器驱动波光、闪光与岸边泡沫脉冲（地表层新增水面遮罩，
岩壁与物件分层绘制避免着色器外溢）；地表烘焙加入平滑色带、草丛、干草斑、湿沙、
水下卵石、雪粒与等高线环境光遮蔽，岩壁带层理、苔帽与坡脚碎石；小人重画为约13像素
带轮廓的细角色（骑兵含奔马与骑手），有肤色/发型/衣色变化、四肢摆动、持矛执盾与
挥锤动画；环境层新增屋顶炊烟、施工扬尘、窗户灯光、飞鸟、行军尘土、近战火花、
占领涟漪与飘动军旗。角色与建筑素材位已预留在 `assets/actors/`（含清单与提示词），
PNG 放入即自动替换代码绘制版本，见[素材规格](../../../docs/design/素材补充与生图规格.md)。

2026-10-24 演出批：跟随镜头放大到 1.15×–1.55×（战斗最近）并改为平滑变焦，
动工/落成直接对准建筑本体而非格子中心；新地基分四阶段升起（场地→地基→立柱→支架），
落成有金框脉冲与揭幕尘雾；征兵演出为士兵从聚落走向集结点列队扬尘；
交战双方随进度逼近并周期冲击，战斗镜头带轻微震屏，伤亡保留倒地与血渍。

人物改用原包剑兵、矛兵、斧兵、骑兵的方向/行走/攻击帧，居民增加衣色、发型、头巾与携带物。
同一类步兵可混用剑、矛、斧外观；素材变体不新增规则兵种。
战斗分为接敌、三次交锋、受击与战果，血条在交锋点逐段抵达本次真实结算结果；
阵亡只按已确认的规则结果播放，普通近战不再出现无依据的箭雨。
市政厅加大到64像素高，周围有石板广场、国旗、水井、住宅与摊位，居民更密集。
住宅与摊位仅为装饰，生产设施数量仍以详情清单为准。
含油地块显示不规则沥青渗出斑，数量随真实石油丰度变化，避开房屋、广场、树木和矿石。
黄金、岩石与山岩相互避让；资源飘字按实际文字宽度排列，并避让军队血条。

2026-10-24 开局主菜单：默认启动进入主菜单（新对局 / 加载存档 / 设置 · API 档案）。
新对局从 10 个剧本国中任取 2–10 家（行序即行动顺序），自定种子、地图边长、回合上限
与上下文窗口；每国可选 规则AI / 简易策略 / 大模型，大模型国家引用本地 API 档案
（`archive/desktop-settings.json`，仅保存在本地文件）。每国与全局的"附加 JSON"逐层合并，
表达力与 mp_config.json 等价，生成的配置走既有 `ZHANGUO_DESKTOP_CONFIG` 通道；
加载存档列出 `archive/desktop` 最近 30 档一键续看。`--menu-output` 提供实窗自检。
见[本批记录](../../../docs/reviews/GUI开局主菜单-20261024.md)。

2026-10-24 界面修订：正文采用可读的系统中文字体；思考、发言、小结分开显示，
长思考可展开并滚动。真正的模型文本在 OpenAI 兼容 / Responses / Anthropic 流片段到达时更新；
工具参数和操作回执继续在顶部演出横幅展示，不会冒充模型发言。
规则 AI 不生成模拟思考。失败请求重试会清除旧片段，切换国家不会混入前一个模型的文字。
右下角支持透明 PNG 半身图，保持比例：优先读取 `assets/portraits/deepseek.png`、
`qwen.png` 等，也兼容原 `assets/logos/`。没有图片时显示模型名徽章；素材由用户提供。
具体见[半身图放置说明](assets/portraits/README.md)。

市政厅、农田、矿场、油井、工厂、兵营已加入本次生成的透明建筑图，
完整提示词及哈希见 `assets/actors/*.prompt.txt` 和 `generated-buildings.json`。
它们与原 Mini World 贴图分别记录来源，不借用原包许可声明。

另有[原创 SVG 素材第一批](assets/unified-v1/README.md)：格状山体与岩壁、2×2 普通建筑、
分层树木和统一人物动作，附[组合场景与素材预览](../../../docs/design/unified-v1/index.html)。
这批目前是独立美术样板，尚未替换上述游戏内素材。

离线检查多种窗口尺寸、长正文和半身图比例（不会调用模型）：

```bash
python3 desktop.py --ui-review-output archive/previews/ui-review.png
python3 desktop.py --menu-output archive/previews/menu-review.png
```

前者使用用户提供的七张图片与标明“非模型输出”的合成文本检查布局，未提供图片时回退几何样例；
真实模型流程另由离线模拟传输测试验证。七张原图不改动，通过清单只显示上半身。
[修订与验证记录](../../../docs/reviews/GUI流式正文与布局修订-20261024.md)。

离线检查同格步骑兵、实际战斗伤亡、快进切换、城镇、沥青与资源飘字：

```bash
python3 scripts/review_desktop_actors.py --output archive/previews/actors-review-new
```

无桌面 Linux 在命令前加 `xvfb-run -a -s '-screen 0 1440x900x24'`。
脚本新建独立世界并明确添加测试兵力，输出不是正常开局；默认对局依旧零军队起步。
[本批图片与验收](../../../docs/reviews/GUI半身图与军团城镇-20261024.md)。

## 第一阶段地貌样板

按[四周留余量的排期](../../../docs/plans/GUI第一阶段.md)分批推进阶段 A。
当前可运行：

```bash
python3 desktop.py --sample terrain
```

这是独立的 3×3 合成比例草样，展示 256×256 像素大地区、连续山谷、谷地房舍、
四档木资源森林、含金岩层和农田。人物暂为 6×8 像素草图。全景/专题按钮、
鼠标缩放/拖动/点选及 F 全图可用；不创建 Python 对局。布局固定种子可重现。
第二批已补二维山体起伏、成簇林缘、绿色松树、岩石与矿洞，并试用 GPT / 本机 Antigravity 生成的两种山岩素材。
当前固定输入 `Samples/terrain-v2.json`（布局版本 2），首批 v1 文件仅保留作历史对照。
阶段 A 仍在进行，地貌美术和实际 GPU 验收在后续批次完善。
第三批已增加“邻接对照”和连续岩壁，镜头操作复用静态绘制缓存，地图外缘裁切完整。
实际窗口录像和持续测量见[第三批记录](../../../docs/reviews/GUI阶段A第三批-20261023.md)。
素材来源和 Gemini 可复用提示词见[素材规格](../../../docs/design/素材补充与生图规格.md)。

`--sample terrain --smoke-output /tmp/terrain.png` 输出实际窗口的全景/近景与输入核对 JSON。
追加 `--sample-tour` 会在约5秒预热后执行32秒镜头巡览，按墙上时钟记录帧间隔与内存/节点/缓存。
Linux 实际窗口录制：`python3 scripts/record_terrain_sample.py --output archive/previews/terrain-recording-new`，
需要 Xvfb、ffmpeg、ffprobe，输出目录不能已存在。录制也会占用 CPU；软渲染数据与实际 GPU 性能分别验收。
现有离线八国演示继续使用下文的默认入口。

默认离线八国地图现已接入自然边界：海洋覆盖最外圈，高山沿大陆内缘起伏，显示海岸与雪峰，
点选会说明其不可通行、占领或建造；原山地继续可通行。运行 `python desktop.py` 查看新开局。
`--sample terrain` 仍为固定3×3比例样板；新生成地图的规则与显示验收见
[自然边界记录](../../../docs/reviews/自然边界与高山海洋-20261023.md)。

## 启动

安装 Godot **4.7.2 .NET**、.NET **8 SDK**、Python **3.11+**，在仓库根目录执行：

```bash
python3 desktop.py --godot /path/to/godot-mono --dotnet /path/to/dotnet
```

工具已在 PATH 中时直接 `python3 desktop.py`。本开发机亦自动发现
`~/tools/zhanguo-desktop/` 内的工具。Windows 使用 `python desktop.py`，
并通过参数指定 `Godot_v4.7.2-stable_mono_win64.exe` 和 `dotnet.exe`。

Windows 使用 WinGet 安装时，包名必须是 **GodotEngine.GodotEngine.Mono**。
`GodotEngine.GodotEngine` 是不支持 C# 的标准版，即使 `dotnet build` 成功，
也会在载入场景时报告 `No loader found for resource: ...Desktop.cs`。
两个包都使用 `godot` / `godot_console` 命令别名，建议先卸载误装的标准版：

```powershell
winget uninstall --id GodotEngine.GodotEngine --exact
winget install --id GodotEngine.GodotEngine.Mono --exact --version 4.7.2
```

重新打开 PowerShell，然后在仓库目录运行：

```powershell
godot_console --headless --version
python desktop.py
```

版本字符串应包含 `.mono.`。启动器会在编译前检查这一点。
若保留两个版本，请使用 `--godot` 明确指定 Mono 包内的可执行文件。

Windows 的 Vulkan 地貌巡览也统一通过启动器运行：

```powershell
python desktop.py --sample terrain --rendering-method mobile --rendering-driver vulkan --sample-tour --smoke-output windows-vulkan.png --log-file windows-vulkan.log
```

启动器会编译、导入，随后打开实际窗口，预热后自动巡览32秒并退出。
输出 `windows-vulkan.png` 等六张截图、`windows-vulkan.png.json` 指标和 `windows-vulkan.log` 日志。
请保持窗口可见，并以日志中的图形 API 和显卡名称确认实际后端。
OpenGL 对照使用 `--rendering-method gl_compatibility --rendering-driver opengl3`，并换一组输出文件名。
这些选项只作用于本次窗口运行，不修改 `project.godot`。

查看本批更新的全窗八国地图，使用默认场景：

```powershell
python desktop.py --rendering-method mobile --rendering-driver vulkan
```

加 `--smoke-output windows-world.png --log-file windows-world.log` 可自动核对抽屉和点选，截图保存后退出。
`--sample terrain` 仍用于固定地貌样板，`--sample-tour` 也只适用于样板。

若直接运行 `godot_console` 报 `.NET: Assemblies not found`，尤其崩溃路径位于
`Microsoft\WinGet\Links`，优先使用上面的 Python 入口：它先将命令别名解析成真实路径，
让 Godot 从实际安装目录寻找随包的 `GodotSharp/Api`，并沿用编译时的 .NET 环境。
启动器会输出解析后的 Godot 路径。若仍失败，检查该路径旁是否完整保留了 `GodotSharp`；
使用 `--godot` 指定完整解压的 .NET 版可执行文件，不能只拷贝一个 exe。
该报错发生在[Godot 自身 API 程序集目录检查](https://github.com/godotengine/godot/blob/4.7.2-stable/modules/mono/mono_gd/gd_mono.cpp#L645-L651)，
与项目 DLL 是否编译成功分别检查。

也可用 Godot .NET 编辑器打开 `project.godot`，构建后运行；宿主默认使用
`python3`（Windows 为 `python`），`ZHANGUO_PYTHON` 可指定解释器。

默认创建**暂停的离线八国 v13 规则 AI 演示局**，不读取 `mp_config.json`、不调用 LLM。
遵循正常剧本开局：初始没有军队，不再附送演示骑兵；军队由v13按规则征召。
界面从实际客户端读取版本及经营偏好，不凭静态标签判断版本。
上限为第 300 回合。每次启动保存到独立目录：

```text
archive/desktop/<时间>-<随机后缀>/
  config.json
  session.json
  session.json.audit/…
```

从演示档继续（复制到新目录，保留原档）：

```bash
python3 desktop.py --resume archive/desktop/<旧目录>/session.json
```

现役 LLM 对局的接管、远程连接与三平台独立安装包尚未接入。
首版依赖完整源码仓库与 Python；不是已完成的 Steam 发布包。

## 操作

- 左下角列国 / 山河 / 市集 / 纪事：打开对应抽屉，再点同一按钮或“收起 ×”关闭；Esc 收起全部。
- 左键地块：打开山河抽屉，查看地形、归属、完整建筑类型/等级、驻军编号与 HP。
- 滚轮：以鼠标为中心缩放；右键 / 中键：拖动。
- 列国抽屉中的国名：聚焦并查看库存、资产、国土、兵种数量。
- F / 总览天下：适应全图；国界和军队可单独隐藏。
- 继续 / 暂停 / 空格：按完整回合边界暂停并保存。
- 演出中暂停会立即停住当前片段；规则宿主完成当前合法周期后保存，画面停在当前播放位置。
- 速度下拉框：细看0.5×、慢速1×、常速2×、快速4×、快进16×；“跟随”控制逐动作镜头。
- 下一回合：推进一次完整周期并保存；保存按钮保存合法边界。
- 关闭窗口：请求完成当前回合、保存，宿主退出后关闭窗口。

Python 执行回合时界面继续响应。命令队列在完整回合边界执行，界面分别显示
“请求排队”和“已经保存”。异常回合不覆盖最近完整存档；宿主或保存错误在
纪事区显示，窗口保留。鼠标悬停状态栏可查看保存路径。

## 实现边界

`ui/desktop.py` 是离线运行控制器，只在一个执行线程访问 Session；输入线程
只排队，不读写世界。stdout 专用于 `zhanguo.desktop/1` NDJSON；其他输出走
stderr。消息携带 generation 与单调 revision，桌面拒绝旧代或旧 revision。
宿主只发送脱离世界的观察者投影，不向玩家 API 提供全图。

`UILayer.desktop_project()` 决定项目入口，`desktop_frame()` 决定展示投影，
替换 UI Mod 时不固定启动标准地图。通用引擎无 Godot、地形或美术依赖。
本版控制器尚未与终端入口合并；支持按配置开新 LLM 局或复制续看，未接管正在运行的终端进程。

单个执行线程通过既有 `cycle(on_result=...)` 采集写动作及只读场景事实。
宿主完整结算并保存后发送至多一周期演出，等待正确代次和序号的 `playback_ack` 后才能继续，
不在后台堆积多个回合。单期上限2048片段，采集异常退回完整快照；图形采集异常不会变成规则动作异常。
消息通道上限64、纪事上限160；地表缓存一局复用，每地区一个静态装饰节点，变化只重绘相关地区。
居民与军队覆盖层空闲时按24Hz更新，镜头和片段按渲染帧更新；小人是代表性演出数量，不新增人口规则或兵力。
潜在野人来自发行版对地块、清除记录和可占领性的只读检查，不生成实体、改变RNG或向玩家泄露全图。
国力按真实兵种数量统计，HP在驻军详情与实际结算片段列出。

美术包含包内原图和另外记录的生成建筑图。原包文件与哈希见 [manifest.json](assets/manifest.json)，
来源及许可见 [CREDITS.md](assets/CREDITS.md)。当前建筑为通用视觉代用图标，
种类以地块详情为准；居民与密集军队采用代码绘制的微型角色，含走动、施工、接敌和战损表现。
房舍、树木和矿点使用 Mini World 原图，地表与岩壁按实际地图生成；密集设施在空间不足时合并为计数标记，详情保留完整列表。
行军使用与发行版可达性搜索一致的一条合法演出路线，不宣称规则持久化了逐小格路径。
战斗、军需与回血使用结构化前后状态和实际资源支出，生产图标来自逐设施经济事实；不解析中文战报猜伤亡。
声音、完整三级城市主题、历史任意回放和平台安装包仍待后续实现。

市集同时列库存、参考价及首单位买卖参考价；缺货显示“无货”，很低的正价格保留有效数字。
`0.000001`不会再被两位小数显示成`0.00`。实际大单成交仍以对应国家的行动回执为准。

## 验证

```bash
python3 -m unittest tests.test_desktop tests.test_observer tests.test_ui_mod -q
python3 desktop.py --build-only
```

Linux 无桌面验收可使用 Xvfb：

```bash
xvfb-run -a -s '-screen 0 1440x900x24' python3 desktop.py --smoke-output /tmp/zhanguo.png
```

窗口验收会播放三个完整回合（续档则从当前回合继续），等待完整边界暂停，验证地图铺满视口、抽屉按钮和收起按钮的真实指针事件、
开关时镜头与地图尺寸不变、按国聚焦及鼠标选中地块，截取全景、四个抽屉和近景，并保存退出。
截图旁 `.json` 记录回合、地块/设施/军队数、地表缓存、操作检查结果与存档路径。
单测核对576地块、八国、正常开局0军队及292处潜在守军、脱离世界与玩家权限、慢回合
期间的暂停排队、完整周期保存读回及异常不覆盖旧档。

录制正常开局四期后的实际演出：

```bash
python3 scripts/record_desktop_demo.py --output archive/previews/desktop-recording-new
```

需要Xvfb、ffmpeg、ffprobe，输出目录不能已存在。脚本先在全新离线局正常推演四期，再用桌面续档，
录制32秒生产/军需/战斗镜头，附原始指标和阶段截图。录像为实际窗口采样，编码帧率不等于引擎帧率。

2026-10-24 本批活地图演出已在 Linux ARM64 / Xvfb / Mesa llvmpipe 验证。
此前固定3×3样板已有 Windows / Iris Xe / Vulkan 短测，不能代替本批24×24活地图的实际 GPU 性能验收；macOS 待验。
