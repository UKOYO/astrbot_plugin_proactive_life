"""群聊跟读（融合自 astrbot_plugin_reread 的核心判定）。

只保留纯状态机：指纹、尾 N 全同判定、去重、冷却；禁言/动作由宿主 adapter 执行。
闭嘴期（_muted_until）由宿主统一管控，本模块绝不绕过。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any


def make_fingerprint(messages: list[Any]) -> str:
    """从单条消息组件列表生成类型指纹。"""
    if len(messages) != 1:
        return ""
    comp = messages[0]
    kind = type(comp).__name__
    if kind == "Plain":
        return "text:" + str(getattr(comp, "text", "") or "").strip()
    if kind == "Image":
        return "image:" + str(getattr(comp, "url", "") or getattr(comp, "file", "") or "")
    if kind == "Face":
        return "face:" + str(getattr(comp, "id", "") or "")
    return ""


def uniform_tail(window: deque[str], threshold: int) -> str:
    """尾 N 个指纹全同且非空时返回该指纹，否则空串。"""
    threshold = max(2, int(threshold))
    if len(window) < threshold:
        return ""
    tail = list(window)[-threshold:]
    first = tail[0]
    return first if first and all(x == first for x in tail) else ""


class RereadState:
    """每个群一份状态；带锁，避免并发重复触发。"""

    def __init__(self, threshold: int = 2, min_interval: float = 8.0) -> None:
        self.threshold = max(2, int(threshold))
        self.min_interval = max(0.0, float(min_interval))
        self.windows: dict[str, deque[str]] = {}
        self.last_fingerprint: dict[str, str] = {}
        self.last_at: dict[str, float] = {}
        self.lock = asyncio.Lock()

    def update_config(self, threshold: int | None = None, min_interval: float | None = None) -> None:
        """面板改完立即生效。"""
        if threshold is not None:
            t = max(2, int(threshold))
            if t != self.threshold:
                self.threshold = t
                for gid in list(self.windows):
                    self.windows[gid] = deque(
                        list(self.windows[gid])[-t:], maxlen=t
                    )
        if min_interval is not None:
            self.min_interval = max(0.0, float(min_interval))

    def clear_if_same_sender(self, gid: str) -> None:
        """need_different 语义由宿主在「同一人连发」时调用。"""
        self.windows.pop(gid, None)

    def observe(self, gid: str, fp: str) -> str:
        """记录一次指纹，返回可跟读的指纹（空串表示不触发）。"""
        if not fp:
            return ""
        win = self.windows.setdefault(gid, deque(maxlen=self.threshold))
        win.append(fp)
        candidate = uniform_tail(win, self.threshold)
        if not candidate or candidate == self.last_fingerprint.get(gid, ""):
            return ""
        now = time.monotonic()
        if now - self.last_at.get(gid, 0.0) < self.min_interval:
            return ""
        return candidate

    def mark_handled(self, gid: str, fp: str) -> None:
        self.last_fingerprint[gid] = fp
        self.last_at[gid] = time.monotonic()
        self.windows.pop(gid, None)

    def clear(self, gid: str) -> None:
        self.windows.pop(gid, None)
        self.last_fingerprint.pop(gid, None)
        self.last_at.pop(gid, None)

    def clear_all(self) -> None:
        self.windows.clear()
        self.last_fingerprint.clear()
        self.last_at.clear()
