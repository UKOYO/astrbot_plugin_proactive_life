"""八股清洗（融合自 astrbot_plugin_prose_polish）。

保留纯清洗逻辑，去掉 AstrBot Star 壳与 /八股 指令，由统一出站管线调用。
任何一步异常都回退原文，宁可八股也不让消息发不出去。
"""

from __future__ import annotations

import random
import re
from typing import Any

from .polish_rules import (
    ENDING_PATTERNS, SENTENCE_RULES, SOFTENER_EXCLUDE, SOFTENERS, WORD_RULES, rule_ids,
)
from .polish_bagu import (
    all_phrases, detect_high_risk, downgrade_dash,
    trim_closing_tail, trim_flattery_head, trim_narration_head, trim_official_head,
)

# 只检测、不硬改的规则：硬改容易砍出语病（"不是A而是B"删一半会变病句），
# 这类交给上游 prompt 约束 + 干净历史来根治，这里只统计命中。
DETECT_ONLY_RULE_IDS = frozenset({
    "negative_parallelism",
    "negative_parallelism_cross",
    "negative_parallelism_2",
    # 依赖上下文句法（前面常带动词），硬改会把「带着三分醉意」改成「带着有点醉意」
    "ratio_split",
    "less_more",
    # 词池是谓语短语、匹配点是宾语名词位置，替换后剩「他有声音低」这种残句
    "voice_rasp",
    "cold_pool_eyes",
})

DEFAULT_CONFIG: dict[str, Any] = {
    # 默认「止损档」：出站正则看不懂语义，只做格式兜底 + 整句裁剪，
    # 换词/删句中短语/水词/合句这些「越想润色越容易出语病」的默认全关。
    "清洗档位": "minimal",
    "改句式": False, "换词": False, "删八股短语": False, "激进删词": False,
    "水词配额": 1, "合并碎句": False, "碎句阈值": 8, "删结尾升华": True,
    "保护细节块": True, "保护代码表格": True, "保护引号台词": True,
    "禁用规则": [], "打印详情": False, "演练模式": False,
}

# 「止损档」只跑这三类：格式噪音（structure）、客服腔（service）、升华尾（elevate）。
# 其它类别（开场白/自我指称/冗余修饰/排比/职场黑话）都需要语义理解，
# 交给上游 prompt 治；它们里的 detect_only 规则仍会计数。
_MINIMAL_AI_CATEGORIES = frozenset({"structure", "service", "elevate"})

# 占位符用控制字符包起来，正常文本里几乎不可能出现
PH_OPEN = "\u0001"
PH_CLOSE = "\u0002"

CODE_BLOCK_RE = re.compile(
    r"```[\s\S]*?```|<(?:pre|code|style|script)\b[^>]*>[\s\S]*?</(?:pre|code|style|script)>",
    re.I,
)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
DETAILS_RE = re.compile(r"<details\b[^>]*>[\s\S]*?</details>", re.I)
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)
HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>\n]*>")

QUOTE_RES = [
    re.compile(r"“[^”\n]{1,600}”"),
    re.compile(r"「[^」\n]{1,600}」"),
    re.compile(r"『[^』\n]{1,600}』"),
    re.compile(r'"[^"\n]{1,600}"'),
]

ELLIPSIS_RE = re.compile(r"…{2,}|\.{3,}|。{3,}")
DASH_RE = re.compile(r"—{2,}|－{2,}")

SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?…])")
SEG_SPLIT_RE = re.compile(r"(?<=[。！？!?…，,；;\n])")


# 删短语后左边停在这些词上、右边紧跟标点/句末 → 中心词被挖走（"水有点浑浊，"→"水有点，"）
_DANGLING_LEFT = (
    "的", "地", "得", "很", "太", "挺", "更", "最", "都", "也", "还",
    "有点", "有些", "比较", "十分", "非常", "特别", "突然", "忽然",
    "几乎", "仿佛", "已经", "正在", "一直", "微微", "轻轻", "缓缓",
)
_PUNCT_OR_END = "，,。！？；、!?…～~ \n\t"


