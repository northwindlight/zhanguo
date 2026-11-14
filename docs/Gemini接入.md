# 原生 Gemini 接入

将国家的 `provider` 设为 `gemini` 后，战国使用 Google 官方 `google-genai` SDK，直接发送 Gemini `generateContent` 请求。官方 Gemini API 和实现同一协议的代理均可使用。

```json
{
  "client": "llm",
  "provider": "gemini",
  "base_url": "https://generativelanguage.googleapis.com",
  "api_key": "填入密钥",
  "model": "gemini-3.8-flash",
  "reasoning_effort": "high",
  "max_tokens": 16384
}
```

模型名和权限以接入服务的目录为准。兼容代理可填写自己的 `base_url`；地址末尾可以带 `v1beta`，不会重复添加版本路径。默认API版本为 `v1beta`，也可显式配置 `gemini_api_version`。

源码环境在项目虚拟环境中安装 `pip install -r requirements.txt`；只补Gemini依赖时使用 `requirements-gemini.txt`。Windows便携构建内置SDK及其依赖，下载固定版本的Windows wheel并校验SHA-256，用户无需另装Python或库。纯规则客户端及其他协议不会在运行时导入Google SDK。

`thinking=disabled` 显式关闭思考；启用时可用 `reasoning_effort` 指定模型支持的强度，或用 `gemini_thinking_budget` 指定预算。SDK的自动函数执行与自动续写均关闭，所有工具调用仍经过战国正式玩家API校验、执行并产生回执。

响应中的原生Part、每个Part的思考签名和函数调用关联一起保存在既有客户端回放记录中。并行调用的结果按调用顺序组装，签名不合并、不借用其他模型的签名。旧协议存档没有Gemini原生签名时，对其历史函数调用使用Google文档中的迁移标记；不会把历史动作改成新的实际执行。

配置 `ctx_old_reasoning=strip` 时，旧思考摘要移出请求，但保留函数调用Part本身的签名。异常结束、空响应、缺失完成帧或不完整工具调用均报错并遵循既有重试策略，不把它们当作有效行动次数。记忆压缩保留工具声明但禁止调用，并显式关闭思考。

参考：[Google Python SDK](https://github.com/googleapis/python-genai)、[Gemini函数调用](https://ai.google.dev/gemini-api/docs/function-calling)、[思考签名](https://ai.google.dev/gemini-api/docs/thought-signatures)。
