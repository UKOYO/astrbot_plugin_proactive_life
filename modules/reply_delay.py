"""作息驱动的拟真回复延迟（零 Token 成本）。

真人不会在你说话的一瞬间就秒回：正在洗澡、在开会、在路上时，回复天然会晚
一点。这里读**已有缓存**的日程（`common.read_schedule`，带 mtime 缓存，不读
模型、不额外请求），命中忙碌/睡眠关键词时，在回复链路前加一段可配延迟。

豁免（绝不能延迟）：
- 指令消息（`/xxx`）——等 10 秒才执行指令像卡死；
- 急事词（救命/赶紧/医院…）——这时候慢回就是失职；
- 戳一戳 / 艾特（`_poke_trigger`）——明确点名，立刻回。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

# 日程里出现这些词，说明「此刻不太方便立刻回」
_DEFAULT_BUSY = (
    "睡", "洗澡", "出门", "开会", "上课", "做饭", "吃饭", "路上",
    "地铁", "公交", "运动", "跑步", "忙", "加班", "赶路", "排队",
)

# 出现这些词就别摆作息架子了
_DEFAULT_URGENT = (
    "急", "救命", "赶紧", "快点", "马上", "立刻", "报警", "医院",
    "不舒服", "疼", "哭", "出事了", "怎么办", "帮我",
)


def _cfg(cfg: Any, key: str, default: Any) -> Any:
    try:
        return cfg.get(key, default)
    except Exception:  # noqa: BLE001
        return default


def _words(cfg: Any, key: str, default: tuple[str, ...]) -> list[str]:
    raw = _cfg(cfg, key, None)
    if not raw:
        return list(default)
    if isinstance(raw, str):
        raw = raw.replace("，", ",").split(",")
    try:
        return [str(x).strip() for x in raw if str(x).strip()]
    except TypeError:
        return list(default)


def current_activity(cfg: Any) -> str:
    """读当前日程里的「此刻在做」（只读缓存，失败返回空串）。

    注意导入层级：本文件在 ``modules/`` 子包下，`common` 在插件根目录，
    必须用 ``..common``。写成 ``.common`` 会 ImportError，而外层 except 会把
    它吞掉 —— 结果就是「作息延迟」永远算出 0 秒、功能静默失效。
    """
    try:
        from ..common import read_schedule

        st = str(_cfg(cfg, "日程生成时间", "00:00") or "00:00")
        info = read_schedule(st)
        return str((info or {}).get("current_activity") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("proactive_life | 作息延迟读取日程失败，本次不延迟: %s", e)
        return ""


def compute_delay(cfg: Any, text: str, activity: str = "") -> float:
    """算出该延迟多少秒（0 = 立刻回）。纯函数，便于测试。"""
    if not bool(_cfg(cfg, "作息延迟_启用", False)):
        return 0.0
    body = str(text or "").strip()
    if body.startswith("/") or body.startswith("／"):
        return 0.0
    if any(w in body for w in _words(cfg, "作息延迟_急事词", _DEFAULT_URGENT)):
        return 0.0
    act = str(activity or "").strip()
    if not act:
        return 0.0
    # 只看开头：日程条目常是长句，后段可能提到别的事
    head = act[:40]
    if not any(w in head for w in _words(cfg, "作息延迟_忙碌词", _DEFAULT_BUSY)):
        return 0.0
    try:
        cap = float(_cfg(cfg, "作息延迟_最多秒", 12) or 12)
    except (TypeError, ValueError):
        cap = 12.0
    cap = max(0.0, min(120.0, cap))
    # 睡眠/洗澡这类「手上有水/已经躺下」的场景给满额，其余给 6 成
    heavy = any(w in head for w in ("睡", "洗澡", "开会"))
    return round(cap if heavy else cap * 0.6, 1)


async def apply_reply_delay(plugin: Any, event: Any) -> float:
    """被动回复前按作息延迟；返回实际睡了多久（秒）。"""
    try:
        if bool(event.get_extra("_poke_trigger")):
            return 0.0
        if bool(event.get_extra("_pl_delayed")):
            return 0.0
    except Exception:  # noqa: BLE001
        pass
    cfg = getattr(plugin, "cfg", None)
    text = str(getattr(event, "message_str", "") or "")
    delay = compute_delay(cfg, text, current_activity(cfg))
    if delay <= 0:
        return 0.0
    try:
        event.set_extra("_pl_delayed", True)
    except Exception:  # noqa: BLE001
        pass
    logger.debug("proactive_life | 作息延迟 %.1fs（%s）", delay, text[:16])
    await asyncio.sleep(delay)
    return delay