def _would_dangle(left: str, right: str) -> bool:
    """删完这一处会不会把谓语挖空：左边停在程度/副词上，右边紧跟标点或句末。"""
    tail = left.rstrip()
    head = right.lstrip()
    if head and head[0] not in _PUNCT_OR_END:
        return False
    return tail.endswith(_DANGLING_LEFT)


def _drop_phrase(text: str, word: str) -> tuple[str, int]:
    """逐次删短语，每次都做「挖洞」检查；会出残局的那一处直接跳过。"""
    if not word or word not in text:
        return text, 0
    out: list[str] = []
    i = 0
    removed = 0
    while True:
        j = text.find(word, i)
        if j < 0:
            out.append(text[i:])
            break
        left = "".join(out) + text[i:j]
        right = text[j + len(word):]
        if _would_dangle(left, right):
            out.append(text[i : j + len(word)])
        else:
            out.append(text[i:j])
            removed += 1
        i = j + len(word)
    return "".join(out), removed


def _delete_phrases(text: str, phrases: list[str], ratio: float = 0.45) -> tuple[str, int]:
    """删八股短语。按分句处理，每句删完过一遍安全阀，删废了就退回原句。

    安全阀拦三种情况：
      1. 删完剩不到原句一半，说明中心词被一起削掉了
      2. 删完以「的/地/感/标点」开头，说明句子被拦腰截断
      3. 删完中间被挖洞（"水有点浑浊，" → "水有点，"）：见 :func:`_would_dangle`
    """
    hits = 0
    out: list[str] = []

    for seg in SEG_SPLIT_RE.split(text):
        if not seg.strip():
            out.append(seg)
            continue

        core = seg
        n = 0
        for w in phrases:
            if w in core:
                core, k = _drop_phrase(core, w)
                n += k

        if n == 0:
            out.append(seg)
            continue

        stripped = core.strip()
        seg_stripped = seg.strip()
        core_len = len(stripped.rstrip("，,。！？、；"))
        # 只削掉开头的情况：剩下的正好是原句尾巴，中心词还在，放行
        trimmed_head = core_len >= 2 and seg_stripped.endswith(stripped)
        broken = (
            not stripped
            or (
                not trimmed_head
                and core_len < max(2, len(seg_stripped) * ratio)
            )
            or re.match(r"^[的地得感，,。！？、；]", stripped) is not None
        )
        if broken:
            out.append(seg)
        else:
            out.append(core)
            hits += n

    return "".join(out), hits


def _strip_softeners(text: str, quota: int) -> tuple[str, int]:
    """软化副词按条数留，超出配额的一律删掉。quota<0 视为不限。"""
    if quota < 0:
        return text, 0
    hits = 0
    kept = 0

    def _sub(m: re.Match) -> str:
        nonlocal hits, kept
        if kept < quota:
            kept += 1
            return m.group(0)
        hits += 1
        return ""

    # 把紧跟其后的「地」一起匹配走：否则删完会留下「我地看着」这种孤字。
    # 固定词（悄悄话 / 微微一笑 / 默默无闻）用负向前瞻跳过，别把词拆开。
    parts = [w + SOFTENER_EXCLUDE.get(w, "") for w in SOFTENERS]
    pattern = re.compile("(?:" + "|".join(parts) + r")地?")
    return pattern.sub(_sub, text), hits


def _merge_short(text: str, threshold: int) -> tuple[str, int]:
    """把连续的超短句并回上一句。只在一行之内做，不跨段落。"""
    if threshold <= 0:
        return text, 0

    out_lines: list[str] = []
    merged_total = 0

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ">", "-", "*", "|", PH_OPEN)):
            out_lines.append(line)
            continue

        parts = [p for p in SENTENCE_SPLIT_RE.split(stripped) if p]
        if len(parts) < 2:
            out_lines.append(line)
            continue

        merged: list[str] = []
        run: list[str] = []

        def _flush_run() -> None:
            nonlocal merged_total
            if not run:
                return
            if len(run) >= 2:
                merged_total += len(run) - 1
                tail = run[-1]
                body = "".join(run[:-1]).rstrip("，,。！？!?… ")
                merged.append(body + "，" + tail.lstrip("，, "))
            else:
                merged.append(run[0])
            run.clear()

        for part in parts:
            core = part.rstrip("。！？!?…，, ")
            if len(core) <= threshold:
                run.append(part)
            else:
                _flush_run()
                merged.append(part)
        _flush_run()

        out_lines.append("".join(merged))

    return "\n".join(out_lines), merged_total


