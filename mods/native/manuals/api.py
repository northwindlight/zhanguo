"""Read-only access to the hand-written manuals under ``mods/native/docs``.

The manuals are plain documents, not rule sections: they are exposed only
through ``query(panel=manual)`` and never joined into ``rule_sections()``.
"""
from pathlib import Path


class ManualsAPI:
    def __init__(self, mod):
        self._mod = mod

    def read(self, actor, doc):
        return self._mod.read(doc)
