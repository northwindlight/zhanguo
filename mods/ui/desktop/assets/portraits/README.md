# 模型半身图

已接入用户从主目录 `立绘.zip` 提供的七张透明 PNG：
`deepseek.png`、`qwen.png`、`kimi.png`、`gemini.png`、`gpt.png`、`claude.png`、`grok.png`。
原图逐字节保留；来源、SHA-256、尺寸和透明度记录在 `manifest.json`。
作者、原始网址和许可未随压缩包提供，清单保留为空，不套用 Mini World 的许可。
其他支持名：`glm`、`gemini`、`gpt`、`kimi`、`claude`、`grok`、`llama`、
`hunyuan`、`doubao`、`minimax`；未知模型可用 `generic.png`。

半身、胸像、方图均可，不要求固定尺寸。建议高度 512–1024px，人物轮廓四周留少量透明空间。
这批源文件是全身图，界面通过清单中的 `display_region` 只取头部到腰部的上半身，
保持比例、不裁头、不拉宽，不改动源文件。其他未配置裁切区域的图片整图等比显示。
半身图高度独立于气泡文字长度，小窗口自动收窄，避免长正文把人物撑成全身大图。
图内无需写模型名，界面单独显示。模型名匹配不区分大小写。

保存后重新运行 `python desktop.py`，启动器自动导入。
同一模型同时存在时，此目录优先于旧 `assets/logos/<模型>.png`。
缺图时使用文字徽章。将来添加图片时应一并记录作者、来源和许可。