def _trim_ending(text: str) -> tuple[str, int]:
    """砍掉结尾那句老套升华。按最后一句判断，不是按最后一行。"""
    lines = text.rstrip().split("\n")
    if not lines:
        return text, 0

    removed = 0
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue

        parts = [p for p in SENTENCE_SPLIT_RE.split(last) if p.strip()]
        target = parts[-1] if parts else last
        core = target.strip().rstrip("。！？!?…")

        if not any(p.match(core) for p in ENDING_PATTERNS):
            break

        removed += 1
        rest = "".join(parts[:-1]).strip() if len(parts) > 1 else ""
        if rest:
            lines[-1] = rest
        else:
            lines.pop()

    if not lines:
        return text, 0  # 全砍光了，说明原文就一句升华，留着
    return "\n".join(lines), removed


ORPHAN_BEFORE_PUNCT_RE = re.compile(r"[，,、]\s*(?=[。！？!?…])")
ORPHAN_AFTER_PUNCT_RE = re.compile(r"(?<=[。！？!?…])[，,、]")
ORPHAN_REPEAT_RE = re.compile(r"[，,、]{2,}")
ORPHAN_TAIL_RE = re.compile(r"(?<=\S)[，,、]+\s*$", re.M)


def _clean_orphan_punct(text: str) -> tuple[str, int]:
    """删词/砍尾句之后留下的悬空逗号，收干净。"""
    total = 0
    for pattern, repl in (
        (ORPHAN_BEFORE_PUNCT_RE, ""),
        (ORPHAN_AFTER_PUNCT_RE, ""),
        (ORPHAN_REPEAT_RE, "，"),
        (ORPHAN_TAIL_RE, ""),
    ):
        text, n = pattern.subn(repl, text)
        total += n
    return text, total


def _normalize_punct(text: str) -> tuple[str, int]:
    hits = 0
    new, n1 = ELLIPSIS_RE.subn("…", text)
    new, n2 = DASH_RE.subn("——", new)
    hits = n1 + n2
    return new, hits


