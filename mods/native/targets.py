"""Model-facing nation references: accept display names as well as actor IDs."""
import re

from engine.runtime import ToolArgumentError

_LABEL = re.compile(r'^(.*?)[（(]\s*(?:nation/)?(scenario:[^）)]+)\s*[）)]\s*$')


def _rows(world):
    try:
        ids = world.runtime.object_call(world, 'native:nation-inspection', 'ids')
        return tuple(world.runtime.object_call(world, 'native:nation-inspection', 'get', ident)
                     for ident in ids)
    except Exception:
        # Some tool owners (e.g. buildings/quote) do not declare the inspection
        # interface; fall back to the world's public roster without writing.
        nations = getattr(world, 'nations', None)
        try:
            return tuple(nations.snapshot())
        except Exception:
            return ()


def _candidates(rows):
    return [f'{row.name}={row.id}' for row in rows if getattr(row, 'alive', True)]


def nation_reference(world, actor, value):
    """Normalize one nation reference; leave non-nation entity targets untouched.

    Accepted: actor id, nation/<id>, display name, short local id, and
    ``名字（scenario:id）``. Unknown Chinese names get a corrective error; other
    unknown strings are passed through so alliance/entity targets keep working.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text.startswith('bloc/'):
        return value
    if text.startswith('nation/'):
        text = text[len('nation/'):].strip()
    label = _LABEL.match(text)
    if label:
        text = label.group(2).strip()
    rows = _rows(world)
    if not rows:
        return text
    if any(row.id == text for row in rows):
        return text
    local = [row for row in rows if row.id.split(':', 1)[-1] == text]
    if len(local) == 1:
        return local[0].id
    named = [row for row in rows if row.name == text]
    if len(named) == 1:
        return named[0].id
    if len(named) > 1:
        raise ToolArgumentError(
            f'国家名不唯一：{text}；请用国家键：' + '、'.join(row.id for row in named),
            {'reason': 'unknown_nation', 'value': text,
             'candidates': [f'{row.name}={row.id}' for row in named]})
    # 只对“看起来像中文国名”的未知值报错；英文/带命名空间的键可能是联盟或实体 ID。
    if text and all('一' <= char <= '鿿' for char in text):
        raise ToolArgumentError(
            f'未识别的国家：{text}；可用：' + '、'.join(_candidates(rows)),
            {'reason': 'unknown_nation', 'value': text, 'candidates': _candidates(rows)})
    return text


def nation_list(world, actor, value):
    """Normalize a list of nation references (e.g. bloc_found.tos)."""
    if not isinstance(value, (list, tuple)):
        return value
    return [nation_reference(world, actor, item) for item in value]
