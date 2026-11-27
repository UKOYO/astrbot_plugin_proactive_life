"""统一出站清洗管线（单入单出、幂等、顺序确定）。

顺序固定：
1. LLM response 层：过滤误吐思考（thinking_filter）
2. decorating 层：八股清洗（prose_polish）
3. decorating 层：节奏修剪（rhythm_trimmer，由宿主 reshape 执行）

幂等：以 event extras 里的 marker 记录「已清洗的 (阶段, 文本 hash)」，
同一次事件重复进入不会二次改写；任一阶段异常直接回退原文，不影响发送。
"""

from __future__ import annotations

import datetime
import hashlib
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 出站管线"
_MARKER_KEY = "_pl_outbound_cleaned"


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()[:16]


def _get_marks(event: Any) -> dict[str, str]:
    try:
        fn = getattr(event, "get_extra", None)
        value = fn(_MARKER_KEY) if callable(fn) else None
        return dict(value) if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _set_mark(event: Any, stage: str, digest: str) -> None:
    try:
        fn = getattr(event, "set_extra", None)
        if not callable(fn):
            return
        marks = _get_marks(event)
        marks[stage] = digest
        fn(_MARKER_KEY, marks)
    except Exception:  # noqa: BLE001
        pass


def clean_llm_text(text: str, cfg: Any, *, stage: str = "think") -> tuple[str, bool]:
    """LLM response 层清洗：标签思考 + 裸思考 + 输出整形。"""
    if not text or not text.strip():
        return text, False
    try:
        def g(key: str, default):
            try:
                return cfg.get(key, default)
            except Exception:
                return default

        if not bool(g("模块_思考过滤", True)):
            return text, False
        from .think_filtering import filter_reasoning_text, normalize_output
        from .bare_thinking import guard as bare_guard

        tags = g("出站_思考标签", ["think", "thinking", "reasoning", "analysis",
                                  "thought", "reflection"])
        cleaned, hits = filter_reasoning_text(
            text, tags,
            orphan_closing_action=str(g("出站_孤闭合处理", "remove_prefix")),
            unclosed_opening_action=str(g("出站_未闭合处理", "remove_suffix")),
            case_sensitive=bool(g("出站_思考标签大小写敏感", False)),
        )
        if bool(g("出站_裸思考过滤", True)):
            guarded = bare_guard(cleaned)
            if guarded is None:
                return g("出站_空回复兜底", "（刚才走神了，再说一遍？）"), True
            cleaned = guarded
        cleaned = normalize_output(
            cleaned,
            trim=bool(g("出站_修剪空白", True)),
            collapse_blank_lines=bool(g("出站_压缩空行", True)),
        )
        if not cleaned.strip():
            return g("出站_空回复兜底", "（刚才走神了，再说一遍？）"), True
        return cleaned, hits > 0 or cleaned != text
    except Exception as e:  # noqa: BLE001
        logger.debug("%s | 思考过滤异常，回退原文: %s", _LOG, e)
        return text, False


def clean_llm_response(resp: Any, cfg: Any) -> bool:
    """直接清洗 LLMResponse 对象（on_llm_response 层）。返回是否改动。"""
    text = str(getattr(resp, "completion_text", "") or "")
    cleaned, changed = clean_llm_text(text, cfg, stage="think")
    if changed:
        try:
            setattr(resp, "completion_text", cleaned)
        except Exception:  # noqa: BLE001
            return False
    # reasoning_content 清空（不代表改动主文本）
    try:
        if hasattr(resp, "reasoning_content") and getattr(resp, "reasoning_content"):
            setattr(resp, "reasoning_content", "")
    except Exception:  # noqa: BLE001
        pass
    return changed


# 长效 polisher（保留词袋状态，避免连续撞词）+ 最近清洗记录
_POLISHER = None
_POLISH_LOG: list[dict] = []
_POLISH_LOG_MAX = 30


def polish_history() -> list[dict]:
    """最近 N 条八股清洗记录（供面板展示）。"""
    return list(_POLISH_LOG)


def polish_text(text: str, cfg: Any, event: Any = None, *, stage: str = "polish") -> str:
    """decorating 层：八股清洗（幂等，异常回退原文）。"""
    global _POLISHER
    if not text or not bool(_cfg(cfg, "模块_八股清洗", True)):
        return text
    digest = _hash(text)
    marks = _get_marks(event)
    if marks.get(stage) == digest:
        return text
    try:
        from .prose_polish import ProsePolisher

        def _map(key: str, default):
            return _cfg(cfg, "出站_八股_" + key, _cfg(cfg, "八股_" + key, default))

        if _POLISHER is None:
            _POLISHER = ProsePolisher({})
        _POLISHER.config.update({
            # 档位：minimal（默认）只做格式兜底 + 整句裁剪；full 才跑换词/删词
            "清洗档位": str(_cfg(cfg, "出站_清洗档位", "minimal") or "minimal"),
            "清AI八股": bool(_map("清AI八股", True)),
            "改句式": bool(_map("改句式", True)),
            "换词": bool(_map("换词", True)),
            "删八股短语": bool(_map("删八股短语", True)),
            "激进删词": bool(_map("激进删词", False)),
            "水词配额": int(_map("水词配额", 1)),
            "合并碎句": bool(_map("合并碎句", False)),
            "碎句阈值": int(_map("碎句阈值", 8)),
            "删结尾升华": bool(_map("删结尾升华", True)),
            "保护细节块": bool(_map("保护细节块", True)),
            "保护代码表格": bool(_map("保护代码表格", True)),
            "保护引号台词": bool(_map("保护引号台词", True)),
            "禁用规则": list(_map("禁用规则", [])),
            "演练模式": bool(_map("演练模式", False)),
        })
        out, hits, rules = _POLISHER.polish(text)
        if hits and not (out or "").strip():
            # 整条都是八股，清洗后什么都没有：明确告诉调用方丢弃这条
            logger.debug("%s | 整条均为八股，清洗后为空，丢弃该段", _LOG)
            return ""
        if out and out.strip():
            _set_mark(event, stage, digest)
            risk = dict(getattr(_POLISHER, "last_risk", {}) or {})
            if hits or risk:
                _POLISH_LOG.append({
                    "time": datetime.datetime.now().strftime("%H:%M:%S"),
                    "before": text[:180],
                    "after": out[:180],
                    "hits": hits,
                    "rules": rules[:10],
                    "risk": risk,          # 高风险句式命中（只统计不改写）
                })
                del _POLISH_LOG[:-_POLISH_LOG_MAX]
            return out
        return text
    except Exception as e:  # noqa: BLE001
        logger.debug("%s | 八股清洗异常，回退原文: %s", _LOG, e)
        return text


def _cfg(cfg: Any, key: str, default):
    try:
        return cfg.get(key, default)
    except Exception:
        return default
