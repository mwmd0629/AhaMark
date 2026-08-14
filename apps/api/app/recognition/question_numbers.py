"""Shared, conservative normalization for question-number anchors."""

from __future__ import annotations

import re

_QUESTION_NUMBER = re.compile(
    r"^\s*(?:(?:\u7b2c|\u9898|[Qq])\s*)?[\uff08(]?\s*(?P<parent>[0-9]{1,3})"
    r"(?:(?:\.(?P<decimal>[0-9]{1,3}))|"
    r"(?:\s*[\uff08(]\s*(?P<sub>[0-9]{1,3}|[A-Za-z])\s*[\uff09)])|"
    r"(?P<letter>[A-Za-z]))?\s*[\uff09)]?(?:\s*\u9898)?"
    r"(?=\s|[.\u3001\uff0e:\uff1a)]|$)"
)

_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_CIRCLED_DIGITS = {character: index for index, character in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳", 1)}
_CHINESE_NUMBER = re.compile(
    r"^\s*(?:第\s*)?(?P<number>[一二三四五六七八九十]{1,3})(?:\s*题)?(?=\s|[、.．:：)]|$)"
)
_PAREN_CHINESE_NUMBER = re.compile(r"^\s*[（(]\s*(?P<number>[一二三四五六七八九十]{1,3})\s*[）)]")
_CIRCLED_NUMBER = re.compile(r"^\s*(?P<number>[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])")


def _chinese_integer(value: str) -> int | None:
    if value in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value]
    if "十" not in value or len(value) > 3:
        return None
    left, right = value.split("十", 1)
    tens = _CHINESE_DIGITS.get(left, 1) if left else 1
    units = _CHINESE_DIGITS.get(right, 0) if right else 0
    result = tens * 10 + units
    return result if 1 <= result <= 99 else None


def normalize_question_number(text: str) -> str | None:
    """Return one stable display key only when *text* starts with an anchor.

    Numeric parenthesized subquestions use ``2(3)``. Historical decimal and
    alphabetic forms remain ``2.1`` and ``2a`` for backwards compatibility.
    Four-digit years and in-sentence references do not match.
    """

    match = _QUESTION_NUMBER.match(text)
    if match is None:
        circled = _CIRCLED_NUMBER.match(text)
        if circled is not None:
            return str(_CIRCLED_DIGITS[circled.group("number")])
        chinese = _PAREN_CHINESE_NUMBER.match(text) or _CHINESE_NUMBER.match(text)
        if chinese is None:
            return None
        value = _chinese_integer(chinese.group("number"))
        return str(value) if value is not None else None
    parent = str(int(match.group("parent")))
    decimal = match.group("decimal")
    if decimal is not None:
        return f"{parent}.{int(decimal)}"
    sub = match.group("sub")
    if sub is not None:
        return f"{parent}({int(sub)})" if sub.isdigit() else f"{parent}{sub.lower()}"
    letter = match.group("letter")
    return f"{parent}{letter.lower()}" if letter is not None else parent
