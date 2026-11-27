"""历史防污染：别让「泄露的八股」留在 conversation.history 里反复回喂。

模型很吃历史先例：前面两轮只要漏出一句客服腔 / 排比句，后面就算加再强的
系统提示，它也会顺着历史继续模仿那个文风。所以出站清洗之外还要做一层
**入库前隔离**：

- 对刚发出的那条回复算一个「八股分」（高风险句式 + 格式噪音 + 客服尾）；
- 分数 ≥ 阈值时，从会话历史里把这轮 assistant 消息**摘掉**（宁可少一轮
  上下文，也不让脏样本喂养下一轮）；
- 只动 history，不动已经发出去的消息本身。

全部包在 try/except 里：历史接口在版本间有差异，失败只留 debug 日志。
"""

from __future__ import annotations

import json
import re
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 历史防污染"

_SERVICE_TAIL = re.compile(
    r"(?:希望.{0,10}(?:帮助|有用)|以上(?:就是|是)?|还有(?:什么|其他).{0,6}(?:问题|需要)"
    r"|你觉得呢|你怎么看|欢迎随时)[。！？!?]?\s*$"
)
_FORMAT_NOISE = re.compile(r"\*\*|^#{1,6}\s|\n\s*[-*+]\s|^\s*\d+[.、)]\s", re.M)


def boilerplate_score(text: str) -> int:
    """给一条回复打「八股分」：越高越不该留在历史里。"""
    body = str(text or "")
    if not body.strip():
        return 0
    score = 0
    try:
        from .polish_bagu import detect_high_risk

        score += sum(int(v) for v in (detect_high_risk(body) or {}).values())
    except Exception:  # noqa: BLE001
        pass
    try:
        score += len(_FORMAT_NOISE.findall(body))
    except Exception:  # noqa: BLE001
        pass
    try:
        if _SERVICE_TAIL.search(body):
            score += 1
    except Exception:  # noqa: BLE001
        pass
    return score


def _cfg(cfg: Any, key: str, default: Any) -> Any:
    try:
        return cfg.get(key, default)
    except Exception:  # noqa: BLE001
        return default


_WS_RE = re.compile(r"\s+")


def _text_of(content: Any) -> str:
    """历史节点可能是字符串，也可能是多段内容列表（图文/组件）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item or ""))
        return "".join(parts)
    return str(content or "")


def _same_turn(history_text: str, sent_text: str) -> bool:
    """历史里那条 assistant 是不是本轮这条发出的消息（去空白 + 前缀比对）。"""
    a = _WS_RE.sub("", _text_of(history_text))
    b = _WS_RE.sub("", str(sent_text or ""))
    if not a or not b:
        return False
    if a == b:
        return True
    n = min(40, len(a), len(b))
    return n >= 8 and a[:n] == b[:n]


async def drop_dirty_turn(
    plugin: Any, event: Any, sent_text: str, raw_score: int | None = None
) -> bool:
    """八股分超阈值时，把这轮 assistant 消息从会话历史里摘掉。返回是否动过。

    ``raw_score``：清洗**前**的原始分（由 `on_llm_response` 提前挂到事件上）。
    不传才用洗后的文本现算——洗后的格式噪音/客服尾几乎都被清掉了，会算不出分。

    安全边界（宁可不动，也不误删别人的回合）：
    - 只处理 LLM 产物：调用方必须确认 `result.is_llm_result()`；
    - 摘之前校验历史里那条 assistant 的正文与本轮 sent_text 对得上。
    """
    cfg = getattr(plugin, "cfg", None)
    if not bool(_cfg(cfg, "出站_历史防污染", True)):
        return False
    try:
        threshold = int(_cfg(cfg, "出站_历史清洗阈值", 3) or 3)
    except (TypeError, ValueError):
        threshold = 3
    score = int(raw_score) if raw_score is not None else boilerplate_score(sent_text)
    if score < max(1, threshold):
        return False

    umo = str(getattr(event, "unified_msg_origin", "") or "")
    if not umo:
        return False
    try:
        cm = getattr(getattr(plugin, "context", None), "conversation_manager", None)
        if cm is None:
            return False
        cid = await cm.get_curr_conversation_id(umo)
        if not cid:
            return False
        conv = await cm.get_conversation(umo, cid)
        raw = getattr(conv, "history", "") if conv else ""
        history = json.loads(raw) if raw else []
        if not isinstance(history, list) or not history:
            return False
        # 从尾部找最近一条 assistant，并**确认它确实是这一轮**（同一条消息）
        idx = None
        for i in range(len(history) - 1, -1, -1):
            item = history[i]
            if isinstance(item, dict) and str(item.get("role")) == "assistant":
                idx = i
                break
        if idx is None:
            return False
        if not _same_turn(history[idx].get("content"), sent_text):
            # 历史里那条不是刚发的这条（命令回包/其他插件输出/还没入库），不动
            logger.debug(
                "%s | 末条 assistant 与本次发送内容不一致，跳过（%s）", _LOG,
                _text_of(history[idx].get("content"))[:20],
            )
            return False
        history.pop(idx)
        await cm.update_conversation(umo, cid, history=history)
        logger.info(
            "proactive_life | 历史防污染：八股分 %d 达标，已摘掉上一轮 assistant（%s）",
            score, umo,
        )
        return True
    except Exception as e:  # noqa: BLE001 - 历史接口差异不该影响主流程
        logger.debug("%s | 摘除失败（不影响回复）: %s", _LOG, e)
        return False