class ProsePolisher:
    """可复用清洗器；内部 RNG/词袋跨消息保持，避免连续撞词。"""

    def __init__(self, config: dict | None = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._rng = random.Random()
        self._bags: dict[str, dict] = {}
        self._stats = {"last_hits": 0, "last_rules": [], "total": 0, "messages": 0}

    def _pick(self, rule_id: str, pool: list[str]) -> str:
        """洗牌不放回地抽一个替换词，一轮抽空才重洗。"""
        st = self._bags.setdefault(rule_id, {"bag": [], "last": None})
        if not st["bag"]:
            st["bag"] = list(pool)
            self._rng.shuffle(st["bag"])
            # 新一轮的头一个别跟上一次撞
            if st["last"] is not None and len(st["bag"]) > 1 and st["bag"][0] == st["last"]:
                st["bag"].append(st["bag"].pop(0))
        value = st["bag"].pop(0)
        st["last"] = value
        return value

    # ------------------------------------------------------------ 保护区

    def _protect(self, text: str) -> tuple[str, list[str]]:
        vault: list[str] = []

        def _stash(m: re.Match) -> str:
            vault.append(m.group(0))
            return f"{PH_OPEN}{len(vault) - 1}{PH_CLOSE}"

        patterns: list[re.Pattern] = []
        if self.config.get("保护代码表格", True):
            patterns += [CODE_BLOCK_RE, INLINE_CODE_RE, TABLE_ROW_RE]
        if self.config.get("保护细节块", True):
            patterns.append(DETAILS_RE)
        if self.config.get("保护引号台词", True):
            patterns += QUOTE_RES
        # HTML 标签最后收，免得前面的规则把标签里的东西动了
        patterns.append(HTML_TAG_RE)

        for p in patterns:
            text = p.sub(_stash, text)
        return text, vault

    @staticmethod
    def _restore(text: str, vault: list[str]) -> str:
        if not vault:
            return text

        def _put(m: re.Match) -> str:
            idx = int(m.group(1))
            return vault[idx] if 0 <= idx < len(vault) else m.group(0)

        return re.sub(PH_OPEN + r"(\d+)" + PH_CLOSE, _put, text)

    # ------------------------------------------------------------ 管线

    def _apply_ai_rules(
        self, body: str, disabled: set, minimal: bool = False
    ) -> tuple[str, int, list[str]]:
        """跑 AI 八股规则；带词池的走洗牌抽取，其余直接替换。

        ``minimal=True``（默认档）时只跑格式类规则，其余（开场白/客服腔/升华/排比/
        冗余修饰/职场黑话）一律跳过——这些靠上游 prompt 治，正则硬改只会改坏话。
        ``detect_only`` 规则两种档位都只统计、不改写。
        """
        from .polish_ai_rules import ALL_AI_RULES

        hits = 0
        hit_ids: list[str] = []
        for rule in ALL_AI_RULES:
            if rule["id"] in disabled:
                continue
            if minimal and rule.get("category") not in _MINIMAL_AI_CATEGORIES and not rule.get("detect_only"):
                continue
            if rule.get("detect_only"):
                try:
                    n = len(rule["pattern"].findall(body))
                except Exception:  # noqa: BLE001
                    continue
                if n:
                    hits += n
                    hit_ids.append(rule["id"])
                continue
            pool = rule.get("pool")
            try:
                if pool:
                    def _sub(m: re.Match, _rule=rule) -> str:
                        return self._pick(_rule["id"], _rule["pool"])

                    body, n = re.subn(rule["pattern"], _sub, body)
                else:
                    body, n = rule["pattern"].subn(rule["repl"], body)
            except Exception:  # noqa: BLE001 - 单条规则异常不影响其它
                continue
            if n:
                hits += n
                hit_ids.append(rule["id"])
        return body, hits, hit_ids

    def polish(self, text: str) -> tuple[str, int, list[str]]:
        if not text or not text.strip():
            return text, 0, []

        disabled = set(self.config.get("禁用规则") or [])
        # 档位：minimal（默认，止损档）只做格式兜底 + 整句裁剪 + 只统计；
        # full 才会跑换词/删句中短语/水词/合句这些需要语义理解的操作。
        minimal = str(self.config.get("清洗档位", "minimal") or "minimal").strip().lower() != "full"
        self.last_risk = {}          # 高风险句式统计（只观测不改写）
        body, vault = self._protect(text)
        # 高风险句式必须在**动手清洗之前**、且针对**挖空保护区后的正文**统计：
        # 放到最后会同时踩两个坑——正文的破折号已被 downgrade_dash 换掉（永远
        # 统计不到），而代码块/引号台词刚被 _restore 还原回来（被误报成八股）。
        try:
            self.last_risk = detect_high_risk(body)
        except Exception:  # noqa: BLE001
            self.last_risk = {}
        hits = 0
        hit_ids: list[str] = []

        # 0.5 高确定性裁剪：掐马屁头 / 掐公文头（洗完不能为空）
        if self.config.get("清AI八股", True):
            if "trim_flattery_head" not in disabled:
                body, n = trim_flattery_head(body)
                if n:
                    hits += n
                    hit_ids.append("trim_flattery_head")
            if "trim_official_head" not in disabled:
                body, n = trim_official_head(body)
                if n:
                    hits += n
                    hit_ids.append("trim_official_head")
            if "trim_narration_head" not in disabled:
                body, n = trim_narration_head(body)
                if n:
                    hits += n
                    hit_ids.append("trim_narration_head")

        # 0. AI 八股清洗：止损档只剥格式噪音（加粗/标题/列表/分隔线）
        if self.config.get("清AI八股", True):
            body, n, ids = self._apply_ai_rules(body, disabled, minimal=minimal)
            if n:
                hits += n
                hit_ids.extend(ids)

        # 1. 改句式：DETECT_ONLY 规则任何档位都只计数；改写需 full 档且开关打开
        for rule in SENTENCE_RULES:
            if rule["id"] in disabled:
                continue
            if rule["id"] in DETECT_ONLY_RULE_IDS:
                try:
                    # 规则里的 pattern 可能是字符串也可能是已编译对象，统一用 re.findall
                    n = len(re.findall(rule["pattern"], body))
                except Exception:  # noqa: BLE001
                    continue
                if n:
                    hits += n
                    hit_ids.append(rule["id"])
                continue
            if minimal or not self.config.get("改句式", True):
                continue
            body, n = rule["pattern"].subn(rule["repl"], body)
            if n:
                hits += n
                hit_ids.append(rule["id"])

        # 2. 换词（文学词表：默认止损档不跑，无语境替换会误伤人名词；
        #    DETECT_ONLY 的规则任何档位都只计数）
        for rule in WORD_RULES:
            if rule["id"] in disabled:
                continue
            if rule["id"] in DETECT_ONLY_RULE_IDS:
                try:
                    n = len(re.findall(rule["pattern"], body))
                except Exception:  # noqa: BLE001
                    continue
                if n:
                    hits += n
                    hit_ids.append(rule["id"])
                continue
            if minimal or not self.config.get("换词", True):
                continue
            pool = rule["pool"]
            if not pool:
                continue

            def _sub(m: re.Match, _rule=rule) -> str:
                return self._pick(_rule["id"], _rule["pool"])

            body, n = re.subn(rule["pattern"], _sub, body)
            if n:
                hits += n
                hit_ids.append(rule["id"])

        # 3. 删八股短语（句中抠词：默认档不跑，只在 full 档且显式开启时做）
        if not minimal and self.config.get("删八股短语", True):
            aggressive = bool(self.config.get("激进删词", False))
            phrases = all_phrases(aggressive)
            body, n = _delete_phrases(body, phrases, 0.6 if aggressive else 0.45)
            if n:
                hits += n
                hit_ids.append("delete_phrases")

        # 4. 水词配额（同样是句中抠词，默认档不动）
        if not minimal:
            quota = int(self.config.get("水词配额", 1))
            body, n = _strip_softeners(body, quota)
            if n:
                hits += n
                hit_ids.append("softeners")

        # 4. 合并碎句
        if not minimal and self.config.get("合并碎句", True):
            body, n = _merge_short(body, int(self.config.get("碎句阈值", 8)))
            if n:
                hits += n
                hit_ids.append("merge_short")

        # 4.5 去结案/留客/升华尾句（仅多句回复）
        if self.config.get("清AI八股", True) and "trim_closing_tail" not in disabled:
            body, n = trim_closing_tail(body)
            if n:
                hits += n
                hit_ids.append("trim_closing_tail")

        # 5. 删结尾升华
        if self.config.get("删结尾升华", True):
            body, n = _trim_ending(body)
            if n:
                hits += n
                hit_ids.append("trim_ending")

        # 6. 收悬空标点 + 标点归一
        body, n = _clean_orphan_punct(body)
        if n:
            hits += n
            hit_ids.append("orphan_punct")

        # 破折号降级（代码/引用已被 vault 保护）
        if self.config.get("清AI八股", True) and "downgrade_dash" not in disabled:
            body, n = downgrade_dash(body)
            if n:
                hits += n
                hit_ids.append("downgrade_dash")

        body, n = _normalize_punct(body)
        if n:
            hits += n
            hit_ids.append("punctuation")

        body = self._restore(body, vault)
        # 演练模式：只统计命中（面板要看数字），正文保持原样不改写
        if self.config.get("演练模式", False):
            return text, hits, hit_ids
        if not body.strip():
            # 整条都是八股。短消息（<=12 字，多为单句马屁/结案）保底保留，
            # 否则这条回复会直接消失；长消息才清空，由调用方丢弃。
            if hits and len(text.strip()) > 12:
                return "", hits, hit_ids
            return text, 0, []
        return body, hits, hit_ids

    # ------------------------------------------------------------ 钩子

