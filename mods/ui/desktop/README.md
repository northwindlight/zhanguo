# 战国 · 观天下

Godot 4.7.2 .NET / C# 原生桌面，默认 Compatibility 渲染器，可通过启动参数使用 Vulkan。
上帝视角观察者专用，Python 继续持有世界、规则、AI 和存档。

![全窗地图与实际地貌](../../../docs/design/gui-world-fullscreen-20261023.png)

默认 `python desktop.py` 已接入与地貌样板共用的连续山体、林地、农田、海岸和雪峰绘制。
输入来自当前八国演示局的真实观察者快照，含种子、地形、资源和建筑；不使用固定3×3地图替代。
地图占满整个窗口，列国、山河详情、市集、纪事为覆盖地图的抽屉，默认全部收起。
打开抽屉不会改变地图尺寸或镜头；播放、单回合与保存按钮悬浮在右上角。
外缘曲线、真实指针操作与缓存检查见[本批记录](../../../docs/reviews/GUI全窗地图与抽屉-20261023.md)。

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
为了展示真实单位，使用观察者补助接口给每国两支骑兵，其余规则沿用原剧本。
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
本版控制器尚未与终端入口合并，后续接 LLM 前应先共用安全运行控制。

离线八国场景只绘制当前快照，消息通道上限 64、纪事上限 160，不累积历史场景节点。
军队/国界覆盖层绘制 12 Hz，静态地貌复用缓存，镜头输入与界面按渲染帧更新。每支军队均来自观察者事实；
未物化的无主守军不凭空生成。国力按真实兵种数量统计，HP 只在驻军详情列出。
缩小时按国家标名，近看显示逐格归属。

美术使用包内原图，文件与哈希见 [manifest.json](assets/manifest.json)，
来源及许可见 [CREDITS.md](assets/CREDITS.md)。当前建筑为通用视觉代用图标，
种类以地块详情为准；骑兵仅播放待机帧，步兵为静态待机帧。
房舍、树木和矿点使用 Mini World 原图，地表与岩壁按实际地图生成；密集设施在空间不足时合并为计数标记，详情保留完整列表。
**尚未实现**行军路径、攻击/受击/阵亡、建筑施工动画、事件回放及声音。
不从“单位已移动”推断权威行军路径，也不在战斗结算前绘制确定伤亡。

## 验证

```bash
python3 -m unittest tests.test_desktop tests.test_observer tests.test_ui_mod -q
python3 desktop.py --build-only
```

Linux 无桌面验收可使用 Xvfb：

```bash
xvfb-run -a -s '-screen 0 1440x900x24' python3 desktop.py --smoke-output /tmp/zhanguo.png
```

窗口验收会推进三回合、等待完整边界暂停，验证地图铺满视口、抽屉按钮和收起按钮的真实指针事件、
开关时镜头与地图尺寸不变、按国聚焦及鼠标选中地块，截取全景、四个抽屉和近景，并保存退出。
截图旁 `.json` 记录回合、地块/设施/军队数、地表缓存、操作检查结果与存档路径。
单测核对 576 地块、八国、16 支初始真实军队、脱离世界与玩家权限、慢回合
期间的暂停排队、完整周期保存读回及异常不覆盖旧档。

2026-10-23 本批全窗活地图已在 Linux ARM64 / Xvfb / Mesa llvmpipe 验证。
此前固定3×3样板已有 Windows / Iris Xe / Vulkan 短测，不能代替本批24×24活地图的实际 GPU 性能验收；macOS 待验。
