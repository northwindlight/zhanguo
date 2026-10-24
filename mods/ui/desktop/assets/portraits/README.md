# 模型半身图

用户提供的透明 PNG 放在本目录：`deepseek.png`、`qwen.png`。
其他支持名：`glm`、`gemini`、`gpt`、`kimi`、`claude`、`grok`、`llama`、
`hunyuan`、`doubao`、`minimax`；未知模型可用 `generic.png`。

半身、胸像、方图均可，不要求固定尺寸。建议高度 512–1024px，人物轮廓四周留少量透明空间。
显示时整图等比缩入右下角区域，不裁头、不拉宽，不需要准备全身立绘。
图内无需写模型名，界面单独显示。模型名匹配不区分大小写。

保存后重新运行 `python desktop.py`，启动器自动导入。
同一模型同时存在时，此目录优先于旧 `assets/logos/<模型>.png`。
缺图时使用文字徽章。将来添加图片时应一并记录作者、来源和许可。
