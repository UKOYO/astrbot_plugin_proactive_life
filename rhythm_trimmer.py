"""
真人节奏修剪核心逻辑（从 astrbot_plugin_human_rhythm 并入）。

在消息发出前按真人的长度与标点习惯修剪：
  - split（默认）：按句读拆条分发，内容一字不丢
  - punctuation：只删颜文字/去句末标点，不改结构
  - trim：截断（保留兼容，不推荐）

保护机制：
  - 含代码块/表格/列表不拆
  - 含图片/@/表情等非文本组件不拆
  - 花括号 {...} 动作神态整块不切
"""

from __future__ import annotations

import re
from typing import Tuple, List

# 常见颜文字
EMOJI_FACES = [
    "(¬‿¬)", "( •̀ ω •́ )y", "(´,,•ω•,,)", "(╯°□°）╯", "╮(╯▽╰)╭",
    "(´･ω･`)", "(๑•̀ㅂ•́)و", "(｡•́︿•̀｡)", "(๑>؂<๑)", "(=^･ω･^=)",
    "(*/ω＼*)", "(⁄ ⁄•⁄ω⁄•⁄ ⁄)", "(๑´ㅂ`๑)", "(。-`ω´-)", "ヽ(￣ω￣(￣ω￣〃)",
    "(´；ω；`)", "(つд⊂)", "ヾ(≧▽≦*)o", "( • ̀ω•́ )✧", "(๑•́ ₃ •̀๑)",
    "(￣▽￣)", "(￣ε￣)", "(¬_¬)", "(・∀・)", "( ﾟДﾟ)", "(°∀°)",
    "ε(┬┬﹏┬┬)3", "( ˘ω˘ )", "(๑˃̵ᴗ˂̵)", "(๑•̀ㅂ•́)و✧",
]

EMOJI_RE = re.compile(r"[（(][\s\S]{1,14}?[）)]")

# 长文（干活用的）最多分几条，防刷屏。短回复走配置里的「最多几条」，不用这个。
LONG_TEXT_HARD_CAP = 20

# 逗号/空格切分时，短于这个字数的碎片不单独成段
MIN_CHUNK = 6


def has_markdown(text: str) -> bool:
    if "```" in text:
        return True
    if re.search(r"^\s*\|.*\|\s*$", text, re.M):
        return True
    if re.search(r"^\s*[-*+]\s+\S", text, re.M):
        return True
    if re.search(r"^\s*\d+[.、]\s+\S", text, re.M):
        return True
    if re.search(r"^#{1,6}\s+\S", text, re.M):
        return True
    return False


def strip_faces(text: str) -> Tuple[str, int]:
    """删颜文字。先固定串，再保守正则。"""
    n = 0
    out = text
    for face in EMOJI_FACES:
        if face in out:
            n += out.count(face)
            out = out.replace(face, "")

    def _repl(m: re.Match) -> str:
        inner = m.group(0)
        body = inner[1:-1]
        meaningful = [c for c in body if c.strip() and (
            "\u4e00" <= c <= "\u9fff" or c.isalnum())]
        if body.strip() and not meaningful and len(body) <= 16:
            nonlocal n
            n += 1
            return ""
        return inner

    out = EMOJI_RE.sub(_repl, out)
    return out, n


def strip_trailing_punct(text: str) -> Tuple[str, int]:
    """去掉整段末尾的 句号/感叹号/波浪号（问号保留）。"""
    n = 0
    out = text.rstrip()
    while out and out[-1] in "。！!.～~":
        out = out[:-1].rstrip()
        n += 1
    return out, n


BRACE_BLOCK_RE = re.compile(r"\{[^{}]*\}|｛[^｛｝]*｝")


def _is_brace_block(text: str) -> bool:
    """整段就是一个花括号块（动作/神态），这种单独占一个气泡。"""
    s = text.strip()
    return bool(s) and s[0] in "{｛" and s[-1] in "}｝"


