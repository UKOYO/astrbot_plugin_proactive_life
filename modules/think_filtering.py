from __future__ import annotations

import re
from collections.abc import Iterable


_VALID_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def normalize_tags(value: object) -> tuple[str, ...]:
    """清理配置中的标签名，并保持原有顺序去重。"""
    values: Iterable[object]
    if isinstance(value, list):
        values = value
    elif value is None:
        values = ()
    else:
        values = (value,)

    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        tag = str(item).strip()
        key = tag.lower()
        if not _VALID_TAG.fullmatch(tag) or key in seen:
            continue
        seen.add(key)
        result.append(tag)
    return tuple(result)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def filter_reasoning_text(
    text: str,
    tags: object,
    *,
    orphan_closing_action: str = "remove_prefix",
    unclosed_opening_action: str = "remove_suffix",
    case_sensitive: bool = False,
) -> tuple[str, int]:
    """删除思考标签及其中内容，返回清理后的文本和命中区域数。"""
    normalized_tags = normalize_tags(tags)
    if not text or not normalized_tags:
        return text, 0

    alternatives = "|".join(
        sorted((re.escape(tag) for tag in normalized_tags), key=len, reverse=True)
    )
    flags = 0 if case_sensitive else re.IGNORECASE
    token_pattern = re.compile(
        rf"<\s*(/?)\s*({alternatives})(?=\s|/?>)[^>]*>",
        flags,
    )
    tokens = list(token_pattern.finditer(text))
    if not tokens:
        return text, 0

    stack: list[tuple[str, int, int]] = []
    ranges: list[tuple[int, int]] = []

    for token in tokens:
        is_closing = bool(token.group(1))
        tag = token.group(2) if case_sensitive else token.group(2).lower()
        if not is_closing:
            stack.append((tag, token.start(), token.end()))
            continue

        opening_index = next(
            (index for index in range(len(stack) - 1, -1, -1) if stack[index][0] == tag),
            None,
        )
        if opening_index is None and stack:
            # 部分模型会混用 <think> 与 </thinking>，仍将其视为同一思考区块。
            opening_index = len(stack) - 1
        if opening_index is not None:
            ranges.append((stack[opening_index][1], token.end()))
            del stack[opening_index:]
        elif orphan_closing_action == "remove_prefix":
            ranges.append((0, token.end()))
        elif orphan_closing_action == "remove_tag":
            ranges.append((token.start(), token.end()))

    if unclosed_opening_action == "remove_suffix" and stack:
        ranges.extend((start, len(text)) for _, start, _ in stack)
    elif unclosed_opening_action == "remove_tag":
        ranges.extend((start, end) for _, start, end in stack)

    merged = _merge_ranges(ranges)
    if not merged:
        return text, 0

    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts), len(merged)


def normalize_output(text: str, *, trim: bool, collapse_blank_lines: bool) -> str:
    if collapse_blank_lines:
        text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    return text.strip() if trim else text
