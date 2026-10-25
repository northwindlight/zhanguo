# Windows 便携体验包

[v0.5.1 发布页](https://github.com/northwindlight/zhanguo/releases/tag/v0.5.1) · [Windows x64 下载](https://github.com/northwindlight/zhanguo/releases/download/v0.5.1/zhanguo-0.5.1-windows-x64.zip)

用于让朋友直接体验地图、界面与演出，并给音乐、美术提建议。
目标为 Windows 10/11、x64。完整解压后双击 `Zhanguo.exe`，无需安装 Godot、Python 或 .NET SDK。
默认新的对局是暂停的 v13 规则 AI 八国局；点开始和继续即可离线观看。

ZIP 中有：

| 内容 | 用途 |
| --- | --- |
| `Zhanguo.exe`、`Zhanguo.pck`、`data_…` | 导出的游戏、素材和自带 .NET 运行库 |
| `runtime/python`、`game` | 官方嵌入式 Python 与运行所需的源码、规则、翻译 |
| `userdata`（首次使用后创建） | 本机存档、菜单配置与 API 设置 |
| `开始体验.txt` | 操作、暂停/快进、镜头与启动说明 |
| `给音乐和美术的说明.md`、`music` | 场景需求、音乐交付和画面反馈入口 |
| `美术预览/index.html`、`美术预览/素材` | 图录、合成预览、SVG母版、PNG、帧表 |
| `演出参考.mp4` | 32秒实际战斗演出，方便讨论节奏 |
| `portable.json`、`bundle-manifest.json`、`LICENSES` | 构建提交、文件哈希与各自的来源/许可 |

`userdata` 里的 API 配置属于使用者自己；转发给朋友请用原始 ZIP。
原始包从跟踪文件的白名单取运行源码，不复制已有存档、本机配置、缓存或开发环境。
`music` 中新放的文件暂不自动播放，本版尚无正式音乐/音效；朋友可以先做小样，收到后再接入。

## 构建与验证

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

最后一项使用构建机的图形后端，不能代替朋友电脑上 Vulkan 或实际显卡的性能测试。

官方资料：[Godot Windows 导出](https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_windows.html)、
[C# 运行与构建要求](https://docs.godotengine.org/en/stable/tutorials/scripting/c_sharp/c_sharp_basics.html)、
[Python 嵌入式发行版](https://docs.python.org/3.13/using/windows.html#the-embeddable-package)。
