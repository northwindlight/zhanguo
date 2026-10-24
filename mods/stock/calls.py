"""按 (账户, 回合) 统计模型工具调用次数。

只服务于一句口头提醒：调用次数快用满时催玩家收尾。
计数放在各 Mod 的 before_tool（每次工具调用恰好一次），
不放在渲染层——同一张回执会被模型和看海控制台各渲染一次，按渲染计数会翻倍。
"""


class CallCounter:
    def __init__(self):
        self.rows = {}

    def note(self, actor, period):
        row = self.rows.setdefault((actor, period), [0])
        row[0] += 1
        return row[0]

    def count(self, actor, period):
        row = self.rows.get((actor, period))
        return row[0] if row else 0

    def clear(self):
        self.rows.clear()


CALLS = CallCounter()
