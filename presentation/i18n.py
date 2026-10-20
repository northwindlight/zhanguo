"""Per-client keyed catalogs. No process-global locale or state translation.

Some messages are shared with the distribution tool layer. Instead of keeping a
second copy of the text, ``aliases.json`` maps a UI key to its tool-layer key;
the alias is resolved only after the tool catalog has been merged.
"""
import json
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).with_name('locales')


def _fields(text):
    return {name for _, name, _, _ in Formatter().parse(text) if name is not None}


def _load_aliases():
    path = ROOT / 'aliases.json'
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()):
        raise ValueError('Invalid UI alias catalog')
    return data


ALIASES = _load_aliases()


def _tool_catalog(language):
    """Alias targets live in the tool layer; load them when no layer is mounted."""
    path = ROOT.parents[1] / 'mods' / 'native' / 'tools' / 'locales' / f'{language}.json'
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


class Translator:
    def __init__(self, language='zh-CN', catalog=None):
        self.language = {'zh':'zh-CN','zh_CN':'zh-CN','en':'en-US','en_US':'en-US'}.get(language, language)
        if self.language not in ('zh-CN','en-US'):
            if catalog is None:raise ValueError(f'Unknown UI language: {self.language}')
            self.language = str(self.language)
        base = json.loads((ROOT/'zh-CN.json').read_text(encoding='utf-8'))
        selected = json.loads((ROOT/f'{self.language}.json').read_text(encoding='utf-8')) if self.language in ('zh-CN','en-US') else {}
        if catalog is not None:
            if not isinstance(catalog,dict):raise ValueError('UI catalog must be an object')
            selected.update(catalog)
        for key,value in selected.items():
            if not isinstance(value,str):raise ValueError(f'Invalid UI message: {key}')
            if key in base and _fields(value)!=_fields(base[key]):raise ValueError(f'UI placeholders differ: {key}')
        self.messages = {**base, **selected}
        self._apply_aliases()

    def _apply_aliases(self):
        missing = {target for alias, target in ALIASES.items()
                   if alias not in self.messages and target not in self.messages}
        if missing:
            fallback = _tool_catalog(self.language)
            for target in missing:
                if target in fallback:
                    self.messages[target] = fallback[target]
        for alias, target in ALIASES.items():
            if alias in self.messages or target not in self.messages:
                continue
            self.messages[alias] = self.messages[target]

    def __call__(self, key, **values):
        return self.messages[key].format(**values)

    def resolve(self,key,fallback,**values):
        message=self.messages.get(key,fallback)
        return message.format(**values) if values else message

    def extend(self, messages):
        """Merge a component-owned catalog after validating placeholders."""
        for key,value in messages.items():
            if not isinstance(value,str):
                raise ValueError(f'Invalid UI message: {key}')
            if key in self.messages and _fields(value)!=_fields(self.messages[key]):
                raise ValueError(f'UI placeholders differ: {key}')
        self.messages.update(messages)
        self._apply_aliases()
        return self

    @classmethod
    def from_config(cls,cfg,tool_layer=None):
        catalog=cfg.get('ui_catalog')
        if isinstance(catalog,str):catalog=json.loads(Path(catalog).read_text(encoding='utf-8'))
        result=cls(cfg.get('language','zh-CN'),catalog)
        if tool_layer is not None:
            extra=tool_layer.catalogs().get(result.language,{})
            if extra:result.extend(extra)
        else:
            result._apply_aliases()
        return result
