"""撤回取消（融合自 astrbot_plugin_recall_cancel）。

设计取舍：
- 保留核心：识别 OneBot 撤回通知 → 用 UMO::message_id 关联 → 取消 LLM 请求/回复/发送；
- 与本项目 debounce 联动：撤回时清理对应会话的防抖桶与租约，取消未发分段任务；
- 舍弃：context_aware 私有字段的直接操作（强耦合），改为可选 adapter，有则清理、无则跳过；
- 不 monkey-patch send_message，避免与其他插件的 patch 互相覆盖。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

_LOG = "proactive_life | 撤回取消"


@dataclass
class PendingRequest:
    """一个已发出的 LLM 请求，等待撤回信号。"""

    umo: str
    message_id: str
    event: Any = None
    started_at: float = field(default_factory=time.monotonic)
    stage: str = ""


@dataclass
class RecalledMark:
    operator: str = ""
    at: float = field(default_factory=time.monotonic)


class RecallStateManager:
    """内存状态：pending 请求 + 已撤回标记。带锁、可过期清理。"""

    def __init__(self, expire_sec: float = 300.0) -> None:
        self._expire = max(30.0, float(expire_sec))
        self._pending: dict[str, PendingRequest] = {}
        self._recalled: dict[str, RecalledMark] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def compose_key(umo: str, message_id: str) -> str:
        return f"{umo}::{message_id}"

    async def add_pending(self, umo: str, message_id: str, event: Any = None, stage: str = "") -> None:
        if not umo or not message_id:
            return
        key = self.compose_key(umo, message_id)
        async with self._lock:
            self._pending[key] = PendingRequest(umo, message_id, event, stage=stage)

    async def get_pending(self, umo: str, message_id: str) -> PendingRequest | None:
        return self._pending.get(self.compose_key(umo, message_id))

    async def remove_pending(self, umo: str, message_id: str) -> PendingRequest | None:
        return self._pending.pop(self.compose_key(umo, message_id), None)

    async def mark_recalled(self, umo: str, message_id: str, operator: str = "") -> None:
        if not umo or not message_id:
            return
        async with self._lock:
            self._recalled[self.compose_key(umo, message_id)] = RecalledMark(operator)

    async def is_recalled(self, umo: str, message_id: str) -> bool:
        return self.compose_key(umo, message_id) in self._recalled

    async def cleanup(self, now: float | None = None) -> None:
        """按单调时钟清理过期记录，避免无限增长。"""
        now = now or time.monotonic()
        async with self._lock:
            for k, v in list(self._pending.items()):
                if now - v.started_at > self._expire:
                    self._pending.pop(k, None)
            for k, v in list(self._recalled.items()):
                if now - v.at > self._expire:
                    self._recalled.pop(k, None)

    def counts(self) -> dict[str, int]:
        return {"pending": len(self._pending), "recalled": len(self._recalled)}


def is_recall_event(event: Any) -> tuple[bool, str, str]:
    """识别 OneBot 撤回通知，返回 (是否, message_id, operator_id)。"""
    try:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if not isinstance(raw, dict):
            return False, "", ""
        if str(raw.get("post_type") or "") != "notice":
            return False, "", ""
        if str(raw.get("notice_type") or "") not in ("group_recall", "friend_recall"):
            return False, "", ""
        mid = str(raw.get("message_id") or "").strip()
        if not mid:
            return False, "", ""
        operator = str(raw.get("operator_id") or raw.get("user_id") or "").strip()
        return True, mid, operator
    except Exception:  # noqa: BLE001
        return False, "", ""


def stop_event_best_effort(event: Any) -> bool:
    """尽力中止事件；缺方法时只返回 False，不抛。"""
    try:
        fn = getattr(event, "stop_event", None)
        if callable(fn):
            fn()
            try:
                fn2 = getattr(event, "set_extra", None)
                if callable(fn2):
                    fn2("agent_stop_requested", True)
            except Exception:  # noqa: BLE001
                pass
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s | stop_event 调用失败: %s", _LOG, exc)
    return False


def cleanup_debounce(debounce: Any, umo: str) -> int:
    """撤回时清理对应会话的防抖桶与锁；返回清理的 item 数。"""
    if debounce is None or not umo:
        return 0
    try:
        pipe = debounce.get_if_exists(umo)
        if pipe is None:
            return 0
        bucket = getattr(pipe, "current_bucket", None)
        n = len(bucket.items) if bucket else 0
        pipe.current_bucket = None
        # 取消计时器任务，避免旧桶晚到又发车
        if bucket:
            for t in (getattr(bucket, "debounce_task", None),
                      getattr(bucket, "max_wait_task", None)):
                try:
                    if t and not t.done():
                        t.cancel()
                except Exception:  # noqa: BLE001
                    pass
        lock = getattr(pipe, "lease_lock", None)
        if lock is not None:
            tok = getattr(lock, "_current_token", None)
            if tok:
                try:
                    lock.release(tok, "recalled")
                except Exception:  # noqa: BLE001
                    pass
        return n
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s | 防抖清理失败: %s", _LOG, exc)
        return 0
