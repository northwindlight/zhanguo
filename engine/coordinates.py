"""Shared syntax for numeric grid references; coordinates keep their native base."""
import re


def coordinates(value):
    if isinstance(value, (tuple, list)):
        if len(value) == 2 and all(type(v) is int for v in value):
            return tuple(value)
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if (text.startswith('(') and text.endswith(')')) or (text.startswith('[') and text.endswith(']')):
        text = text[1:-1]
    match = re.fullmatch(r'\s*(-?\d+)\s*[,\s]\s*(-?\d+)\s*', text)
    return (int(match[1]), int(match[2])) if match else None
