# 战国 · legacy

这里保存战国的旧版单体实现，也就是原来的 main。当前开发入口已移到模块化的 [`main`](https://github.com/northwindlight/zhanguo/tree/main)，本分支用于对照旧规则、研究历史对局和使用对应版本的旧存档。[English](README.en.md)

战国是一款面向 LLM 的回合制大战略游戏。模型分别治理国家，通过工具调用经营资源、建设、征兵、调兵、通信、结盟与战争；人类主要观察过程，也可以发送公告和来信。

## 真实游戏日志与完整存档

[研究资料目录](research/game-records/README.md) 公开实际游玩的完整 JSON 存档和原始日志，供研究与参考：

| 对局 | 保存回合 | 使用模型 | 状态 |
| --- | --- | --- | --- |
| 八国，种子 20260921 | 258 | `qwen3.8flash`、DeepSeek | 终局，燕为最后存续国家 |
| 八国，种子 20261009 | 158 | `qwen3.8flash` | 公开快照 |

258 局确实使用过 DeepSeek，部分原始推理签名标识为 `deepseek-flash[1m]`。模型信息由对局提供者说明，没有据此推断完整的各国、各回合模型切换表。

资料只更换存放位置和文件名，原始字节完整保留；目录内提供清单、来源文件名和 SHA-256。日志可能含重复或未完成回合，实际保存回合以存档顶层 `turn` 为准。两局来自旧版 World v4 代码体系；续局应使用与生成存档对应的历史提交，不能交给新 main 的模块化存档加载器。

## 旧版代码结构

- `game.py`、`mp.py`：世界状态、经济与军事行为、多国游戏。
- `mapgen.py`、`balance.py`：地图生成与规则参数。
- `mp_ai.py`、`ctx.py`、`llm_provider.py`：LLM 工具交互、跨回合记忆和模型传输。
- `rule_ai.py`、`ruleai/`：旧版规则玩家。
- `mp_run.py`、`console.py`、`render.py`：宿主、观察者终端与地图呈现。

领域规则与宿主在旧版中直接相互调用。新 main 则使用独立发行版、声明式模块、绑定身份的统一玩家 API，以及独立 UI Mod；两套结构和整局格式不同。

## 使用旧版

```bash
git switch legacy
cp mp_config.example.json mp_config.json
# 配置各国模型接入，或使用规则 AI。
./start.sh --config mp_config.json --new
```

Windows 可使用 `start.ps1`。启动脚本建立本地虚拟环境并安装依赖。续局省略 `--new`，另行指定自己的存档路径；`--new` 会覆盖目标，公开研究文件应保留为只读参考副本。

本分支不替代模型接入配置；私人 API key 不随公开资料发布。历史日志含真实模型错误、战争与战略失败，适合研究长期行为，不能直接据此生成标准化模型排行榜。

## 文档入口

- [游戏说明书](docs/游戏说明书.md)、[经济学手册](docs/经济学手册.md)。
- [基础指南](docs/基础指南.md)、[战争手册](docs/战争手册.md)、[战略手册](docs/战略手册.md)。
- [真实对局资料](research/game-records/README.md)。
- [当前模块化版本](https://github.com/northwindlight/zhanguo/tree/main)。

文档描述本分支的历史实现。新增发行版、实验性议会、库存市场和 Python 沙盒的说明在 main。
