"""Per-client keyed catalogs. No process-global locale or state translation."""
import json
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).with_name('locales')

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
            fields=lambda s:{name for _,name,_,_ in Formatter().parse(s) if name is not None}
            if key in base and fields(value)!=fields(base[key]):raise ValueError(f'UI placeholders differ: {key}')
        self.messages = {**base, **selected}

    def __call__(self, key, **values):
        return self.messages[key].format(**values)

    def resolve(self,key,fallback,**values):
        message=self.messages.get(key,fallback)
        return message.format(**values) if values else message

    @classmethod
    def from_config(cls,cfg):
        catalog=cfg.get('ui_catalog')
        if isinstance(catalog,str):catalog=json.loads(Path(catalog).read_text(encoding='utf-8'))
        return cls(cfg.get('language','zh-CN'),catalog)
