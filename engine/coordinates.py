"""Shared syntax for numeric grid references; coordinates keep their native base."""
import re
import unicodedata


def coordinates(value):
    if isinstance(value, (tuple, list)):
        if len(value) == 2 and all(type(v) is int for v in value):
            return tuple(value)
        return None
    if not isinstance(value, str):
        return None
    # NFKC 同时收全角括号/逗号/数字；不要在这里做 1-based 转换。
    text = unicodedata.normalize('NFKC', value.strip())
    if (text.startswith('(') and text.endswith(')')) or (text.startswith('[') and text.endswith(']')):
        text = text[1:-1]
    match = re.fullmatch(r'\s*(-?\d+)\s*[,\s]\s*(-?\d+)\s*', text)
    if match:
        return int(match[1]), int(match[2])
    # 模型常照抄 UI 的「名字(21, 8)」/「名字 21 8」；取最后一组坐标。
    match = re.search(r'[\(\[]\s*(-?\d+)\s*[,\s]\s*(-?\d+)\s*[\)\]]\s*$', text)
    if match:
        return int(match[1]), int(match[2])
    match = re.search(r'(?<!\d)(-?\d+)\s*[,\s]\s*(-?\d+)\s*$', text)
    if match:
        return int(match[1]), int(match[2])
    return None
