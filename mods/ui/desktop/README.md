# 战国 · 观天下

Godot 4.7.2 .NET / C# 原生桌面。全窗地图看各国经营与交战，侧边功能收进抽屉；
Python 继续持有世界、规则、AI 与存档。默认 Compatibility，也可通过启动参数使用 Vulkan。

![统一像素城镇](../../../docs/design/gui-unified-town-20261024.png)

当前地图使用 [unified-v1 原创图集](assets/unified-v1/README.md)，参考 WorldBox 的格状地貌与
小比例聚落组织：16×16 地形小格、每档8像素高的岩壁、阶梯海岸与深浅海水。
普通建筑占32×32世界像素，工厂48×32、市政厅64×48；居民、步兵与骑兵共用同一世界像素缩放。
居民搬运和劳动，步兵保持剑/矛/斧外观混编；肤色、金属和马匹不随衣服一起染色。
恢复 Fusion Pixel 中文像素字体，面板采用方角边框。

树干固定、叶冠8帧轻摆；三档水面各6帧；六类人物使用四方向待机、行走与劳动/攻击帧。
施工脚手架分阶段出现，烟从素材标记的烟囱冒出；金矿、铁矿和地表沥青读取实际资源丰度，
布局避让建筑、树冠和其他矿点。旧素材和来源保留在[旧素材说明](assets/actors/README.md)。
[实际窗口截图、录像与验收](../../../docs/reviews/GUI统一像素世界与动画-20261024.md)。

地图来源是当前对局的观察者快照，包含种子、地形、资源和建筑，默认正常 v13 离线八国开局。
远景铺国色，放大后淡化。列国、详情、市集和纪事抽屉覆盖地图，开关不会挤压地图。
军团显示真实 HP / 上限、实际防御和脚下的步骑兵数量；队形中的小人数只用于表现。
按实际轮换顺序显示国家与回合横幅、聚焦行动地点，逐项播放采购、建设、征兵和军令。
一次模型响应中的所有工具（含脚本内部动作）先全部执行，再统一播放这一批操作；播完才继续调用模型或换国。
行军、抵达与接敌随本批命令演出；回合末播放采集生产、军需、逐场交锋、伤亡、易帜与落成，播完再进入下一期。
0.5× / 1× / 2× / 4× / 16× 档位只用于规则 AI 与结算；LLM 思考、发言与行动自动1×。
[调用批次、行军与 Esc 菜单的实现和验收](../../../docs/reviews/GUI调用批次演出与暂停菜单-20261025.md)。

主菜单支持新局、存档和 API 档案。可配置剧本国子集、种子、地图、每国客户端和模型，
附加 JSON 表达表单之外的配置；API key 仅存本地设置与配置。
模型思考、发言和小结分别显示，长思考可折叠、滚动；工具参数与回执留在行动横幅。
七张用户半身图按原图比例展示，缺图时显示模型名；见[半身图说明](assets/portraits/README.md)。

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

这是独立的 3×3 合成比例草样，展示 256×256 像素大地区、格状山谷、谷地房舍、
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

### Windows 便携体验包

Windows 构建流程会导出 `Zhanguo.exe`，并带上 .NET 运行库、官方嵌入式 Python 与游戏宿主。
完整解压后双击即可，不需要安装开发工具。默认兼容性渲染，另附 Vulkan 启动脚本。
存档与设置写到包内 `userdata`，移动目录后继续从新位置找运行环境。
包内还有操作说明、音乐与美术评审说明、原创素材母版/图录及32秒演出参考。

构建步骤和产物下载见[Windows 便携打包说明](../../../docs/Windows便携包.md)。

### 源码启动

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

现役 LLM 对局的接管、远程连接与其他平台独立安装包尚未接入。
源码启动依赖完整仓库与 Python；Windows 另有上述便携构建，Steam 发布尚未进行。

## 操作

- 左下角列国 / 山河 / 市集 / 纪事：打开对应抽屉，再点同一按钮或“收起 ×”关闭。
- 左键地块：打开山河抽屉，查看地形、归属、完整建筑类型/等级、驻军编号与 HP。
- 滚轮：以鼠标为中心缩放；右键 / 中键：拖动。
- 列国抽屉中的国名：聚焦并查看库存、资产、国土、兵种数量。
- F / 总览天下：适应全图；国界和军队可单独隐藏。
- 继续 / 暂停 / 空格：立即暂停当前片段，宿主停在本批操作后，继续后才进入后续调用。
- 菜单 / Esc：自动暂停演出并打开继续观看、保存、保存并退出；再次 Esc 返回。原本暂停的局面保持暂停。
- 速度下拉框：细看0.5×、慢速1×、常速2×、快速4×、快进16×；“跟随”控制逐动作镜头。
- 下一回合：按批观看一次完整周期并保存。菜单中的保存只写完整回合；未完成时提示排队，继续观看至回合结束后写入。
- 关闭窗口：请求完成当前回合、保存，宿主退出后关闭窗口。

Python 执行回合时界面继续响应。模型调用期间输入先排队；调用结束、本批演出等待时可处理暂停、继续、保存请求与退出。
只有完整结算后才保存，界面分别显示“保存已排队”和“已经保存”。异常回合不覆盖最近完整存档；宿主或保存错误在
纪事区显示，窗口保留。鼠标悬停状态栏可查看保存路径。

## 实现边界

`ui/desktop.py` 是离线运行控制器，只在一个执行线程访问 Session；输入线程
只排队，不读写世界。stdout 专用于 `zhanguo.desktop/1` NDJSON；其他输出走
stderr。消息携带 generation 与单调 revision，桌面拒绝旧代或旧 revision。
宿主只发送脱离世界的观察者投影，不向玩家 API 提供全图。

`UILayer.desktop_project()` 决定项目入口，`desktop_frame()` 决定展示投影，
替换 UI Mod 时不固定启动标准地图。通用引擎无 Godot、地形或美术依赖。
本版控制器尚未与终端入口合并；支持按配置开新 LLM 局或复制续看，未接管正在运行的终端进程。

单个执行线程通过 `cycle(on_result=..., on_batch=...)` 采集写动作并确定批次边界。
LLM 每次响应的工具执行完毕才发送一个 action 演出及同一时点的快照；规则 AI 每国一批，批内仍逐项播放操作。
宿主等正确 generation / sequence 的 `playback_ack` 后才继续下一批；自动补交的 end_turn 也在结算前展示。
最终 settlement 演出在完整结算保存后发送并等待确认。只保留一个待播放批次，不在后台积压多期。
每期累计上限2048片段（不会随批次清零），采集异常退回当前完整快照；图形采集异常不会变成规则动作异常。
中途暂停不写非法存档；退出解除播放等待，完成当前周期、保存并停止，避免窗口关闭后等待无人回复的确认。
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
python3 -m unittest tests.test_desktop tests.test_desktop_activity tests.test_desktop_batches tests.test_desktop_streaming tests.test_observer tests.test_ui_mod -q
python3 desktop.py --build-only
```

Linux 无桌面验收可使用 Xvfb：

```bash
xvfb-run -a -s '-screen 0 1440x900x24' python3 desktop.py --smoke-output /tmp/zhanguo.png
```

窗口验收会播放三个完整回合（续档则从当前回合继续），等待完整边界暂停，验证地图铺满视口、抽屉按钮和收起按钮的真实指针事件、
开关时镜头与地图尺寸不变、按国聚焦及鼠标选中地块；在真实动作批次中按 Esc 检查自动暂停、保存排队和恢复，截取菜单、全景、四个抽屉和近景，并保存退出。
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
