# 发行版 Session：场景与 API 提供入口

host 加载 `Distribution.Session.Entry`，把运行时、外部配置和客户端工厂传给发行版。发行版决定场景和 API，通用 Session 提供运行机制。host 不引用 native 的资源表、首都、地形或领域对象。

## 示例先看这里

- [native Session](../mods/native/session.py)：落位、资源和首都、作用域、定时与手动登场、管理接口。
- [独立 mini Session](../mods/examples/minigame/session.py)：自有 `mini:` 内容与客户端类型，九个领域模块，无 native 领域依赖或占位市场。
- [experimental 议会服务](../mods/experimental/council.py)：统一处理国策提交、记录证据、裁决与交接，客户端无需知道审核流程。
- [独立计数游戏验收](../tests/test_session_api.py)：不使用国家、建筑、地图或资源域，也能通过同一 host 开局、行动、结算和存取。

```toml
[Distribution.Session]
Entry = "mods.examples.minigame.session:MiniSession"
Observer = { Capability = "mini:intelligence", Records = "to_save", Announce = "announce", Send = "send" }

[Distribution.ClientFactories.mini]
Entry = "mods.examples.minigame.client:MinigameClient"
```

```python
from host import GameHost
host = GameHost.new({'mods':'mods.minigame.toml', 'map_size':12, 'seed':3,
                     'nations':[{'id':'mini:alpha','name':'甲','client':'mini'}]})
player = host.player('mini:alpha')
print(player.observe())
host.cycle()
# 仅写临时或专门指定的测试存档。
```

## Session 工厂与通用基类

入口为 `包.模块:工厂名`，签名 `factory(runtime, cfg, client_factory)`。返回对象必须提供 `start / restore / to_save / save / player / cycle / actors / clock`。缺少入口或方法时启动失败。终端管理能力另行检查，无地图或无终端的游戏可以使用同一个 host。

发行版通常继承 [engine.session.Session](../engine/session.py)，实现以下方法：

| 方法 | 责任与返回值 |
| --- | --- |
| `entries()` | 返回已规范化条目，公共装配使用 `id / name / client` |
| `configure()` | 初始化该游戏的场景参数 |
| `_positions(count)` | 返回 count 个初始位置，值的结构由 `spawn` 定义 |
| `spawn(item, position)` | 创建玩家的领域状态，返回 actor ID；不创建客户端 |
| `actors()` | 当前参与轮转的身份序列；默认读取声明的 `ActorCollection` |
| `identities()` | 全部历史身份，包括已经退出的身份，用于存档校验 |
| `clock()` | 当前整数周期；默认调用声明的 `PlayerClock` |
| `scheduled()` | 处理该游戏的定时登场；无待登场身份时基类无需做任何事 |
| `validate_boundary()` | 检查领域是否处于完整可存取边界，失败抛 `SaveFormatError` |
| `after_spawn()` | 可选；玩家注册后更新发行版的衍生场景状态 |

基类 `start()` 按 `appearance=initial / scheduled / manual` 和 `enabled` 选择初始玩家；`register(item, position)` 装配服务和客户端。作用域的默认登场方式来自已加载的 Policy 声明。发行版可覆盖生命周期方法来提供不同场景协议。

`join(item, position)` 在候选世界中注册，失败恢复世界、roster、客户端和服务装配、代次、上下文与审计；已有玩家句柄在调用时取得当前世界。发行版的可选 `add(name)` 决定配置查找与位置选择，再调用 join。完整边界上的行政登场仍可存档，不能把半回合变成完整边界。

### 可选方格场景策略

[engine.scenario.GridSession](../engine/scenario.py) 继承通用 Session，提供 native 和 mini 共用的方格场景策略：圆形初始落位、手动登场时的空间搜索，以及按种子确定的定时登场。发行版只需实现 `grid()` 返回 `(size, seed)`，`occupied()` 返回已占用的 `(x, y)` 坐标，并提供自己的 `spawn()`。显式位置交给 `spawn()` 校验；自动落位采用最小切比雪夫距离 3。策略方法均可覆盖。

