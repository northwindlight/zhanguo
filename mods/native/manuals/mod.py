"""Hand-written manual documents, on demand and outside the rule assembly."""
from pathlib import Path

from engine.runtime import GameMod

DOCS = (
    ('basics', '基础指南.md'),
    ('economics', '经济学手册.md'),
    ('war', '战争手册.md'),
    ('strategy', '战略手册.md'),
    ('manual', '游戏说明书.md'),
)
DOC_DIR = Path(__file__).resolve().parents[1] / 'docs'


def _title(text, fallback):
    for line in text.splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return fallback


class Manuals:
    def __init__(self, directory=DOC_DIR):
        self._dir = Path(directory)
        self._docs = tuple((key, filename) for key, filename in DOCS)
        self._cache = {}

    def _load(self, key):
        if key in self._cache:
            return self._cache[key]
        filename = dict(self._docs).get(key)
        if filename is None:
            raise ValueError('未知手册：' + str(key))
        path = self._dir / filename
        if not path.is_file():
            raise ValueError('手册文件缺失：' + filename)
        text = path.read_text(encoding='utf-8')
        entry = {'id': key, 'title': _title(text, key), 'body': text}
        self._cache[key] = entry
        return entry

    def catalog(self):
        return tuple({'id': key, 'title': self._load(key)['title']} for key, _ in self._docs)

    def read(self, doc):
        if not isinstance(doc, str) or not doc:
            raise ValueError('需要手册 id')
        return dict(self._load(doc))


class ManualsMod(GameMod):
    def __init__(self):
        self.manuals = Manuals()

    def mixins(self):return ()

    def objects(self, world):
        return {'native:manuals': self.manuals}

    def player_api(self, world):
        from .api import ManualsAPI
        return ManualsAPI(self.manuals)

    def rule_sections(self, world=None):
        return ()
