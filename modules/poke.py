"""戳一戳互动（融合自 astrbot_plugin_poke_to_llm 的核心判定）。

只保留纯判定与轻量策略；OneBot call_action 与 request_llm 由宿主 adapter 提供。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 戳一戳"


def is_poke_event(event: Any) -> tuple[bool, str, str, str]:
    """识别「戳的是机器人自己」的 poke 通知，返回 (是否, bot_id, sender_id, group_id)。"""
    ok, bot_id, sender, target, group = parse_poke(event)
    if not ok or not bot_id or not target or target != bot_id:
        return False, "", "", ""
    return True, bot_id, sender, group


def parse_poke(event: Any) -> tuple[bool, str, str, str, str]:
    """解析 poke 通知，返回 (是否, bot_id, sender_id, target_id, group_id)。

    比 :func:`is_poke_event` 多返回 ``target_id``：跟戳（别人互相戳）需要知道
    被戳的是谁，而「被戳我」的判定只关心 target 是否等于 bot。
    返回的 ``是否`` 为 True 表示这确实是一条 poke 通知（不论戳的是谁）。
    """
    try:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if not isinstance(raw, dict):
            return False, "", "", "", ""
        if str(raw.get("post_type")) != "notice" or str(raw.get("notice_type")) != "notify":
            return False, "", "", "", ""
        if str(raw.get("sub_type")) != "poke":
            return False, "", "", "", ""
        bot_id = str(getattr(event, "get_self_id", lambda: "")() or raw.get("self_id") or "")
        target = str(raw.get("target_id") or "")
        sender = str(getattr(event, "get_sender_id", lambda: "")() or raw.get("user_id") or "")
        group = str(getattr(event, "get_group_id", lambda: "")() or "")
        if not group:
            # 部分适配器只在原始报文里带 group_id；但私聊事件绝不能从 raw 兜底，
            # 否则私聊会被当成群聊（进 pending 后就不回复了）。
            try:
                private = bool(event.is_private_chat())
            except Exception:  # noqa: BLE001
                private = False
            if not private:
                group = str(raw.get("group_id") or "")
        return True, bot_id, sender, target, group
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s | 事件解析异常: %s", _LOG, exc)
        return False, "", "", "", ""


class PokeCooldown:
    """个人/群组冷却；管理员可豁免。"""

    def __init__(self, personal_sec: float = 30.0, group_sec: float = 60.0) -> None:
        self.personal = max(0.0, float(personal_sec))
        self.group = max(0.0, float(group_sec))
        self._last: dict[str, float] = {}

    def update_config(self, personal_sec: float | None = None, group_sec: float | None = None) -> None:
        """面板改完冷却立即生效。"""
        if personal_sec is not None:
            self.personal = max(0.0, float(personal_sec))
        if group_sec is not None:
            self.group = max(0.0, float(group_sec))

    def _key(self, sender: str, group: str) -> str:
        return f"g:{group}" if group else f"u:{sender}"

    def is_cooling(self, sender: str, group: str = "", *, is_admin: bool = False,
                   now: float | None = None) -> bool:
        if is_admin:
            return False
        now = now or time.monotonic()
        ttl = self.group if group else self.personal
        if ttl <= 0:
            return False
        last = self._last.get(self._key(sender, group), 0.0)
        return (now - last) < ttl

    def mark(self, sender: str, group: str = "", *, now: float | None = None) -> None:
        self._last[self._key(sender, group)] = now or time.monotonic()

    def clear(self) -> None:
        self._last.clear()
