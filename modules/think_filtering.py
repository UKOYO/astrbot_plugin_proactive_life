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


# 代码围栏 / 行内代码：里面的 `<think>` 之类是**用户要看的内容**（比如让人解释
# 这个标签、或者示例里写了 `<thinking>` 却没闭合），不能当思考块删掉。
# 覆盖三种写法：``` 围栏、~~~ 围栏、`行内代码`（含 ``双反引号``）。
CODE_SPAN_RE = re.compile(
    r"```[\s\S]*?```"
    r"|~~~[\s\S]*?~~~"
    r"|`{1,2}[^`\n]+`{1,2}"
)

# 掩码占位符：用 NUL 包住，正文里几乎不可能出现
MASK_TPL = "\x00PLCODE{idx}\x00"


def mask_code_spans(text: str) -> tuple[str, list[str]]:
    """把代码围栏/行内代码换成占位符，返回 (掩码文本, 原文列表)。

    先用 :func:`unmask_code_spans` 还原；掩码后跑任何正则都不会动到代码内容。
    """
    spans: list[str] = []

    def _repl(m: "re.Match[str]") -> str:
        spans.append(m.group(0))
        return MASK_TPL.format(idx=len(spans) - 1)

    return CODE_SPAN_RE.sub(_repl, text), spans


def unmask_code_spans(text: str, spans: list[str]) -> str:
    """把 :func:`mask_code_spans` 的占位符换回原文。"""
    for idx, span in enumerate(spans):
        text = text.replace(MASK_TPL.format(idx=idx), span)
    return text


def _code_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in CODE_SPAN_RE.finditer(text)]


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def filter_reasoning_text(
    text: str,
    tags: object,
    *,
    orphan_closing_action: str = "remove_prefix",
    unclosed_opening_action: str = "remove_suffix",
    case_sensitive: bool = False,
) -> tuple[str, int]:
    """删除思考标签及其中内容，返回清理后的文本和命中区域数。

    代码围栏内的标签**不处理**（围栏内的内容要原样发给用户）。
    """
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
    fences = _code_spans(text)
    tokens = [
        m for m in token_pattern.finditer(text) if not _in_spans(m.start(), fences)
    ]
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
