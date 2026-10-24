# 模型立绘（右下角头像）预留位

桌面端"模型气泡"层在对话框右下角显示当前发言模型的立绘，上方标模型短名，
对话框标题显示"DeepSeek 扮演 秦"这类全称。PNG 缺失时显示代码绘制的徽章
（模型短名 + 国色描边），放入同名文件即自动替换。

## 规格

- 128×128 或更大方形 PNG，真透明背景；内容为该模型的 logo/吉祥物图形，
  不要文字（名字由界面显示）。
- 显示时保持比例缩进 84×84 的描边框；描边色跟随国家颜色。
- 像素或扁平风格均可，边缘干净，缩到 84px 仍可辨。

## 文件名（= 版本串关键字，小写）

`deepseek` `glm` `gemini` `gpt` `qwen` `kimi` `claude` `grok` `llama`
`hunyuan` `doubao` `minimax` `generic`（其余模型统一用 generic）

版本串匹配规则见 `Scripts/SpeechBubbles.cs` 的 `ModelKeywords`；
例如配置 `model: deepseek-reasoner` → `deepseek.png`。

## 放置后

运行 `python desktop.py`（或带 `--config` 的 LLM 对局）即可看到；
不合适删除文件即回退徽章。
