"""Handbook toolcall: the strategy guide catalog."""
from engine.runtime import Tool


def tools(runtime):
    def handbooks(world, actor):
        return runtime.player_api(world, 'native:handbooks').books(actor)

    return (
        Tool('handbooks', handbooks, owner='native:handbooks', api=('books',), read_only=True, fact=True, panel=True,
             description='策略指南目录。正文用 player_rules(topic=标题) 查询；「基础指南」在前 20 回合常驻提示里，其余按需查询。'),
    )


def _handbooks(data, args, actor, ctx):
    return ctx.text('tool.handbooks.head') + '\n' + '\n'.join(
        ctx.text('tool.handbooks.row', title=row['title'], topic=row['title']) for row in data)


PANELS = {'handbooks': _handbooks}
RECEIPTS = {}
CAPTIONS = {'handbooks': lambda args, actor, receipt, ctx: ctx.text('tool.caption.handbooks')}
