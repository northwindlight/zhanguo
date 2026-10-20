"""Vision toolcall: the visible map fact (rendered by the UI map layer)."""
from engine.runtime import Tool


def tools(runtime):
    def view(world, actor):
        return runtime.player_api(world, 'native:vision').tiles(actor)

    return (
        Tool('view', view, owner='native:vision', api=('tiles',), read_only=True, fact=True, panel=True,
             description='本国当前完整可见地图，以坐标数组表示；他国仅公开建筑和归属，不泄露迷雾或资源。单格细节使用 tile，图形与文字地图由 UI 呈现。'),
    )


PANELS = {}
RECEIPTS = {}
CAPTIONS = {'view': lambda args, actor, receipt, ctx: ctx.text('tool.caption.view')}