def _split_plain(text: str, target: int) -> List[str]:
    """纯文本按句读切；单句超长再按逗号/空格切。这里面不处理花括号。"""
    n = len(text)

    # ---- 句级切分 ----
    sent_ranges: List[Tuple[int, int]] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in "。！？!?…":
            sent_ranges.append((start, i + 1))
            start = i + 1
    if start < n:
        sent_ranges.append((start, n))

    pieces: List[str] = []
    for s, e in sent_ranges:
        seg = text[s:e]
        if not seg.strip():
            continue
        if len(seg.strip()) <= target * 1.6:
            pieces.append(seg.strip())
            continue

        # ---- 逗号级切分 ----
        cuts = []
        for i in range(s, e):
            ch = text[i]
            if ch in "，,；;、":
                cuts.append(i + 1)
            elif ch in " 　":
                # 中文口白常用空格断句；英文单词中间那个空格别切
                left = text[i - 1] if i - 1 >= s else ""
                right = text[i + 1] if i + 1 < e else ""
                ascii_mid = (left.isascii() and left.isalnum()
                             and right.isascii() and right.isalnum())
                if not ascii_mid:
                    cuts.append(i + 1)
        cuts.append(e)
        buf = ""
        cur = s
        for c in cuts:
            chunk = text[cur:c]
            # 太短的碎片（"那个"这种）别单独成段，粘在上一段尾巴上，
            # 宁可这一条略超 target，也别凭空多出一个两字气泡
            if buf.strip() and len(chunk.strip()) < MIN_CHUNK:
                buf += chunk
            elif len(buf) + len(chunk) <= target:
                buf += chunk
            else:
                if buf.strip():
                    pieces.append(buf.lstrip().rstrip("　"))
                buf = chunk
            cur = c
        if buf.strip():
            pieces.append(buf.lstrip().rstrip("　"))
    return pieces


def _split_line(line: str, target: int, protect_brace: bool = True) -> List[str]:
    """单行内部分片段。

    花括号块（动作/神态）先整块摘出来单独成段，前后文各按句读切，
    这样 {她把杯子放下} 不会跟后面说的话粘进同一个气泡。
    """
    if not protect_brace or not BRACE_BLOCK_RE.search(line):
        return _split_plain(line, target)

    pieces: List[str] = []
    pos = 0
    for m in BRACE_BLOCK_RE.finditer(line):
        head = line[pos:m.start()]
        if head.strip():
            pieces.extend(_split_plain(head.strip(), target))
        pieces.append(m.group(0).strip())
        pos = m.end()
    tail = line[pos:]
    if tail.strip():
        pieces.extend(_split_plain(tail.strip(), target))
    return pieces


def _merge_within(pieces: List[str], target: int) -> List[str]:
    """行内相邻片段合并到不超过 target。花括号块永远独立，前后都不合。"""
    out: List[str] = []
    for p in pieces:
        if not p:
            continue
        if _is_brace_block(p):
            out.append(p)
            continue
        if out and not _is_brace_block(out[-1]) and len(out[-1]) + len(p) <= target:
            out[-1] = out[-1] + p
        else:
            out.append(p)
    return out


def _repack(segments: List[str], max_segments: int) -> List[str]:
    """条数超上限时的妥协：按字数均分重装，压进 max_segments 条。

    均分长度就是每条的目标字量，不再额外放宽，这样压完长度是均匀的，
    不会出现某条 35 字、某条 9 字这种落差。
    """
    if len(segments) <= max_segments:
        return segments

    out: List[str] = list(segments)
    # 每次合掉当前最短的一对相邻片段，压完长度最均匀，
    # 不会出现前面都很短、最后一条却是一堵墙的情况
    while len(out) > max_segments:
        i = min(range(len(out) - 1), key=lambda k: len(out[k]) + len(out[k + 1]))
        out[i:i + 2] = [out[i] + out[i + 1]]
    return out


def split_message(text: str, target: int = 22, max_segments: int = 5,
                  protect_brace: bool = True) -> List[str]:
    """把一条回复拆成多条气泡，内容一字不丢。

    换行是硬边界：模型自己用 \\n 断的行，一行至少一个气泡，绝不跨行合并。
    但也不依赖换行：没有换行时退回老逻辑，按句读和逗号切。

    流程：
      1. 按 \\n 切行（连续空行并成一个，空行不产出空气泡）
      2. 每行内部按句读切，单句超长再按逗号切
      3. 每行内部合并到不超过 target，行与行之间不合
      4. 行数超上限时才妥协：跨行摊平成不超过 max_segments 条
    """
    text = text.strip()
    if not text:
        return []

    lines = [ln for ln in re.split(r"\r?\n+", text) if ln.strip()]
    if not lines:
        return []

    blocks: List[List[str]] = []
    for ln in lines:
        pieces = _split_line(ln, target, protect_brace)
        blocks.append(_merge_within(pieces, target) if pieces else [])

    segments: List[str] = [s for b in blocks for s in b if s.strip()]
    if not segments:
        return []

    total = sum(len(s) for s in segments)
    if len(segments) > max_segments:
        # 短回复被拆得太碎 -> 压回上限，别把一句话撕成渣
        # 长文是干活用的 -> 不压成巨型气泡，按正常长度发，只留一个防刷屏的硬顶
        if total <= max_segments * target:
            segments = _repack(segments, max_segments)
        else:
            hard = max(max_segments, LONG_TEXT_HARD_CAP)
            if len(segments) > hard:
                segments = _repack(segments, hard)

    return [s.strip() for s in segments if s.strip()]
