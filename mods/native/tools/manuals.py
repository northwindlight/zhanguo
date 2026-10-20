"""Read-only manual panel over the hand-written documents."""
from engine.runtime import Tool

DOCS = ('basics', 'economics', 'war', 'strategy', 'manual')


def tools(runtime):
    def manual(world, actor, doc: str):
        return runtime.player_api(world, 'native:manuals').read(actor, doc)

    return (
        Tool('manual', manual, owner='native:manuals', api=('read',), read_only=True, panel=True,
             parameters={'type': 'object',
                         'properties': {'doc': {'type': 'string', 'enum': list(DOCS)}},
                         'required': ['doc'], 'additionalProperties': False},
             description='只读手册：doc 选一本文档（基础指南/经济学手册/战争手册/战略手册/游戏说明书），返回全文。文档为手写维护，不在规则目录里。'),
    )


def _manual(data, args, actor, ctx):
    return (data or {}).get('body') if isinstance(data, dict) else None


PANELS = {'manual': _manual}
RECEIPTS = {'manual': lambda args, receipt, actor, ctx: (receipt.get('data') or {}).get('body')}
CAPTIONS = {}
