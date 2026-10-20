"""最小 Mod：一块状态、一个工具、一个阶段、一段规则。

Mod 是一个继承 `GameMod` 的类。引擎在装载时调用它的若干方法，
每个方法返回一样东西：状态键、工具、阶段、规则文字。
只实现你需要的那几个，其余用基类的空实现。
"""
from engine.runtime import GameMod, RuleSection, Stage, Tool


class HelloMod(GameMod):
    # mixins() 返回要混进 World 类的类，用来给世界**挂属性或方法**。
    # 不需要挂东西就返回空元组 —— 多数模块如此。
    def mixins(self):
        return ()

    # state_keys() 声明本模块**独占写入**的状态键。
    # 一个键只能有一个所有者；重名在装载时即被拒绝。
    def state_keys(self):
        return ('hello',)

    # provides() 声明本模块对外提供的能力名，别的模块靠它找到你。
    def provides(self):
        return ('example:hello',)

    # initialize_world() 在世界构造完成后调用一次，用来放置状态。
    # 状态可以有任意形状；这里用一个 dict：键是国家 ID，值是计数。
    def initialize_world(self, world):
        world.hello = {}

    # tools() 声明玩家动作。处理函数的前两个参数固定是 (world, actor)，
    # 其余参数由类型标注生成 JSON Schema，并在调用时自动转换
    # （字符串 "3" 会转成整数 3）。read_only=True 的工具可被查询、
    # 不受"仅行动阶段可提交"的限制。
    def tools(self):
        return (Tool('hello_add', self._add, ('打招呼',)),
                Tool('hello_total', self._total, read_only=True))

    @staticmethod
    def _add(world, actor, amount: int):
        if amount < 1:
            raise ValueError('amount 必须是正整数')
        world.hello[actor] = world.hello.get(actor, 0) + amount
        return {'actor': actor, 'count': world.hello[actor]}

    @staticmethod
    def _total(world, actor):
        return {'counts': dict(world.hello), 'total': sum(world.hello.values())}

    # stages() 声明本模块在结算里的接入点。发行版用一张挂点表定义骨架顺序；
    # 单模块运行（没有发行版）时可以省略 hook，此时阶段之间只按声明排序。
    def stages(self):
        return (Stage('example:hello.settle', self._settle, title='打招呼结算'),)

    @staticmethod
    def _settle(world, context):
        # context 是本轮结算所有阶段共享的字典，也是阶段之间传递数据的唯一途径：
        # 阶段的返回值会被忽略。
        context['hello_total'] = sum(world.hello.values())

    # rule_sections() 提供给玩家的规则文字。
    # {{SETTLEMENT_STAGES}} 会替换成发行版算出的结算顺序表。
    def rule_sections(self, world=None):
        return (RuleSection('打招呼',
                            '用 hello_add 给自己记数，hello_total 查全场的数。\n\n{{SETTLEMENT_STAGES}}',
                            ('hello', '打招呼')),)

    # 存档分两半：save_state 返回本模块的状态，load_state 把它验回来。
    # 引擎不做任何推断，两个方法写什么就存什么。
    def save_state(self, world):
        return dict(world.hello)

    def load_state(self, world, state):
        if not isinstance(state, dict) or any(
                not isinstance(k, str) or type(v) is not int for k, v in state.items()):
            raise ValueError('invalid hello state')
        world.hello = dict(state)
