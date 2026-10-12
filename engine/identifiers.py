"""Content identity is independent of display language."""
import re

_CONTENT_ID = re.compile(r'[a-z][a-z0-9_.-]*:[a-z][a-z0-9_./-]*', re.ASCII)


def content_id(value: str) -> str:
    if not isinstance(value, str) or not _CONTENT_ID.fullmatch(value):
        raise ValueError(f'Invalid namespaced content ID: {value!r}')
    return value
