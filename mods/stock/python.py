"""股市版 Python 沙盒。

沿用原版 ``experimental:python`` 的沙盒本体：同一套受限 Python（无导入、无文件、
无网络、无随机、无动态执行）、同一套回执形状（打印输出、报错类型与行列、
步数与 API 调用次数）。这里只改两处：

1. 时钟换成本回合历 ``stock:clock.period``，每回合一次执行额度不变；
2. 写脚本、改脚本、跑脚本各算一次「窗口额度」消耗；
   ``check`` / ``status`` 是只读检查，不占额度。

脚本一旦跑起来就算「开过盘」，本回合剩下的动作转入回合末统一工作窗口。
"""
from engine.runtime import RuleSection
from mods.experimental.scripting import Programs, ScriptingMod
from mods.experimental.scripting_api import ScriptingAPI


class StockScriptingAPI(ScriptingAPI):
    """脚本 API：跑完顺手通知时钟「开过盘了」。"""

    def claim(self, actor, inputs=None):
        claim = super().claim(actor, inputs)
        self.world.runtime.object_call(self.world, 'stock:clock', 'mark_open', actor)
        return claim


class StockPythonMod(ScriptingMod):
    def initialize_world(self, world):
        world.scripts = Programs(
            self.limits,
            lambda actor: actor in getattr(world, self.runtime.distribution.get('ActorCollection', 'actors')),
            lambda: self.runtime.object_call(world, 'stock:clock', 'period'))

    def player_api(self, world):
        return StockScriptingAPI(world)

    def before_tool(self, world, actor, name, arguments):
        """写脚本与跑脚本都记一次窗口额度；只读检查不记。"""
        if name != 'script':
            return
        action = arguments.get('action') if isinstance(arguments, dict) else None
        if action in ('check', 'status'):
            return
        self.runtime.object_call(world, 'stock:clock', 'charge', actor)

    def rule_sections(self, world=None):
        limits = self.limits
        return (RuleSection(
            'Python 量化沙盒',
            '买卖只能靠脚本下单：脚本里 api.call("stock:market","buy",symbol=...,quantity=...) 买入、'
            'api.call("stock:market","sell",symbol=...,quantity=...) 卖出；'
            'api.call("stock:market","quotes") 取当前行情，'
            'api.call("stock:market","history",symbol=...,limit=...) 取历史收盘价，'
            'api.call("stock:portfolio","holdings") 取自己的持仓与财富，'
            'api.call("stock:portfolio","borrow",amount=...) 借钱。'
            'api.call 被拒绝会终止脚本并把回执原样带回来，脚本自己要用 if 判断。'
            'print 的内容会出现在下回合开局简报里。'
            f'总预算 {limits["steps"]} 计算单位、{limits["calls"]} 次 API 调用；'
            '禁止导入、安装库、文件、网络、反射、随机与动态执行，没有 try/except。',
            ('脚本', '沙盒', 'python', 'sandbox', '量化', '下单'),
            '脚本怎么写、怎么在脚本里下单',
            system=True),)
