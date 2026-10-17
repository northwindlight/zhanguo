"""Distribution-level tool layer: one table for every player-facing toolcall.

Modules expose a player API through a capability; this layer owns names, schemas,
argument adapters, ownership/groups, and the prose rendered for each call.
It never contains rule logic or domain state.
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.runtime import ModError

from . import army, bank, buildings, diplomacy, economy, government, handbooks, intelligence, nations, query, territory, vision


def _merge(target, source, label):
    for key, value in source.items():
        if key in target:
            raise ModError(f'tool 层 {label} 重复：{key}')
        target[key] = value


def _catalogs():
    directory = Path(__file__).with_name('locales')
    result = {}
    for language in ('zh-CN', 'en-US'):
        path = directory / f'{language}.json'
        if path.exists():
            result[language] = json.loads(path.read_text(encoding='utf-8'))
    return result


class ToolLayer:
    def __init__(self, runtime, tools, receipts, captions, panels, catalogs):
        self.runtime = runtime
        self._tools = tuple(tools)
        self._receipts = receipts
        self._captions = captions
        self._panels = panels
        self._catalogs = catalogs

    def tools(self):
        return self._tools

    def catalogs(self):
        return self._catalogs

    def extend(self, *, tools=(), receipts=None, captions=None, panels=None):
        merged_receipts = dict(self._receipts)
        merged_captions = dict(self._captions)
        merged_panels = dict(self._panels)
        _merge(merged_receipts, receipts or {}, '回执')
        _merge(merged_captions, captions or {}, '抬头')
        _merge(merged_panels, panels or {}, '面板')
        return ToolLayer(self.runtime, (*self._tools, *tools),
                         merged_receipts, merged_captions, merged_panels, self._catalogs)

    def _canonical(self, action):
        tool = self.runtime.tools.get(action) if isinstance(action, str) else None
        return tool.name if tool is not None else action

    def caption(self, action, args, actor, receipt, ctx):
        render = self._captions.get(self._canonical(action))
        if render is None:
            return None
        try:
            return render(args or {}, actor, receipt or {}, ctx)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            return None

    def receipt(self, action, args, receipt, actor, ctx):
        render = self._receipts.get(self._canonical(action))
        if render is None:
            return None
        try:
            return render(args or {}, receipt or {}, actor, ctx)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            return None

    def panel(self, panel, data, args, actor, ctx):
        render = self._panels.get(panel)
        if render is None:
            return None
        try:
            return render(data, args or {}, actor, ctx)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            return None


def build(runtime):
    tools, receipts, captions, panels = [], {}, {}, {}
    for module in (nations, government, army, territory, vision, buildings, economy, bank, diplomacy, intelligence, handbooks):
        for spec in module.tools(runtime):
            if spec.owner in runtime.capabilities:
                tools.append(spec)
        _merge(receipts, module.RECEIPTS, '回执')
        _merge(captions, module.CAPTIONS, '抬头')
        _merge(panels, module.PANELS, '面板')
    if 'native:query' in runtime.capabilities:
        tools.append(query.build(runtime, tools))
    return ToolLayer(runtime, tools, receipts, captions, panels, _catalogs())
