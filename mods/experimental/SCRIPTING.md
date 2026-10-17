# Python 沙盒

君主决定目标与预算，把重复的查询、计算和操作交给保存的短脚本。experimental 装配 `experimental:python` 和独立 Session 脚本服务；native 默认发行版不启用。脚本使用真正的绑定玩家 API，国策审核、权限、费用、回执和控制权变化沿用标准入口。

模型侧统一用 `script` 伞工具：`script(action=write, source=...)` 保存程序，`script(action=check)` 免费检查，再在想运行的回合手动 `script(action=run, inputs={...})`。旧名 `script_write/script_check/script_run` 仍按名注册、可被脚本与规则 AI 调用，只是不进模型工具表。以后不用重发源码，只发送本回合的决策参数。示例 [后勤采购](examples/logistics.py) 接受 `food_target` 和 `budget`，只在资源、市场库存和预算允许时采购；[市场插值](examples/market_interpolation.py) 接受公开的 `history=[[回合,价格],...]` 与 `target`，完成短序列插值或线性外推，不进行搜索。

```python
# 一次保存的脚本；每回合由君主手动决定是否执行。
m = api.query('market')
print('粮价', dict(m['prices'])['native:food'])
if inputs.get('buy_food', 0) > 0:
    api.submit('buy', {'resource': 'native:food',
                       'quantity': inputs['buy_food']})
```

## 编辑、检查与执行

| 工具 | 行为 |
| --- | --- |
| `script_write(source)` | 保存或完全重写本国唯一脚本，修订号递增；不执行 |
| `script_patch(old, new)` | 精确替换源码中唯一一次出现的 old，避免重发全文；找不到或有歧义则拒绝 |
| `script_check(source=None)` | 检查提供的源码或已存脚本；不执行任何计算或游戏 API，不扣游戏资源、不使用执行额度 |
| `script_status(include_source=False)` | 查看额度、修订号、预算和上次结果；默认不重复返回源码 |
| `script_run(inputs=None)` | 执行已存脚本，inputs 为 JSON 参数对象；每国每回合一次 |

检查覆盖语法、受支持构造与结构安全限制，不预测库存、费用、报价或运行结果；`1 / 0` 可以通过语法检查，执行时仍会失败。没有免费的试运行或结果预演。

额度在正式执行开始、解析源码之前占用。语法错误、运行错误、预算超限、失败的游戏动作都消耗本回合额度；修改源码、换君主或再次调用不会恢复额度，下一回合才可以再执行。没有定时器、自动执行、自动重试或回合后台任务。工作变量每次新建；跨回合保存的是源码、修订号和最近执行结果，输入按本次调用提供。

已成功的游戏动作始终保留，不因之后的错误撤销。`api.submit` 得到失败回执时终止脚本，保留原始失败回执。脚本结束不自动结束国家行动；可以显式提交标准 `end_turn`，也可以交回君主继续决策。国策被打回或罢免同样遵守标准 API，旧控制权句柄不能继续行动，也不能在同回合多运行一次脚本。

## 支持的 Python 基础子集

支持 None、布尔、整数、有限浮点数、字符串、列表、元组、字典、切片、解包、赋值与增量赋值；普通算术、比较、短路布尔、条件表达式；if、for、while、break、continue、函数、默认/命名参数、return 和递归；列表和字典推导式。

内置函数：`abs / all / any / bool / dict / enumerate / float / int / len / list / max / min / print / range / reversed / round / sorted / str / sum / tuple / zip`。sorted/min/max 支持已定义函数作为 key；sort 支持 key 与 reverse。字符串和容器只开放有限方法，具体清单在 [解释器](../../engine/scripting.py)。没有 Python 解释器的导入机制和完整标准库；不支持 class、lambda、生成器、装饰器、异常捕获、f-string 或字符串 `%` 格式化，输出可用 `print` 和 `str`。幂运算只接受有界整数指数。

`api.observe/actions/query/submit/events/rules/action_state` 对应宿主标准 API。返回的是独立数据，可以本地修改；不能改宿主对象、指定其他行动者或获得隐藏情报。脚本不能嵌套调用任何 `script_*` 控制工具，也不能获取新一代控制权句柄。

## 计算预算与边界

发行版在 [scripting.toml](scripting.toml) 固定预算；模型不能修改。当前总计 12,000 计算单位、最多 24 次 API 调用，源码 16 KiB、最多 2,048 AST 节点、容器最多 4,096 项、字符串最多 64 KiB、单个值按固定表示计最多 1 MiB、输出最多 4 KiB、整数最多 256 位。语法、值嵌套与调用深度另有结构保护，防止宿主栈耗尽。

计费版本随模块版本固定：AST 求值和语句、循环推进各计基础单位；值访问按元素数和 UTF-8 文本长度收费；排序按 `n * bit_length(n)` 及比较键大小保守收费；字典构造按碰撞最坏情况、容器复制/展开、重复字符串与幂按数据规模收费；每次 API 请求另计 100 单位及参数/返回数据检查。昂贵的原生容器操作先检查或收费，再调用。不把“一个内置函数”当成一个计算单位。预算拒绝时 steps 最大显示上限加一，表示已超出额度，不表示已完成那个昂贵操作。

小型插值和递归可用同一预算；大型搜索、无限循环或过大的序列展开会停止，不按算法名称禁止。没有墙钟超时阈值，也不返回 elapsed、CPU 时间或高精度计时信息，因此不会因为平台速度不同改变计算额度。哈希随机种子不会改变公开输出或计费；浮点幂用固定乘除序列，不调用不同平台的 pow 库。成本模型不表示 CPU 指令数或物理内存大小，跨 Python 版本的原始错误文字也不作完全一致保证。

脚本由 AST 子集解释，不调用 exec/eval/compile，不暴露宿主 Python 对象、任意属性、反射、文件、网络、进程、随机源或计时器。不能安装任何库。不提供物理主机级侧信道防护；本边界防止脚本获取宿主计时/系统观测能力，不承诺抵抗同机器的外部攻击者。

## 回执、审计与续档

执行回执默认返回 `output / steps / calls / actions / error / failed_action / revision / period`；error 保留原类型、原消息、行列与对应源码，没有宿主内部栈路径。失败动作保留完整原回执；成功的中间回执逐条进入 `host.audit`，无需每回合全部塞回 LLM 上下文。标准观察者回调同样接收内部动作。

源码与额度按国家保存，最近结果随模块状态续档；Session 保存服务身份、审计和控制权代次。采用严格发行版依赖检查，1.0 之前不提供旧档适配。当前测试只用合成世界、模拟模型、临时文件，没有付费联网或真实存档访问。

```bash
.venv/bin/python -m unittest tests.test_scripting tests.test_script_service -v
```

失败只消耗本回合的脚本执行额度，不消耗普通国家行动机会；君主仍可通过其他工具完成本回合。模型工具说明提供 api 用法和保存、免费检查、手动执行流程，只有装配脚本能力的发行版才显示该提示。
