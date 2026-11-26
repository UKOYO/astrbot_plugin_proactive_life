# -*- coding: utf-8 -*-
"""
裸文本思考拦截 · 第二层
thinking_filter 只管 <thinking> 类标签，管不住模型直接吐的裸思考链
本模块只认「开头成片的思考段」，碰到正文立刻停手，防误伤

改善点（相对第一版）：
  1. 思考段连续性判定：起手词开头后，连续吃「分析腔」行，遇结论腔/短句即止
  2. 不再整行连坐：行内起手词只削到标点，后半句正文保住
  3. 代码围栏保留：``` 内容原文不动，只剥围栏外的思考
  4. 英文思考词限定在段首，不在正文中间乱砍
  5. 空值保护：削空了返回 None，由调用方吞掉
"""

from __future__ import annotations

import re

# ── 成对标签：先整体摘除 ──
BLOCK_TAGS = [
    r"<thinking>.*?</thinking>",
    r"<reasoning>.*?</reasoning>",
    r"<analysis>.*?</analysis>",
    r"<thought>.*?</thought>",
    r"<reflection>.*?</reflection>",
]

# ── 段首起手词：命中即进入「思考段」状态 ──
HEAD_PATTERNS = [
    r"^\s*(让我想|让我分析|让我先|让我来|先想想|先想清楚|先分析|先理|梳理一下|理一下)",
    r"^\s*(我需要先|我应该先|我要先|我必须先)",
    r"^\s*(作为一个|身为一个)\s*(AI|人工智能|助手|模型|程序|语言模型)",
    r"^\s*(用户|对方|他|她|提问者)?\s*(想要|问的是|的意图是|的需求是|在问)",
    r"^\s*(Let me|I need to|I should|I'll|Let's|First,|Okay, so|Hmm,)",
]

# ── 分析腔续行：思考段里继续吃的行 ──
ANALYSIS_PATTERNS = [
    r"^\s*[-*•]?\s*\d+[\.、)]",          # 1. 2. 分条
    r"^\s*[-*•]\s",                        # - 列表
    r"^\s*(首先|其次|然后|接着|再者|另外|不过|但是|然而|所以|因此|综上|总的来看)",
    r"^\s*(需要|应该|要|得|必须|可以|可能|或许|大概)\s*(考虑|注意|满足|回答|回应|确认|分析)",
    r"^\s*(这|那)?\s*(意味着|说明|表示|看来|显然)",
    r"^\s*(第[一二三四五六七八九十]+[、,，点])",
]

# ── 结论腔 / 口语：碰到立刻退出思考段（说明正文来了）──
CONTENT_STOP = [
    r"^\s*(好|好嘞|好呀|嗯|诶|啊|哈|那么|总之|结论|答案|推荐|给你|告诉你|要不|不如|直接说|简单说)",
    r"^\s*(先给|先上|上结论|说结论|一句话)",
    r"^\s*(Oha|Okie|Shisshi|唔—)",
    r"^[^。！？\n]{0,12}[。！？]\s*$",       # 短句成行，像正常应答
]

# ── 行内自述句：只削到标点，不连坐整行 ──
INLINE_SELF = [
    r"^[^。！？\n]{0,24}?(我需要回答|需要回答中文|要回应用户|要满足用户|回答要满足)[^。！？\n]{0,40}?[。！？]",
]

CODE_FENCE = re.compile(r"```.*?```", re.S)


def _is_head(line: str) -> bool:
    return any(re.match(p, line, flags=re.I) for p in HEAD_PATTERNS)


def _is_analysis(line: str) -> bool:
    return any(re.match(p, line, flags=re.I) for p in ANALYSIS_PATTERNS)


def _is_stop(line: str) -> bool:
    return any(re.match(p, line, flags=re.I) for p in CONTENT_STOP)


def strip_bare_thinking(text: str, max_scan: int = 30) -> str:
    """剥离开头成片的裸思考段；代码围栏内原文不动。"""
    if not text or not text.strip():
        return text

    # 1. 成对标签先摘（围栏外的才摘）
    for p in BLOCK_TAGS:
        text = re.sub(p, "", text, flags=re.S | re.I)

    # 2. 切行，护住代码围栏
    lines = text.split("\n")
    protected: list[bool] = []
    in_fence = False
    for ln in lines:
        protected.append(in_fence)
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence

    # 3. 从第一个非空行开始判定是否进入思考段
    out: list[str] = []
    i = 0
    n = len(lines)
    entered = False          # 是否已进入思考段
    stopped = False          # 思考段是否已结束

    while i < n:
        ln = lines[i]
        if protected[i] or i >= max_scan + 10:
            out.append(ln)
            i += 1
            continue

        if not stopped and not entered:
            # 还没进思考段：找起手词
            if ln.strip() and _is_head(ln):
                entered = True
                i += 1
                continue
            out.append(ln)
            i += 1
            continue

        if entered and not stopped:
            stripped = ln.strip()
            if not stripped:
                # 空行：思考段里跳过，正文里保留
                i += 1
                continue
            if _is_stop(ln):
                entered = False
                stopped = True
                out.append(ln)
                i += 1
                continue
            if _is_analysis(ln):
                i += 1
                continue
            # 起手词行紧跟的短句，也算思考段尾巴（仅纯虚词口水，严防误吃正文）
            if (
                0 < len(stripped) <= 12
                and not re.search(r"[。！？!?]$", stripped)
                and not stripped.startswith("```")
                and not _is_stop(ln)
                and not re.search(r"[\u4e00-\u9fff]{2,}[的吧了呢啊哦呀嘛]", stripped)
                and not re.search(r"[A-Za-z]{3,}", stripped)
            ):
                i += 1
                continue
            # 真正文：退出思考段
            entered = False
            stopped = True
            out.append(ln)
            i += 1
            continue

        out.append(ln)
        i += 1

    result = "\n".join(out)

    # 4. 行内自述句，削到标点
    for p in INLINE_SELF:
        result = re.sub(p, "", result)

    # 5. 收尾整形
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


def guard(raw: str) -> str | None:
    """返回 None 表示整条被削空，应吞掉不发。"""
    if not raw or not raw.strip():
        return None
    cleaned = strip_bare_thinking(raw)
    if not cleaned.strip():
        return None
    return cleaned