无地图、六边形地图或采用其他登场规则的发行版直接继承 Session，提供自身场景方法，无需伪造方格地图。通用 Session 不调用 `grid()` 或 `occupied()`。

`cycle()` 调用发行版的 scheduled，轮转客户端，调用世界的声明结算，通知服务与客户端的 `on_settlement(player)`，最后开放完整边界存档。控制权变化后取得新玩家句柄继续该周期；同一国家超过八次控制权变化会停止本局，防止无界轮转。

## 管理 API

`GameHost` 将调用转给发行版的 Session。当前 CLI 使用以下管理契约：

| 方法 | 返回或行为 |
| --- | --- |
| `add(name)` | 配置声明的身份登场，返回 actor ID |
| `actors()` | 当前活动身份 |
| `clock()` | 当前周期 |
| `records()` | 观察者事件 `{events, messages}`，事件含 `id / period / kind / text` |
| `announce(text)` | 发送公开公告 |
| `send(actor, text)` | 发送指定玩家可见来信 |
| `board()` | 返回由 UI Mod 编排的终端面板 |
| `board_rows()` | 基类 board 使用的公开汇总行：`name / land / army / gold` |

这组 CLI 契约由发行版实现；其他产品入口可调用发行版自己的管理 API。`management()` 列出当前已实现的可选管理接口，继承的占位方法不计入。CLI 在建立观察者之前检查 `add / records / announce / send / board`；缺失时明确拒绝该终端入口，独立 host 的开局、行动与存取不受影响。基类 `board()` 仅在发行版提供 `board_rows()` 时可用。

`Session.Observer` 可将 `Records / Announce / Send` 映射到声明能力的公开方法。装配时检查能力、对象、可调用性与参数绑定：Records 无参数、Announce 接收正文、Send 接收身份和正文，均按位置调用；额外参数必须有默认值。方法名或签名不匹配直接报 `ModError`，不执行管理方法来探测签名。可只声明需要的管理能力，也可以覆盖对应 Session 方法。

读取 Records 时校验 JSON 返回结构：顶层包含 `events / messages` 两个列表；事件含 `id / period / kind / text`，来信含 `id / sampled / source / target / payload`。ID 为正整数，period / sampled 为非负整数，kind / text / source / target 为字符串，payload 为 JSON 数据。允许额外字段，返回值与领域状态脱离；结构不符时抛出标明字段位置的 `ModError`。

`world` 留给发行版和剧本的可信行政代码，客户端使用 `player(actor)`。

## 玩家 API 与服务

