"""Owner/scope shell for the tool layer's unified read-only query entry.

The toolcall itself, its panel list and its schema live in
`mods/native/tools/query.py`; this module only provides the `native:query`
capability so the tool has a stable owner.
"""
from engine.runtime import GameMod


class QueryMod(GameMod):
    def mixins(self): return ()