`player(actor)` 返回 [BoundPlayer](../engine/api.py)，公共方法见 [玩家契约](客户端接线.md#玩家契约)。工具和事实继续来自模块声明，服务不会给模型添加内部对象接口。

```toml
[Distribution.Session.Services.council]
Entry = "mods.experimental.council:CouncilService"
[Distribution.Session.Services.council.Services.government]
Capability = "native:government"
Methods = ["enroll", "decide", "replace"]
```

每个 actor 独立创建 `factory(cfg, *, record=None, services)`。`cfg` 是该玩家的外部配置，record 是该服务自己保存的 JSON 状态；接入凭据留在外部配置。服务顺序按发行版声明的顺序，不按动作名称或发行版名称分支。

| 服务成员 | 调用约定 |
| --- | --- |
| `submit(player, action, arguments, next_submit)` | 可选；action 为工具的规范名称，arguments 为副本；调用 next_submit 取得下游回执，或直接返回拒绝回执 |
| `record(player, row)` | 可选；最终行动审计写入后通知，row 为独立副本 |
| `record_dialogue(player, messages)` | 可选；客户端主动提供的完整上下文或对话片段 |
| `on_settlement(player)` | 可选；结算后取得最新事实 |
| `to_save()` | 必需；返回服务自有的 JSON 状态 |

`next_submit()` 不接收替代身份或参数，对同次请求只允许调用一次；末端仍执行引擎的权限、参数和领域校验。服务接收规范工具名，原始别名保留在回执和审计中。错误和拒绝均记录，不用客户端回调补写。

`services['ui']` 为当前 UI；声明的能力别名是 `services[alias](method, *args, **kwargs)`。它只允许 Methods 中列出的公开方法，自动把绑定 actor 作为第一个领域参数，不能指定其他 actor。对象或方法缺失时装配失败。服务还获得 `services['invalidate'](context)`，使该 actor 的旧句柄失效，并保存独立 JSON 交接上下文。

客户端工厂可声明同样形式的能力授权；experimental 已移除客户端的议会权力。发行版服务具备显式授予的权限，应作为可信代码实现。这是进程内接口，Python 对象隔离和不可信脚本执行另属沙盒任务。

## 轻量剧本 Mod 与唯一胜利钩子

剧本是无自有世界状态的轻量钩子 Mod，继承 `engine.scenario_mod.ScenarioMod`，通过声明过的领域接口安装特效。发行版的 Members 装载其 mod.toml，再显式挂载可选剧本：

```toml
[Distribution.Scenarios.eight_nations]
Mod = "scenario:eight-nations"
```

外部配置用 `"scenario": "eight_nations"` 选择；未选择时不执行剧本钩子。未挂载的名称或非 ScenarioMod 拒绝启动。native 和 experimental 挂载 [同一个八国剧本 Mod](../mods/scenarios/eight_nations/mod.toml)。入口不能由玩家配置或存档传入任意 Python 路径。旧的发行版 `Entry` 程序仍可用于其他游戏，但同一声明不能同时使用 Mod 和 Entry。

```python
from engine.scenario_mod import ScenarioMod

class MyScenarioMod(ScenarioMod):
    text = '本剧本公开目标，君主和议长持续可见。'

    def on_start(self, world):
        # 经声明的领域接口配置本剧本特效；不要在共享 Mod 实例保存 world。
        pass

    def on_new_game(self, world):
        # 全部初始国家登场后，只在新局执行一次；可建立初始条约。
        pass

    def on_settlement(self, world, trigger_victory):
        if self.my_condition(world):
            trigger_victory({'type': 'my_game.special', 'message': '自定义胜利'})
```

`on_start(world)` 绑定每个新会话的世界，在新局、恢复和行政登场替换世界时重新绑定；这不是每回合重跑的开局赠送钩子。配置效果应幂等，不能在此无条件扣费或发物资。`on_new_game(world)` 在全部初始国家登场后执行一次，适合建立初始条约或赠送资源；续档、重新绑定及后续登场均不执行，不能借此恢复已经撤销的条约。`on_settlement(world, trigger_victory)` 在每个完整回合的领域结算之后、客户端结算通知与保存之前运行。三个生命周期方法都在所属 Mod 的接口权限上下文执行；需要的能力和方法必须在 TOML 声明。Mod 不保存会话实例引用，因此共享 Runtime 的多局互不串状态；真正需要持久化的领域效果归对应领域存档。

唯一胜利入口 `trigger_victory(event)` 接收非空、有限数值的 JSON 对象，条件、奖项和文本完全归剧本。引擎只保留首次事件，复制数据并停止后续回合和玩家写动作，保留只读查询。存档保存剧本选择和事件；恢复不更换剧本、不从存档导入代码，终局恢复不额外运行回合。

八国剧本负责周的专属特效：周任盟主时，其他盟友自动成为10%生产上贡、保留正常盟友权力的周天下附庸；向周主动称臣在回合末触发自动建盟，首次成功创建扣周100金、周任盟主，后续不再收建盟费。周不足100金则失败，不透支；已有联盟的称臣申请保留成员表决。同回合向独立周称臣的国家作为共同创始者结算，只扣一次建盟费。原宗主加入周天下时，原附庸一并移交周，均成为周的直接10%特殊附庸；原宗主不保留双层附属关系。通用外交只实现可配置的特殊宗主联盟，不识别周的身份、10%贡率或胜利规则。朝贡按每国每期各类资源的全国实际总产出乘贡率，小数按附庸、宗主及资源跨期累计，累计额向下取整扣取，余数继续结转。

周及其特殊附庸覆盖全部存续国家、总数至少5国（含周）时，当回合完整结算即判仅周获胜，其他成员臣属保全（未胜未败，不列入主胜或次胜），不增加上贡等待期；普通附庸不计入。周不能成为附庸、周在位时盟主权不可移交；周灭国后正常继任盟主并保留联盟，但贡赋与特殊附庸移交机制消失。只剩唯一国家则为兼并终极胜利。公开事件、终局档和结算报告保留实际获胜身份，消费排名独立展示。

## 控制权、审计与存取

`action_state()` 返回 `{generation, current_generation, active, can_act, context}`。旧句柄不会自动升级；控制权变更后其提交返回 `unavailable_action`，不会执行后续工具。查询仍可读取当前公开事实，observe 的 can_act 为 false；新句柄由 Session 重新绑定。

LLM 和规则 AI 按新代次读取交接上下文，`context.reset_memory=true` 时清空自身决策记忆。议会归档仍归服务所有。LLM 同一工具批次的剩余调用补齐失败回执，再结束旧控制者的行动；真实 provider replay 保持配对。

`audit` 每项包含 `id / actor / generation / action / arguments / receipt`；程序异常另存 `error.type / error.message`。每一次绑定 API 的 submit 都写一项，脚本内部多次提交也各自记录。记录最终回执后触发 `cycle(on_result=...)` 观察者回调，客户端不重复发出这份回调。

`zhanguo.session/4` 保存模块依赖与状态、客户端状态、服务状态、roster、场景文案、代次、交接上下文和审计。只保存完整周期边界；失败周期不能覆盖最近完整存档。服务清单、身份或依赖不匹配会拒绝加载。

1.0 前不提供旧档承诺、兼容适配或迁移。当前 UI 对事实布局和地图图例的完整抽象、领域语言键体系仍是后续任务，见 [PLAN](plans/PLAN.md)。

配置准备独立于 Session 生命周期，通过 `Distribution.Configuration.Entry` 组织组件配置；见 [配置层契约](配置层.md)。Session 接收配置副本，不再负责 native / mini 的场景规范化。

## 历史归档与当前格式

当前整局格式为 `zhanguo.session/4`。`save(path)` 先校验完整边界，再写 `<path>.audit/<stream>/` 的不可变审计与领域历史分段，最后原子写 Session 文件；成功后才清空待归档领域缓冲。主档保留最近 2048 条审计和下个编号；经济累计检查点保留全部消费，过期逐期明细离线归档。已发布报表、市场成交仍保留，存档总体不承诺常量大小。备份主档时一并复制归档目录，续局本身只需要主档与匹配的发行版。

native 的自动登场距离在尺寸至少 32 时为 8，小图为 3。`finished()` 在收到剧本胜利事件时成立；未触发事件时，仍保留曾有多个国家、现只剩一个且没有待定时登场国家的终局判断。

领域模块可选实现 `pending_archive(world)`、`archive_committed(world)`、`restore_archive(world, rows)`。记录须为 JSON 数据并带非负整数 period；Session 对内容哈希命名的分段落盘，主档成功写入后才确认清理缓冲。join 的候选世界复制待归档缓冲，避免中途登场吞历史。`to_save()` 仅生成可续局状态；需要历史分段时应调用 `save(path)`。


八国尊王事件的 `winners` 仅含周，`preserved` 单独列出其他存续周天下成员，`secondary_winners` 为空。臣属保全是接受周的天下秩序和朝贡义务后保全国号与国祚的结局：未胜未败，不授予主胜、次胜或共同胜利；提前入盟不保证最终保全，已亡国者不列入。消费排名继续独立统计，结算表及模型复盘同时展示实际结局。上述字段是八国剧本自己的事件约定，通用引擎仍只接收任意 `trigger_victory(event)`，不识别周或保全等级。
